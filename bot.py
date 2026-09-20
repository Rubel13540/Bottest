import os
import sqlite3
import logging
from decimal import Decimal

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

# =========================================================
# CONFIG
# =========================================================

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
SMM_API_KEY = os.getenv("SMM_API_KEY", "Bd48e602a5dfe6d1dbcb31102130458c").strip()
SMM_API_URL = "https://socialpanel.pro/api/v2"
ADMIN_ID = int(os.getenv("ADMIN_ID", "5293614793"))

USD_TO_BDT = Decimal("120")
PROFIT_MULTIPLIER = Decimal("1.30")
DB_FILE = "bot.db"

# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is missing.")

if not SMM_API_KEY:
    raise RuntimeError("SMM_API_KEY environment variable is missing.")

# =========================================================
# DATABASE
# =========================================================

def db_connect():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db_connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            provider_order_id TEXT,
            service_id TEXT,
            service_name TEXT,
            link TEXT,
            quantity INTEGER,
            charge REAL,
            status TEXT DEFAULT 'Pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def register_user(user):
    conn = db_connect()
    conn.execute(
        """
        INSERT INTO users (user_id, username, first_name)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name
        """,
        (user.id, user.username or "", user.first_name or ""),
    )
    conn.commit()
    conn.close()

def get_balance(user_id):
    conn = db_connect()
    row = conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return Decimal(str(row["balance"])) if row else Decimal("0")

# =========================================================
# SMM API
# =========================================================

async def smm_request(payload):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(SMM_API_URL, json=payload, headers=headers) as response:
                text = await response.text()
                if response.status != 200:
                    logger.error("SMM API HTTP ERROR: %s", text)
                    return None
                try:
                    return await response.json(content_type=None)
                except Exception:
                    logger.error("SMM API returned invalid JSON: %s", text)
                    return None
    except Exception as e:
        logger.exception("SMM API request error: %s", e)
        return None

async def fetch_smm_services():
    payload = {"key": SMM_API_KEY, "action": "services"}
    result = await smm_request(payload)
    if isinstance(result, list):
        return result
    return []

def detect_platform(service_name):
    name = service_name.lower()
    if any(x in name for x in ["facebook", "fb"]):
        return "Facebook"
    if any(x in name for x in ["youtube", "yt"]):
        return "YouTube"
    if "tiktok" in name or "tik tok" in name:
        return "TikTok"
    return None

def calculate_customer_price(rate):
    try:
        usd_rate = Decimal(str(rate))
        return (usd_rate * USD_TO_BDT * PROFIT_MULTIPLIER).quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0")

# =========================================================
# MAIN MENU KEYBOARD
# =========================================================

