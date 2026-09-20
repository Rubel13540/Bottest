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

# ---------------------------------------------------------
# শুধুমাত্র FB, TikTok, YouTube ফিল্টার করার ফাংশন
# ---------------------------------------------------------
def detect_platform(service_name):
    name = service_name.lower()
    
    if any(x in name for x in ["facebook", "fb"]):
        return "Facebook"
    if any(x in name for x in ["youtube", "yt"]):
        return "YouTube"
    if "tiktok" in name or "tik tok" in name:
        return "TikTok"

    return None  # অন্য সকল প্ল্যাটফর্ম বাদ দেওয়া হলো

def calculate_customer_price(rate):
    try:
        usd_rate = Decimal(str(rate))
        return (usd_rate * USD_TO_BDT * PROFIT_MULTIPLIER).quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0")

# =========================================================
# HANDLERS
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user)

    keyboard = [
        [InlineKeyboardButton("🛍 Services", callback_data="services"), InlineKeyboardButton("💰 Balance", callback_data="balance")],
        [InlineKeyboardButton("📦 My Orders", callback_data="my_orders"), InlineKeyboardButton("🔄 Refresh Services", callback_data="refresh_services")],
    ]
    if user.id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("👑 Admin", callback_data="admin")])

    await update.message.reply_text(
        "🤖 *SMM Service Bot*\n\nস্বাগতম! নিচের অপশনগুলো থেকে বেছে নিন:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    balance = get_balance(query.from_user.id)
    keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="home")]]
    await query.edit_message_text(
        f"💰 *Your Balance*\n\nBalance: `{balance:.2f} BDT`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def show_services(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("⏳ Loading services...")

    services = await fetch_smm_services()
    if not services:
        await query.edit_message_text(
            "❌ কোনো service পাওয়া যায়নি।",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Try Again", callback_data="refresh_services")]])
        )
        return

    context.user_data["all_services"] = services
    counts = {}
    
    for service in services:
        platform = detect_platform(str(service.get("name", "")))
        if platform:  # শুধু Facebook, YouTube, TikTok গণনায় নেওয়া হবে
            counts[platform] = counts.get(platform, 0) + 1

    # শুধুমাত্র নির্দিষ্ট ৩টি প্ল্যাটফর্ম
    target_platforms = ["Facebook", "TikTok", "YouTube"]
    keyboard = []
    
    for p in target_platforms:
        count = counts.get(p, 0)
        if count > 0:
            keyboard.append([InlineKeyboardButton(f"📱 {p} ({count})", callback_data=f"platform_{p}")])

    if not keyboard:
        await query.edit_message_text(
            "❌ Facebook, TikTok বা YouTube-এর কোনো সার্ভিস পাওয়া যায়নি।",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="home")]])
        )
        return

    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="home")])
    await query.edit_message_text("🛍 *Select Platform*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def platform_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    platform = query.data.replace("platform_", "", 1)
    services = context.user_data.get("all_services", [])

    if not services:
        services = await fetch_smm_services()

    matched = [s for s in services if detect_platform(str(s.get("name", ""))) == platform]

    if not matched:
        await query.edit_message_text("❌ এই platform-এর কোনো service পাওয়া যায়নি।")
        return

    # প্রতি ক্যাটাগরিতে সর্বোচ্চ ১৫টি বাটন (Telegram Limits এড়াতে)
    keyboard = []
    service_map = {}
    
    for service in matched[:15]:  
        service_id = str(service.get("service", service.get("id", "")))
        if not service_id:
            continue

        service_map[service_id] = service
        name = str(service.get("name", "Service"))[:30]
        rate = calculate_customer_price(service.get("rate", 0))

        keyboard.append([InlineKeyboardButton(f"{name} - {rate} BDT", callback_data=f"service_{service_id}")])

    context.user_data["service_map"] = service_map
    keyboard.append([InlineKeyboardButton("⬅️ Back to Platforms", callback_data="services")])

    await query.edit_message_text(
        f"📱 *{platform} Services*\n\nSelect a service:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def service_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    service_id = query.data.replace("service_", "", 1)
    service = context.user_data.get("service_map", {}).get(service_id)

    if not service:
        await query.edit_message_text("❌ Service পাওয়া যায়নি! আবার চেষ্টা করুন।")
        return

    context.user_data["selected_service"] = service
    context.user_data["order_step"] = "link"

    rate = calculate_customer_price(service.get("rate", 0))
    await query.edit_message_text(
        f"🛍 *{service.get('name')}*\n\n"
        f"💵 Price: `{rate} BDT / 1000`\n"
        f"🔢 Min: `{service.get('min', 1)}` | Max: `{service.get('max', 100000)}`\n\n"
        f"🔗 এখন আপনার পোস্ট/Profile-এর Link দিন:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="services")]])
    )

async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user)

    step = context.user_data.get("order_step")

    if step == "link":
        link = update.message.text.strip()
        if len(link) < 5:
            await update.message.reply_text("❌ একটি সঠিক (Valid) লিংক দিন।")
            return

        context.user_data["order_link"] = link
        context.user_data["order_step"] = "quantity"
        service = context.user_data.get("selected_service", {})

        await update.message.reply_text(
            f"🔢 Quantity (সংখ্যা) দিন:\n"
            f"Minimum: {service.get('min', 1)}\n"
            f"Maximum: {service.get('max', 100000)}\n\n"
            f"উদাহরণ: `1000`",
            parse_mode="Markdown"
        )
        return

    if step == "quantity":
        try:
            quantity = int(update.message.text.strip())
        except ValueError:
            await update.message.reply_text("❌ Quantity অবশ্যই সংখ্যায় (Number) হতে হবে।")
            return

        service = context.user_data.get("selected_service")
        if not service:
            context.user_data.clear()
            await update.message.reply_text("❌ Session মেয়াদ শেষ। /start দিন।")
            return

        min_qty = int(service.get("min", 1))
        max_qty = int(service.get("max", 1000000))

        if quantity < min_qty or quantity > max_qty:
            await update.message.reply_text(f"❌ Quantity অবশ্যই {min_qty} থেকে {max_qty} এর মধ্যে হতে হবে।")
            return

        rate = calculate_customer_price(service.get("rate", 0))
        total_cost = (Decimal(quantity) / Decimal("1000")) * rate

        await update.message.reply_text(
            f"✅ *Order Details Summary*\n\n"
            f"📌 Service: {service.get('name')}\n"
            f"🔗 Link: {context.user_data.get('order_link')}\n"
            f"🔢 Quantity: {quantity}\n"
            f"💳 Total Cost: `{total_cost:.2f} BDT`",
            parse_mode="Markdown"
        )
        context.user_data.clear()

# =========================================================
# MAIN FUNCTION
# =========================================================

def main():
    init_db()
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(start, pattern="^home$"))
    app.add_handler(CallbackQueryHandler(show_services, pattern="^services$"))
    app.add_handler(CallbackQueryHandler(show_services, pattern="^refresh_services$"))
    app.add_handler(CallbackQueryHandler(show_balance, pattern="^balance$"))
    app.add_handler(CallbackQueryHandler(platform_callback, pattern="^platform_"))
    app.add_handler(CallbackQueryHandler(service_callback, pattern="^service_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message))

    logger.info("Bot started successfully...")
    app.run_polling()

if __name__ == "__main__":
    main()
