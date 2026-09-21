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
            KeyboardButton("👤 Account"),
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


SERVICE_CATEGORIES = {
    "facebook": "📘 Facebook",
    "tiktok": "🎵 TikTok",
    "youtube": "▶️ YouTube",
    "traffic": "🚦 Traffic",
}


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
        [
            InlineKeyboardButton("❌ Close", callback_data="close_services")
        ],
    ])


def service_category(service):
    # Provider service name/description/category থেকে শুধু ৪টি allowed category detect করা হবে।
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

    return None


def filter_services_by_category(services, category):
    return [
        service for service in services
        if service_category(service) == category
    ]


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
            "⬅️ Categories",
            callback_data="service_categories",
        ),
        InlineKeyboardButton(
            "❌ Close",
            callback_data="close_services",
        ),
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

    ensure_user(update.effective_user)

    await update.message.reply_text(
        "🛍 Services\n\nএকটি category নির্বাচন করুন:",
        reply_markup=services_category_keyboard(),
    )


async def show_category_services(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    category: str,
):

    query = update.callback_query
    await query.answer()

    services = context.user_data.get("all_services_cache")

    if services is None:
        await query.edit_message_text("⏳ Services loading...")
        services = await fetch_services()
        context.user_data["all_services_cache"] = services

    if not services:
        await query.edit_message_text(
            "❌ এখন services পাওয়া যাচ্ছে না।\n\n"
            "API connection অথবা provider check করুন।"
        )
        return

    filtered = filter_services_by_category(services, category)
    context.user_data["services_cache"] = filtered

    title = SERVICE_CATEGORIES.get(category, "Services")

    if not filtered:
        await query.edit_message_text(
            f"{title}\n\n❌ এই category-তে কোনো service পাওয়া যায়নি।",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Categories", callback_data="service_categories")],
            ]),
        )
        return

    await query.edit_message_text(
        f"{title}\n\n"
        f"Available Services: {len(filtered)}\n\n"
        "একটি service নির্বাচন করুন:",
        reply_markup=services_keyboard(filtered),
    )


# =========================================================
# SERVICE SELECTED
# =========================================================

async def service_category_selected(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    category = query.data.replace("category_", "", 1)
    if category not in SERVICE_CATEGORIES:
        await query.answer("Invalid category", show_alert=True)
        return
    await show_category_services(update, context, category)


async def service_categories_back(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🛍 Services\n\nএকটি category নির্বাচন করুন:",
        reply_markup=services_category_keyboard(),
    )


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
            f"🔢 Quantity: {qty}\n"
            f"💰 Charge: ৳{charge:.2f}\n"
            f"💳 Your Balance: ৳{balance:.2f}\n\n"
            f"{balance_warning}",
            reply_markup=keyboard,
        )

        return True

    return False


# =========================================================
# CONFIRM ORDER
# =========================================================