def get_main_reply_keyboard():
    keyboard = [
        [KeyboardButton("🚀 সার্ভিস ক্যাটালগ"), KeyboardButton("📍 অর্ডার ট্র্যাক করুন")],
        [KeyboardButton("👤 প্রোফাইল ও ব্যালেন্স"), KeyboardButton("💳 ব্যালেন্স রিচার্জ")],
        [KeyboardButton("💬 ২৪/৭ হেল্পলাইন"), KeyboardButton("📜 শর্তাবলী ও নিয়ম")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# =========================================================
# HANDLERS
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user)

    await update.message.reply_text(
        "🤖 *Quick SMM Panel Bot*-এ স্বাগতম!\n\n"
        "নিচের মেনু থেকে যেকোনো অপশন বেছে নিন:",
        parse_mode="Markdown",
        reply_markup=get_main_reply_keyboard(),
    )

async def handle_reply_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id

    if text == "🚀 সার্ভিস ক্যাটালগ":
        await show_services_msg(update, context)
    elif text == "👤 প্রোফাইল ও ব্যালেন্স":
        balance = get_balance(user_id)
        await update.message.reply_text(
            f"👤 *আপনার প্রোফাইল*\n\n"
            f"🆔 User ID: `{user_id}`\n"
            f"💰 Balance: `{balance:.2f} BDT`",
            parse_mode="Markdown"
        )
    elif text == "💳 ব্যালেন্স রিচার্জ":
        await update.message.reply_text("💳 ব্যালেন্স রিচার্জ করতে এডমিনের সাথে যোগাযোগ করুন।")
    elif text == "💬 ২৪/৭ হেল্পলাইন":
        await update.message.reply_text("💬যেকোনো সমস্যায় সাপোর্ট পেতে মেসেজ দিন: @admin_username")
    elif text == "📜 শর্তাবলী ও নিয়ম":
        await update.message.reply_text("📜 *শর্তাবলী ও নিয়মাবলী:*\n১. ভুল লিংক দিলে অর্ডার বাতিল হবে না।\n২. ব্যালেন্স এড করার পর রিফান্ড দেওয়া হয় না।")
    elif text == "📍 অর্ডার ট্র্যাক করুন":
        await update.message.reply_text("📍 আপনার সাম্প্রতিক অর্ডার দেখতে /my_orders লিখুন।")
    else:
        # Step process if user sends link or quantity
        await process_order_steps(update, context)

async def show_services_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ সার্ভিস লোড হচ্ছে...")

    services = await fetch_smm_services()
    if not services:
        await msg.edit_text("❌ কোনো সার্ভিস পাওয়া যায়নি।")
        return

    context.user_data["all_services"] = services
    counts = {}
    for service in services:
        platform = detect_platform(str(service.get("name", "")))
        if platform:
            counts[platform] = counts.get(platform, 0) + 1

    target_platforms = ["Facebook", "TikTok", "YouTube"]
    keyboard = []
    
    for p in target_platforms:
        count = counts.get(p, 0)
        if count > 0:
            keyboard.append([InlineKeyboardButton(f"📱 {p} ({count})", callback_data=f"platform_{p}")])

    if not keyboard:
        await msg.edit_text("❌ কোনো FB, TikTok, বা YouTube সার্ভিস নেই।")
        return

    await msg.edit_text(
        "🛍 *সার্ভিস প্ল্যাটফর্ম নির্বাচন করুন:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def platform_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    platform = query.data.replace("platform_", "", 1)
    services = context.user_data.get("all_services", [])

    if not services:
        services = await fetch_smm_services()

    matched = [s for s in services if detect_platform(str(s.get("name", ""))) == platform]

    keyboard = []
    service_map = {}
    
    for service in matched[:15]:  
        service_id = str(service.get("service", service.get("id", "")))
        if not service_id: continue

        service_map[service_id] = service
        name = str(service.get("name", "Service"))[:30]
        rate = calculate_customer_price(service.get("rate", 0))

        keyboard.append([InlineKeyboardButton(f"{name} - {rate} BDT", callback_data=f"service_{service_id}")])

    context.user_data["service_map"] = service_map

    await query.edit_message_text(
        f"📱 *{platform} Services*\n\nযেকোনো একটি সার্ভিস বেছে নিন:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def service_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    service_id = query.data.replace("service_", "", 1)
    service = context.user_data.get("service_map", {}).get(service_id)

    if not service:
        await query.edit_message_text("❌ সার্ভিস পাওয়া যায়নি! আবার চেষ্টা করুন।")
        return

    context.user_data["selected_service"] = service
    context.user_data["order_step"] = "link"

    rate = calculate_customer_price(service.get("rate", 0))
    await query.edit_message_text(
        f"🛍 *{service.get('name')}*\n\n"
        f"💵 মূল্য: `{rate} BDT / 1000`\n"
        f"🔢 সর্বনিম্ন: `{service.get('min', 1)}` | সর্বোচ্চ: `{service.get('max', 100000)}`\n\n"
        f"🔗 এখন আপনার পোস্ট/প্রোফাইল লিংক পাঠান:",
        parse_mode="Markdown"
    )

async def process_order_steps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get("order_step")

    if step == "link":
        link = update.message.text.strip()
        if len(link) < 5:
            await update.message.reply_text("❌ সঠিক লিংক দিন।")
            return

        context.user_data["order_link"] = link
        context.user_data["order_step"] = "quantity"
        service = context.user_data.get("selected_service", {})

        await update.message.reply_text(
            f"🔢 এবার পরিমাণ (Quantity) লিখুন:\n"
            f"Min: {service.get('min', 1)} - Max: {service.get('max', 100000)}"
        )
        return

    if step == "quantity":
        try:
            quantity = int(update.message.text.strip())
        except ValueError:
            await update.message.reply_text("❌ সংখ্যায় সঠিক পরিমাণ লিখুন।")
            return

        service = context.user_data.get("selected_service")
        if not service:
            context.user_data.clear()
            await update.message.reply_text("❌ মেমোরি ক্লিয়ার হয়ে গেছে, আবার সার্ভিস নির্বাচন করুন।")
            return

        rate = calculate_customer_price(service.get("rate", 0))
        total_cost = (Decimal(quantity) / Decimal("1000")) * rate

        await update.message.reply_text(
            f"✅ *অর্ডার বিবরণী*\n\n"
            f"📌 Service: {service.get('name')}\n"
            f"🔗 Link: {context.user_data.get('order_link')}\n"
            f"🔢 Quantity: {quantity}\n"
            f"💳 মোট খরচ: `{total_cost:.2f} BDT`",
            parse_mode="Markdown"
        )
        context.user_data.clear()

# =========================================================
# MAIN
# =========================================================

def main():
    init_db()
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(platform_callback, pattern="^platform_"))
    app.add_handler(CallbackQueryHandler(service_callback, pattern="^service_"))
    
    # Text Message Handler for Reply Keyboard Buttons & Inputs
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reply_buttons))

    logger.info("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
