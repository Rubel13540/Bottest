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
    if not user:
        return
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
# KEYBOARDS
# =========================================================

def get_main_reply_keyboard():
    keyboard = [
        [KeyboardButton("🚀 সার্ভিস ক্যাটালগ"), KeyboardButton("📍 অর্ডার ট্র্যাক করুন")],
        [KeyboardButton("👤 প্রোফাইল ও ব্যালেন্স"), KeyboardButton("💳 ব্যালেন্স রিচার্জ")],
        [KeyboardButton("💬 ২৪/৭ হেল্পলাইন"), KeyboardButton("📜 শর্তাবলী ও নিয়ম")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_admin_inline_keyboard():
    keyboard = [
        [InlineKeyboardButton("➕ ব্যালেন্স যোগ করুন (Add)", callback_data="admin_add_bal")],
        [InlineKeyboardButton("➖ ব্যালেন্স কাটুন (Deduct)", callback_data="admin_deduct_bal")],
        [InlineKeyboardButton("❌ বাতিল (Cancel)", callback_data="admin_cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)

# =========================================================
# ADMIN PANEL COMMANDS & CALLBACKS
# =========================================================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.id != ADMIN_ID:
        return

    register_user(user)
    context.user_data.clear()
    await update.message.reply_text(
        "🛠 *অ্যাডমিন কন্ট্রোল প্যানেল*\n\n"
        "নিচের বোতামগুলো ব্যবহার করে ব্যালেন্স কন্ট্রোল করুন:",
        parse_mode="Markdown",
        reply_markup=get_admin_inline_keyboard()
    )

async def admin_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    if user_id != ADMIN_ID:
        await query.answer("❌ আপনার জন্য এই অপশনটি অনুমোদিত নয়!", show_alert=True)
        return

    await query.answer()
    data = query.data

    if data == "admin_add_bal":
        context.user_data["admin_action"] = "add"
        context.user_data["admin_step"] = "awaiting_user_id"
        cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ বাতিল করুন", callback_data="admin_cancel")]])
        await query.edit_message_text(
            "➕ *ব্যালেন্স অ্যাড করার প্রক্রিয়া:*\n\n"
            "ধাপ ১/২: যে ইউজারের ব্যালেন্স বাড়াতে চান তার *User ID* লিখে টেক্সট পাঠান:",
            parse_mode="Markdown",
            reply_markup=cancel_btn
        )

    elif data == "admin_deduct_bal":
        context.user_data["admin_action"] = "deduct"
        context.user_data["admin_step"] = "awaiting_user_id"
        cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ বাতিল করুন", callback_data="admin_cancel")]])
        await query.edit_message_text(
            "➖ *ব্যালেন্স কেটে নেওয়ার প্রক্রিয়া:*\n\n"
            "ধাপ ১/২: যে ইউজারের ব্যালেন্স কমাতে চান তার *User ID* লিখে টেক্সট পাঠান:",
            parse_mode="Markdown",
            reply_markup=cancel_btn
        )

    elif data == "admin_cancel":
        context.user_data.clear()
        await query.edit_message_text("❌ *অ্যাডমিন প্রসেস বাতিল করা হয়েছে।*", parse_mode="Markdown")

async def process_admin_steps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return False

    step = context.user_data.get("admin_step")
    if not step:
        return False

    cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ বাতিল করুন", callback_data="admin_cancel")]])

    if step == "awaiting_user_id":
        text_input = update.message.text.strip()
        if not text_input.isdigit():
            await update.message.reply_text(
                "❌ *অকার্যকর ইউজার আইডি!* শুধুমাত্র সংখ্যা ব্যবহার করুন (যেমন: 5293614793):",
                parse_mode="Markdown",
                reply_markup=cancel_btn
            )
            return True

        context.user_data["target_user_id"] = int(text_input)
        context.user_data["admin_step"] = "awaiting_amount"
        action_name = "যোগ করতে" if context.user_data.get("admin_action") == "add" else "কাটতে"

        await update.message.reply_text(
            f"👤 ইউজার আইডি: `{text_input}`\n\n"
            f"ধাপ ২/২: কত টাকা (Amount BDT) {action_name} চান লিখে পাঠান:",
            parse_mode="Markdown",
            reply_markup=cancel_btn
        )
        return True

    if step == "awaiting_amount":
        text_input = update.message.text.strip()
        try:
            amount = Decimal(text_input)
            if amount <= 0:
                raise ValueError
        except Exception:
            await update.message.reply_text(
                "❌ *অকার্যকর পরিমাণ!* অনুগ্রহ করে সঠিক সংখ্যা দিন (যেমন: 50, 100, 500):",
                parse_mode="Markdown",
                reply_markup=cancel_btn
            )
            return True

        target_user_id = context.user_data.get("target_user_id")
        action = context.user_data.get("admin_action")

        if action == "add":
            update_balance(target_user_id, amount)
            new_bal = get_balance(target_user_id)
            await update.message.reply_text(
                f"✅ *ব্যালেন্স সফলভাবে যোগ করা হয়েছে!*\n\n"
                f"👤 User ID: `{target_user_id}`\n"
                f"➕ যোগকৃত পরিমাণ: `{amount} BDT`\n"
                f"💰 বর্তমান মোট ব্যালেন্স: `{new_bal:.2f} BDT`",
                parse_mode="Markdown"
            )
            try:
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text=f"🎉 *আপনার অ্যাকাউন্টে {amount} BDT জমা করা হয়েছে!*\nবর্তমান ব্যালেন্স: `{new_bal:.2f} BDT`",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

        elif action == "deduct":
            update_balance(target_user_id, -amount)
            new_bal = get_balance(target_user_id)
            await update.message.reply_text(
                f"✅ *ব্যালেন্স কেটে নেওয়া হয়েছে!*\n\n"
                f"👤 User ID: `{target_user_id}`\n"
                f"➖ কর্তনকৃত পরিমাণ: `{amount} BDT`\n"
                f"💰 বর্তমান মোট ব্যালেন্স: `{new_bal:.2f} BDT`",
                parse_mode="Markdown"
            )

        context.user_data.clear()
        return True

    return False

# =========================================================
# HANDLERS
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user)
    context.user_data.clear()

    await update.message.reply_text(
        "🤖 *Quick SMM Panel Bot*-এ স্বাগতম!\n\n"
        "নিচের মেনু থেকে যেকোনো অপশন বেছে নিন:",
        parse_mode="Markdown",
        reply_markup=get_main_reply_keyboard(),
    )

async def handle_reply_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user)

    # Check if admin is currently doing step-by-step balance operation
    if user.id == ADMIN_ID and context.user_data.get("admin_step"):
        is_processed = await process_admin_steps(update, context)
        if is_processed:
            return

    text = update.message.text.strip()

    if "শর্তাবলী" in text or "নিয়ম" in text or text == "📜 শর্তাবলী ও নিয়ম":
        context.user_data.pop("order_step", None)
        terms_text = (
            "📜 *Quick SMM Panel Bot-এর নিয়ম-কানুন ও শর্তাবলী:*\n\n"
            "১. *লিংক ও অর্ডার চেক:*\n"
            "   • অর্ডার করার সময় অবশ্যই সঠিক লিংক এবং পরিমাণ নির্বাচন করবেন।\n"
            "   • ভুল লিংক দিলে অর্ডার সম্পন্ন হবে না এবং টাকা রিফান্ড দেওয়া হবে না।\n\n"
            "২. *অ্যাকাউন্ট পাবলিক রাখা:*\n"
            "   • সার্ভিস নেওয়ার সময় আপনার প্রোফাইল/পোস্ট অবশ্যই *Public* রাখতে হবে।\n\n"
            "৩. *ব্যালেন্স অ্যাড ও রিফান্ড:*\n"
            "   • বটে ব্যালেন্স অ্যাড করার পর কোনো অবস্থাতেই ক্যাশআউট বা রিফান্ড দেওয়া হবে না।\n\n"
            "💬 যেকোনো সহায়তার জন্য যোগাযোগ করুন:"
        )
        keyboard = [[InlineKeyboardButton("💬 Contact Support (WhatsApp)", url=WHATSAPP_LINK)]]
        await update.message.reply_text(
            terms_text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if "সার্ভিস ক্যাটালগ" in text:
        context.user_data.pop("order_step", None)
        await show_services_msg(update, context)
        return

    if "প্রোফাইল" in text:
        context.user_data.pop("order_step", None)
        balance = get_balance(user.id)
        await update.message.reply_text(
            f"👤 *আপনার প্রোফাইল*\n\n"
            f"🆔 User ID: `{user.id}`\n"
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
            "💳 *ব্যালেন্স রিচার্জ পেমেন্ট মেথড*\n\n"
            "আপনি কিসের মাধ্যমে টাকা রিচার্জ করতে চান নির্বাচন করুন:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if "হেল্পলাইন" in text:
        context.user_data.pop("order_step", None)
        keyboard = [[InlineKeyboardButton("💬 Contact Support (WhatsApp)", url=WHATSAPP_LINK)]]
        await update.message.reply_text(
            "💬 যেকোনো সমস্যা বা সাহায্যের জন্য সরাসরি হোয়াটসঅ্যাপে যোগাযোগ করুন:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if "অর্ডার ট্র্যাক" in text:
        context.user_data.pop("order_step", None)
        await update.message.reply_text("📍 আপনার সাম্প্রতিক অর্ডার দেখতে /my_orders লিখুন।")
        return

    await process_order_steps(update, context)

async def show_services_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ সার্ভিস লোড হচ্ছে...")

    services = await fetch_smm_services()
    if not services:
        await msg.edit_text("❌ কোনো সার্ভিস পাওয়া যায়নি। API বা নেটওয়ার্ক সমস্যা।")
        return

    context.user_data["all_services"] = services
    counts = {"Facebook": 0, "TikTok": 0, "YouTube": 0}

    for service in services:
        name = str(service.get("name", ""))
        platform = detect_platform(name)
        if platform:
            counts[platform] += 1

    keyboard = []
    for p in ["Facebook", "TikTok", "YouTube"]:
        if counts[p] > 0:
            keyboard.append([InlineKeyboardButton(f"📱 {p} ({counts[p]})", callback_data=f"platform_{p}")])

    if not keyboard:
        await msg.edit_text("❌ কোনো Facebook, TikTok বা YouTube সার্ভিস পাওয়া যায়নি।")
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
    
    for service in matched[:20]:  
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

    rate_per_1000 = calculate_customer_price(service.get("rate", 0))

    await query.edit_message_text(
        f"🛍 *{service.get('name')}*\n\n"
        f"💵 মূল সার্ভিস মূল্য: `{rate_per_1000:.2f} BDT / 1000`\n"
        f"🔢 সর্বনিম্ন: `{service.get('min', 1)}` | সর্বোচ্চ: `{service.get('max', 100000)}`\n\n"
        f"🔗 *এখন আপনার পোস্ট/প্রোফাইল লিংক পাঠান:*",
        parse_mode="Markdown"
    )

async def payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    method = "bKash" if query.data == "pay_bkash" else "Nagad"
    number = BKASH_NUMBER if method == "bKash" else NAGAD_NUMBER

    text = (
        f"💳 *{method} Personal - Send Money*\n\n"
        f"📱 নম্বর: `{number}` (Personal)\n\n"
        f"📌 *ধাপসমূহ:*\n"
        f"১. আপনার {method} অ্যাপ/ডায়াল করে ওপরের নম্বরে *Send Money* করুন।\n"
        f"২. টাকা পাঠানো হয়ে গেলে ট্রানজেকশন আইডি (TrxID) এবং পেমেন্টের একটি *স্ক্রিনশট* তুলুন।\n"
        f"৩. নিচের হোয়াটসঅ্যাপ বাটনে ক্লিক করে স্ক্রিনশট, TrxID এবং আপনার ইউজার আইডি আমাদের পাঠিয়ে দিন।\n\n"
        f"🆔 আপনার ইউজার আইডি: `{query.from_user.id}`"
    )

    keyboard = [[InlineKeyboardButton("📲 WhatsApp-এ পেমেন্ট প্রমাণ দিন", url=WHATSAPP_LINK)]]

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def process_order_steps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get("order_step")
    user_id = update.effective_user.id

    if not step:
        return

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
        except ValueE
