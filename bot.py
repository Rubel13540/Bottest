‎"""Telegram bot for automated URL checking loop with automatic free proxy rotation and human simulation (100 visits).
‎
‎The bot accepts a public HTTP(S) URL, loops 100 times, fetches a fresh free proxy
‎for each visit, and simulates human-like browser behavior.
‎"""
‎
‎from __future__ import annotations
‎
‎import asyncio
‎import ipaddress
‎import logging
‎import os
‎import random
‎import re
‎import socket
‎import time
‎from dataclasses import dataclass
‎from urllib.parse import urlsplit
‎
‎import httpx
‎from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
‎from telegram import Update
‎from telegram.error import TelegramError
‎from telegram.ext import (
‎    ApplicationBuilder,
‎    CommandHandler,
‎    ContextTypes,
‎    MessageHandler,
‎    filters,
‎)
‎
‎
‎LOGGER = logging.getLogger(__name__)
‎REQUEST_TIMEOUT_SECONDS = 20
‎RATE_LIMIT_SECONDS = 5
‎MAX_URL_LENGTH = 2048
‎
‎# বিভিন্ন রিয়েল ডিভাইসের ইউজার এজেন্ট ও স্ক্রিন সাইজের লিস্ট
‎DEVICE_PROFILES = [
‎    {
‎        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
‎        "viewport": {"width": 1366, "height": 768},
‎        "is_mobile": False,
‎    },
‎    {
‎        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2.1 Safari/605.1.15",
‎        "viewport": {"width": 1440, "height": 900},
‎        "is_mobile": False,
‎    },
‎    {
‎        "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
‎        "viewport": {"width": 390, "height": 844},
‎        "is_mobile": True,
‎    },
‎    {
‎        "user_agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
‎        "viewport": {"width": 412, "height": 915},
‎        "is_mobile": True,
‎    },
‎]
‎
‎
‎@dataclass(frozen=True)
‎class CheckResult:
‎    """The response details shown to the Telegram user."""
‎
‎    visit_number: int
‎    status_code: int
‎    elapsed_ms: int
‎    page_title: str
‎    redirected: bool
‎    proxy_used: str
‎
‎
‎class URLCheckError(ValueError):
‎    """Raised when a URL is not safe or cannot be checked."""
‎
‎
‎_last_checked_at: dict[int, float] = {}
‎
‎
‎def _allowed_domains() -> tuple[str, ...]:
‎    """Read an optional comma-separated domain allowlist."""
‎    return tuple(
‎        domain.strip().lower().lstrip(".")
‎        for domain in os.getenv("ALLOWED_DOMAINS", "").split(",")
‎        if domain.strip()
‎    )
‎
‎
‎def _host_is_allowed(hostname: str) -> bool:
‎    """Return whether the hostname matches the configured allowlist."""
‎    allowed_domains = _allowed_domains()
‎    if not allowed_domains:
‎        return True
‎
‎    return any(
‎        hostname == domain or hostname.endswith(f".{domain}")
‎        for domain in allowed_domains
‎    )
‎
‎
‎def _host_resolves_publicly(hostname: str) -> bool:
‎    """Reject localhost, private networks, and other non-public destinations."""
‎    try:
‎        addresses = socket.getaddrinfo(
‎            hostname,
‎            None,
‎            type=socket.SOCK_STREAM,
‎        )
‎    except socket.gaierror as error:
‎        raise URLCheckError("ডোমেইনের ঠিকানা খুঁজে পাওয়া যায়নি।") from error
‎
‎    if not addresses:
‎        raise URLCheckError("ডোমেইনের কোনো নেটওয়ার্ক ঠিকানা পাওয়া যায়নি।")
‎
‎    for address in addresses:
‎        ip = ipaddress.ip_address(address[4][0])
‎
‎        if not ip.is_global:
‎            raise URLCheckError(
‎                "নিরাপত্তার কারণে লোকাল, ব্যক্তিগত বা অভ্যন্তরীণ নেটওয়ার্কের "
‎                "ঠিকানা ব্লক করা হয়েছে।"
‎            )
‎
‎    return True
‎
‎
‎def validate_url(raw_url: str) -> str:
‎    """Validate and normalize a user-provided URL."""
‎    url = raw_url.strip()
‎
‎    if len(url) > MAX_URL_LENGTH:
‎        raise URLCheckError("লিংকটি খুব দীর্ঘ। অনুগ্রহ করে ছোট একটি URL পাঠান।")
‎
‎    parsed = urlsplit(url)
‎
‎    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
‎        raise URLCheckError("দয়া করে সম্পূর্ণ HTTP বা HTTPS লিংক পাঠান।")
‎
‎    if parsed.username or parsed.password:
‎        raise URLCheckError(
‎            "নিরাপত্তার কারণে ইউজারনেম বা পাসওয়ার্ডসহ লিংক গ্রহণ করা হয় না।"
‎        )
‎
‎    hostname = parsed.hostname.rstrip(".").lower()
‎
‎    if not _host_is_allowed(hostname):
‎        raise URLCheckError(
‎            "এই ডোমেইনটি অনুমোদিত তালিকায় নেই, তাই লিংকটি ব্লক করা হয়েছে।"
‎        )
‎
‎    _host_resolves_publicly(hostname)
‎
‎    return url
‎
‎
‎async def fetch_free_proxy() -> str | None:
‎    """Fetch a live free HTTP proxy from public API."""
‎    try:
‎        async with httpx.AsyncClient(timeout=5.0) as client:
‎            response = await client.get("https://proxylist.geonode.com/api/proxy-list?limit=20&anonymityLevel=elite&protocols=http")
‎            if response.status_code == 200:
‎                data = response.json().get("data", [])
‎                if data:
‎                    proxy_item = random.choice(data)
‎                    ip = proxy_item.get("ip")
‎                    port = proxy_item.get("port")
‎                    if ip and port:
‎                        return f"http://{ip}:{port}"
‎    except Exception:
‎        pass
‎    return None
‎
‎
‎async def check_url_single_browser(url: str, visit_number: int) -> CheckResult:
‎    """Open a browser with a random free proxy (with direct IP fallback) and human behavior simulation."""
‎    started_at = time.perf_counter()
‎    profile = random.choice(DEVICE_PROFILES)
‎    proxy_url = await fetch_free_proxy()
‎
‎    async with async_playwright() as p:
‎        launch_args = [
‎            "--disable-blink-features=AutomationControlled",
‎            "--no-sandbox",
‎            "--disable-setuid-sandbox",
‎            "--disable-dev-shm-usage",
‎        ]
‎        
‎        browser_kwargs = {"headless": True, "args": launch_args}
‎        browser = await p.chromium.launch(**browser_kwargs)
‎        
‎        # প্রক্সি কাজ না করলে সরাসরি ডিরেক্ট কানেকশনে ফলব্যাক করার ব্যবস্থা
‎        used_proxy_label = proxy_url or "Direct IP"
‎        
‎        try:
‎            context_kwargs = {
‎                "user_agent": profile["user_agent"],
‎                "viewport": profile["viewport"],
‎                "is_mobile": profile["is_mobile"],
‎                "locale": "en-US,en;q=0.9",
‎            }
‎            if proxy_url:
‎                context_kwargs["proxy"] = {"server": proxy_url}
‎
‎            context = await browser.new_context(**context_kwargs)
‎            page = await context.new_page()
‎
‎            await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
‎
‎            try:
‎                response = await page.goto(
‎                    url,
‎                    timeout=REQUEST_TIMEOUT_SECONDS * 1000,
‎                    wait_until="domcontentloaded",
‎                )
‎            except PlaywrightTimeoutError:
‎                # প্রক্সি ফেইল করলে একবার প্রক্সি ছাড়া ট্রাই করার সুযোগ দেওয়া যেতে পারে
‎                if proxy_url:
‎                    used_proxy_label = "Direct IP (Fallback)"
‎                    await context.close()
‎                    context_kwargs.pop("proxy", None)
‎                    context = await browser.new_context(**context_kwargs)
‎                    page = await context.new_page()
‎                    response = await page.goto(
‎                        url,
‎                        timeout=REQUEST_TIMEOUT_SECONDS * 1000,
‎                        wait_until="domcontentloaded",
‎                    )
‎                else:
‎                    raise
‎
‎            if response is None:
‎                raise URLCheckError("সার্ভার থেকে কোনো রেসপন্স পাওয়া যায়নি।")
‎
‎            status_code = response.status
‎            try:
‎                page_title = await page.title()
‎            except Exception:
‎                page_title = "No Title"
‎            
‎            redirected = page.url.rstrip("/") != url.rstrip("/")
‎
‎            try:
‎                await page.mouse.move(random.randint(50, 200), random.randint(50, 200))
‎                await page.evaluate("window.scrollBy(0, window.innerHeight / 2)")
‎                await page.wait_for_timeout(random.randint(2000, 4000))
‎            except Exception:
‎                pass
‎
‎        except PlaywrightTimeoutError as error:
‎            raise URLCheckError(
‎                "প্রক্সি কাজ করছে না বা ব্রাউজার সময়মতো লোড করতে পারেনি।"
‎            ) from error
‎        except Exception as error:
‎            raise URLCheckError(
‎                f"ব্রাউজার ভিজিটের সময় ত্রুটি ঘটেছে: {error}"
‎            ) from error
‎        finally:
‎            await browser.close()
‎
‎    elapsed_ms = round((time.perf_counter() - started_at) * 1000)
‎
‎    return CheckResult(
‎        visit_number=visit_number,
‎        status_code=status_code,
‎        elapsed_ms=elapsed_ms,
‎        page_title=page_title or "No Title",
‎        redirected=redirected,
‎        proxy_used=used_proxy_label,
‎    )
‎
‎
‎def _rate_limit_message(chat_id: int) -> str | None:
‎    """Return a wait message when a chat is checking too frequently."""
‎    now = time.monotonic()
‎    last_checked_at = _last_checked_at.get(chat_id)
‎
‎    if last_checked_at is not None:
‎        remaining = RATE_LIMIT_SECONDS - (now - last_checked_at)
‎
‎        if remaining > 0:
‎            return (
‎                f"নিরাপত্তার জন্য অনুগ্রহ করে আরও {round(remaining)} সেকেন্ড অপেক্ষা "
‎                "করুন, তারপর আরেকটি লিংক পরীক্ষা করুন।"
‎            )
‎
‎    _last_checked_at[chat_id] = now
‎    return None
‎
‎
‎def _http_failure_reason(status_code: int) -> str:
‎    """Return a clear Bengali explanation for common HTTP failure codes."""
‎    reasons = {
‎        400: "অনুরোধটি সঠিক নয়",
‎        401: "অনুমতি প্রয়োজন",
‎        403: "সার্ভার অনুরোধ প্রত্যাখ্যান করেছে",
‎        404: "লিংকটি পাওয়া যায়নি",
‎        408: "সার্ভার সময়মতো উত্তর দেয়নি",
‎        429: "সার্ভার অনেক বেশি অনুরোধ পেয়েছে",
‎        500: "সার্ভারে অভ্যন্তরীণ ত্রুটি হয়েছে",
‎        502: "সার্ভার থেকে ভুল উত্তর পাওয়া গেছে",
‎        503: "সার্ভার বর্তমানে সেবা দিচ্ছে না",
‎        504: "সার্ভার সময়মতো অন্য সার্ভার থেকে উত্তর পায়নি",
‎    }
‎
‎    return reasons.get(status_code, "সার্ভার অনুরোধটি ব্যর্থ করেছে")
‎
‎
‎def _format_result(result: CheckResult) -> str:
‎    """Format a check result for Telegram."""
‎    if result.status_code >= 400:
‎        return (
‎            f"ভিজিট নম্বর: {result.visit_number}/100\n"
‎            "ফলাফল: ব্যর্থ\n"
‎            f"কারণ: HTTP {result.status_code} — "
‎            f"{_http_failure_reason(result.status_code)}\n"
‎            f"আইপি/প্রক্সি: {result.proxy_used}\n"
‎            f"পেজ টাইটেল: {result.page_title}\n"
‎            f"লোডের সময়: {result.elapsed_ms} মিলিসেকেন্ড"
‎        )
‎
‎    status = "রিডাইরেক্ট" if result.redirected else "সফল"
‎    return (
‎        f"ভিজিট নম্বর: {result.visit_number}/100\n"
‎        f"ফলাফল: {status} (প্রক্সি ও হিউম্যান ভিজিট)\n"
‎        f"HTTP স্ট্যাটাস: {result.status_code}\n"
‎        f"আইপি/প্রক্সি: {result.proxy_used}\n"
‎        f"পেজ টাইটেল: {result.page_title}\n"
‎        f"লোডের সময়: {result.elapsed_ms} মিলিসেকেন্ড"
‎    )
‎
‎
‎async def run_hundred_visits(update: Update, url: str) -> None:
‎    """Loop 100 times using free proxy rotation and human-like actions with safe message editing."""
‎    if not update.message:
‎        return
‎
‎    progress_message = await update.message.reply_text("🔄 প্রক্সি রোটেশন ও হিউম্যান সিমুলেশনসহ ১০০ বার ভিজিট শুরু হচ্ছে...")
‎
‎    success_count = 0
‎    fail_count = 0
‎    status_text = ""
‎
‎    for i in range(1, 101):
‎        if i > 1:
‎            await asyncio.sleep(random.randint(2, 4))
‎
‎        try:
‎            result = await check_url_single_browser(url, i)
‎            if result.status_code < 400:
‎                success_count += 1
‎            else:
‎                fail_count += 1
‎            status_text = _format_result(result)
‎        except URLCheckError as error:
‎            fail_count += 1
‎            status_text = (
‎                f"ভিজিট নম্বর: {i}/100\n"
‎                "ফলাফল: ব্যর্থ\n"
‎                f"কারণ: {error}"
‎            )
‎
‎        # প্রতি ৫ বা ১০ ভিজিট পর পর নিরাপদভাবে মেসেজ আপডেট করা যাতে টেলিগ্রাম ফ্লাড লিমিট না ধরে
‎        if i % 5 == 0 or i == 100:
‎            try:
‎                summary_header = f"📊 ভিজিট প্রোগ্রেস: {i}/100 সম্পন্ন (সফল: {success_count}, ব্যর্থ: {fail_count})\n\n"
‎                await progress_message.edit_text(summary_header + status_text)
‎            except TelegramError:
‎                pass  # ফ্লাড কন্ট্রোল বা সেইম টেক্সট এক্সেপশন হ্যান্ডেল করার জন্য
‎
‎    try:
‎        await update.message.reply_text(f"🎉 ১০০টি ভিজিট সম্পন্ন হয়েছে!\nমোট সফল: {success_count}\nমোট ব্যর্থ: {fail_count}")
‎    except Exception:
‎        pass
‎
‎
‎async def handle_message(
‎    update: Update,
‎    context: ContextTypes.DEFAULT_TYPE,
‎) -> None:
‎    """Validate a URL and start the 100-visit browser loop."""
‎    del context
‎
‎    if not update.message or not update.effective_chat:
‎        return
‎
‎    raw_url = update.message.text or ""
‎    rate_limit_message = _rate_limit_message(update.effective_chat.id)
‎
‎    if rate_limit_message:
‎        await update.message.reply_text(rate_limit_message)
‎        return
‎
‎    try:
‎        url = validate_url(raw_url)
‎    except URLCheckError as error:
‎        await update.message.reply_text(
‎            f"লিংকটি পরীক্ষা করা যায়নি। কারণ: {error}"
‎        )
‎        return
‎
‎    asyncio.create_task(run_hundred_visits(update, url))
‎
‎
‎async def start(
‎    update: Update,
‎    context: ContextTypes.DEFAULT_TYPE,
‎) -> None:
‎    """Welcome users and explain how to use the bot."""
‎    del context
‎
‎    if update.message:
‎        await update.message.reply_text(
‎            "Welcome! Send me a link for proxy-rotated browser visits."
‎        )
‎
‎
‎def main() -> None:
‎    """Start the Telegram bot using the Replit secret."""
‎    token = os.getenv("TELEGRAM_BOT_TOKEN")
‎
‎    if not token:
‎        raise RuntimeError(
‎            "TELEGRAM_BOT_TOKEN is not configured. Add it to Replit Secrets."
‎        )
‎
‎    token = token.strip()
‎
‎    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token):
‎        raise RuntimeError(
‎            "TELEGRAM_BOT_TOKEN has an invalid format. "
‎            "Use the token from BotFather."
‎        )
‎
‎    logging.basicConfig(
‎        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
‎        level=logging.INFO,
‎    )
‎
‎    logging.getLogger("httpx").setLevel(logging.WARNING)
‎    logging.getLogger("httpcore").setLevel(logging.WARNING)
‎
‎    application = ApplicationBuilder().token(token).build()
‎
‎    application.add_handler(CommandHandler("start", start))
‎    application.add_handler(
‎        MessageHandler(
‎            filters.TEXT & ~filters.COMMAND,
‎            handle_message,
‎        )
‎    )
‎
‎    LOGGER.info("Telegram bot is running with Free Proxy Rotation loop.")
‎    application.run_polling()
‎
‎
‎if __name__ == "__main__":
‎    main()
