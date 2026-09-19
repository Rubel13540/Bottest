import os
import re
import sqlite3
import logging
from decimal import Decimal, InvalidOperation

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

# আপনার প্রদান করা API Key এখানে বসিয়ে দেওয়া হয়েছে
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

# =========================================================
# BASIC VALIDATION
# =========================================================

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError(
        "TELEGRAM_BOT_TOKEN environment variable is missing."
    )

if not SMM_API_KEY:
    raise RuntimeError(
        "SMM_API_KEY environment variable is missing."
    )


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
        (
            user.id,
            user.username or "",
            user.first_name or "",
        ),
    )

    conn.commit()
    conn.close()


def get_balance(user_id):
    conn = db_connect()

    row = conn.execute(
        "SELECT balance FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()

    conn.close()

    if not row:
        return Decimal("0")

    return Decimal(str(row["balance"]))


def add_balance(user_id, amount):
    conn = db_connect()

    conn.execute(
        """
        INSERT INTO users (user_id, balance)
        VALUES (?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET balance = balance + excluded.balance
        """,
        (user_id, float(amount)),
    )

    conn.commit()
    conn.close()


def deduct_balance(user_id, amount):
    conn = db_connect()

    conn.execute(
        """
        UPDATE users
        SET balance = balance - ?
        WHERE user_id = ?
        """,
        (float(amount), user_id),
    )

    conn.commit()
    conn.close()


def save_order(
    user_id,
    provider_order_id,
    service_id,
    service_name,
    link,
    quantity,
    charge,
):
    conn = db_connect()

    cursor = conn.execute(
        """
        INSERT INTO orders
        (
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
        """,
        (
            user_id,
            str(provider_order_id),
            str(service_id),
            service_name,
            link,
            quantity,
            float(charge),
            "Pending",
        ),
    )

    order_id = cursor.lastrowid

    conn.commit()
    conn.close()

    return order_id


def get_user_orders(user_id, limit=10):
    conn = db_connect()

    rows = conn.execute(
        """
        SELECT *
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
# SMM API
# =========================================================

async def smm_request(payload):
    """
    SocialPanel.Pro API request.

    Documentation currently describes:
    POST + application/json + JSON response.
    """

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        timeout = aiohttp.ClientTimeout(total=20)

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.post(
                SMM_API_URL,
                json=payload,
                headers=headers,
            ) as response:

                text = await response.text()

                logger.info(
                    "SMM API HTTP %s",
                    response.status
                )

                if response.status != 200:
                    logger.error(
                        "SMM API HTTP ERROR: %s",
                        text
                    )
                    return None

                try:
                    result = await response.json(
                        content_type=None
                    )
                except Exception:
                    logger.error(
                        "SMM API returned invalid JSON: %s",
                        text
                    )
                    return None

                # Do NOT log API key.
                logger.info(
                    "SMM API response received."
                )

                return result

    except aiohttp.ClientError as e:
        logger.exception(
            "SMM API connection error: %s",
            e
        )
        return None

    except Exception as e:
        logger.exception(
            "SMM API request error: %s",
            e
        )
        return None


# =========================================================
# SERVICES
# =========================================================

async def fetch_smm_services():
    payload = {
        "key": SMM_API_KEY,
        "action": "services",
    }

    result = await smm_request(payload)

    if isinstance(result, list):
        logger.info(
            "SMM API: %s services received",
            len(result)
        )
        return result

    logger.error(
        "SMM API service response: %s",
        result
    )

    return []


def detect_platform(service_name):
    """
    Detect platform from service name.
    """

    name = service_name.lower()

    if any(x in name for x in [
        "instagram",
        "insta",
    ]):
        return "Instagram"

    if any(x in name for x in [
        "facebook",
        "fb ",
        " fb",
    ]):
        return "Facebook"

    if any(x in name for x in [
        "youtube",
        "yt ",
        " yt",
    ]):
        return "YouTube"

    if "tiktok" in name or "tik tok" in name:
        return "TikTok"

    if any(x in name for x in [
        "telegram",
        "tg ",
        " tg",
    ]):
        return "Telegram"

    if any(x in name for x in [
        "twitter",
        "twitter/x",
        " x ",
        "retweet",
    ]):
        return "Twitter/X"

    if "linkedin" in name:
        return "LinkedIn"

    if "spotify" in name:
        return "Spotify"

    if "snapchat" in name:
        return "Snapchat"

    if any(x in name for x in [
        "website traffic",
        "web traffic",
        "traffic",
    ]):
        return "Website Traffic"

    return "Other"


def format_rate(rate):
    try:
        return Decimal(str(rate))
    except Exception:
        return Decimal("0")


def calculate_customer_price(rate):
    """
    Provider rate is USD per 1000.
    Convert USD -> BDT and add markup.
    """

    usd_rate = format_rate(rate)

    bdt_rate = (
        usd_rate
        * USD_TO_BDT
        * PROFIT_MULTIPLIER
    )

    return bdt_rate.quantize(Decimal("0.01"))


# =========================================================
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    register_user(user)

    keyboard = [
        [
            InlineKeyboardButton(
                "🛍 Services",
                callback_data="services"
            ),
            InlineKeyboardButton(
                "💰 Balance",
                callback_data="balance"
            ),
        ],
        [
            InlineKeyboardButton(
                "📦 My Orders",
                callback_data="my_orders"
            ),
            InlineKeyboardButton(
                "🔄 Refresh Services",
                callback_data="refresh_services"
            ),
        ],
    ]

    if user.id == ADMIN_ID:
        keyboard.append([
            InlineKeyboardButton(
                "👑 Admin",
                callback_data="admin"
            )
        ])

    await update.message.reply_text(
        "🤖 *SMM Service Bot*\n\n"
        "Welcome!\n\n"
        "🛍 Services — available services\n"
        "💰 Balance — your wallet balance\n"
        "📦 My Orders — your recent orders\n",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# =========================================================
# BALANCE
# =========================================================

async def show_balance(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    register_user(query.from_user)

    balance = get_balance(user_id)

    keyboard = [
        [
            InlineKeyboardButton(
                "⬅️ Back",
                callback_data="home"
            )
        ]
    ]

    await query.edit_message_text(
        f"💰 *Your Balance*\n\n"
        f"Balance: `{balance:.2f} BDT`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# =========================================================
# SERVICES
# =========================================================

async def show_services(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "⏳ Loading services..."
    )

    services = await fetch_smm_services()

    if not services:
        await query.edit_message_text(
            "❌ কোনো service পাওয়া যায়নি।\n\n"
            "সম্ভাব্য কারণ:\n"
            "• API key ভুল\n"
            "• Provider API সমস্যা\n"
            "• API response পরিবর্তন হয়েছে\n\n"
            "Deployment log-এ `SMM API service response:` দেখুন।",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔄 Try Again",
                        callback_data="refresh_services"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Back",
                        callback_data="home"
                    )
                ],
            ]),
        )
        return

    context.user_data["all_services"] = services

    counts = {}

    for service in services:
        name = str(service.get("name", ""))
        platform = detect_platform(name)

        counts[platform] = counts.get(
            platform,
            0
        ) + 1

    platforms = [
        "Facebook",
        "Instagram",
        "YouTube",
        "TikTok",
        "Telegram",
        "Twitter/X",
        "LinkedIn",
        "Spotify",
        "Snapchat",
        "Website Traffic",
        "Other",
    ]

    keyboard = []

    for platform in platforms:
        count = counts.get(platform, 0)

        if count > 0:
            keyboard.append([
                InlineKeyboardButton(
                    f"{platform} ({count})",
                    callback_data=f"platform_{platform}"
                )
            ])

    keyboard.append([
        InlineKeyboardButton(
            "⬅️ Back",
            callback_data="home"
        )
    ])

    await query.edit_message_text(
        f"🛍 *Available Services*\n\n"
        f"Total services: `{len(services)}`\n\n"
        f"Select a platform:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# =========================================================
# PLATFORM SERVICES
# =========================================================

async def platform_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    platform = query.data.replace(
        "platform_",
        "",
        1
    )

    services = context.user_data.get(
        "all_services",
        []
    )

    if not services:
        services = await fetch_smm_services()

    matched = []

    for service in services:

        name = str(
            service.get("name", "")
        )

        if detect_platform(name) == platform:
            matched.append(service)

    if not matched:
        await query.edit_message_text(
            "❌ এই platform-এর কোনো service পাওয়া যায়নি।",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ Platforms",
                        callback_data="services"
                    )
                ]
            ]),
        )
        return

    # Store complete service list for callback lookup.
    service_map = {}

    keyboard = []

    for index, service in enumerate(matched):

        service_id = str(
            service.get(
                "service",
                service.get("id", "")
            )
        )

        if not service_id:
            continue

        service_map[service_id] = service

        name = str(
            service.get(
                "name",
                "Unnamed Service"
            )
        )

        rate = calculate_customer_price(
            service.get("rate", 0)
        )

        min_qty = service.get(
            "min",
            "?"
        )

        max_qty = service.get(
            "max",
            "?"
        )

        text = (
            f"{name[:45]}\n"
            f"💵 {rate} BDT / 1K"
        )

        keyboard.append([
            InlineKeyboardButton(
                text,
                callback_data=f"service_{service_id}"
            )
        ])

    context.user_data["service_map"] = service_map

    keyboard.append([
        InlineKeyboardButton(
            "⬅️ Platforms",
            callback_data="services"
        )
    ])

    await query.edit_message_text(
        f"📱 *{platform} Services*\n\n"
        f"Total: `{len(matched)}`\n\n"
        f"Select a service:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# =========================================================
# SERVICE SELECT
# =========================================================

async def service_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    service_id = query.data.replace(
        "service_",
        "",
        1
    )

    service_map = context.user_data.get(
        "service_map",
        {}
    )

    service = service_map.get(service_id)

    if not service:
        await query.edit_message_text(
            "❌ Service information পাওয়া যায়নি। "
            "Services আবার load করুন।",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🛍 Services",
                        callback_data="services"
                    )
                ]
            ]),
        )
        return

    name = str(
        service.get(
            "name",
            "Unknown Service"
        )
    )

    rate = calculate_customer_price(
        service.get("rate", 0)
    )

    min_qty = service.get(
        "min",
        1
    )

    max_qty = service.get(
        "max",
        1000000
    )

    context.user_data["selected_service"] = service

    context.user_data["order_step"] = "link"

    await query.edit_message_text(
        f"🛍 *{name}*\n\n"
        f"💵 Price: `{rate} BDT / 1000`\n"
        f"🔢 Minimum: `{min_qty}`\n"
        f"🔢 Maximum: `{max_qty}`\n\n"
        f"🔗 এখন আপনার পোস্ট/profile-এর link পাঠান:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data="services"
                )
            ]
        ]),
    )


# =========================================================
# TEXT INPUT
# =========================================================

async def text_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user

    register_user(user)

    step = context.user_data.get(
        "order_step"
    )

    if step == "link":
        link = update.message.text.strip()

        if len(link) < 5:
            await update.message.reply_text(
                "❌ Valid link দিন।"
            )
            return

        context.user_data["order_link"] = link
        context.user_data["order_step"] = "quantity"

        service = context.user_data.get(
            "selected_service"
        )

        min_qty = service.get(
            "min",
            1
        )

        max_qty = service.get(
            "max",
            1000000
        )

        await update.message.reply_text(
            f"🔢 Quantity দিন:\n\n"
            f"Minimum: {min_qty}\n"
            f"Maximum: {max_qty}\n\n"
            f"Example: `1000`",
            parse_mode="Markdown",
        )

        return

    if step == "quantity":

        raw_quantity = update.message.text.strip()

        try:
            quantity = int(raw_quantity)
        except ValueError:
            await update.message.reply_text(
                "❌ Quantity অবশ্যই number হতে হবে।"
            )
            return

        service = context.user_data.get(
            "selected_service"
        )

        if not service:
            context.user_data.clear()

            await update.message.reply_text(
                "❌ Service session expired। "
                "/start দিয়ে আবার শুরু করুন।"
            )
            return

        try:
            min_qty = int(
                Decimal(
                    str(service.get("min", 1))
                )
            )

            max_qty = int(
                Decimal(
                    str(service.get("max", 1000000))
                )
            )
        except Exception:
            min_qty = 1
            max_qty = 1000000

        if quantity < min_qty:
            await update.message.reply_text(
      
