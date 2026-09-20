import os
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

# =========================================================
# CONFIG
# =========================================================

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

# Test/Fake API key as provided
SMM_API_KEY = "7c40378a49cfaec87f7e0b54fd646dd4"

SMM_API_URL = "https://socialpanel.pro/api/v2"

ADMIN_ID = 5293614793

BKASH_NUMBER = "01911198221"
NAGAD_NUMBER = "01911198221"

WHATSAPP_LINK = "https://wa.me/message/ZFPUNOUHWSWRI1"

USD_TO_BDT = Decimal("120")
PROFIT_MULTIPLIER = Decimal("1.30")

DB_FILE = "bot.db"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# DATABASE
# =========================================================

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance REAL DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
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


def ensure_user(user_id, username="", first_name=""):
    """
    User না থাকলে তৈরি করবে।
    থাকলে username/name update করবে।
    """
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO users (user_id, username, first_name, balance)
        VALUES (?, ?, ?, 0)
        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name
    """, (user_id, username or "", first_name or ""))

    conn.commit()
    conn.close()


def get_user(user_id):
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user_id,)
    )

    user = cur.fetchone()
    conn.close()

    return user


def get_balance(user_id):
    user = get_user(user_id)

    if not user:
        return Decimal("0")

    return Decimal(str(user["balance"]))


def update_balance(user_id, amount):
    """
    Positive amount = Add
    Negative amount = Deduct
    """

    conn = get_db()
    cur = conn.cursor()

    # User না থাকলে automatically create
    cur.execute("""
        INSERT INTO users (user_id, balance)
        VALUES (?, 0)
        ON CONFLICT(user_id) DO NOTHING
    """, (user_id,))

    cur.execute("""
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
    """, (float(amount), user_id))

    conn.commit()

    cur.execute(
        "SELECT balance FROM users WHERE user_id = ?",
        (user_id,)
    )

    row = cur.fetchone()
    conn.close()

    if row:
        return Decimal(str(row["balance"]))

    return Decimal("0")


def save_order(
    user_id,
    provider_order_id,
    service_id,
    service_name,
    link,
    quantity,
    charge,
    status="Pending"
):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO orders (
            user_id,
            provider_order_id,
            service_id,
            service_name,
            link,
            quantity,
            charge,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        str(provider_order_id),
        str(service_id),
        service_name,
        link,
        quantity,
        float(charge),
        status,
    ))

    conn.commit()
    conn.close()


# =========================================================
# ADMIN CHECK
# =========================================================

def is_admin(user_id):
    return user_id == ADMIN_ID


# =========================================================
# MAIN KEYBOARD
# =========================================================

def main_keyboard():
    return ReplyKeyboardMarkup(
        [
            [
                KeyboardButton("🛍 Services"),
                KeyboardButton("📦 My Orders"),
            ],
            [
                KeyboardButton("💰 Balance"),
                KeyboardButton("💳 Recharge"),
            ],
            [
                KeyboardButton("☎️ Helpline"),
                KeyboardButton("📜 Terms"),
            ],
        ],
        resize_keyboard=True,
    )


def admin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "➕ Add Balance",
                callback_data="admin_add_balance"
            ),
            InlineKeyboardButton(
                "➖ Deduct Balance",
                callback_data="admin_deduct_balance"
            ),
        ],
        [
            InlineKeyboardButton(
                "👤 User Info",
                callback_data="admin_user_info"
            ),
        ],
        [
            InlineKeyboardButton(
                "❌ Close",
                callback_data="admin_close"
            ),
        ],
    ])


# =========================================================
# SMM API
# =========================================================

async def smm_api(action, extra_data=None):
    data = {
        "key": SMM_API_KEY,
        "action": action,
    }

    if extra_data:
        data.update(extra_data)

    try:
        timeout = aiohttp.ClientTimeout(total=30)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                SMM_API_URL,
                json=data
            ) as response:

                text = await response.text()

                logger.info(
                    "SMM API response: %s",
                    text[:1000]
                )

                try:
                    return await response.json(
                        content_type=None
                    )
                except Exception:
                    return {
                        "error": "Invalid API response",
                        "raw": text,
                    }

    except Exception as e:
        logger.exception("SMM API error")
        return {
            "error": str(e)
        }


async def get_services():
    return await smm_api("services")


# =========================================================
# SERVICE HELPERS
# =========================================================

def get_platform(service):
    text = (
        str(service.get("name", "")) +
        " " +
        str(service.get("category", ""))
    ).lower()

    if "instagram" in text:
        return "Instagram"

    if "facebook" in text:
        return "Facebook"

    if "youtube" in text:
        return "YouTube"

    if "tiktok" in text:
        return "TikTok"

    if "telegram" in text:
        return "Telegram"

    if "twitter" in text or "x.com" in text:
        return "Twitter/X"

    return "Other"


def customer_price(service):
    try:
        provider_rate = Decimal(
            str(service.get("rate", "0"))
        )

        # Provider rate normally per 1000
        price_usd = provider_rate / Decimal("1000")

        price_bdt = price_usd * USD_TO_BDT

        final_price = price_bdt * PROFIT_MULTIPLIER

        return final_price.quantize(Decimal("0.01"))

    except Exception:
        return Decimal("0")


def service_text(service):
    name = str(service.get("name", "Unknown Service"))
    service_id = str(service.get("service", ""))

    minimum = service.get("min", "0")
    maximum = service.get("max", "0")

    price = customer_price(service)

    return (
        f"🆔 ID: {service_id}\n"
        f"📌 {name}\n"
        f"💰 Price: {price} BDT / 1000\n"
        f"📊 Min: {minimum} | Max: {maximum}"
    )


# =========================================================
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    ensure_user(
        user.id,
        user.username,
        user.first_name
    )

    context.user_data.clear()

    await update.message.reply_text(
        f"👋 Welcome {user.first_name}!\n\n"
        "🛍 আমাদের SMM Service Panel-এ আপনাকে স্বাগতম।\n\n"
        "নিচের menu থেকে service নির্বাচন করুন।",
        reply_markup=main_keyboard()
    )


# =========================================================
# ADMIN PANEL
# =========================================================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    if not is_admin(user.id):
        await update.message.reply_text(
            "❌ আপনি Admin নন।"
        )
        return

    context.user_data.clear()

    await update.message.reply_text(
        "👑 Admin Panel\n\n"
        "নিচের option থেকে কাজ নির্বাচন করুন:",
        reply_markup=admin_keyboard()
    )


# =========================================================
# ADD BALANCE FLOW
# =========================================================

async def start_add_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        await query.edit_message_text(
            "❌ Access denied."
        )
        return

    context.user_data.clear()
    context.user_data["admin_action"] = "add_balance"
    context.user_data["admin_step"] = "user_id"

    await query.edit_message_text(
        "➕ Add Balance\n\n"
        "👤 এখন User ID পাঠান:\n\n"
        "উদাহরণ:\n"
        "123456789\n\n"
        "❌ Cancel করতে /cancel লিখুন।"
    )


# =========================================================
# DEDUCT BALANCE FLOW
# =========================================================

async def start_deduct_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        await query.edit_message_text(
            "❌ Access denied."
        )
        return

    context.user_data.clear()
    context.user_data["admin_action"] = "deduct_balance"
    context.user_data["admin_step"] = "user_id"

    await query.edit_message_text(
        "➖ Deduct Balance\n\n"
        "👤 এখন User ID পাঠান:\n\n"
        "উদাহরণ:\n"
        "123456789\n\n"
        "❌ Cancel করতে /cancel লিখুন।"
    )


# =========================================================
# ADMIN USER INFO
# =========================================================

async def start_user_info(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        await query.edit_message_text(
            "❌ Access denied."
        )
        return

    context.user_data.clear()
    context.user_data["admin_action"] = "user_info"
    context.user_data["admin_step"] = "user_id"

    await query.edit_message_text(
        "👤 User Info\n\n"
        "User ID পাঠান:"
    )


# =========================================================
# ADMIN MESSAGE PROCESSOR
# =========================================================

async def process_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    if not is_admin(user.id):
        return False

    action = context.user_data.get("admin_action")
    step = context.user_data.get("admin_step")

    if not action or not step:
        return False

    text = update.message.text.strip()

    # -----------------------------------------------------
    # CANCEL
    # -----------------------------------------------------

    if text.lower() in ["/cancel", "cancel", "❌ cancel"]:

        context.user_data.clear()

        await update.message.reply_text(
            "❌ Operation cancelled.",
            reply_markup=main_keyboard()
        )

        return True

    # -----------------------------------------------------
    # USER ID STEP
    # -----------------------------------------------------

    if step == "user_id":

        try:
            target_user_id = int(text)

            if target_user_id <= 0:
                raise ValueError

        except ValueError:

            await update.message.reply_text(
                "❌ Invalid User ID.\n\n"
                "শুধু numeric User ID পাঠান।\n"
                "উদাহরণ: 123456789"
            )

            return True

        context.user_data["target_user_id"] = target_user_id

        # USER INFO
        if action == "user_info":

            target = get_user(target_user_id)

            if not target:

                await update.message.reply_text(
                    f"❌ User `{target_user_id}` database-এ পাওয়া যায়নি.",
                    parse_mode="Markdown"
                )

                context.user_data.clear()

                return True

            orders_count = 0

            conn = get_db()
            cur = conn.cursor()

            cur.execute(
                "SELECT COUNT(*) FROM orders WHERE user_id = ?",
                (target_user_id,)
            )

            row = cur.fetchone()

            if row:
                orders_count = row[0]

            conn.close()

            await update.message.reply_text(
                "👤 User Information\n\n"
                f"🆔 User ID: {target['user_id']}\n"
                f"👤 Name: {target['first_name'] or 'N/A'}\n"
                f"🔗 Username: @{target['username'] if target['username'] else 'N/A'}\n"
                f"💰 Balance: {Decimal(str(target['balance'])):.2f} BDT\n"
                f"📦 Orders: {orders_count}"
            )

            context.user_data.clear()

            return True

        # ADD / DEDUCT
        context.user_data["admin_step"] = "amount"

        if action == "add_balance":

            await update.message.reply_text(
                f"👤 User ID: {target_user_id}\n\n"
                "💰 এখন কত BDT Add করতে চান?\n\n"
                "উদাহরণ: 500"
            )

        elif action == "deduct_balance":

            current_balance = get_balance(target_user_id)

            await update.message.reply_text(
                f"👤 User ID: {target_user_id}\n"
                f"💰 Current Balance: {current_balance:.2f} BDT\n\n"
                "💸 এখন কত BDT Deduct করতে চান?\n\n"
                "উদাহরণ: 100"
            )

        return True

    # -----------------------------------------------------
    # AMOUNT STEP
    # -----------------------------------------------------

    if step == "amount":

        try:
            amount = Decimal(text)

            if amount <= 0:
                raise InvalidOperation

            amount = amount.quantize(Decimal("0.01"))

        except (InvalidOperation, ValueError):

            await update.message.reply_text(
                "❌ Invalid amount.\n\n"
                "শুধু positive amount দিন।\n"
                "উদাহরণ: 500 অথবা 100.50"
            )

            return True

        target_user_id = context.user_data.get("target_user_id")

        if not target_user_id:
            context.user_data.clear()

            await update.message.reply_text(
                "❌ Session expired. আবার Admin Panel খুলুন।"
            )

            return True

        # DEDUCT CHECK
        if action == "deduct_balance":

            current_balance = get_balance(target_user_id)

            if amount > current_balance:

                await update.message.reply_text(
                    "❌ Insufficient balance.\n\n"
                    f"Current Balance: {current_balance:.2f} BDT\n"
                    f"Requested Deduct: {amount:.2f} BDT"
                )

                return True

        context.user_data["amount"] = str(amount)

        # CONFIRMATION BUTTON
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Confirm",
                    callback_data="admin_confirm_balance"
                ),
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data="admin_cancel_balance"
                ),
            ]
        ])

        if action == "add_balance":

            await update.message.reply_text(
                "⚠️ Confirm Add Balance\n\n"
                f"👤 User ID: {target_user_id}\n"
                f"💰 Amount: {amount:.2f} BDT\n\n"
                "Balance add করার জন্য Confirm চাপুন।",
                reply_markup=keyboard
            )

        else:

            current_balance = get_balance(target_user_id)

            await update.message.reply_text(
                "⚠️ Confirm Deduct Balance\n\n"
                f"👤 User ID: {target_user_id}\n"
                f"💸 Deduct: {amount:.2f} BDT\n"
                f"💰 Current: {current_balance:.2f} BDT\n"
                f"💰 After: {(current_balance - amount):.2f} BDT\n\n"
                "Confirm চাপুন।",
                reply_markup=keyboard
            )

        context.user_data["admin_step"] = "confirm"

        return True

    return False


# =========================================================
# CONFIRM BALANCE
# =========================================================

async def confirm_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        await query.edit_message_text(
            "❌ Access denied."
        )
        return

    action = context.user_data.get("admin_action")
    target_user_id = context.user_data.get("target_user_id")
    amount_text = context.user_data.get("amount")

    if not action or not target_user_id or not amount_text:

        await query.edit_message_text(
            "❌ Session expired.\n"
            "আবার Admin Panel থেকে শুরু করুন।"
        )

        context.user_data.clear()
        return

    try:
        amount = Decimal(str(amount_text))
    except Exception:

        await query.edit_message_text(
            "❌ Invalid amount."
        )

        context.user_data.clear()
        return

    # -----------------------------------------------------
    # ADD
    # -----------------------------------------------------

    if action == "add_balance":

        new_balance = update_balance(
            target_user_id,
            amount
        )

        await query.edit_message_text(
            "✅ Balance Added Successfully!\n\n"
            f"👤 User ID: {target_user_id}\n"
            f"➕ Added: {amount:.2f} BDT\n"
            f"💰 New Balance: {new_balance:.2f} BDT"
        )

    # -----------------------------------------------------
    # DEDUCT
    # -----------------------------------------------------

    elif action == "deduct_balance":

        current_balance = get_balance(target_user_id)

        if amount > current_balance:

            await query.edit_message_text(
                "❌ Balance deduction failed.\n\n"
                f"Current Balance: {current_balance:.2f} BDT"
            )

            context.user_data.clear()
            return

        new_balance = updat