async def confirm_order(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    service = context.user_data.get(
        "selected_service"
    )

    link = context.user_data.get(
        "order_link"
    )

    qty = context.user_data.get(
        "order_quantity"
    )

    charge_raw = context.user_data.get(
        "order_charge"
    )

    if not all([
        service,
        link,
        qty,
        charge_raw,
    ]):

        await query.edit_message_text(
            "❌ Order session expired. আবার service select করুন।"
        )

        context.user_data.clear()

        return

    charge = Decimal(
        str(charge_raw)
    )

    user_id = update.effective_user.id

    # =====================================================
    # ATOMIC BALANCE DEDUCTION
    # =====================================================

    conn = get_db()
    cur = conn.cursor()

    try:

        cur.execute(
            "BEGIN IMMEDIATE"
        )

        cur.execute(
            "SELECT balance FROM users WHERE user_id = ?",
            (user_id,),
        )

        row = cur.fetchone()

        current = Decimal(
            str(
                row["balance"]
                if row
                else 0
            )
        )

        if current < charge:

            conn.rollback()

            await query.edit_message_text(
                f"❌ পর্যাপ্ত balance নেই.\n\n"
                f"Required: ৳{charge:.2f}\n"
                f"Balance: ৳{current:.2f}\n\n"
                "💳 Recharge করে আবার order করুন।"
            )

            context.user_data.clear()

            return

        new_balance = (
            current - charge
        )

        cur.execute(
            """
            UPDATE users
            SET balance = ?
            WHERE user_id = ?
            """,
            (
                float(new_balance),
                user_id,
            ),
        )

        conn.commit()

    except Exception:

        conn.rollback()

        logger.exception(
            "Balance deduction failed"
        )

        await query.edit_message_text(
            "❌ Balance update করতে সমস্যা হয়েছে। আবার চেষ্টা করুন।"
        )

        return

    finally:

        conn.close()

    await query.edit_message_text(
        "⏳ Order provider-এ পাঠানো হচ্ছে..."
    )

    # =====================================================
    # SEND ORDER TO SMM PROVIDER
    # =====================================================

    result = await api_request(
        "add",
        {
            "service": str(
                service.get("service")
            ),
            "link": link,
            "quantity": str(qty),
        },
    )

    provider_order_id = ""
    status = "Pending"

    # =====================================================
    # SUCCESS
    # =====================================================

    if (
        isinstance(result, dict)
        and result.get("order")
    ):

        provider_order_id = str(
            result.get("order")
        )

        # Provider order ID পাওয়া মানে provider order গ্রহণ করেছে।
        # Actual delivery completion status আলাদা; সেটি provider status API ছাড়া নিশ্চিত করা যায় না।
        status = "Success"

    # =====================================================
    # FAILED -> REFUND
    # =====================================================

    else:

        change_balance(
            user_id,
            charge,
        )

        error = "Unknown provider error"

        if isinstance(
            result,
            dict,
        ):

            error = str(
                result.get(
                    "error",
                    result,
                )
            )

        elif result is None:

            error = (
                "Provider connection failed"
            )

        await query.edit_message_text(
            "❌ Order failed.\n\n"
            f"Reason: {error[:500]}\n\n"
            f"💰 ৳{charge:.2f} "
            "আপনার balance-এ ফেরত দেওয়া হয়েছে।"
        )

        context.user_data.clear()

        return

    # =====================================================
    # SAVE ORDER
    # =====================================================

    db_order_id = save_order(
        user_id=user_id,
        service_id=service.get(
            "service"
        ),
        service_name=service.get(
            "name",
            "Service",
        ),
        link=link,
        quantity=qty,
        charge=charge,
        provider_order_id=provider_order_id,
        status=status,
    )

    await query.edit_message_text(
        "✅ Order Success!\n\n"
        f"🆔 Order ID: {db_order_id}\n"
        f"🔢 Provider ID: {provider_order_id}\n"
        f"📌 Service: {service.get('name', 'Service')}\n"
        f"🔢 Quantity: {qty}\n"
        f"💰 Charge: ৳{charge:.2f}\n"
        f"💳 Remaining Balance: "
        f"৳{get_balance(user_id):.2f}"
    )

    context.user_data.clear()


# =========================================================
# BALANCE
# =========================================================

async def balance(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    ensure_user(
        update.effective_user
    )

    bal = get_balance(
        update.effective_user.id
    )

    await update.message.reply_text(
        f"💰 Your Balance\n\n"
        f"৳{bal:.2f}",
        reply_markup=main_keyboard(
            update.effective_user.id
        ),
    )


# =========================================================
# RECHARGE
# =========================================================

async def account(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    ensure_user(update.effective_user)

    user = update.effective_user
    bal = get_balance(user.id)
    username = f"@{user.username}" if user.username else "Not set"

    await update.message.reply_text(
        "👤 My Account\n\n"
        f"🆔 Account Number: {user.id}\n"
        f"👤 Name: {user.first_name or '-'}\n"
        f"🔹 Username: {username}\n"
        f"💰 Balance: ৳{bal:.2f}",
        reply_markup=main_keyboard(user.id),
    )


async def recharge(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    text = (
        "💳 Recharge\n\n"
        f"bKash: {BKASH_NUMBER}\n"
        f"Nagad: {NAGAD_NUMBER}\n\n"
        "Payment করার পর transaction ID সহ "
        "admin-কে message করুন।\n\n"
        f"WhatsApp: {WHATSAPP_URL}"
    )

    await update.message.reply_text(
        text
    )


# =========================================================
# HELPLINE
# =========================================================

async def helpline(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "📞 Helpline\n\n"
        f"WhatsApp: {WHATSAPP_URL}\n\n"
        "Payment/order সমস্যা হলে "
        "আপনার Telegram ID এবং order ID পাঠান।"
    )


# =========================================================
# TERMS
# =========================================================

async def terms(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "📜 Terms & Conditions\n\n"
        "1. Order করার আগে service details ও quantity limit দেখে নিন।\n"
        "2. ভুল link/quantity দিলে refund নাও হতে পারে।\n"
        "3. Provider-এর delivery time service অনুযায়ী পরিবর্তিত হতে পারে।\n"
        "4. Balance recharge/payment সংক্রান্ত সমস্যায় transaction proof দিন।"
    )


# =========================================================
# MY ORDERS
# =========================================================

async def my_orders(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    ensure_user(
        update.effective_user
    )

    rows = get_user_orders(
        update.effective_user.id,
        10,
    )

    if not rows:

        await update.message.reply_text(
            "📦 আপনার এখনো কোনো order নেই।"
        )

        return

    lines = [
        "📦 Your Recent Orders\n"
    ]

    for row in rows:

        lines.append(
            f"🆔 #{row['id']} | {row['status']}\n"
            f"📌 {str(row['service_name'])[:50]}\n"
            f"🔢 Qty: {row['quantity']} | "
            f"৳{Decimal(str(row['charge'])):.2f}\n"
            f"🔢 Provider: "
            f"{row['provider_order_id'] or '-'}\n"
            f"🕒 {row['created_at']}\n"
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


# =========================================================
# ADMIN PANEL
# =========================================================

async def admin_panel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not is_admin(
        update.effective_user.id
    ):

        await update.message.reply_text(
            "❌ Admin only."
        )

        return

    context.user_data.clear()

    await update.message.reply_text(
        "👑 Admin Panel\n\n"
        "একটি option নির্বাচন করুন:",
        reply_markup=admin_keyboard(),
    )


# =========================================================
# START ADMIN ACTION
# =========================================================

async def start_admin_action(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    action,
):

    query = update.callback_query

    await query.answer()

    if not is_admin(
        update.effective_user.id
    ):

        await query.edit_message_text(
            "❌ Admin only."
        )

        return

    context.user_data.clear()

    context.user_data[
        "admin_action"
    ] = action

    context.user_data[
        "admin_step"
    ] = "user_id"

    if action == "add":

        title = "➕ Add Balance"

    elif action == "deduct":

        title = "➖ Deduct Balance"

    else:

        title = "👤 User Info"

    await query.edit_message_text(
        f"{title}\n\n"
        "প্রথমে User ID পাঠান:",
        reply_markup=cancel_keyboard(),
    )


# =========================================================
# ADMIN CALLBACK
# =========================================================

async def admin_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    data = update.callback_query.data

    if data == "admin_add":

        await start_admin_action(
            update,
            context,
            "add",
        )

    elif data == "admin_deduct":

        await start_admin_action(
            update,
            context,
            "deduct",
        )

    elif data == "admin_user_info":

        await start_admin_action(
            update,
            context,
            "info",
        )

    elif data == "admin_close":

        await update.callback_query.answer()

        context.user_data.clear()

        await update.callback_query.edit_message_text(
            "Admin panel closed."
        )


# =========================================================
# ADMIN MESSAGE FLOW
# =========================================================

async def handle_admin_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not is_admin(
        update.effective_user.id
    ):

        return False

    action = context.user_data.get(
        "admin_action"
    )

    step = context.user_data.get(
        "admin_step"
    )

    if not action or not step:

        return False

    text = (
        update.message.text
        or ""
    ).strip()

    # =====================================================
    # STEP 1 - USER ID
    # =====================================================

    if step == "user_id":

        try:

            target_id = int(text)

            if target_id <= 0:
                raise ValueError

        except ValueError:

            await update.message.reply_text(
                "❌ সঠিক numeric Telegram User ID দিন।"
            )

            return True

        context.user_data[
            "target_user_id"
        ] = target_id

        # USER INFO
        if action == "info":

            row = get_user_info(
                target_id
            )

            if not row:

                await update.message.reply_text(
                    f"❌ User {target_id} "
                    "database-এ পাওয়া যায়নি।"
                )

            else:

                username = (
                    f"@{row['username']}"
                    if row["username"]
                    else "-"
                )

                await update.message.reply_text(
                    "👤 User Info\n\n"
                    f"🆔 ID: {row['user_id']}\n"
                    f"👤 Name: {row['first_name'] or '-'}\n"
                    f"🔗 Username: {username}\n"
                    f"💰 Balance: "
                    f"৳{Decimal(str(row['balance'])):.2f}\n"
                    f"🕒 Created: {row['created_at']}"
                )

            context.user_data.clear()

            return True

        # ADD / DEDUCT
        context.user_data[
            "admin_step"
        ] = "amount"

        label = (
            "➕ Add"
            if action == "add"
            else "➖ Deduct"
        )

        await update.message.reply_text(
            f"{label} Balance\n\n"
            f"User ID: {target_id}\n\n"
            "এখন amount (BDT) পাঠান:",
            reply_markup=cancel_keyboard(),
        )

        return True

    # =====================================================
    # STEP 2 - AMOUNT
    # =====================================================

    if step == "amount":

        amount = parse_amount(
            text
        )

        if amount is None:

            await update.message.reply_text(
                "❌ সঠিক positive amount দিন।\n"
                "Example: 100"
            )

            return True

        target_id = context.user_data.get(
            "target_user_id"
        )

        context.user_data[
            "amount"
        ] = str(amount)

        context.user_data[
            "admin_step"
        ] = "confirm"

        current = get_balance(
            target_id
        )

        if action == "add":

            after = current + amount

            title = (
                "➕ Add Balance Confirmation"
            )

        else:

            after = current - amount

            title = (
                "➖ Deduct Balance Confirmation"
            )

        warning = ""

        if (
            action == "deduct"
            and amount > current
        ):

            warning = (
                "\n\n⚠️ Insufficient balance — "
                "এই deduction করা যাবে না।"
            )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Confirm",
                    callback_data="admin_confirm",
                ),
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data="cancel_action",
                ),
            ]
        ])

        await update.message.reply_text(
            f"{title}\n\n"
            f"👤 User ID: {target_id}\n"
            f"💰 Current: ৳{current:.2f}\n"
            f"💵 Amount: ৳{amount:.2f}\n"
            f"📊 After: ৳{after:.2f}"
            f"{warning}\n\n"
            "Confirm করবেন?",
            reply_markup=keyboard,
        )

        return True

    return False


# =========================================================
# ADMIN CONFIRM
# =========================================================

async def admin_confirm(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    if not is_admin(
        update.effective_user.id
    ):

        await query.edit_message_text(
            "❌ Admin only."
        )

        return

    action = context.user_data.get(
        "admin_action"
    )

    target_id = context.user_data.get(
        "target_user_id"
    )

    amount_raw = context.user_data.get(
        "amount"
    )

    if (
        action not in ("add", "deduct")
        or not target_id
        or not amount_raw
    ):

        await query.edit_message_text(
            "❌ Admin action expired. "
            "আবার Admin Panel খুলুন।"
        )

        context.user_data.clear()

        return

    amount = Decimal(
        str(amount_raw)
    )

    # ADD
    if action == "add":

        new_balance = change_balance(
            target_id,
            amount,
        )

        await query.edit_message_text(
            "✅ Balance Added Successfully\n\n"
            f"👤 User ID: {target_id}\n"
            f"➕ Added: ৳{amount:.2f}\n"
            f"💰 New Balance: ৳{new_balance:.2f}"
        )

    # DEDUCT
    else:

        new_balance = deduct_balance(
            target_id,
            amount,
        )

        if new_balance is None:

            await query.edit_message_text(
                "❌ Deduct করা যায়নি।\n"
                "User-এর balance পর্যাপ্ত নয়।"
            )

        else:

            await query.edit_message_text(
                "✅ Balance Deducted Successfully\n\n"
                f"👤 User ID: {target_id}\n"
                f"➖ Deducted: ৳{amount:.2f}\n"
                f"💰 New Balance: ৳{new_balance:.2f}"
            )

    context.user_data.clear()


# =========================================================
# CANCEL BUTTON
# =========================================================

async def cancel_action(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    context.user_data.clear()

    await query.edit_message_text(
        "❌ Action cancelled."
    )


# =========================================================
# TEXT ROUTER
# =========================================================

async def text_router(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if (
        not update.message
        or not update.message.text
    ):
        return

    text = update.message.text.strip()

    user_id = update.effective_user.id

    ensure_user(
        update.effective_user
    )

    # =====================================================
    # ADMIN FLOW FIRST
    # =====================================================

    if (
        is_admin(user_id)
        and context.user_data.get(
            "admin_action"
        )
    ):

        handled = await handle_admin_message(
            update,
            context,
        )

        if handled:
            return

    # =====================================================
    # ORDER FLOW
    # =====================================================

    if context.user_data.get(
        "order_step"
    ):

        handled = await handle_order_message(
            update,
            context,
        )

        if handled:
            return

    # =====================================================
    # MAIN MENU
    # =====================================================

    if text == "🛍 Services":

        await show_services(
            update,
            context,
        )

    elif text == "👤 Account":

        await account(
            update,
            context,
        )

    elif text == "📦 My Orders":

        await my_orders(
            update,
            context,
        )

    elif text == "💳 Recharge":

        await recharge(
            update,
            context,
        )

    elif text == "📞 Helpline":

        await helpline(
            update,
            context,
        )

    elif text == "📜 Terms":

        await terms(
            update,
            context,
        )

    elif (
        text == "👑 Admin Panel"
        and is_admin(user_id)
    ):

        await admin_panel(
            update,
            context,
        )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):

    logger.exception(
        "Unhandled exception",
        exc_info=context.error,
    )


# =========================================================
# MAIN
# =========================================================

def main():

    init_db()

    if not TELEGRAM_BOT_TOKEN:

        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing. "
            "Add TELEGRAM_BOT_TOKEN in Railway Variables."
        )

    # Railway log-এ এগুলো দেখা যাবে
    print(
        "BOT STARTING...",
        flush=True,
    )

    print(
        f"ADMIN_ID: {ADMIN_ID}",
        flush=True,
    )

    print(
        "Telegram polling is starting...",
        flush=True,
    )

    application = (
        Application
        .builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    # =====================================================
    # COMMAND HANDLERS
    # =====================================================

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        CommandHandler(
            "cancel",
            cancel_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "my_orders",
            my_orders,
        )
    )

    # =====================================================
    # CALLBACK HANDLERS
    # =====================================================

    application.add_handler(
        CallbackQueryHandler(
            confirm_order,
            pattern=r"^confirm_order$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            admin_confirm,
            pattern=r"^admin_confirm$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            cancel_action,
            pattern=r"^cancel_action$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            admin_callback,
            pattern=r"^admin_(add|deduct|user_info|close)$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            service_category_selected,
            pattern=r"^category_(facebook|tiktok|youtube|traffic)$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            service_categories_back,
            pattern=r"^service_categories$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            service_selected,
            pattern=r"^service_.+",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            lambda update, context:
                update.callback_query.answer(),
            pattern=r"^close_services$",
        )
    )

    # =====================================================
    # TEXT HANDLER
    # =====================================================

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_router,
        )
    )

    application.add_error_handler(
        error_handler
    )

    # =====================================================
    # IMPORTANT RAILWAY POLLING SETUP
    # =====================================================
    #
    # drop_pending_updates=True
    # পুরোনো pending Telegram messages clear করে
    # নতুন করে polling শুরু করবে।
    #
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
