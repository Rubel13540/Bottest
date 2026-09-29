import os
import asyncio
import sqlite3
import logging
from decimal import Decimal, InvalidOperation

import aiohttp
from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

# =========================================================
# CONFIG
# =========================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

SMM_API_KEY = os.getenv(
    "SMM_API_KEY",
    "7c40378a49cfaec87f7e0b54fd646dd4",
).strip()

SMM_API_URL = "https://socialpanel.pro/api/v2"

ADMIN_ID = 5293614793

BKASH_NUMBER = "01911198221 (Personal)"
NAGAD_NUMBER = "01911198221 (Personal)"
WHATSAPP_URL = "https://wa.me/message/ZFPUNOUHWSWRI1"

USD_TO_BDT = Decimal("120")
PROFIT_MULTIPLIER = Decimal("1.30")

WELCOME_BONUS = Decimal("0.00") 

# প্রতি পেজে ১৫টি সার্ভিস দেখানোর কনফিগারেশন
SERVICES_PER_PAGE = 15

def get_welcome_bonus():
    try:
        raw = get_setting("welcome_bonus", str(WELCOME_BONUS))
        amount = Decimal(str(raw))
        return amount if amount >= 0 else Decimal("0")
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")

DB_FILE = "bot.db"

# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# =========================================================
# DATABASE
# =========================================================

def get_db():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def column_exists(conn, table, column):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in rows)


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance REAL NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            service_id TEXT,
            service_name TEXT,
            link TEXT,
            quantity INTEGER,
            charge REAL,
            provider_order_id TEXT,
            status TEXT DEFAULT 'Pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS recharge_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            method TEXT,
            amount REAL NOT NULL,
            transaction_id TEXT,
            status TEXT DEFAULT 'Pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    if not column_exists(conn, "users", "welcome_bonus_given"):
        cur.execute(
            "ALTER TABLE users ADD COLUMN welcome_bonus_given INTEGER NOT NULL DEFAULT 0"
        )

    conn.commit()
    conn.close()


def ensure_user(tg_user):
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT user_id, welcome_bonus_given FROM users WHERE user_id = ?",
        (tg_user.id,),
    )
    row = cur.fetchone()

    if row is None:
        bonus = get_welcome_bonus()
        cur.execute(
            """
            INSERT INTO users (
                user_id, username, first_name, balance, welcome_bonus_given
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                tg_user.id,
                tg_user.username or "",
                tg_user.first_name or "",
                float(bonus),
                1,
            ),
        )
        is_new = True
    else:
        cur.execute(
            """
            UPDATE users
            SET username = ?, first_name = ?
            WHERE user_id = ?
            """,
            (
                tg_user.username or "",
                tg_user.first_name or "",
                tg_user.id,
            ),
        )
        is_new = False

    conn.commit()
    conn.close()
    return is_new


def get_setting(key, default=""):
    conn = get_db()
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?",
        (key,),
    ).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    conn = get_db()
    conn.execute(
        """
        INSERT INTO settings(key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, str(value)),
    )
    conn.commit()
    conn.close()


def maintenance_enabled():
    return get_setting("maintenance", "0") == "1"


