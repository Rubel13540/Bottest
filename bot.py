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
# CONFIGURATION
# =========================================================

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8958095649:AAGQLX-Yoe72u4lI7C6cCJMMt3knfsbgAdw").strip()
SMM_API_KEY = os.getenv("SMM_API_KEY", "7c40378a49cfaec87f7e0b54fd646dd4").strip()
SMM_API_URL = "https://socialpanel.pro/api/v2"
ADMIN_ID = int(os.getenv("ADMIN_ID", "5293614793"))

BKASH_NUMBER = "01911198221"
NAGAD_NUMBER = "01911198221"
WHATSAPP_LINK = "https://wa.me/message/ZFPUNOUHWSWRI1"

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

def update_balance(user_id, amount):
    conn = db_connect()
    conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (float(amount), user_id))
    conn.commit()
    conn.close()

# =========================================================
# SMM API FUNCTIONS
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
                    logger.error("SMM API Error: %s", text)
                    return None
                try:
                    return await response.json(content_type=None)
                except Exception:
                    logger.error("Invalid JSON from API: %s", text)
                    return None
    except Exception as e:
        logger.exception("SMM API Connection error: %s", e)
        return None

async def fetch_smm_services():
    payload = {"key": SMM_API_KEY, "action": "services"}
    result = await smm_request(payload)
    if isinstance(result, list):
        return result
    return []

async def place_smm_order(service_id, link, quantity):
    payload = {
        "key": SMM_API_KEY,
        "action": "add",
        "service": service_id,
        "link": link,
        "quantity": quantity
    }
    return await smm_request(payload)

def detect_platform(service_name):
    name = service_name.lower()
    if any(x in name for x in ["facebook", "fb"]):
        return "Facebook"
    if any(x in name for x in ["youtube", "yt"]):
        return "YouTube"
    if any(x in name for x in ["tiktok", "tik tok"]):
        return "TikTok"
    return None

def calculate_customer_price(rate):
    try:
        usd_rate = Decimal(str(rate))
        return (usd_rate * USD_TO_BDT * PROFIT_MULTIPLIER).quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0")

# =========================================================
# KEYBOARD MENUS
# =========================================================

