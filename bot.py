"""Telegram bot for automated URL checking loop with automatic free proxy rotation 
and human simulation (100 visits), optimized to prevent crashes and flood limits.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import random
import re
import socket
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


LOGGER = logging.getLogger(__name__)
REQUEST_TIMEOUT_SECONDS = 20
RATE_LIMIT_SECONDS = 5
MAX_URL_LENGTH = 2048

DEVICE_PROFILES = [
    {
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "viewport": {"width": 1366, "height": 768},
        "is_mobile": False,
    },
    {
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2.1 Safari/605.1.15",
        "viewport": {"width": 1440, "height": 900},
        "is_mobile": False,
    },
    {
        "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
        "viewport": {"width": 390, "height": 844},
        "is_mobile": True,
    },
    {
        "user_agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
        "viewport": {"width": 412, "height": 915},
        "is_mobile": True,
    },
]


@dataclass(frozen=True)
class CheckResult:
    visit_number: int
    status_code: int
    elapsed_ms: int
    page_title: str
    redirected: bool
    proxy_used: str


class URLCheckError(ValueError):
    """Raised when a URL is not safe or cannot be checked."""


_last_checked_at: dict[int, float] = {}


def _host_is_allowed(hostname: str) -> bool:
    allowed_domains = tuple(
        domain.strip().lower().lstrip(".")
        for domain in os.getenv("ALLOWED_DOMAINS", "").split(",")
        if domain.strip()
    )
    if not allowed_domains:
        return True
    return any(hostname == domain or hostname.endswith(f".{domain}") for domain in allowed_domains)


def _host_resolves_publicly(hostname: str) -> bool:
    try:
        addresses = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise URLCheckError("ডোমেইনের ঠিকানা খুঁজে পাওয়া যায়নি।") from error

    if not addresses:
        raise URLCheckError("ডোমেইনের কোনো নেটওয়ার্ক ঠিকানা পাওয়া যায়নি।")

    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise URLCheckError("নিরাপত্তার কারণে লোকাল বা ব্যক্তিগত নেটওয়ার্কের ঠিকানা ব্লক করা হয়েছে।")

    return True


def validate_url(raw_url: str) -> str:
    url = raw_url.strip()
    if len(url) > MAX_URL_LENGTH:
        raise URLCheckError("লিংকটি খুব দীর্ঘ।")

    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise URLCheckError("দয়া করে সম্পূর্ণ HTTP বা HTTPS লিংক পাঠান।")

    if parsed.username or parsed.password:
        raise URLCheckError("ইউজারনেম বা পাসওয়ার্ডসহ লিংক গ্রহণ করা হয় না।")

    hostname = parsed.hostname.rstrip(".").lower()
    if not _host_is_allowed(hostname):
        raise URLCheckError("এই ডোমেইনটি অনুমোদিত তালিকায় নেই।")

    _host_resolves_publicly(hostname)
    return url


async def fetch_free_proxy() -> str | None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get("https://proxylist.geonode.com/api/proxy-list?limit=20&anonymityLevel=elite&protocols=http")
            if response.status_code == 200:
                data = response.json().get("data", [])
                if data:
                    proxy_item = random.choice(data)
                    ip = proxy_item.get("ip")
                    port = proxy_item.get("port")
                    if ip and port:
                        return f"http://{ip}:{port}"
    except Exception:
        pass
    return None


async def check_url_with_browser(browser, url: str, visit_number: int) -> CheckResult:
    """একটিভ ব্রাউজার ইনস্ট্যান্স ব্যবহার করে সিঙ্গেল ভিজিট সম্পন্ন করে।"""
    started_at = time.perf_counter()
    profile = random.choice(DEVICE_PROFILES)
    proxy_url = await fetch_free_proxy()
    used_proxy_label = proxy_url or "Direct IP"

    context_kwargs = {
        "user_agent": profile["user_agent"],
        "viewport": profile["viewport"],
        "is_mobile": profile["is_mobile"],
        "locale": "en-US,en;q=0.9",
    }
    if proxy_url:
        context_kwargs["proxy"] = {"server": proxy_url}

    context = await browser.new_context(**context_kwargs)
    page = await context.new_page()

    try:
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        try:
            response = await page.goto(url, timeout=REQUEST_TIMEOUT_SECONDS * 1000, wait_until="domcontentloaded")
        except PlaywrightTimeoutError:
            if proxy_url:
                used_proxy_label = "Direct IP (Fallback)"
                await context.close()
                context_kwargs.pop("proxy", None)
                context = await browser.new_context(**context_kwargs)
                page = await context.new_page()
                response = await page.goto(url, timeout=REQUEST_TIMEOUT_SECONDS * 1000, wait_until="domcontentloaded")
            else:
                raise

        if response is None:
            raise URLCheckError("সার্ভার থেকে কোনো রেসপন্স পাওয়া যায়নি।")

        status_code = response.status
        try:
            page_title = await page.title()
        except Exception:
            page_title = "No Title"

        redirected = page.url.rstrip("/") != url.rstrip("/")

        try:
            await page.mouse.move(random.randint(50, 200), random.randint(50, 200))
            await page.evaluate("window.scrollBy(0, window.innerHeight / 2)")
            await page.wait_for_timeout(random.randint(1000, 2000))
        except Exception:
            pass

    except PlaywrightTimeoutError as error:
        raise URLCheckError("প্রক্সি কাজ করছে না বা ব্রাউজার সময়মতো লোড করতে পারেনি।") from error
    except Exception as error:
        raise URLCheckError(f"ত্রুটি ঘটেছে: {error}") from error
    finally:
        await context.close()

    elapsed_ms = round((time.perf_counter() - started_at) * 1000)
    return CheckResult(
        visit_number=visit_number,
        status_code=status_code,
        elapsed_ms=elapsed_ms,
        page_title=page_title or "No Title",
        redirected=redirected,
        proxy_used=used_proxy_label,
    )


async def run_hundred_visits(update: Update, url: str) -> None:
    """লুপ চালিয়ে ১০০ বার ভিজিট শেষ করে এবং একবারে ফাইনাল রিপোর্ট পাঠায়।"""
    if not update.message:
        return

    progress_message = await update.message.reply_text("🔄 প্রক্সি রোটেশন ও হিউম্যান সিমুলেশনসহ ১০০ বার ভিজিট শুরু হয়েছে... দয়া করে অপেক্ষা করুন।")

    success_count = 0
    fail_count = 0
    last_status_text = ""

    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
    ]

    # ব্রাউজার একবারই ওপেন হবে, ১০০ বার ক্র্যাশ হওয়া থেকে বাঁচবে
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=launch_args)
        
        try:
            for i in range(1, 101):
                if i > 1:
                    await asyncio.sleep(random.randint(1, 3))

                try:
                    result = await check_url_with_browser(browser, url, i)
                    if result.status_code < 400:
                        success_count += 1
                    else:
                        fail_count += 1
                    
                    status = "রিডাইরেক্ট" if result.redirected else "সফল"
                    last_status_text = (
                        f"শেষ ভিজিট ({i}/100):\n"
                        f"ফলাফল: {status} | HTTP: {result.status_code}\n"
                        f"প্রক্সি: {result.proxy_used}\n"
                        f"টাইটেল: {result.page_title}"
                    )
                except URLCheckError as error:
                    fail_count += 1
                    last_status_text = f"শেষ ভিজিট ({i}/100):\nফলাফল: ব্যর্থ\nকারণ: {error}"

        finally:
            await browser.close()

    # প্রোগ্রেস মেসেজটি ডিলিট বা এডিট করে ফাইনাল রিপোর্ট পাঠিয়ে দেওয়া
    try:
        await progress_message.delete()
    except Exception:
        pass

    final_report = (
        f"🎉 **১০০টি ভিজিট সফলভাবে সম্পন্ন হয়েছে!**\n\n"
        f"✅ মোট সফল: {success_count}\n"
        f"❌ মোট ব্যর্থ: {fail_count}\n\n"
        f"--- সাম্প্রতিক স্ট্যাটাস ---\n"
        f"{last_status_text}"
    )

    try:
        await update.message.reply_text(final_report, parse_mode="Markdown")
    except TelegramError:
        await update.message.reply_text(final_report)


def _rate_limit_message(chat_id: int) -> str | None:
    now = time.monotonic()
    last_checked_at = _last_checked_at.get(chat_id)
    if last_checked_at is not None:
        remaining = RATE_LIMIT_SECONDS - (now - last_checked_at)
        if remaining > 0:
            return f"নিরাপত্তার জন্য আরও {round(remaining)} সেকেন্ড অপেক্ষা করুন।"
    _last_checked_at[chat_id] = now
    return None


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not update.message or not update.effective_chat:
        return

    raw_url = update.message.text or ""
    rate_limit_message = _rate_limit_message(update.effective_chat.id)
    if rate_limit_message:
        await update.message.reply_text(rate_limit_message)
        return

    try:
        url = validate_url(raw_url)
    except URLCheckError as error:
        await update.message.reply_text(f"লিংকটি পরীক্ষা করা যায়নি। কারণ: {error}")
        return

    asyncio.create_task(run_hundred_visits(update, url))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.message:
        await update.message.reply_text("Welcome! Send me a link for proxy-rotated browser visits.")


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token or not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token.strip()):
        raise RuntimeError("Valid TELEGRAM_BOT_TOKEN is required.")

    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    
    application = ApplicationBuilder().token(token.strip()).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    LOGGER.info("Bot is running...")
    application.run_polling()


if __name__ == "__main__":
    main()
