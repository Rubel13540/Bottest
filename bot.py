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

load_dotenv()

# =========================================================
# CONFIG
# =========================================================

# Railway Variables থেকে নতুন Telegram Bot Token নেবে
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

# আগের test/demo SMM API key
SMM_API_KEY = os.getenv(
    "SMM_API_KEY",
    "7c40378a49cfaec87f7e0b54fd646dd4",
).strip()

SMM_API_URL = "https://socialpanel.pro/api/v2"

ADMIN_ID = 5293614793

BKASH_NUMBER = "01911198221"
NAGAD_NUMBER = "01911198221"

WHATSAPP_URL = "https://wa.me/message/ZFPUNOUHWSWRI1"

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

def get_db():
    conn = sqlite3.connect(DB_FILE, timeout=30)
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

    conn.commit()
    conn.close()


def ensure_user(tg_user):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO users (
            user_id,
            username,
            first_name
        )
        VALUES (?, ?, ?)

        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name
    """, (
        tg_user.id,
        tg_user.username or "",
        tg_user.first_name or "",
    ))

    conn.commit()
    conn.close()


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


def change_balance(user_id, amount):
    amount = Decimal(str(amount))

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT balance FROM users WHERE user_id = ?",
        (user_id,),
    )

    row = cur.fetchone()

    if not row:
        cur.execute(
            "INSERT INTO users (user_id, balance) VALUES (?, 0)",
            (user_id,),
        )
        current = Decimal("0")
    else:
        current = Decimal(str(row["balance"]))

    new_balance = current + amount

    cur.execute(
        "UPDATE users SET balance = ? WHERE user_id = ?",
        (float(new_balance), user_id),
    )

    conn.commit()
    conn.close()

    return new_balance


def deduct_balance(user_id, amount):
    amount = Decimal(str(amount))

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT balance FROM users WHERE user_id = ?",
        (user_id,),
    )

    row = cur.fetchone()

    if not row:
        conn.close()
        return None

    current = Decimal(str(row["balance"]))

    if current < amount:
        conn.close()
        return None

    new_balance = current - amount

    cur.execute(
        "UPDATE users SET balance = ? WHERE user_id = ?",
        (float(new_balance), user_id),
    )

    conn.commit()
    conn.close()

    return new_balance


def save_order(
    user_id,
    service_id,
    service_name,
    link,
    quantity,
    charge,
    provider_order_id,
    status="Pending",
):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO orders (
            user_id,
            service_id,
            service_name,
            link,
            quantity,
            charge,
            provider_order_id,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        str(service_id),
        service_name,
        link,
        quantity,
        float(charge),
        str(provider_order_id or ""),
        status,
    ))

    conn.commit()

    order_id = cur.lastrowid

    conn.close()

    return order_id


def get_user_info(user_id):
    conn = get_db()

    row = conn.execute("""
        SELECT
            user_id,
            username,
            first_name,
            balance,
            created_at
        FROM users
        WHERE user_id = ?
    """, (user_id,)).fetchone()

    conn.close()

    return row


def get_user_orders(user_id, limit=10):
    conn = get_db()

    rows = conn.execute("""
        SELECT
            id,
            service_name,
            link,
            quantity,
            charge,
            provider_order_id,
            status,
            created_at
        FROM orders
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT ?
    """, (user_id, limit)).fetchall()

    conn.close()

    return rows


# =========================================================
# HELPERS
# =========================================================

def money(value):
    return f"{Decimal(str(value)):.2f}"


def customer_rate(provider_rate_usd):
    return (
        Decimal(str(provider_rate_usd))
        * USD_TO_BDT
        * PROFIT_MULTIPLIER
        / Decimal("1000")
    )


def parse_amount(text):
    try:
        amount = Decimal(text.strip())

        if amount <= 0:
            return None

        return amount.quantize(Decimal("0.01"))

    except (InvalidOperation, ValueError):
        return None


def parse_quantity(text):
    try:
        qty = int(text.strip())

        if qty <= 0:
            return None

        return qty

    except ValueError:
        return None


def is_admin(user_id):
    return user_id == ADMIN_ID


# =========================================================
# KEYBOARDS
# =========================================================

def main_keyboard(user_id=None):

    rows = [
        [
            KeyboardButton("🛍 Services"),
            KeyboardButton("💰 Balance"),
        ],
        [
            KeyboardButton("📦 My Orders"),
            KeyboardButton("💳 Recharge"),
        ],
        [
            KeyboardButton("📞 Helpline"),
            KeyboardButton("📜 Terms"),
        ],
    ]

    if user_id == ADMIN_ID:
        rows.append([
            KeyboardButton("👑 Admin Panel")
        ])

    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
    )


def admin_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "➕ Add Balance",
                callback_data="admin_add",
            ),
            InlineKeyboardButton(
                "➖ Deduct Balance",
                callback_data="admin_deduct",
            ),
        ],
        [
            InlineKeyboardButton(
                "👤 User Info",
                callback_data="admin_user_info",
            ),
        ],
        [
            InlineKeyboardButton(
                "❌ Close",
                callback_data="admin_close",
            )
        ],
    ])


def cancel_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data="cancel_action",
            )
        ]
    ])


def services_keyboard(services):

    buttons = []

    for service in services[:50]:

        sid = str(service.get("service", ""))

        name = str(
            service.get(
                "name",
                "Service",
            )
        )

        if len(name) > 55:
            name = name[:52] + "..."

        callback = f"service_{sid}"

        # Telegram callback_data max 64 bytes
        if len(callback.encode("utf-8")) <= 64:

            buttons.append([
                InlineKeyboardButton(
                    name,
                    callback_data=callback,
                )
            ])

    buttons.append([
        InlineKeyboardButton(
            "❌ Close",
            callback_data="close_services",
        )
    ])

    return InlineKeyboardMarkup(buttons)


# =========================================================
# SMM API
# =========================================================

async def api_request(action, data=None):

    payload = {
        "key": SMM_API_KEY,
        "action": action,
    }

    if data:
        payload.update(data)

    timeout = aiohttp.ClientTimeout(total=30)

    try:

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.post(
                SMM_API_URL,
                data=payload,
            ) as response:

                text = await response.text()

                if response.status != 200:

                    logger.error(
                        "SMM API HTTP %s: %s",
                        response.status,
                        text[:500],
                    )

                    return None

                try:
                    import json

                    return json.loads(text)

                except Exception:

                    logger.error(
                        "SMM API returned non-JSON: %s",
                        text[:500],
                    )

                    return None

    except Exception:

        logger.exception(
            "SMM API request failed"
        )

        return None


async def fetch_services():

    result = await api_request(
        "services"
    )

    if isinstance(result, list):
        return result

    if isinstance(result, dict):

        if isinstance(
            result.get("services"),
            list,
        ):
            return result["services"]

        if result.get("error"):
            logger.error(
                "SMM API services error: %s",
                result.get("error"),
            )

    return []


def find_service(
    services,
    service_id,
):

    for service in services:

        if str(
            service.get("service")
        ) == str(service_id):

            return service

    return None


def detect_platform(name):

    text = name.lower()

    platforms = [
        ("facebook", "Facebook"),
        ("instagram", "Instagram"),
        ("tiktok", "TikTok"),
        ("youtube", "YouTube"),
        ("telegram", "Telegram"),
        ("twitter", "Twitter/X"),
        ("x.com", "Twitter/X"),
        ("spotify", "Spotify"),
    ]

    for key, label in platforms:

        if key in text:
            return label

    return "Social Media"


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    ensure_user(
        update.effective_user
    )

    context.user_data.clear()

    name = (
        update.effective_user.first_name
        or "User"
    )

    text = (
        f"👋 Welcome {name}!\n\n"
        "🚀 Welcome to Quick SMM Panel Bot.\n"
        "এখান থেকে Social Media service order করতে পারবেন।\n\n"
        "নিচের menu থেকে একটি option নির্বাচন করুন।"
    )

    await update.message.reply_text(
        text,
        reply_markup=main_keyboard(
            update.effective_user.id
        ),
    )


# =========================================================
# CANCEL COMMAND
# =========================================================

async def cancel_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    context.user_data.clear()

    await update.message.reply_text(
        "❌ Current action cancelled.",
        reply_markup=main_keyboard(
            update.effective_user.id
        ),
    )


# =========================================================
# SERVICES
# =========================================================

async def show_services(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    ensure_user(
        update.effective_user
    )

    await update.message.reply_text(
        "⏳ Services loading..."
    )

    services = await fetch_services()

    if not services:

        await update.message.reply_text(
            "❌ এখন services পাওয়া যাচ্ছে না.\n\n"
            "API connection অথবা provider check করুন।"
        )

        return

    context.user_data[
        "services_cache"
    ] = services

    await update.message.reply_text(
        f"🛍 Available Services ({len(services)})\n\n"
        "একটি service নির্বাচন করুন:",
        reply_markup=services_keyboard(
            services
        ),
    )


# =========================================================
# SERVICE SELECTED
# =========================================================

async def service_selected(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    service_id = query.data.replace(
        "service_",
        "",
        1,
    )

    services = context.user_data.get(
        "services_cache",
        [],
    )

    service = find_service(
        services,
        service_id,
    )

    if not service:

        services = await fetch_services()

        service = find_service(
            services,
            service_id,
        )

        context.user_data[
            "services_cache"
        ] = services

    if not service:

        await query.edit_message_text(
            "❌ Service পাওয়া যায়নি। আবার Services খুলুন।"
        )

        return

    context.user_data[
        "selected_service"
    ] = service

    name = str(
        service.get(
            "name",
            "Service",
        )
    )

    rate = service.get(
        "rate",
        0,
    )

    minimum = service.get(
        "min",
        "-",
    )

    maximum = service.get(
        "max",
        "-",
    )

    description = (
        service.get(
            "desc",
            "",
        )
        or ""
    )

    try:

        customer = customer_rate(
            rate
        )

        rate_text = (
            f"৳{customer:.4f} / 1000"
        )

    except Exception:

        rate_text = "N/A"

    platform = detect_platform(
        name
    )

    text = (
        f"📌 {name}\n\n"
        f"📱 Platform: {platform}\n"
        f"💵 Price: {rate_text}\n"
        f"🔢 Min: {minimum}\n"
        f"🔢 Max: {maximum}\n"
    )

    if description:

        text += (
            f"\nℹ️ {description[:800]}\n"
        )

    text += (
        "\n🔗 এখন আপনার target link পাঠান:"
    )

    context.user_data[
        "order_step"
    ] = "link"

    await query.edit_message_text(
        text,
        reply_markup=cancel_keyboard(),
    )


# =========================================================
# ORDER MESSAGE
# =========================================================

async def handle_order_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    step = context.user_data.get(
        "order_step"
    )

    if not step:
        return False

    text = (
        update.message.text
        or ""
    ).strip()

    service = context.user_data.get(
        "selected_service"
    )

    if not service:

        context.user_data.pop(
            "order_step",
            None,
        )

        return False

    # -------------------------
    # LINK
    # -------------------------

    if step == "link":

        if (
            len(text) < 3
            or text.startswith("/")
        ):

            await update.message.reply_text(
                "❌ সঠিক target link পাঠান।"
            )

            return True

        context.user_data[
            "order_link"
        ] = text

        context.user_data[
            "order_step"
        ] = "quantity"

        await update.message.reply_text(
            "🔗 Link received.\n\n"
            "এখন quantity পাঠান.\n"
            f"Min: {service.get('min', '-')}"
            f" | Max: {service.get('max', '-')}",
            reply_markup=cancel_keyboard(),
        )

        return True

    # -------------------------
    # QUANTITY
    # -------------------------

    if step == "quantity":

        qty = parse_quantity(
            text
        )

        if qty is None:

            await update.message.reply_text(
                "❌ Quantity অবশ্যই positive number হতে হবে।"
            )

            return True

        try:

            min_q = int(
                service.get(
                    "min",
                    0,
                )
            )

            max_q = int(
                service.get(
                    "max",
                    10**18,
                )
            )

        except (
            ValueError,
            TypeError,
        ):

            min_q = 0
            max_q = 10**18

        if qty < min_q or qty > max_q:

            await update.message.reply_text(
                f"❌ Quantity limit ঠিক নেই.\n"
                f"Min: {min_q}\n"
                f"Max: {max_q}"
            )

            return True

        try:

            charge = (
                Decimal(
                    str(
                        service.get(
                            "rate",
                            0,
                        )
                    )
                )
                * Decimal(qty)
                * USD_TO_BDT
                * PROFIT_MULTIPLIER
                / Decimal("1000")
            )

            charge = charge.quantize(
                Decimal("0.01")
            )

        except Exception:

            await update.message.reply_text(
                "❌ Service price পড়তে সমস্যা হয়েছে।"
            )

            context.user_data.clear()

            return True

        balance = get_balance(
            update.effective_user.id
        )

        context.user_data[
            "order_quantity"
        ] = qty

        context.user_data[
            "order_charge"
        ] = str(charge)

        context.user_data[
            "order_step"
        ] = "confirm"

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Confirm Order",
                    callback_data="confirm_order",
                ),
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data="cancel_action",
                ),
            ]
        ])

        if balance < charge:

            balance_warning = (
                "⚠️ Balance insufficient. "
                "Please recharge first."
            )

        else:

            balance_warning = (
                "Confirm করলে order provider-এ পাঠানো হবে।"
            )

        await update.message.reply_text(
            "🧾 Order Summary\n\n"
            f"📌 Service: {service.get('name', 'Service')}\n"
            f"🔗 Link: {context.user_data['order_link']}\n"
            f"🔢 Quantity: {qty}\