def get_main_reply_keyboard():
    keyboard = [
        [KeyboardButton("🚀 সার্ভিস ক্যাটালগ"), KeyboardButton("📍 অর্ডার ট্র্যাক করুন")],
        [KeyboardButton("👤 প্রোফাইল ও ব্যালেন্স"), KeyboardButton("💳 ব্যালেন্স রিচার্জ")],
        [KeyboardButton("💬 ২৪/৭ হেল্পলাইন"), KeyboardButton("📜 শর্তাবলী ও নিয়ম")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# =========================================================
# ADMIN CONTROLLER
# =========================================================

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    keyboard = [
        [InlineKeyboardButton("➕ Add Balance", callback_data="admin_add_bal_start")],
        [InlineKeyboardButton("➖ Deduct Balance", callback_data="admin_deduct_bal_start")]
    ]
    
    await update.message.reply_text(
        "🛠 *Admin Control Panel*\n\nনিচের অপশন সিলেক্ট করুন:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        return

    await query.answer()

    if query.data == "admin_add_bal_start":
        context.user_data["admin_step"] = "add_get_id"
        await query.edit_message_text("👤 *গ্রাহকের User ID দিন:*", parse_mode="Markdown")

    elif query.data == "admin_deduct_bal_start":
        context.user_data["admin_step"] = "deduct_get_id"
        await query.edit_message_text("👤 *গ্রাহকের User ID দিন:*", parse_mode="Markdown")

# =========================================================
# USER BOT HANDLERS
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user)
    context.user_data.clear()

    await update.message.reply_text(
        "🤖 *Quick SMM Panel Bot*-এ স্বাগতম!\n\n"
        "নিচের বোতামগুলো ব্যবহার করে কাজ করুন:",
        parse_mode="Markdown",
        reply_markup=get_main_reply_keyboard(),
    )

async def handle_reply_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id

    # Admin Process logic
    admin_step = context.user_data.get("admin_step")
    if user_id == ADMIN_ID and admin_step:
        if admin_step == "add_get_id":
            try:
                target_id = int(text)
                context.user_data["admin_target_id"] = target_id
                context.user_data["admin_step"] = "add_get_amount"
                await update.message.reply_text(f"✅ Target User: `{target_id}`\n\nকত টাকা (BDT) যোগ করবেন লিখুন:", parse_mode="Markdown")
            except ValueError:
                await update.message.reply_text("❌ সঠিক Numeric ID দিন।")
            return

        elif admin_step == "add_get_amount":
            try:
                amount = Decimal(text)
                target_id = context.user_data.get("admin_target_id")
                update_balance(target_id, amount)
                new_bal = get_balance(target_id)

                await update.message.reply_text(f"🎉 성공! {amount} BDT যোগ করা হয়েছে।\nবর্তমান ব্যালেন্স: `{new_bal:.2f} BDT`", parse_mode="Markdown")
                try:
                    await context.bot.send_message(chat_id=target_id, text=f"🎉 আপনার অ্যাকাউন্টে `{amount} BDT` রিচার্জ করা হয়েছে!")
                except Exception:
                    pass

                context.user_data.pop("admin_step", None)
            except Exception:
                await update.message.reply_text("❌ সঠিক সংখ্যা লিখুন।")
            return

    # User Button Clicks
    if "সার্ভিস ক্যাটালগ" in text:
        context.user_data.pop("order_step", None)
        await show_services_msg(update, context)
        return

    if "প্রোফাইল" in text:
        context.user_data.pop("order_step", None)
        balance = get_balance(user_id)
        await update.message.reply_text(
            f"👤 *আপনার প্রোফাইল*\n\n"
            f"🆔 User ID: `{user_id}`\n"
            f"💰 Balance: `{balance:.2f} BDT`",
            parse_mode="Markdown"
        )
        return

    if "ব্যালেন্স রিচার্জ" in text:
        context.user_data.pop("order_step", None)
        keyboard = [
            [
                InlineKeyboardButton("💖 bKash (বিকাশ)", callback_data="pay_bkash"),
                InlineKeyboardButton("🟠 Nagad (নগদ)", callback_data="pay_nagad"),
            ]
        ]
        await update.message.reply_text(
            "💳 *পেমেন্ট পদ্ধতি সিলেক্ট করুন:*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if "হেল্পলাইন" in text:
        context.user_data.pop("order_step", None)
        keyboard = [[InlineKeyboardButton("💬 Contact Support (WhatsApp)", url=WHATSAPP_LINK)]]
        await update.message.reply_text("💬 সরাসরি সাপোর্টের জন্য হোয়াটসঅ্যাপে মেসেজ দিন:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if "শর্তাবলী" in text:
        context.user_data.pop("order_step", None)
        terms = (
            "📜 *নিয়মাবলী ও শর্তাবলী:*\n\n"
            "১. সঠিক ও সঠিক ফরম্যাটের লিংক দিন।\n"
            "২. সার্ভিস চলাকালীন একাউন্ট পাবলিক রাখুন।\n"
            "৩. ব্যালেন্স অ্যাড হলে তা অফেরতযোগ্য।"
        )
        await update.message.reply_text(terms, parse_mode="Markdown")
        return

    if "অর্ডার ট্র্যাক" in text:
        context.user_data.pop("order_step", None)
        await update.message.reply_text("📍 সার্ভিস ট্র্যাক করার সুবিধা শীঘ্রই আসছে।")
        return

    await process_order_steps(update, context)

async def show_services_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ সার্ভিস তালিকা প্রস্তুত হচ্ছে...")

    services = await fetch_smm_services()
    if not services:
        await msg.edit_text("❌ কোনো সার্ভিস পাওয়া যায়নি।")
        return

    context.user_data["all_services"] = services
    counts = {"Facebook": 0, "TikTok": 0, "YouTube": 0}

    for service in services:
        p = detect_platform(str(service.get("name", "")))
        if p:
            counts[p] += 1

    keyboard = []
    for p in ["Facebook", "TikTok", "YouTube"]:
        if counts[p] > 0:
            keyboard.append([InlineKeyboardButton(f"📱 {p} ({counts[p]})", callback_data=f"platform_{p}")])

    await msg.edit_text("🛍 *প্ল্যাটফর্ম বেছে নিন:*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

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
    
    for service in matched[:20]:
        service_id = str(service.get("service", service.get("id", "")))
        if not service_id: continue

        service_map[service_id] = service
        name = str(service.get("name", "Service"))[:30]
        rate = calculate_customer_price(service.get("rate", 0))

        keyboard.append([InlineKeyboardButton(f"{name} - {rate} BDT", callback_data=f"service_{service_id}")])

    context.user_data["service_map"] = service_map
    await query.edit_message_text(f"📱 *{platform} Services*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def service_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    service_id = query.data.replace("service_", "", 1)
    service = context.user_data.get("service_map", {}).get(service_id)

    if not service:
        await query.edit_message_text("❌ সার্ভিস পাওয়া যায়নি।")
        return

    context.user_data["selected_service"] = service
    context.user_data["order_step"] = "link"

    rate_per_1000 = calculate_customer_price(service.get("rate", 0))

    await query.edit_message_text(
        f"🛍 *{service.get('name')}*\n\n"
        f"💵 প্রতি ১০০০টির দাম: `{rate_per_1000:.2f} BDT`\n"
        f"🔢 Min: `{service.get('min', 1)}` | Max: `{service.get('max', 100000)}`\n\n"
        f"🔗 *আপনার লিংকটি পাঠিয়ে দিন:*",
        parse_mode="Markdown"
    )

async def payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    method = "bKash" if query.data == "pay_bkash" else "Nagad"
    number = BKASH_NUMBER if method == "bKash" else NAGAD_NUMBER

    text = (
        f"💳 *{method} (Personal)*\n\n"
        f"📱 নম্বর: `{number}`\n\n"
        f"Send Money করার পর স্ক্রিনশট ও TrxID নিয়ে নিচের বাটন দিয়ে আমাদের জানান।"
    )
    keyboard = [[InlineKeyboardButton("📲 Support WhatsApp", url=WHATSAPP_LINK)]]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def process_order_steps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get("order_step")
    user_id = update.effective_user.id

    if not step:
        return

    if step == "link":
        link = update.message.text.strip()
        context.user_data["order_link"] = link
        context.user_data["order_step"] = "quantity"
        await update.message.reply_text("🔢 এবার পরিমাণ (Quantity) লিখে পাঠান:")
        return

    if step == "quantity":
        try:
            quantity = int(update.message.text.strip())
        except ValueError:
            await update.message.reply_text("❌ সঠিক সংখ্যা লিখুন।")
            return

        service = context.user_data.get("selected_service")
        if not service:
            context.user_data.clear()
            await update.message.reply_text("❌ সময় শেষ, আবার চেষ্টা করুন।")
            return

        rate_per_1000 = calculate_customer_price(service.get("rate", 0))
        total_cost = (Decimal(quantity) / Decimal("1000")) * rate_per_1000
        user_balance = get_balance(user_id)

        if user_balance < total_cost:
            await update.message.reply_text(
                f"❌ *পর্যাপ্ত ব্যালেন্স নেই!*\n"
                f"প্রয়োজন: `{total_cost:.2f} BDT` | আছে: `{user_balance:.2f} BDT`",
                parse_mode="Markdown"
            )
            context.user_data.clear()
            return

        service_id = str(service.get("service", service.get("id", "")))
        link = context.user_data.get("order_link")
        response = await place_smm_order(service_id, link, quantity)

        if response and "order" in response:
            update_balance(user_id, -total_cost)
            await update.message.reply_text(
                f"✅ *অর্ডার সফল হয়েছে!*\n Order ID: `{response['order']}`\nমোট খরচ: `{total_cost:.2f} BDT`",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text("❌ সার্ভিস প্যানেলে সমস্যা হয়েছে, পরে চেষ্টা করুন।")

        context.user_data.clear()

# =========================================================
# MAIN ENTRY
# =========================================================

def main():
    init_db()
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    
    app.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^admin_"))
    app.add_handler(CallbackQueryHandler(platform_callback, pattern="^platform_"))
    app.add_handler(CallbackQueryHandler(service_callback, pattern="^service_"))
    app.add_handler(CallbackQueryHandler(payment_callback, pattern="^pay_"))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reply_buttons))

    logger.info("Bot Started!")
    app.run_polling()

if __name__ == "__main__":
    main()
