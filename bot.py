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

# Test/demo key from the original bot. If this is ever a real key,
# keep it in Railway Variables instead of exposing it in chat/code.
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

# Set to 0 to disable. Change this number if you want a new-user bonus.
WELCOME_BONUS = Decimal("0.00")  # Default: no welcome bonus

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

    # Safe migrations for databases created by older versions.
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

    try:
        cur.execute("BEGIN IMMEDIATE")
        row = cur.execute(
            "SELECT balance FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()

        if not row:
            conn.rollback()
            return None

        current = Decimal(str(row["balance"]))
        if current < amount:
            conn.rollback()
            return None

        new_balance = current - amount
        cur.execute(
            "UPDATE users SET balance = ? WHERE user_id = ?",
            (float(new_balance), user_id),
        )
        conn.commit()
        return new_balance
    except Exception:
        conn.rollback()
        logger.exception("Deduct balance failed")
        return None
    finally:
        conn.close()


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
    cur.execute(
        """
        INSERT INTO orders (
            user_id, service_id, service_name, link, quantity,
            charge, provider_order_id, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            str(service_id),
            service_name,
            link,
            quantity,
            float(charge),
            str(provider_order_id or ""),
            status,
        ),
    )
    conn.commit()
    order_id = cur.lastrowid
    conn.close()
    return order_id


def update_order_status(db_order_id, status):
    conn = get_db()
    conn.execute(
        "UPDATE orders SET status = ? WHERE id = ?",
        (status, db_order_id),
    )
    conn.commit()
    conn.close()


def get_order(db_order_id, user_id=None):
    conn = get_db()
    if user_id is None:
        row = conn.execute(
            "SELECT * FROM orders WHERE id = ?",
            (db_order_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM orders WHERE id = ? AND user_id = ?",
            (db_order_id, user_id),
        ).fetchone()
    conn.close()
    return row


def get_user_info(user_id):
    conn = get_db()
    row = conn.execute(
        """
        SELECT user_id, username, first_name, balance,
               created_at, welcome_bonus_given
        FROM users
        WHERE user_id = ?
        """,
        (user_id,),
    ).fetchone()
    conn.close()
    return row


def get_trackable_orders(limit=100):
    conn = get_db()
    rows = conn.execute(
        """
        SELECT id, user_id, provider_order_id, status
        FROM orders
        WHERE provider_order_id IS NOT NULL
          AND provider_order_id != ''
          AND status NOT IN ('Completed', 'Canceled', 'Cancelled', 'Refunded', 'Failed')
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return rows


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


def get_all_user_ids():
    conn = get_db()
    rows = conn.execute("SELECT user_id FROM users ORDER BY user_id").fetchall()
    conn.close()
    return [row["user_id"] for row in rows]


def get_stats():
    conn = get_db()
    users = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    orders = conn.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"]
    successful = conn.execute(
        "SELECT COUNT(*) AS n FROM orders WHERE status IN ('Success', 'Completed')"
    ).fetchone()["n"]
    sales = conn.execute(
        "SELECT COALESCE(SUM(charge), 0) AS n FROM orders WHERE status IN ('Success', 'Completed')"
    ).fetchone()["n"]
    recharge_pending = conn.execute(
        "SELECT COUNT(*) AS n FROM recharge_requests WHERE status = 'Pending'"
    ).fetchone()["n"]
    conn.close()
    return users, orders, successful, Decimal(str(sales)), recharge_pending


def create_recharge_request(user_id, method, amount, transaction_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO recharge_requests (
            user_id, method, amount, transaction_id, status
        ) VALUES (?, ?, ?, ?, 'Pending')
        """,
        (user_id, method, float(amount), transaction_id),
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def get_recharge(rid):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM recharge_requests WHERE id = ?",
        (rid,),
    ).fetchone()
    conn.close()
    return row


def review_recharge(rid, approve):
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute("BEGIN IMMEDIATE")
        row = cur.execute(
            "SELECT * FROM recharge_requests WHERE id = ?",
            (rid,),
        ).fetchone()

        if not row or row["status"] != "Pending":
            conn.rollback()
            return None, "already_reviewed"

        if approve:
            current_row = cur.execute(
                "SELECT balance FROM users WHERE user_id = ?",
                (row["user_id"],),
            ).fetchone()
            current = Decimal(str(current_row["balance"])) if current_row else Decimal("0")
            new_balance = current + Decimal(str(row["amount"]))

            if current_row:
                cur.execute(
                    "UPDATE users SET balance = ? WHERE user_id = ?",
                    (float(new_balance), row["user_id"]),
                )
            else:
                cur.execute(
                    "INSERT INTO users(user_id, balance) VALUES (?, ?)",
                    (row["user_id"], float(new_balance)),
                )

            cur.execute(
                """
                UPDATE recharge_requests
                SET status = 'Approved', reviewed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (rid,),
            )
            conn.commit()
            return new_balance, "approved"

        cur.execute(
            """
            UPDATE recharge_requests
            SET status = 'Rejected', reviewed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (rid,),
        )
        conn.commit()
        return None, "rejected"

    except Exception:
        conn.rollback()
        logger.exception("Recharge review failed")
        return None, "error"
    finally:
        conn.close()


# =========================================================
# HELPERS
# =========================================================

def money(value):
    return f"{Decimal(str(value)):.2f}"


def customer_price_per_1000(provider_rate_usd):
    """Customer price in BDT for 1000 quantity."""
    return (
        Decimal(str(provider_rate_usd))
        * USD_TO_BDT
        * PROFIT_MULTIPLIER
    )


def order_charge(provider_rate_usd, quantity):
    return (
        customer_price_per_1000(provider_rate_usd)
        * Decimal(str(quantity))
        / Decimal("1000")
    ).quantize(Decimal("0.01"))


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
        return qty if qty > 0 else None
    except ValueError:
        return None


def is_admin(user_id):
    return user_id == ADMIN_ID


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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
        [KeyboardButton("🛍 Services"), KeyboardButton("👤 Account")],
        [KeyboardButton("📦 My Orders"), KeyboardButton("💳 Recharge")],
        [KeyboardButton("📞 Helpline"), KeyboardButton("📜 Terms")],
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
            InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
            InlineKeyboardButton("💳 Recharge Requests", callback_data="admin_recharges"),
        ],
        [
            InlineKeyboardButton("🛠 Maintenance", callback_data="admin_maintenance"),
            InlineKeyboardButton("🎁 Welcome Bonus", callback_data="admin_welcome_bonus"),
        ],
        [
            InlineKeyboardButton("❌ Close", callback_data="admin_close"),
        ],
    ])


def cancel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")]
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
        [InlineKeyboardButton("❌ Close", callback_data="close_services")],
    ])


def recharge_method_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("bKash", callback_data="recharge_bkash"),
            InlineKeyboardButton("Nagad", callback_data="recharge_nagad"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")],
    ])


# =========================================================
# SERVICES
# =========================================================

SERVICE_CATEGORIES = {
    "facebook": "📘 Facebook",
    "tiktok": "🎵 TikTok",
    "youtube": "▶️ YouTube",
    "traffic": "🚦 Traffic",
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
    return None


def filter_services_by_category(services, category):
    return [s for s in services if service_category(s) == category]


def services_keyboard(services):
    buttons = []

    for service in services[:50]:
        sid = str(service.get("service", ""))
        name = str(service.get("name", "Service"))

        raw_rate = service.get("rate", "-")
        try:
            api_rate = Decimal(str(raw_rate))
            rate_text = f" | Rate: ${api_rate:g}/1K"
        except Exception:
            rate_text = f" | Rate: ${raw_rate}/1K"

        display_name = f"{name}{rate_text}"
        if len(display_name) > 55:
            display_name = display_name[:52] + "..."

        callback = f"service_{sid}"
        if len(callback.encode("utf-8")) <= 64:
            buttons.append([
                InlineKeyboardButton(display_name, callback_data=callback)
            ])

    buttons.append([
        InlineKeyboardButton("⬅️ Categories", callback_data="service_categories"),
        InlineKeyboardButton("❌ Close", callback_data="close_services"),
    ])
    return InlineKeyboardMarkup(buttons)


# =========================================================
# SMM API
# =========================================================

async def api_request(action, data=None):
    payload = {"key": SMM_API_KEY, "action": action}
    if data:
        payload.update(data)

    timeout = aiohttp.ClientTimeout(total=30)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(SMM_API_URL, data=payload) as response:
                text = await response.text()

                if response.status != 200:
                    logger.error("SMM API HTTP %s: %s", response.status, text[:500])
                    return None

                try:
                    import json
                    return json.loads(text)
                except Exception:
                    logger.error("SMM API returned non-JSON: %s", text[:500])
                    return None

    except Exception:
        logger.exception("SMM API request failed")
        return None


async def fetch_services():
    result = await api_request("services")
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        if isinstance(result.get("services"), list):
            return result["services"]
        if result.get("error"):
            logger.error("SMM API services error: %s", result.get("error"))
    return []


async def fetch_provider_status(provider_order_id):
    if not provider_order_id:
        return None

    result = await api_request(
        "status",
        {"order": str(provider_order_id)},
    )

    if isinstance(result, dict) and not result.get("error"):
        return result
    return None


def find_service(services, service_id):
    for service in services:
        if str(service.get("service")) == str(service_id):
            return service
    return None


def detect_platform(name):
    text = name.lower()
    platforms = [
        ("facebook", "Facebook"),
        ("tiktok", "TikTok"),
        ("youtube", "YouTube"),
        ("traffic", "Traffic"),
    ]
    for key, label in platforms:
        if key in text:
            return label
    return "Social Media"


def normalize_provider_status(raw_status):
    status = str(raw_status or "").strip().lower()
    mapping = {
        "completed": "Completed",
        "complete": "Completed",
        "success": "Success",
        "processing": "Processing",
        "in progress": "Processing",
        "in_progress": "Processing",
        "pending": "Pending",
        "partial": "Partial",
        "canceled": "Canceled",
        "cancelled": "Canceled",
        "refunded": "Refunded",
        "failed": "Failed",
    }
    return mapping.get(status, str(raw_status).strip() if raw_status else "Pending")


# =========================================================
# START / CANCEL
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    is_new = ensure_user(update.effective_user)
    context.user_data.clear()

    name = update.effective_user.first_name or "User"

    bonus_text = ""
    welcome_bonus = get_welcome_bonus()
    if is_new and welcome_bonus > 0:
        bonus_text = (
            f"\n🎁 Welcome Bonus: ৳{WELCOME_BONUS:.2f}"
            "\nআপনার account-এ যোগ হয়েছে।\n"
        )

    await update.message.reply_text(
        f"👋 Welcome {name}!\n\n"
        "🚀 Welcome to Quick SMM Panel Bot.\n"
        "এখান থেকে Social Media service order করতে পারবেন.\n"
        f"{bonus_text}\n"
        "নিচের menu থেকে একটি option নির্বাচন করুন।",
        reply_markup=main_keyboard(update.effective_user.id),
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Current action cancelled.",
        reply_markup=main_keyboard(update.effective_user.id),
    )


# =========================================================
# SERVICES UI
# =========================================================

async def show_services(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await maintenance_guard(update):
        return

    ensure_user(update.effective_user)
    context.user_data.pop("all_services_cache", None)

    await update.message.reply_text(
        "🛍 Services\n\nএকটি category নির্বাচন করুন:",
        reply_markup=services_category_keyboard(),
    )


async def show_category_services(update, context, category):
    query = update.callback_query
    await query.answer()

    if maintenance_enabled() and not is_admin(update.effective_user.id):
        await query.answer("🛠️ Maintenance mode চলছে।", show_alert=True)
        return

    services = context.user_data.get("all_services_cache")
    if services is None:
        await query.edit_message_text("⏳ Services loading...")
        services = await fetch_services()
        context.user_data["all_services_cache"] = services

    if not services:
        await query.edit_message_text(
            "❌ এখন services পাওয়া যাচ্ছে না.\n\n"
            "API connection অথবা provider check করুন."
        )
        return

    filtered = filter_services_by_category(services, category)
    context.user_data["services_cache"] = filtered
    title = SERVICE_CATEGORIES.get(category, "Services")

    if not filtered:
        await query.edit_message_text(
            f"{title}\n\n❌ এই category-তে কোনো service পাওয়া যায়নি.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Categories", callback_data="service_categories")]
            ]),
        )
        return

    await query.edit_message_text(
        f"{title}\n\n"
        f"Available Services: {len(filtered)}\n\n"
        "Service-এর সাথে provider API rate ($/1000) দেখানো আছে.\n"
        "একটি service নির্বাচন করুন:",
        reply_markup=services_keyboard(filtered),
    )


async def service_category_selected(update, context):
    query = update.callback_query
    category = query.data.replace("category_", "", 1)
    if category not in SERVICE_CATEGORIES:
        await query.answer("Invalid category", show_alert=True)
        return
    await show_category_services(update, context, category)


async def service_categories_back(update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🛍 Services\n\nএকটি category নির্বাচন করুন:",
        reply_markup=services_category_keyboard(),
    )


async def service_selected(update, context):
    query = update.callback_query
    await query.answer()

    if maintenance_enabled() and not is_admin(update.effective_user.id):
        await query.answer("🛠️ Maintenance mode চলছে.", show_alert=True)
        return

    service_id = query.data.replace("service_", "", 1)
    services = context.user_data.get("services_cache", [])
    service = find_service(services, service_id)

    if not service:
        services = await fetch_services()
        service = find_service(services, service_id)
        context.user_data["services_cache"] = services

    if not service:
        await query.edit_message_text(
            "❌ Service পাওয়া যায়নি। আবার Services খুলুন."
        )
        return

    context.user_data["selected_service"] = service

    name = str(service.get("name", "Service"))
    rate = service.get("rate", 0)
    minimum = service.get("min", "-")
    maximum = service.get("max", "-")
    description = service.get("desc", "") or ""

    try:
        customer_1000 = customer_price_per_1000(rate).quantize(Decimal("0.01"))
        rate_text = f"৳{customer_1000:.2f} / 1000"
        api_rate_text = f"${Decimal(str(rate)):g} / 1000"
    except Exception:
        rate_text = "N/A"
        api_rate_text = f"${rate} / 1000"

    platform = detect_platform(name)

    text = (
        f"📌 {name}\n\n"
        f"📱 Platform: {platform}\n"
        f"🌐 API Rate: {api_rate_text}\n"
        f"💵 Customer Price: {rate_text}\n"
        f"🔢 Min: {minimum}\n"
        f"🔢 Max: {maximum}\n"
    )

    if description:
        text += f"\nℹ️ {description[:800]}\n"

    text += "\n🔗 এখন আপনার target link পাঠান:"

    context.user_data["order_step"] = "link"

    await query.edit_message_text(
        text,
        reply_markup=cancel_keyboard(),
    )


# =========================================================
# ORDER FLOW
# =========================================================

async def handle_order_message(update, context):
    step = context.user_data.get("order_step")
    if not step:
        return False

    text = (update.message.text or "").strip()
    service = context.user_data.get("selected_service")

    if not service:
        context.user_data.pop("order_step", None)
        return False

    if step == "link":
        if len(text) < 3 or text.startswith("/"):
            await update.message.reply_text("❌ সঠিক target link পাঠান.")
            return True

        context.user_data["order_link"] = text
        context.user_data["order_step"] = "quantity"

        await update.message.reply_text(
            "🔗 Link received.\n\n"
            "এখন quantity পাঠান.\n"
            f"Min: {service.get('min', '-')} | Max: {service.get('max', '-')}",
            reply_markup=cancel_keyboard(),
        )
        return True

    if step == "quantity":
        qty = parse_quantity(text)
        if qty is None:
            await update.message.reply_text(
                "❌ Quantity অবশ্যই positive number হতে হবে."
            )
            return True

        try:
            min_q = int(service.get("min", 0))
            max_q = int(service.get("max", 10**18))
        except (ValueError, TypeError):
            min_q, max_q = 0, 10**18

        if qty < min_q or qty > max_q:
            await update.message.reply_text(
                f"❌ Quantity limit ঠিক নেই.\nMin: {min_q}\nMax: {max_q}"
            )
            return True

        try:
            charge = order_charge(service.get("rate", 0), qty)
        except Exception:
            await update.message.reply_text("❌ Service price পড়তে সমস্যা হয়েছে.")
            context.user_data.clear()
            return True

        balance = get_balance(update.effective_user.id)
        context.user_data["order_quantity"] = qty
        context.user_data["order_charge"] = str(charge)
        context.user_data["order_step"] = "confirm"

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Confirm Order", callback_data="confirm_order"),
                InlineKeyboardButton("❌ Cancel", callback_data="cancel_action"),
            ]
        ])

        if balance < charge:
            balance_warning = (
                "⚠️ Balance insufficient. Please recharge first."
            )
        else:
            balance_warning = "Confirm করলে order provider-এ পাঠানো হবে."

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


async def confirm_order(update, context):
    query = update.callback_query
    await query.answer()

    if maintenance_enabled() and not is_admin(update.effective_user.id):
        await query.answer("🛠️ Maintenance mode চলছে.", show_alert=True)
        return

    service = context.user_data.get("selected_service")
    link = context.user_data.get("order_link")
    qty = context.user_data.get("order_quantity")
    charge_raw = context.user_data.get("order_charge")

    if not all([service, link, qty, charge_raw]):
        await query.edit_message_text(
            "❌ Order session expired. আবার service select করুন."
        )
        context.user_data.clear()
        return

    charge = Decimal(str(charge_raw))
    user_id = update.effective_user.id

    # Atomic deduction prevents two simultaneous orders from spending
    # the same balance.
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute("BEGIN IMMEDIATE")
        row = cur.execute(
            "SELECT balance FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        current = Decimal(str(row["balance"])) if row else Decimal("0")

        if current < charge:
            conn.rollback()
            await query.edit_message_text(
                f"❌ পর্যাপ্ত balance নেই.\n\n"
                f"Required: ৳{charge:.2f}\n"
                f"Balance: ৳{current:.2f}\n\n"
                "💳 Recharge করে আবার order করুন."
            )
            context.user_data.clear()
            return

        new_balance = current - charge
        cur.execute(
            "UPDATE users SET balance = ? WHERE user_id = ?",
            (float(new_balance), user_id),
        )
        conn.commit()

    except Exception:
        conn.rollback()
        logger.exception("Balance deduction failed")
        await query.edit_message_text(
            "❌ Balance update করতে সমস্যা হয়েছে. আবার চেষ্টা করুন."
        )
        return
    finally:
        conn.close()

    await query.edit_message_text("⏳ Order provider-এ পাঠানো হচ্ছে...")

    result = await api_request(
        "add",
        {
            "service": str(service.get("service")),
            "link": link,
            "quantity": str(qty),
        },
    )

    provider_order_id = ""
    status = "Pending"

    if isinstance(result, dict) and result.get("order"):
        provider_order_id = str(result.get("order"))
        status = "Success"
    else:
        # Provider rejected the order: refund immediately.
        change_balance(user_id, charge)

        error = "Unknown provider error"
        if isinstance(result, dict):
            error = str(result.get("error", result))
        elif result is None:
            error = "Provider connection failed"

        await query.edit_message_text(
            "❌ Order failed.\n\n"
            f"Reason: {error[:500]}\n\n"
            f"💰 ৳{charge:.2f} আপনার balance-এ ফেরত দেওয়া হয়েছে."
        )
        context.user_data.clear()
        return

    db_order_id = save_order(
        user_id=user_id,
        service_id=service.get("service"),
        service_name=service.get("name", "Service"),
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
        f"📌 Status: {status}\n"
        f"💳 Remaining Balance: ৳{get_balance(user_id):.2f}"
    )

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "🛒 New Order\n\n"
                f"🆔 DB Order: {db_order_id}\n"
                f"👤 User: {user_id}\n"
                f"📌 Service: {service.get('name', 'Service')}\n"
                f"🔢 Qty: {qty}\n"
                f"💰 Charge: ৳{charge:.2f}\n"
                f"🔢 Provider ID: {provider_order_id}"
            ),
        )
    except Exception:
        logger.exception("Could not notify admin about order")

    context.user_data.clear()


# =========================================================
# ORDER STATUS
# =========================================================

async def refresh_order_status(update, context):
    query = update.callback_query
    await query.answer()

    db_id = safe_int(query.data.replace("refresh_order_", "", 1), 0)
    if not db_id:
        return

    row = get_order(db_id, update.effective_user.id)
    if not row:
        await query.answer("Order পাওয়া যায়নি.", show_alert=True)
        return

    provider_status = await fetch_provider_status(row["provider_order_id"])
    if not provider_status:
        await query.answer(
            "Provider status পাওয়া যায়নি. পরে আবার চেষ্টা করুন.",
            show_alert=True,
        )
        return

    raw = provider_status.get("status")
    normalized = normalize_provider_status(raw)
    update_order_status(db_id, normalized)

    await query.edit_message_text(
        "🔎 Order Status\n\n"
        f"🆔 Order ID: {row['id']}\n"
        f"🔢 Provider ID: {row['provider_order_id'] or '-'}\n"
        f"📌 Service: {row['service_name']}\n"
        f"🔢 Quantity: {row['quantity']}\n"
        f"💰 Charge: ৳{Decimal(str(row['charge'])):.2f}\n"
        f"📌 Status: {normalized}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_order_{row['id']}")],
        ]),
    )


async def my_orders(update, context):
    if await maintenance_guard(update):
        return

    ensure_user(update.effective_user)
    rows = get_user_orders(update.effective_user.id, 10)

    if not rows:
        await update.message.reply_text("📦 আপনার এখনো কোনো order নেই.")
        return

    lines = ["📦 Your Recent Orders\n"]

    for row in rows:
        lines.append(
            f"🆔 #{row['id']} | {row['status']}\n"
            f"📌 {str(row['service_name'])[:50]}\n"
            f"🔢 Qty: {row['quantity']} | ৳{Decimal(str(row['charge'])):.2f}\n"
            f"🔢 Provider: {row['provider_order_id'] or '-'}\n"
            f"🕒 {row['created_at']}\n"
        )

    keyboard = []
    for row in rows[:8]:
        keyboard.append([
            InlineKeyboardButton(
                f"🔎 Check #{row['id']}",
                callback_data=f"refresh_order_{row['id']}",
            )
        ])

    await update.message.reply_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
    )


async def order_status_command(update, context):
    if await maintenance_guard(update):
        return

    if not context.args:
        await update.message.reply_text(
            "🔎 Usage:\n/status ORDER_ID\n\nExample: /status 25"
        )
        return

    db_id = safe_int(context.args[0], 0)
    row = get_order(db_id, update.effective_user.id)

    if not row:
        await update.message.reply_text("❌ এই Order ID আপনার account-এ পাওয়া যায়নি.")
        return

    provider_status = await fetch_provider_status(row["provider_order_id"])
    if provider_status:
        normalized = normalize_provider_status(provider_status.get("status"))
        update_order_status(db_id, normalized)
        status = normalized
    else:
        status = row["status"]

    await update.message.reply_text(
        "🔎 Order Status\n\n"
        f"🆔 Order ID: {row['id']}\n"
        f"🔢 Provider ID: {row['provider_order_id'] or '-'}\n"
        f"📌 Service: {row['service_name']}\n"
        f"🔢 Quantity: {row['quantity']}\n"
        f"💰 Charge: ৳{Decimal(str(row['charge'])):.2f}\n"
        f"📌 Status: {status}",
    )


# =========================================================
# ACCOUNT / RECHARGE / HELP
# =========================================================

async def account(update, context):
    ensure_user(update.effective_user)
    user = update.effective_user
    bal = get_balance(user.id)
    username = f"@{user.username}" if user.username else "Not set"

    await update.message.reply_text(
        "👤 My Account\n\n"
        f"🆔 Account Number: {user.id}\n"
        "📌 এই Account Number-টাই আপনার Telegram account number.\n"
        f"👤 Name: {user.first_name or '-'}\n"
        f"🔹 Username: {username}\n"
        f"💰 Balance: ৳{bal:.2f}",
        reply_markup=main_keyboard(user.id),
    )


async def recharge(update, context):
    if await maintenance_guard(update):
        return

    await update.message.reply_text(
        "💳 Recharge\n\n"
        f"bKash: {BKASH_NUMBER}\n"
        f"Nagad: {NAGAD_NUMBER}\n\n"
        "Payment করার পর নিচের method নির্বাচন করুন:",
        reply_markup=recharge_method_keyboard(),
    )


async def start_recharge(update, context):
    query = update.callback_query
    await query.answer()

    method = query.data.replace("recharge_", "", 1)
    context.user_data["recharge_method"] = method
    context.user_data["recharge_step"] = "amount"

    number = BKASH_NUMBER if method == "bkash" else NAGAD_NUMBER

    await query.edit_message_text(
        f"💳 {method.title()} Recharge\n\n"
        f"Number: {number}\n\n"
        "আপনি কত টাকা payment করেছেন? Amount পাঠান:",
        reply_markup=cancel_keyboard(),
    )


async def handle_recharge_message(update, context):
    step = context.user_data.get("recharge_step")
    if not step:
        return False

    text = (update.message.text or "").strip()

    if step == "amount":
        amount = parse_amount(text)
        if amount is None:
            await update.message.reply_text("❌ সঠিক positive amount দিন. Example: 500")
            return True

        context.user_data["recharge_amount"] = str(amount)
        context.user_data["recharge_step"] = "transaction"

        await update.message.reply_text(
            "🧾 Amount received.\n\n"
            "এখন payment-এর Transaction ID পাঠান:",
            reply_markup=cancel_keyboard(),
        )
        return True

    if step == "transaction":
        if len(text) < 3:
            await update.message.reply_text("❌ সঠিক Transaction ID পাঠান.")
            return True

        method = context.user_data.get("recharge_method", "unknown")
        amount = Decimal(str(context.user_data["recharge_amount"]))

        rid = create_recharge_request(
            update.effective_user.id,
            method,
            amount,
            text,
        )

        await update.message.reply_text(
            "✅ Recharge request submitted!\n\n"
            f"🆔 Request ID: {rid}\n"
            f"💳 Method: {method.title()}\n"
            f"💰 Amount: ৳{amount:.2f}\n"
            f"🧾 Transaction ID: {text}\n\n"
            "Admin verify করার পর balance যোগ হবে.",
            reply_markup=main_keyboard(update.effective_user.id),
        )

        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "💳 New Recharge Request\n\n"
                    f"🆔 Request: {rid}\n"
                    f"👤 User: {update.effective_user.id}\n"
                    f"💳 Method: {method.title()}\n"
                    f"💰 Amount: ৳{amount:.2f}\n"
                    f"🧾 TXID: {text}"
                ),
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("✅ Approve", callback_data=f"recharge_approve_{rid}"),
                        InlineKeyboardButton("❌ Reject", callback_data=f"recharge_reject_{rid}"),
                    ]
                ]),
            )
        except Exception:
            logger.exception("Could not notify admin about recharge")

        context.user_data.clear()
        return True

    return False


async def helpline(update, context):
    await update.message.reply_text(
        "📞 Helpline\n\n"
        f"WhatsApp: {WHATSAPP_URL}\n\n"
        "Payment/order সমস্যা হলে আপনার Telegram ID, order ID বা recharge request ID পাঠান."
    )


async def terms(update, context):
    await update.message.reply_text(
        "📜 Terms & Conditions\n\n"
        "1. Order করার আগে service details ও quantity limit দেখে নিন.\n"
        "2. ভুল link/quantity দিলে refund নাও হতে পারে.\n"
        "3. Provider-এর delivery time service অনুযায়ী পরিবর্তিত হতে পারে.\n"
        "4. Provider order গ্রহণ করলে bot-এ Success দেখাবে; delivery completion provider status-এর উপর নির্ভর করে.\n"
        "5. Recharge approve হওয়ার আগে payment verify করা হয়."
    )


# =========================================================
# ADMIN PANEL
# =========================================================

async def admin_panel(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin only.")
        return

    context.user_data.clear()
    await update.message.reply_text(
        "👑 Admin Panel\n\nএকটি option নির্বাচন করুন:",
        reply_markup=admin_keyboard(),
    )


async def start_admin_action(update, context, action):
    query = update.callback_query
    await query.answer()

    if not is_admin(update.effective_user.id):
        await query.edit_message_text("❌ Admin only.")
        return

    context.user_data.clear()
    context.user_data["admin_action"] = action

    if action in ("add", "deduct", "info"):
        context.user_data["admin_step"] = "user_id"

        title = {
            "add": "➕ Add Balance",
            "deduct": "➖ Deduct Balance",
            "info": "👤 User Info",
        }[action]

        await query.edit_message_text(
            f"{title}\n\nপ্রথমে User ID পাঠান:",
            reply_markup=cancel_keyboard(),
        )

    elif action == "broadcast":
        context.user_data["admin_step"] = "broadcast_text"
        await query.edit_message_text(
            "📢 Broadcast\n\nসব user-কে যে message পাঠাতে চান সেটা পাঠান:",
            reply_markup=cancel_keyboard(),
        )

    elif action == "welcome_bonus":
        current = get_welcome_bonus()
        context.user_data["admin_step"] = "welcome_bonus_amount"
        await query.edit_message_text(
            "🎁 Welcome Bonus Settings\n\n"
            f"বর্তমান bonus: ৳{current:.2f}\n\n"
            "নতুন bonus amount পাঠান।\n"
            "0 দিলে Welcome Bonus পুরোপুরি বন্ধ থাকবে।\n\n"
            "Example: 10 অথবা 0",
            reply_markup=cancel_keyboard(),
        )


async def admin_callback(update, context):
    data = update.callback_query.data

    if data == "admin_bonus_confirm":
        await admin_welcome_bonus_confirm(update, context)
    elif data == "admin_add":
        await start_admin_action(update, context, "add")
    elif data == "admin_deduct":
        await start_admin_action(update, context, "deduct")
    elif data == "admin_user_info":
        await start_admin_action(update, context, "info")
    elif data == "admin_broadcast":
        await start_admin_action(update, context, "broadcast")
    elif data == "admin_stats":
        await admin_stats(update, context)
    elif data == "admin_recharges":
        await admin_recharge_list(update, context)
    elif data == "admin_maintenance":
        await admin_maintenance(update, context)
    elif data == "admin_welcome_bonus":
        await start_admin_action(update, context, "welcome_bonus")
    elif data == "admin_close":
        await update.callback_query.answer()
        context.user_data.clear()
        await update.callback_query.edit_message_text("Admin panel closed.")


async def admin_welcome_bonus_confirm(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(update.effective_user.id):
        await query.edit_message_text("❌ Admin only.")
        return

    raw = context.user_data.get("welcome_bonus_amount")
    if raw is None:
        await query.edit_message_text("❌ Setting expired. আবার Admin Panel খুলুন.")
        context.user_data.clear()
        return

    try:
        amount = Decimal(str(raw))
        if amount < 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError, TypeError):
        await query.edit_message_text("❌ Invalid bonus amount.")
        context.user_data.clear()
        return

    set_setting("welcome_bonus", f"{amount:.2f}")
    context.user_data.clear()

    await query.edit_message_text(
        "✅ Welcome Bonus Updated\n\n"
        f"🎁 New Welcome Bonus: ৳{amount:.2f}\n\n"
        "0 হলে নতুন user-দের কোনো Welcome Bonus দেওয়া হবে না।\n"
        "এই setting শুধু নতুন user-এর জন্য প্রযোজ্য।"
    )


async def admin_stats(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(update.effective_user.id):
        return

    users, orders, successful, sales, pending_recharges = get_stats()
    await query.edit_message_text(
        "📊 Bot Statistics\n\n"
        f"👥 Total Users: {users}\n"
        f"📦 Total Orders: {orders}\n"
        f"✅ Successful/Completed: {successful}\n"
        f"💰 Successful Sales: ৳{sales:.2f}\n"
        f"💳 Pending Recharge: {pending_recharges}\n"
        f"🛠️ Maintenance: {'ON' if maintenance_enabled() else 'OFF'}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Admin Panel", callback_data="admin_back")]
        ]),
    )


async def admin_maintenance(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(update.effective_user.id):
        return

    current = maintenance_enabled()
    new_value = "0" if current else "1"
    set_setting("maintenance", new_value)

    await query.edit_message_text(
        "🛠️ Maintenance Mode\n\n"
        f"Status: {'ON' if new_value == '1' else 'OFF'}\n\n"
        "Admin account সবসময় access রাখতে পারবে.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Admin Panel", callback_data="admin_back")]
        ]),
    )


async def admin_recharge_list(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(update.effective_user.id):
        return

    conn = get_db()
    rows = conn.execute(
        """
        SELECT * FROM recharge_requests
        WHERE status = 'Pending'
        ORDER BY id DESC
        LIMIT 10
        """
    ).fetchall()
    conn.close()

    if not rows:
        await query.edit_message_text(
            "💳 Pending Recharge Requests নেই.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Admin Panel", callback_data="admin_back")]
            ]),
        )
        return

    text = ["💳 Pending Recharge Requests\n"]
    keyboard = []

    for row in rows:
        text.append(
            f"🆔 #{row['id']} | User: {row['user_id']}\n"
            f"💰 ৳{Decimal(str(row['amount'])):.2f} | {row['method']}\n"
            f"🧾 TXID: {row['transaction_id']}\n"
        )
        keyboard.append([
            InlineKeyboardButton(
                f"✅ Approve #{row['id']}",
                callback_data=f"recharge_approve_{row['id']}",
            ),
            InlineKeyboardButton(
                f"❌ Reject #{row['id']}",
                callback_data=f"recharge_reject_{row['id']}",
            ),
        ])

    keyboard.append([
        InlineKeyboardButton("⬅️ Admin Panel", callback_data="admin_back")
    ])

    await query.edit_message_text(
        "\n".join(text),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def recharge_review_callback(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(update.effective_user.id):
        await query.answer("Admin only.", show_alert=True)
        return

    parts = query.data.split("_")
    if len(parts) != 3:
        return

    action = parts[1]
    rid = safe_int(parts[2], 0)
    if not rid:
        return

    approve = action == "approve"
    new_balance, result = review_recharge(rid, approve)

    row = get_recharge(rid)

    if result == "already_reviewed":
        await query.edit_message_text("⚠️ এই recharge request ইতিমধ্যে review করা হয়েছে.")
        return

    if result == "error" or row is None:
        await query.edit_message_text("❌ Recharge review করতে সমস্যা হয়েছে.")
        return

    if result == "approved":
        await query.edit_message_text(
            "✅ Recharge Approved\n\n"
            f"🆔 Request: {rid}\n"
            f"👤 User: {row['user_id']}\n"
            f"💰 Added: ৳{Decimal(str(row['amount'])):.2f}\n"
            f"💳 New Balance: ৳{new_balance:.2f}"
        )
        try:
            await context.bot.send_message(
                chat_id=row["user_id"],
                text=(
                    "✅ Recharge Approved!\n\n"
                    f"🆔 Request ID: {rid}\n"
                    f"💰 Added: ৳{Decimal(str(row['amount'])):.2f}\n"
                    f"💳 Current Balance: ৳{new_balance:.2f}"
                ),
            )
        except Exception:
            logger.exception("Could not notify user about approved recharge")
    else:
        await query.edit_message_text(
            "❌ Recharge Rejected\n\n"
            f"🆔 Request: {rid}\n"
            f"👤 User: {row['user_id']}\n"
            f"💰 Amount: ৳{Decimal(str(row['amount'])):.2f}"
        )
        try:
            await context.bot.send_message(
                chat_id=row["user_id"],
                text=(
                    "❌ Recharge Request Rejected\n\n"
                    f"🆔 Request ID: {rid}\n"
                    "Payment details verify করা যায়নি. প্রয়োজন হলে support-এ যোগাযোগ করুন."
                ),
            )
        except Exception:
            logger.exception("Could not notify user about rejected recharge")


async def handle_admin_message(update, context):
    if not is_admin(update.effective_user.id):
        return False

    action = context.user_data.get("admin_action")
    step = context.user_data.get("admin_step")
    if not action or not step:
        return False

    text = (update.message.text or "").strip()

    if step == "welcome_bonus_amount":
        amount = parse_amount(text)
        if amount is None or amount < 0:
            await update.message.reply_text(
                "❌ সঠিক amount দিন। 0 বা তার বেশি হতে হবে.\nExample: 10 অথবা 0"
            )
            return True

        context.user_data["welcome_bonus_amount"] = str(amount)
        context.user_data["admin_step"] = "welcome_bonus_confirm"

        await update.message.reply_text(
            "🎁 Welcome Bonus Confirmation\n\n"
            f"বর্তমান: ৳{get_welcome_bonus():.2f}\n"
            f"নতুন: ৳{amount:.2f}\n\n"
            "0 দিলে নতুন user-দের কোনো Welcome Bonus দেওয়া হবে না।\n\n"
            "Confirm করবেন?",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Confirm", callback_data="admin_bonus_confirm"),
                    InlineKeyboardButton("❌ Cancel", callback_data="cancel_action"),
                ]
            ]),
        )
        return True

    if step == "broadcast_text":
        user_ids = get_all_user_ids()
        sent = 0
        failed = 0

        await update.message.reply_text(
            f"📢 Broadcast শুরু হয়েছে.\nTotal users: {len(user_ids)}"
        )

        for uid in user_ids:
            try:
                await context.bot.send_message(chat_id=uid, text=text)
                sent += 1
            except Exception:
                failed += 1

        await update.message.reply_text(
            "📢 Broadcast Finished\n\n"
            f"✅ Sent: {sent}\n"
            f"❌ Failed: {failed}",
            reply_markup=main_keyboard(ADMIN_ID),
        )
        context.user_data.clear()
        return True

    if step == "user_id":
        try:
            target_id = int(text)
            if target_id <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ সঠিক numeric Telegram User ID দিন.")
            return True

        context.user_data["target_user_id"] = target_id

        if action == "info":
            row = get_user_info(target_id)
            if not row:
                await update.message.reply_text(
                    f"❌ User {target_id} database-এ পাওয়া যায়নি."
                )
            else:
                username = f"@{row['username']}" if row["username"] else "-"
                await update.message.reply_text(
                    "👤 User Info\n\n"
                    f"🆔 ID: {row['user_id']}\n"
                    f"👤 Name: {row['first_name'] or '-'}\n"
                    f"🔗 Username: {username}\n"
                    f"💰 Balance: ৳{Decimal(str(row['balance'])):.2f}\n"
                    f"🕒 Created: {row['created_at']}"
                )
            context.user_data.clear()
            return True

        context.user_data["admin_step"] = "amount"
        label = "➕ Add" if action == "add" else "➖ Deduct"

        await update.message.reply_text(
            f"{label} Balance\n\n"
            f"User ID: {target_id}\n\n"
            "এখন amount (BDT) পাঠান:",
            reply_markup=cancel_keyboard(),
        )
        return True

    if step == "amount":
        amount = parse_amount(text)
        if amount is None:
            await update.message.reply_text(
                "❌ সঠিক positive amount দিন.\nExample: 100"
            )
            return True

        target_id = context.user_data.get("target_user_id")
        context.user_data["amount"] = str(amount)
        context.user_data["admin_step"] = "confirm"

        current = get_balance(target_id)
        after = current + amount if action == "add" else current - amount
        title = "➕ Add Balance Confirmation" if action == "add" else "➖ Deduct Balance Confirmation"

        warning = ""
        if action == "deduct" and amount > current:
            warning = "\n\n⚠️ Insufficient balance — এই deduction করা যাবে না."

        await update.message.reply_text(
            f"{title}\n\n"
            f"👤 User ID: {target_id}\n"
            f"💰 Current: ৳{current:.2f}\n"
            f"💵 Amount: ৳{amount:.2f}\n"
            f"📊 After: ৳{after:.2f}{warning}\n\n"
            "Confirm করবেন?",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Confirm", callback_data="admin_confirm"),
                    InlineKeyboardButton("❌ Cancel", callback_data="cancel_action"),
                ]
            ]),
        )
        return True

    return False


async def admin_confirm(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(update.effective_user.id):
        await query.edit_message_text("❌ Admin only.")
        return

    action = context.user_data.get("admin_action")
    target_id = context.user_data.get("target_user_id")
    amount_raw = context.user_data.get("amount")

    if action not in ("add", "deduct") or not target_id or not amount_raw:
        await query.edit_message_text(
            "❌ Admin action expired. আবার Admin Panel খুলুন."
        )
        context.user_data.clear()
        return

    amount = Decimal(str(amount_raw))

    if action == "add":
        new_balance = change_balance(target_id, amount)
        await query.edit_message_text(
            "✅ Balance Added Successfully\n\n"
            f"👤 User ID: {target_id}\n"
            f"➕ Added: ৳{amount:.2f}\n"
            f"💰 New Balance: ৳{new_balance:.2f}"
        )
    else:
        new_balance = deduct_balance(target_id, amount)
        if new_balance is None:
            await query.edit_message_text(
                "❌ Deduct করা যায়নি.\nUser-এর balance পর্যাপ্ত নয়."
            )
        else:
            await query.edit_message_text(
                "✅ Balance Deducted Successfully\n\n"
                f"👤 User ID: {target_id}\n"
                f"➖ Deducted: ৳{amount:.2f}\n"
                f"💰 New Balance: ৳{new_balance:.2f}"
            )

    context.user_data.clear()


async def admin_back(update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "👑 Admin Panel\n\nএকটি option নির্বাচন করুন:",
        reply_markup=admin_keyboard(),
    )


async def cancel_action(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text("❌ Action cancelled.")


# =========================================================
# TEXT ROUTER
# =========================================================

async def text_router(update, context):
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    user_id = update.effective_user.id
    ensure_user(update.effective_user)

    if is_admin(user_id) and context.user_data.get("admin_action"):
        if await handle_admin_message(update, context):
            return

    if context.user_data.get("recharge_step"):
        if await handle_recharge_message(update, context):
            return

    if context.user_data.get("order_step"):
        if await handle_order_message(update, context):
            return

    if text == "🛍 Services":
        await show_services(update, context)
    elif text == "👤 Account":
        await account(update, context)
    elif text == "📦 My Orders":
        await my_orders(update, context)
    elif text == "💳 Recharge":
        await recharge(update, context)
    elif text == "📞 Helpline":
        await helpline(update, context)
    elif text == "📜 Terms":
        await terms(update, context)
    elif text == "👑 Admin Panel" and is_admin(user_id):
        await admin_panel(update, context)


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled exception", exc_info=context.error)


# =========================================================
# AUTOMATIC PROVIDER STATUS MONITOR
# =========================================================

async def status_monitor(application):
    # Checks recent provider orders periodically. If the provider supports
    # the standard "status" action, local status is updated automatically.
    while True:
        try:
            rows = get_trackable_orders(100)

            for row in rows:
                try:
                    provider_status = await fetch_provider_status(
                        row["provider_order_id"]
                    )
                    if not provider_status:
                        continue

                    new_status = normalize_provider_status(
                        provider_status.get("status")
                    )

                    if new_status and new_status != row["status"]:
                        update_order_status(row["id"], new_status)

                        # Notify the user only when a meaningful status change occurs.
                        try:
                            await application.bot.send_message(
                                chat_id=row["user_id"],
                                text=(
                                    "🔔 Order Status Updated\n\n"
                                    f"🆔 Order ID: {row['id']}\n"
                                    f"📌 New Status: {new_status}"
                                ),
                            )
                        except Exception:
                            logger.exception(
                                "Could not notify user about status update"
                            )

                    await asyncio.sleep(0.15)

                except Exception:
                    logger.exception(
                        "Status check failed for order %s",
                        row["id"],
                    )

        except Exception:
            logger.exception("Automatic status monitor failed")

        # 10-minute interval to avoid unnecessary provider API traffic.
        await asyncio.sleep(600)


async def post_init(application):
    application.create_task(status_monitor(application))


# =========================================================
# MAIN
# =========================================================

def main():
    init_db()

    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing. Add TELEGRAM_BOT_TOKEN in Railway Variables."
        )

    print("BOT STARTING...", flush=True)
    print(f"ADMIN_ID: {ADMIN_ID}", flush=True)
    print("Telegram polling is starting...", flush=True)

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("my_orders", my_orders))
    application.add_handler(CommandHandler("status", order_status_command))

    # Order/status callbacks
    application.add_handler(
        CallbackQueryHandler(confirm_order, pattern=r"^confirm_order$")
    )
    application.add_handler(
        CallbackQueryHandler(refresh_order_status, pattern=r"^refresh_order_\d+$")
    )

    # Admin callbacks
    application.add_handler(
        CallbackQueryHandler(admin_confirm, pattern=r"^admin_confirm$")
    )
    application.add_handler(
        CallbackQueryHandler(
            admin_callback,
            pattern=r"^admin_(add|deduct|user_info|broadcast|stats|recharges|maintenance|close|welcome_bonus|bonus_confirm)$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(admin_back, pattern=r"^admin_back$")
    )

    # Recharge callbacks
    application.add_handler(
        CallbackQueryHandler(
            start_recharge,
            pattern=r"^recharge_(bkash|nagad)$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            recharge_review_callback,
            pattern=r"^recharge_(approve|reject)_\d+$",
        )
    )

    # Service callbacks
    application.add_handler(
        CallbackQueryHandler(
            service_category_selected,
            pattern=r"^category_(facebook|tiktok|youtube|traffic)$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(service_categories_back, pattern=r"^service_categories$")
    )
    application.add_handler(
        CallbackQueryHandler(service_selected, pattern=r"^service_.+")
    )
    application.add_handler(
        CallbackQueryHandler(
            lambda update, context: update.callback_query.answer(),
            pattern=r"^close_services$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(cancel_action, pattern=r"^cancel_action$")
    )

    # Text
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_router)
    )

    application.add_error_handler(error_handler)

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