def get_balance(user_id):
    conn = get_db()
    row = conn.execute(
        "SELECT balance FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    conn.close()

    if not row:
        return Decimal("0")
    return Decimal(str(row["balance"]))


def get_user_orders(user_id, limit=10):
    conn = get_db()
    rows = conn.execute(
        """
        SELECT id, service_name, link, quantity, charge,
               provider_order_id, status, created_at
        FROM orders
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()
    conn.close()
    return rows


# =========================================================
# HELPERS
# =========================================================

def money(value):
    return f"{Decimal(str(value)):.2f}"


def is_admin(user_id):
    return user_id == ADMIN_ID


async def maintenance_guard(update):
    if not maintenance_enabled():
        return False
    if update.effective_user and is_admin(update.effective_user.id):
        return False
    if update.message:
        await update.message.reply_text(
            "🛠️ Bot maintenance mode-এ আছে।\n"
            "কিছুক্ষণ পরে আবার চেষ্টা করুন।"
        )
    elif update.callback_query:
        await update.callback_query.answer(
            "🛠️ Maintenance mode চলছে।",
            show_alert=True,
        )
    return True


# =========================================================
# KEYBOARDS
# =========================================================

def main_keyboard(user_id=None):
    rows = [
        [KeyboardButton("🛍 Services"), KeyboardButton("🔎 Search ID")],
        [KeyboardButton("👤 Account"), KeyboardButton("📦 My Orders")],
        [KeyboardButton("💳 Recharge"), KeyboardButton("📞 Helpline")],
        [KeyboardButton("📜 Terms")],
    ]
    if user_id == ADMIN_ID:
        rows.append([KeyboardButton("👑 Admin Panel")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def admin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Add Balance", callback_data="admin_add"),
            InlineKeyboardButton("➖ Deduct Balance", callback_data="admin_deduct"),
        ],
        [
            InlineKeyboardButton("👤 User Info", callback_data="admin_user_info"),
            InlineKeyboardButton("📊 Statistics", callback_data="admin_stats"),
        ],
        [
            InlineKeyboardButton("❌ Close", callback_data="admin_close"),
        ],
    ])


def services_category_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📘 Facebook", callback_data="category_facebook"),
            InlineKeyboardButton("🎵 TikTok", callback_data="category_tiktok"),
        ],
        [
            InlineKeyboardButton("▶️ YouTube", callback_data="category_youtube"),
            InlineKeyboardButton("🚦 Traffic", callback_data="category_traffic"),
        ],
        [InlineKeyboardButton("📱 Telegram", callback_data="category_telegram")],
        [InlineKeyboardButton("❌ Close", callback_data="close_services")],
    ])


def recharge_method_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("bKash (Personal)", callback_data="recharge_bkash"),
            InlineKeyboardButton("Nagad (Personal)", callback_data="recharge_nagad"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")],
    ])


# =========================================================
# SERVICES & PAGINATION (15 items per page)
# =========================================================

SERVICE_CATEGORIES = {
    "facebook": "📘 Facebook",
    "tiktok": "🎵 TikTok",
    "youtube": "▶️ YouTube",
    "traffic": "🚦 Traffic",
    "telegram": "📱 Telegram",
}


def service_category(service):
    text = " ".join(
        str(service.get(key, "") or "")
        for key in ("name", "category", "type", "desc")
    ).lower()

    if any(k in text for k in ("facebook", "facebook.com", " fb ", "fb.", "fb_")):
        return "facebook"
    if "tiktok" in text or "tik tok" in text:
        return "tiktok"
    if "youtube" in text or "youtu.be" in text or " yt " in text:
        return "youtube"
    if any(k in text for k in (
        "traffic", "website visitor", "website visitors",
        "web traffic", "site traffic", "seo traffic"
    )):
        return "traffic"
    if any(k in text for k in (
        "telegram", "t.me", "telegram.me", "telegram channel",
        "telegram group", "telegram member", "telegram members",
        "telegram view", "telegram views", "telegram reaction",
        "telegram reactions", "telegram post"
    )):
        return "telegram"
    return None


def filter_services_by_category(services, category):
    return [s for s in services if service_category(s) == category]


def services_keyboard_paginated(services, category, page=0):
    buttons = []
    start_idx = page * SERVICES_PER_PAGE
    end_idx = start_idx + SERVICES_PER_PAGE
    page_services = services[start_idx:end_idx]

    for service in page_services:
        sid = str(service.get("service", ""))
        name = str(service.get("name", "Service"))
        
        display_name = f"{sid} {name}"
        if len(display_name) > 50:
            display_name = display_name[:47] + "..."

        callback = f"service_{sid}"
        buttons.append([
            InlineKeyboardButton(display_name, callback_data=callback)
        ])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"page_{category}_{page-1}"))
    if end_idx < len(services):
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"page_{category}_{page+1}"))
    
    if nav_row:
        buttons.append(nav_row)

    buttons.append([
        InlineKeyboardButton("⬅️ Categories", callback_data="service_categories"),
        InlineKeyboardButton("❌ Close", callback_data="close_services"),
    ])
    return InlineKeyboardMarkup(buttons)


# =========================================================
# SMM API
# =========================================================

async def api_request(payload):
    payload["key"] = SMM_API_KEY
    async with aiohttp.ClientSession() as session:
        async with session.post(SMM_API_URL, data=payload, timeout=20) as resp:
            return await resp.json()


async def get_all_services():
    return await api_request({"action": "services"})


# =========================================================
# COMMANDS & HANDLERS
# =========================================================

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_new = ensure_user(user)
    bonus = get_welcome_bonus()

    msg = f"👋 **Welcome, {user.first_name}!**\n\nআমাদের SMM Panel Bot-এ আপনাকে স্বাগতম।"
    if is_new and bonus > 0:
        msg += f"\n🎉 **নতুন একাউন্ট বোনাস:** ৳{money(bonus)}"

    await update.message.reply_text(
        msg,
        reply_markup=main_keyboard(user.id),
        parse_mode="Markdown"
    )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await maintenance_guard(update):
        return

    text = update.message.text.strip()
    user_id = update.effective_user.id

    # সার্ভিস সার্চ অপশন চেক
    if context.user_data.get("awaiting_search_id"):
        context.user_data["awaiting_search_id"] = False
        all_services = await get_all_services()
        
        if isinstance(all_services, list):
            found_service = next((s for s in all_services if str(s.get("service")) == text), None)
            if found_service:
                sid = found_service.get("service")
                sname = found_service.get("name")
                min_q = found_service.get("min", "N/A")
                max_q = found_service.get("max", "N/A")
                rate = found_service.get("rate", "N/A")

                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🛒 Order Now", callback_data=f"service_{sid}")],
                    [InlineKeyboardButton("❌ Close", callback_data="close_services")]
                ])

                await update.message.reply_text(
                    f"🔍 **Service Found!**\n\n"
                    f"🆔 **ID:** `{sid}`\n"
                    f"📌 **Name:** {sname}\n"
                    f"💰 **Rate per 1k:** ${rate}\n"
                    f"📊 **Min/Max:** {min_q} / {max_q}",
                    reply_markup=kb,
                    parse_mode="Markdown"
                )
                return
            else:
                await update.message.reply_text(
                    f"❌ `{text}` আইডির কোনো সার্ভিস খুঁজে পাওয়া যায়নি। সঠিক আইডি দিন বা সার্ভিস লিস্ট থেকে দেখুন।",
                    parse_mode="Markdown"
                )
                return

    if text == "🛍 Services":
        await update.message.reply_text(
            "📂 অনুগ্রহ করে একটি ক্যাটাগরি বেছে নিন:",
            reply_markup=services_category_keyboard()
        )
    elif text == "🔎 Search ID":
        context.user_data["awaiting_search_id"] = True
        await update.message.reply_text(
            "🔍 আপনি যে সার্ভিসটি খুঁজতে চান তার **Service ID** (যেমন: `23170`) লিখে মেসেজ পাঠান:"
        )
    elif text == "👤 Account":
        bal = get_balance(user_id)
        await update.message.reply_text(
            f"👤 **Account Info**\n\n"
            f"🆔 User ID: `{user_id}`\n"
            f"💰 Balance: ৳{money(bal)} BDT",
            parse_mode="Markdown"
        )
    elif text == "📦 My Orders":
        orders = get_user_orders(user_id)
        if not orders:
            await update.message.reply_text("📦 আপনার কোনো অর্ডার পাওয়া যায়নি।")
            return
        
        msg = "📦 **আপনার সাম্প্রতিক অর্ডারসমূহ:**\n\n"
        for o in orders:
            msg += (
                f"🆔 **Order ID:** `{o['id']}`\n"
                f"📌 Service: {o['service_name']}\n"
                f"🔢 Qty: {o['quantity']} | 💰 Charge: ৳{money(o['charge'])}\n"
                f"📊 Status: `{o['status']}`\n"
                f"----------------------------------------\n"
            )
        await update.message.reply_text(msg, parse_mode="Markdown")
    elif text == "💳 Recharge":
        await update.message.reply_text(
            "💳 **Recharge**\n\nএকটি পেমেন্ট মেথড সিলেক্ট করুন:",
            reply_markup=recharge_method_keyboard()
        )
    elif text == "📞 Helpline":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 WhatsApp Support", url=WHATSAPP_URL)]
        ])
        await update.message.reply_text(
            "📞 কোনো সাহায্য বা সমস্যার জন্য সরাসরি হোয়াটসঅ্যাপে যোগাযোগ করুন:",
            reply_markup=keyboard
        )
    elif text == "📜 Terms":
        await update.message.reply_text(
            "📜 **Terms & Conditions:**\n"
            "১. ভুল লিংক বা ভুল ইনফরমেশন দিলে অর্ডার রিফান্ড দেওয়া হবে না।\n"
            "২. পেমেন্ট ভেরিফিকেশনে কিছুটা সময় লাগতে পারে।"
        )
    elif text == "👑 Admin Panel" and is_admin(user_id):
        await update.message.reply_text(
            "👑 **Admin Panel Controls**",
            reply_markup=admin_keyboard()
        )


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id

    await query.answer()

    if data == "close_services":
        await query.message.delete()
        return

    # Category Selection
    if data.startswith("category_"):
        cat = data.split("_")[1]
        all_services = await get_all_services()
        if isinstance(all_services, list):
            filtered = filter_services_by_category(all_services, cat)
            if not filtered:
                await query.edit_message_text("❌ এই ক্যাটাগরিতে কোনো সার্ভিস পাওয়া যায়নি।")
                return
            
            context.user_data["cat_services"] = filtered
            kb = services_keyboard_paginated(filtered, cat, page=0)
            await query.edit_message_text(
                f"📋 **{SERVICE_CATEGORIES.get(cat, 'Services')} (Page 1):**",
                reply_markup=kb,
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text("❌ সার্ভিস লিস্ট লোড করতে সমস্যা হয়েছে।")

    # Pagination Handler (15 Items Per Page)
    elif data.startswith("page_"):
        _, cat, page_str = data.split("_")
        page = int(page_str)
        filtered = context.user_data.get("cat_services", [])
        
        if not filtered:
            all_services = await get_all_services()
            if isinstance(all_services, list):
                filtered = filter_services_by_category(all_services, cat)
                context.user_data["cat_services"] = filtered

        if filtered:
            kb = services_keyboard_paginated(filtered, cat, page=page)
            await query.edit_message_text(
                f"📋 **{SERVICE_CATEGORIES.get(cat, 'Services')} (Page {page+1}):**",
                reply_markup=kb,
                parse_mode="Markdown"
            )

    elif data == "service_categories":
        await query.edit_message_text(
            "📂 অনুগ্রহ করে একটি ক্যাটাগরি বেছে নিন:",
            reply_markup=services_category_keyboard()
        )

    # Recharge options
    elif data in ("recharge_bkash", "recharge_nagad"):
        method = "bKash" if "bkash" in data else "Nagad"
        number = BKASH_NUMBER if method == "bKash" else NAGAD_NUMBER
        
        await query.edit_message_text(
            f"💳 **{method} Payment**\n\n"
            f"🔹 Number: `{number}`\n\n"
            "অনুগ্রহ করে উপরোক্ত নাম্বারে **Send Money** করার পর Admin-এর সাথে সাপোর্ট নাম্বারে যোগাযোগ করুন।",
            parse_mode="Markdown"
        )


# =========================================================
# MAIN
# =========================================================

def main():
    init_db()

    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is missing!")
        return

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    logger.info("Bot started successfully...")
    app.run_polling()


if __name__ == "__main__":
    main()
