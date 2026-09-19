import sqlite3
import logging
import os
import re
import aiohttp

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

# =========================================================
# CONFIGURATION
# =========================================================

SMM_API_URL = "https://socialpanel.pro/api/v2"

# এখানে আপনার নতুন SMM API Key বসান
SMM_API_KEY = "bd48e602a5dfe6d1dbcb31102130458c"

# এখানে আপনার Telegram Bot Token বসান
TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    "YOUR_BOT_TOKEN_HERE"
)

ADMIN_ID = 5293614793

USD_TO_BDT = 120.0

DB_FILE = "bot_database.db"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# STATES
# =========================================================

ADD_BAL_USER, ADD_BAL_AMOUNT = range(2)
ORDER_LINK, ORDER_QTY = range(2, 4)
TRACK_ORDER_ID = 4
BROADCAST_MSG = 5


# =========================================================
# TRANSLATION
# =========================================================

TRANSLATION_MAP = {
    "YouTube": "ইউটিউব",
    "Subscribers": "সাবস্ক্রাইবার",
    "Subscriber": "সাবস্ক্রাইবার",
    "Views": "ভিউ",
    "View": "ভিউ",
    "Likes": "লাইক",
    "Like": "লাইক",
    "Facebook": "ফেসবুক",
    "Followers": "ফলোয়ার",
    "Follower": "ফলোয়ার",
    "Page": "পেজ",
    "Profile": "প্রোফাইল",
    "Reactions": "রিঅ্যাকশন",
    "Watch Time": "ওয়াচ টাইম",
    "TikTok": "টিকটক",
    "Instagram": "ইনস্টাগ্রাম",
    "Comments": "কমেন্ট",
    "Comment": "কমেন্ট",
    "Share": "শেয়ার",
    "Shares": "শেয়ার",
    "Real": "রিয়েল",
    "Non Drop": "নন-ড্রপ",
    "Refill": "রিফিল",
    "Monetized": "মনিটাইজড",
    "Members": "মেম্বার",
    "Member": "মেম্বার",
    "Group": "গ্রুপ",
    "Telegram": "টেলিগ্রাম",
    "Traffic": "ট্রাফিক",
    "Website": "ওয়েবসাইট",
}


ALLOWED_PLATFORMS = [
    "facebook",
    "youtube",
    "tiktok",
    "instagram",
    "telegram",
    "traffic",
    "website",
]


# =========================================================
# NAVIGATION
# =========================================================

NAV_BUTTONS = [
    "🚀 সার্ভিস ক্যাটালগ",
    "📍 অর্ডার ট্র্যাক করুন",
    "👤 প্রোফাইল ও ব্যালেন্স",
    "👤 মাই প্রোফাইল",
    "💳 ব্যালেন্স রিচার্জ",
    "💳 টাকা জমা দিন (রিচার্জ)",
    "💬 ২৪/৭ হেল্পলাইন",
    "💬 কাস্টমার সাপোর্ট",
    "📜 শর্তাবলী ও নিয়ম",
    "📜 শর্তাবলী ও নিয়ম",
]


class NavButtonFilter(filters.MessageFilter):
    def filter(self, message):
        return bool(
            message
            and message.text
            and message.text in NAV_BUTTONS
        )


class TrackOrderFilter(filters.MessageFilter):
    def filter(self, message):
        return bool(
            message
            and message.text == "📍 অর্ডার ট্র্যাক করুন"
        )


nav_buttons_filter = NavButtonFilter()
track_order_filter = TrackOrderFilter()


# =========================================================
# TRANSLATE
# =========================================================

def translate_to_bangla(text):
    if not text:
        return text

    translated = str(text)

    for eng, bng in TRANSLATION_MAP.items():
        translated = re.sub(
            rf"\b{re.escape(eng)}\b",
            bng,
            translated,
            flags=re.IGNORECASE,
        )

    return translated


# =========================================================
# DATABASE
# =========================================================

def init_db():
    with sqlite3.connect(DB_FILE, timeout=15) as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                balance REAL DEFAULT 0.0,
                is_banned INTEGER DEFAULT 0
            )
            """
        )

        conn.commit()


init_db()


def get_user(user_id):
    with sqlite3.connect(DB_FILE, timeout=15) as conn:
        cursor = conn.cursor()

        cursor.execute(
            "SELECT balance, is_banned FROM users WHERE user_id = ?",
            (user_id,),
        )

        row = cursor.fetchone()

        if row:
            return row

        cursor.execute(
            """
            INSERT INTO users
            (user_id, balance, is_banned)
            VALUES (?, 0.0, 0)
            """,
            (user_id,),
        )

        conn.commit()

        return (0.0, 0)


def update_user_balance(user_id, amount):
    with sqlite3.connect(DB_FILE, timeout=15) as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT OR IGNORE INTO users
            (user_id, balance, is_banned)
            VALUES (?, 0.0, 0)
            """,
            (user_id,),
        )

        cursor.execute(
            """
            UPDATE users
            SET balance = balance + ?
            WHERE user_id = ?
            """,
            (amount, user_id),
        )

        conn.commit()


def deduct_user_balance(user_id, amount):
    """
    Atomic balance deduction.
    Returns True if successful.
    """

    with sqlite3.connect(DB_FILE, timeout=15) as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            UPDATE users
            SET balance = balance - ?
            WHERE user_id = ?
            AND balance >= ?
            """,
            (amount, user_id, amount),
        )

        conn.commit()

        return cursor.rowcount > 0


def get_all_user_ids():
    with sqlite3.connect(DB_FILE, timeout=15) as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT user_id FROM users")

        rows = cursor.fetchall()

        return [row[0] for row in rows]


# =========================================================
# HTTP / SMM API
# =========================================================

async def smm_request(payload):
    try:
        timeout = aiohttp.ClientTimeout(total=15)

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
                        text,
                    )
                    return None

                try:
                    return await response.json(
                        content_type=None
                    )
                except Exception:
                    logger.error(
                        "Invalid JSON from SMM API: %s",
                        text,
                    )
                    return None

    except Exception as e:
        logger.exception("SMM API request error: %s", e)
        return None


async def fetch_smm_services():
    payload = {
        "key": SMM_API_KEY,
        "action": "services",
    }

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
        "quantity": quantity,
    }

    return await smm_request(payload)


async def get_smm_order_status(order_id):
    payload = {
        "key": SMM_API_KEY,
        "action": "status",
        "order": order_id,
    }

    return await smm_request(payload)


# =========================================================
# KEYBOARD
# =========================================================

def main_reply_keyboard():
    keyboard = [
        [
            KeyboardButton("🚀 সার্ভিস ক্যাটালগ"),
            KeyboardButton("📍 অর্ডার ট্র্যাক করুন"),
        ],
        [
            KeyboardButton("👤 প্রোফাইল ও ব্যালেন্স"),
            KeyboardButton("💳 ব্যালেন্স রিচার্জ"),
        ],
        [
            KeyboardButton("💬 ২৪/৭ হেল্পলাইন"),
            KeyboardButton("📜 শর্তাবলী ও নিয়ম"),
        ],
    ]

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
    )


def cancel_inline_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "❌ বাতিল করুন",
                    callback_data="cancel_flow",
                )
            ]
        ]
    )


# =========================================================
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id
    name = update.effective_user.first_name or "User"

    balance, is_banned = get_user(user_id)

    if is_banned:
        if update.message:
            await update.message.reply_text(
                "❌ আপনার একাউন্টটি স্থগিত করা হয়েছে।"
            )
        return ConversationHandler.END

    text = (
        f"👋 *আসসালামু আলাইকুম, {name}!*\n\n"
        f"স্যোশাল মিডিয়া সার্ভিস বোটে আপনাকে স্বাগতম! 🌟\n\n"
        f"🆔 *ইউজার আইডি:* `{user_id}`\n"
        f"💰 *আপনার ব্যালেন্স:* ৳{balance:.2f} BDT\n\n"
        f"👇 *মেনু থেকে অপশন সিলেক্ট করুন:*"
    )

    inline_kb = []

    if user_id == ADMIN_ID:
        inline_kb.append(
            [
                InlineKeyboardButton(
                    "⚙️ অ্যাডমিন কন্ট্রোল প্যানেল",
                    callback_data="admin_panel",
                )
            ]
        )

    markup = (
        InlineKeyboardMarkup(inline_kb)
        if inline_kb
        else None
    )

    if update.message:
        await update.message.reply_text(
            "✨ সেবা পেতে নিচের মেনু ব্যবহার করুন:",
            reply_markup=main_reply_keyboard(),
        )

        await update.message.reply_text(
            text,
            reply_markup=markup,
            parse_mode="Markdown",
        )

    return ConversationHandler.END


# =========================================================
# CANCEL
# =========================================================

async def cancel_action(update, context):

    context.user_data.clear()

    if update.callback_query:

        await update.callback_query.answer()

        await update.callback_query.message.edit_text(
            "❌ প্রক্রিয়া বাতিল করা হয়েছে।"
        )

    elif update.message:

        await update.message.reply_text(
            "❌ প্রক্রিয়া বাতিল করা হয়েছে।",
            reply_markup=main_reply_keyboard(),
        )

    return ConversationHandler.END


# =========================================================
# GENERAL MESSAGE HANDLER
# =========================================================

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.message or not update.message.text:
        return

    text = update.message.text
    user_id = update.effective_user.id

    balance, is_banned = get_user(user_id)

    if is_banned:
        await update.message.reply_text(
            "❌ আপনার একাউন্ট স্থগিত আছে।"
        )
        return

    # -----------------------------------------------------
    # SERVICE CATALOG
    # -----------------------------------------------------

    if text == "🚀 সার্ভিস ক্যাটালগ":

        services = await fetch_smm_services()

        if not services:
            await update.message.reply_text(
                "⚠️ সার্ভিসসমূহ লোড করা যাচ্ছে না। "
                "কিছুক্ষণ পর আবার চেষ্টা করুন।"
            )
            return

        categories = sorted(
            set(
                str(s.get("category", "General"))
                for s in services
                if s.get("category")
            )
        )

        filtered_categories = [
            cat
            for cat in categories
            if any(
                platform in cat.lower()
                for platform in ALLOWED_PLATFORMS
            )
        ]

        if not filtered_categories:
            await update.message.reply_text(
                "⚠️ কোনো সার্ভিস ক্যাটাগরি পাওয়া যায়নি।"
            )
            return

        keyboard = []

        for index, cat in enumerate(filtered_categories):

            bangla_cat = translate_to_bangla(cat)

            emoji = "📁"

            cat_lower = cat.lower()

            if "youtube" in cat_lower:
                emoji = "🔴"
            elif "facebook" in cat_lower:
                emoji = "🔵"
            elif "tiktok" in cat_lower:
                emoji = "🎵"
            elif "instagram" in cat_lower:
                emoji = "📸"
            elif "telegram" in cat_lower:
                emoji = "✈️"
            elif (
                "traffic" in cat_lower
                or "website" in cat_lower
            ):
                emoji = "🌐"

            # index ব্যবহার করা হচ্ছে যাতে category name
            # callback_data-তে কেটে না যায়
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"{emoji} {bangla_cat[:40]}",
                        callback_data=f"catidx_{index}",
                    )
                ]
            )

        # Conversation-এর বাইরে category mapping রাখছি
        context.user_data["service_categories"] = (
            filtered_categories
        )

        await update.message.reply_text(
            "🏷️ *পছন্দের প্ল্যাটফর্ম নির্বাচন করুন:*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )

        return

    # -----------------------------------------------------
    # PROFILE
    # -----------------------------------------------------

    elif text in [
        "👤 প্রোফাইল ও ব্যালেন্স",
        "👤 মাই প্রোফাইল",
    ]:

        await update.message.reply_text(
            f"👤 *আপনার প্রোফাইল*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 *আইডি:* `{user_id}`\n"
            f"💵 *ব্যালেন্স:* ৳{balance:.2f} BDT\n"
            f"⚡ *স্ট্যাটাস:* Active ✅\n"
            f"━━━━━━━━━━━━━━━━━━━",
            parse_mode="Markdown",
        )

    # -----------------------------------------------------
    # RECHARGE
    # -----------------------------------------------------

    elif text in [
        "💳 ব্যালেন্স রিচার্জ",
        "💳 টাকা জমা দিন (রিচার্জ)",
    ]:

        await update.message.reply_text(
            f"💳 *ব্যালেন্স রিচার্জ পেমেন্ট ডিটেইলস*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📱 *বিকাশ (Personal):* `01911198221`\n"
            f"📱 *নগদ (Personal):* `01911198221`\n\n"
            f"📌 *টাকা পাঠানোর পর:*\n"
            f"পেমেন্ট স্ক্রিনশট এবং আপনার "
            f"*User ID (`{user_id}`)* কাস্টমার সাপোর্টে পাঠান।\n\n"
            f"💰 *মিনিমাম রিচার্জ:* ৳50",
            parse_mode="Markdown",
        )

    # -----------------------------------------------------
    # SUPPORT
    # -----------------------------------------------------

    elif text in [
        "💬 ২৪/৭ হেল্পলাইন",
        "💬 কাস্টমার সাপোর্ট",
    ]:

        keyboard = [
            [
                InlineKeyboardButton(
                    "💬 সরাসরি সাপোর্ট মেসেজ দিন",
                    url="https://wa.me/message/ZFPUNOUHWSWRI1",
                )
            ]
        ]

        await update.message.reply_text(
            "🛠️ *যেকোনো সমস্যায় আমাদের সাপোর্ট টিমের "
            "সাথে কথা বলুন:*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )

    # -----------------------------------------------------
    # TERMS
    # -----------------------------------------------------

    elif text in [
        "📜 শর্তাবলী ও নিয়ম",
        "📜 শর্তাবলী ও নিয়ম",
    ]:

        await update.message.reply_text(
            "📜 *নিয়ম ও শর্তাবলী:*\n\n"
            "১. ভুল লিংক বা প্রাইভেট প্রোফাইল লিংকে "
            "অর্ডার করলে টাকা রিফান্ড প্রযোজ্য হবে না।\n\n"
            "২. একটি অর্ডারের কাজ শেষ হওয়ার আগে একই "
            "লিংকে পুনরায় অর্ডার করবেন না।\n\n"
            "৩. রিফান্ড সরাসরি আপনার ওয়ালেট ব্যালেন্সে "
            "যোগ করে দেওয়া হবে।\n\n"
            "৪. যেকোনো সমস্যার সমাধান সর্বোচ্চ "
            "২৪ ঘণ্টার মধ্যে করার চেষ্টা করা হবে।",
            parse_mode="Markdown",
        )


# =========================================================
# CATEGORY CALLBACK
# =========================================================

async def category_callback(update, context):

    query = update.callback_query
    await query.answer()

    try:
        index = int(
            query.data.replace("catidx_", "")
        )
    except ValueError:
        await query.answer(
            "ক্যাটাগরি সঠিক নয়।",
            show_alert=True,
        )
        return

    categories = context.user_data.get(
        "service_categories",
        [],
    )

    if index < 0 or index >= len(categories):

        await query.answer(
            "ক্যাটাগরি পাওয়া যায়নি। আবার চেষ্টা করুন।",
            show_alert=True,
        )
        return

    selected_cat = categories[index]

    services = await fetch_smm_services()

    if not services:

        await query.message.edit_text(
            "⚠️ সার্ভিস লোড করা যাচ্ছে না।"
        )
        return

    keyboard = []

    for service in services:

        category = str(
            service.get("category", "General")
        )

        if category != selected_cat:
            continue

        service_id = service.get("service")

        try:
            raw_rate = float(
                service.get("rate", 0)
            )
        except (ValueError, TypeError):
            continue

        bdt_rate = raw_rate * USD_TO_BDT

        service_name = translate_to_bangla(
            service.get("name", "Service")
        )

        # Service ID আলাদা করে user_data-তে রাখা
        # হচ্ছে যাতে callback-data ছোট থাকে
        service_key = f"service_{service_id}"

        context.user_data.setdefault(
            "service_map",
            {}
        )

        context.user_data["service_map"][
            service_key
        ] = {
            "service_id": str(service_id),
            "rate": bdt_rate,
        }

        keyboard.append(
            [
                InlineKeyboardButton(
                    f"🔥 {service_name[:38]} - "
                    f"৳{bdt_rate:.2f}",
                    callback_data=f"select_{service_key}",
                )
            ]
        )

        if len(keyboard) >= 25:
            break

    if not keyboard:

        await query.message.edit_text(
            "⚠️ এই ক্যাটাগরিতে কোনো সার্ভিস পাওয়া যায়নি।"
        )
        return

    keyboard.append(
        [
            InlineKeyboardButton(
                "🔙 প্রধান মেনু",
                callback_data="cancel_flow",
            )
        ]
    )

    await query.message.edit_text(
        f"💎 *ক্যাটাগরি:* "
        f"{translate_to_bangla(selected_cat)}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


# =========================================================
# SELECT SERVICE
# =========================================================

async def select_service_callback(update, context):

    query = update.callback_query
    await query.answer()

    service_key = query.data.replace(
        "select_",
        "",
        1,
    )

    service_map = context.user_data.get(
        "service_map",
        {},
    )

    service_data = service_map.get(service_key)

    if not service_data:

        await query.answer(
            "সার্ভিসের তথ্য পাওয়া যায়নি। আবার সার্ভিস নির্বাচন করুন।",
            show_alert=True,
        )
        return ConversationHandler.END

    service_id = service_data["service_id"]
    rate = service_data["rate"]

    context.user_data["order_service_id"] = service_id
    context.user_data["order_rate"] = rate

    await query.message.edit_text(
        f"🛒 *অর্ডার প্রক্রিয়া*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 *সার্ভিস ID:* `{service_id}`\n"
        f"💵 *মূল্য (১০০০ টি):* ৳{rate:.2f} BDT\n\n"
        f"🔗 *ধাপ ১/২:* টার্গেট লিংক দিন:",
        reply_markup=cancel_inline_keyboard(),
        parse_mode="Markdown",
    )

    return ORDER_LINK


# =========================================================
# GET ORDER LINK
# =========================================================

async def order_get_link(update, context):

    link = update.message.text.strip()

    if not link:
        await update.message.reply_text(
            "⚠️ একটি সঠিক Link দিন:"
        )
        return ORDER_LINK

    if len(link) > 2000:
        await update.message.reply_text(
            "⚠️ Link অনেক বড়। সঠিক Link দিন:"
        )
        return ORDER_LINK

    context.user_data["order_link"] = link

    await update.message.reply_text(
        "🔢 *ধাপ ২/২:* পরিমাণ (Quantity) লিখুন:",
        reply_markup=cancel_inline_keyboard(),
        parse_mode="Markdown",
    )

    return ORDER_QTY


# =========================================================
# GET QUANTITY
# =========================================================

async def order_get_qty(update, context):

    try:
        qty = int(update.message.text.strip())
    except ValueError:

        await update.message.reply_text(
            "⚠️ সঠিক সংখ্যা লিখুন।\n"
            "উদাহরণ: `1000`",
            parse_mode="Markdown",
        )

        return ORDER_QTY

    if qty <= 0:

        await update.message.reply_text(
            "⚠️ Quantity অবশ্যই ১ বা তার বেশি হতে হবে।"
        )

        return ORDER_QTY

    if qty > 10000000:

        await update.message.reply_text(
            "⚠️ Quantity অনেক বেশি।"
        )

        return ORDER_QTY

    user_id = update.effective_user.id

    service_id = context.user_data.get(
        "order_service_id"
    )

    rate = context.user_data.get(
        "order_rate"
    )

    link = context.user_data.get(
        "order_link"
    )

    if not service_id or not rate or not link:

        await update.message.reply_text(
            "⚠️ অর্ডার সেশন শেষ হয়ে গেছে। "
            "আবার সার্ভিস নির্বাচন করুন।",
            reply_markup=main_reply_keyboard(),
        )

        return ConversationHandler.END

    cost = (qty / 1000.0) * float(rate)

    balance, is_banned = get_user(user_id)

    if is_banned:

        await update.message.reply_text(
            "❌ আপনার একাউন্ট স্থগিত আছে।"
        )

        return ConversationHandler.END

    if balance < cost:

        await update.message.reply_text(
            f"❌ *অপর্যাপ্ত ব্যালেন্স!*\n\n"
            f"প্রয়োজন: ৳{cost:.2f} BDT\n"
            f"বর্তমান ব্যালেন্স: ৳{balance:.2f} BDT",
            reply_markup=main_reply_keyboard(),
            parse_mode="Markdown",
        )

        return ConversationHandler.END

    # -----------------------------------------------------
    # PLACE ORDER FIRST
    # -----------------------------------------------------

    api_res = await place_smm_order(
        service_id,
        link,
        qty,
    )

    if not api_res:

        await update.message.reply_text(
            "⚠️ SMM সার্ভার থেকে কোনো response পাওয়া যায়নি। "
            "আপনার ব্যালেন্স কাটা হয়নি।",
            reply_markup=main_reply_keyboard(),
        )

        return ConversationHandler.END

    if "order" not in api_res:

        error = api_res.get(
            "error",
            "অজানা API ত্রুটি",
        )

        await update.message.reply_text(
            f"⚠️ *অর্ডার সম্পন্ন হয়নি।*\n\n"
            f"কারণ: {error}",
            reply_markup=main_reply_keyboard(),
            parse_mode="Markdown",
        )

        return ConversationHandler.END

    order_id = api_res["order"]

    # -----------------------------------------------------
    # DEDUCT BALANCE AFTER SUCCESS
    # -----------------------------------------------------

    deducted = deduct_user_balance(
        user_id,
        cost,
    )

    if not deducted:

        # API order হয়ে গেছে কিন্তু balance কাটতে পারেনি।
        # Admin-কে জানানো হচ্ছে।
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "⚠️ Balance deduction problem!\n\n"
                    f"User ID: {user_id}\n"
                    f"Order ID: {order_id}\n"
                    f"Cost: ৳{cost:.2f}\n"
                    f"Service: {service_id}"
                ),
            )
        except Exception:
            pass

        await update.message.reply_text(
            f"⚠️ অর্ডার API-তে গ্রহণ হয়েছে, "
            f"কিন্তু ব্যালেন্স আপডেটে সমস্যা হয়েছে।\n\n"
            f"Order ID: `{order_id}`\n"
            f"দয়া করে সাপোর্টে যোগাযোগ করুন।",
            reply_markup=main_reply_keyboard(),
            parse_mode="Markdown",
        )

        return ConversationHandler.END

    new_balance, _ = get_user(user_id)

    await update.message.reply_text(
        f"🎉 *অর্ডার গৃহিত হয়েছে!*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 *Order ID:* `{order_id}`\n"
        f"📦 *পরিমাণ:* {qty}\n"
        f"💸 *খরচ:* ৳{cost:.2f} BDT\n"
        f"💰 *অবশিষ্ট ব্যালেন্স:* "
        f"৳{new_balance:.2f} BDT\n"
        f"━━━━━━━━━━━━━━━━━━━",
        reply_markup=main_reply_keyboard(),
        parse_mode="Markdown",
    )

    context.user_data.clear()

    return ConversationHandler.END


# =========================================================
# TRACK ORDER
# =========================================================

async def start_track_order(update, context):

    await update.message.reply_text(
        "🔎 আপনার *Order ID* নম্বরটি লিখুন:",
        reply_markup=cancel_inline_keyboard(),
        parse_mode="Markdown",
    )

    return TRACK_ORDER_ID


async def process_track_order(update, context):

    order_id = update.message.text.strip()

    if not order_id:
        await update.message.reply_text(
            "⚠️ সঠিক Order ID দিন:"
        )
        return TRACK_ORDER_ID

    result = await get_smm_order_status(order_id)

    if result and "status" in result:

        status_map = {
            "Pending": "অপেক্ষমান ⏳",
            "In progress": "কাজ চলছে ⚡",
            "Completed": "সম্পন্ন হয়েছে ✅",
            "Partial": "আংশিক সম্পন্ন ⚠️",
            "Canceled": "বাতিল হয়েছে ❌",
            "Cancelled": "বাতিল হয়েছে ❌",
        }

        raw_status = str(
            result.get("status", "Unknown")
        )

        bangla_status = status_map.get(
            raw_status,
            raw_status,
        )

        await update.message.reply_text(
            f"📍 *অর্ডার স্ট্যাটাস রিপোর্ট*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 *Order ID:* `{order_id}`\n"
            f"📊 *অবস্থা:* {bangla_status}\n"
            f"📦 *শুরু সংখ্যা:* "
            f"{result.get('start_count', result.get('start_counter', '0'))}\n"
            f"⏳ *বাকি পরিমাণ:* "
            f"{result.get('remains', '0')}\n"
            f"━━━━━━━━━━━━━━━━━━━",
            reply_markup=main_reply_keyboard(),
            parse_mode="Markdown",
        )

    else:

        error = ""

        if isinstance(result, dict):
            error = result.get("error", "")

        await update.message.reply_text(
            "⚠️ সঠিক Order ID পাওয়া যায়নি "
            "বা SMM সার্ভারে সমস্যা হয়েছে."
            + (f"\n\nকারণ: {error}" if error else ""),
            reply_markup=main_reply_keyboard(),
        )

    return ConversationHandler.END


# =========================================================
# ADMIN PANEL
# =========================================================

async def admin_panel_callback(update, context):

    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        await query.answer(
            "আপনার অনুমতি নেই।",
            show_alert=True,
        )
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "➕ ইউজার ব্যালেন্স যোগ",
                callback_data="adm_addbal",
            )
        ],
        [
            InlineKeyboardButton(
                "📢 ব্রডকাস্ট মেসেজ (All)",
                callback_data="adm_broadcast",
            )
        ],
        [
            InlineKeyboardButton(
                "📊 মোট ইউজার সংখ্যা",
                callback_data="adm_stats",
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 বন্ধ করুন",
                callback_data="cancel_flow",
            )
        ],
    ]

    await query.message.edit_text(
        "⚙️ *অ্যাডমিন কন্ট্রোল প্যানেল*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


# =========================================================
# ADMIN ADD BALANCE
# =========================================================

async def adm_start_addbal(update, context):

    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END

    await query.message.edit_text(
        "👤 *ইউজার ID দিন:*",
        parse_mode="Markdown",
    )

    return ADD_BAL_USER


async def adm_get_user(update, context):

    try:
        target_user = int(
            update.message.text.strip()
        )

        if target_user <= 0:
            raise ValueError

        context.user_data["target_user"] = target_user

        await update.message.reply_text(
            "💵 *টাকার পরিমাণ লিখুন:*",
            parse_mode="Markdown",
        )

        return ADD_BAL_AMOUNT

    except ValueError:

        await update.message.reply_text(
            "⚠️ সঠিক User ID দিন:"
        )

        return ADD_BAL_USER


async def adm_get_amount(update, context):

    try:
        amount = float(
            update.message.text.strip()
        )

        if amount <= 0:
            raise ValueError

        target = context.user_data.get(
            "target_user"
        )

        if not target:
            await update.message.reply_text(
                "⚠️ User ID পাওয়া যায়নি। আবার চেষ্টা করুন।"
            )
            return ConversationHandler.END

        update_user_balance(
            target,
            amount,
        )

        await update.message.reply_text(
            f"✅ User ID `{target}`-এ "
            f"৳{amount:.2f} BDT যোগ করা হয়েছে।",
            parse_mode="Markdown",
            reply_markup=main_reply_keyboard(),
        )

        context.user_data.clear()

        return ConversationHandler.END

    except ValueError:

        await update.message.reply_text(
            "⚠️ সঠিক পরিমাণ লিখুন:"
        )

        return ADD_BAL_AMOUNT


# =========================================================
# ADMIN BROADCAST
# =========================================================

async def adm_start_broadcast(update, context):

    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END

    await query.message.edit_text(
        "📢 *সকল ইউজারের কাছে পাঠানোর বার্তাটি লিখুন:*",
        parse_mode="Markdown",
    )

    return BROADCAST_MSG


async def adm_send_broadcast(update, context):

    msg = update.message.text

    user_ids = get_all_user_ids()

    success = 0
    failed = 0

    for uid in user_ids:

        try:

            await context.bot.send_message(
                chat_id=uid,
                text=(
                    "📢 *অফিশিয়াল নোটিশ:*\n\n"
                    f"{msg}"
                ),
                parse_mode="Markdown",
            )

            success += 1

        except Exception as e:

            logger.warning(
                "Broadcast failed for %s: %s",
                uid,
                e,
            )

            failed += 1

    await update.message.reply_text(
        f"✅ ব্রডকাস্ট সম্পন্ন হয়েছে।\n\n"
        f"📨 সফল: {success}\n"
        f"❌ ব্যর্থ: {failed}",
        reply_markup=main_reply_keyboard(),
    )

    return ConversationHandler.END


# =========================================================
# CALLBACK ROUTER
# =========================================================

async def button_handler(update, context):

    query = update.callback_query

    if query.data == "admin_panel":
        await admin_panel_callback(
            update,
            context,
        )

    elif query.data == "adm_stats":

        await query.answer()

        if query.from_user.id != ADMIN_ID:
            await query.answer(
                "অনুমতি নেই।",
                show_alert=True,
            )
            return

        total_users = len(
            get_all_user_ids()
        )

        await query.message.edit_text(
            f"📊 *মোট ইউজার:* "
            f"{total_users} জন",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔙 অ্যাডমিন প্যানেল",
                            callback_data="admin_panel",
                        )
                    ]
                ]
            ),
        )

    elif query.data == "cancel_flow":

        await cancel_action(
            update,
            context,
        )


# =========================================================
# MAIN
# =========================================================

def main():

    token = TELEGRAM_BOT_TOKEN

    if (
        not token
        or token == "YOUR_BOT_TOKEN_HERE"
    ):

        print(
            "❌ Error: TELEGRAM_BOT_TOKEN পাওয়া যায়নি!"
        )

        return

    if (
        not SMM_API_KEY
        or SMM_API_KEY == "YOUR_SMM_API_KEY_HERE"
    ):

        print(
            "❌ Error: SMM_API_KEY বসানো হয়নি!"
        )

        return

    app = (
        ApplicationBuilder()
        .token(token)
        .build()
    )

    # -----------------------------------------------------
    # START
    # -----------------------------------------------------

    app.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    # -----------------------------------------------------
    # CATEGORY
    # -----------------------------------------------------

    app.add_handler(
        CallbackQueryHandler(
            category_callback,
            pattern=r"^catidx_\d+$",
        )
    )

    # -----------------------------------------------------
    # ORDER CONVERSATION
    # -----------------------------------------------------

    order_conv = ConversationHandler(

        entry_points=[
            CallbackQueryHandler(
                select_service_callback,
                pattern=r"^select_service_.+$",
            )
        ],

        states={

            ORDER_LINK: [

                MessageHandler(
                    nav_buttons_filter,
                    cancel_action,
                ),

                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    order_get_link,
                ),
            ],

            ORDER_QTY: [

                MessageHandler(
                    nav_buttons_filter,
                    cancel_action,
                ),

                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    order_get_qty,
                ),
            ],
        },

        fallbacks=[

            CallbackQueryHandler(
                cancel_action,
                pattern=r"^cancel_flow$",
            ),

            MessageHandler(
                nav_buttons_filter,
                cancel_action,
            ),
        ],

        per_message=False,
    )

    # -----------------------------------------------------
    # TRACK ORDER
    # -----------------------------------------------------

    track_conv = ConversationHandler(

        entry_points=[
            MessageHandler(
                track_order_filter,
                start_track_order,
            )
        ],

        states={

            TRACK_ORDER_ID: [

                MessageHandler(
                    nav_buttons_filter,
                    cancel_action,
                ),

                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    process_track_order,
                ),
            ],
        },

        fallbacks=[

            CallbackQueryHandler(
                cancel_action,
                pattern=r"^cancel_flow$",
            ),

            MessageHandler(
                nav_buttons_filter,
                cancel_action,
            ),
        ],

        per_message=False,
    )

    # -----------------------------------------------------
    # ADMIN ADD BALANCE
    # -----------------------------------------------------

    admin_addbal_conv = ConversationHandler(

        entry_points=[
            CallbackQueryHandler(
                adm_start_addbal,
                pattern=r"^adm_addbal$",
            )
        ],

        states={

            ADD_BAL_USER: [

                MessageHandler(
                    nav_buttons_filter,
                    cancel_action,
                ),

                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    adm_get_user,
                ),
            ],

            ADD_BAL_AMOUNT: [

                MessageHandler(
                    nav_buttons_filter,
                    cancel_action,
                ),

                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    adm_get_amount,
                ),
            ],
        },

        fallbacks=[

            CallbackQueryHandler(
                cancel_action,
                pattern=r"^cancel_flow$",
            ),

            MessageHandler(
                nav_buttons_filter,
                cancel_action,
            ),
        ],

        per_message=False,
    )

    # -----------------------------------------------------
    # ADMIN BROADCAST
    # -----------------------------------------------------

    admin_broadcast_conv = ConversationHandler(

        entry_points=[
            CallbackQueryHandler(
                adm_start_broadcast,
                pattern=r"^adm_broadcast$",
            )
        ],

        states={

            BROADCAST_MSG: [

                MessageHandler(
                    nav_buttons_filter,
                    cancel_action,
                ),

                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    adm_send_broadcast,
                ),
            ],
        },

        fallbacks=[

            CallbackQueryHandler(
                cancel_action,
                pattern=r"^cancel_flow$",
            ),

            MessageHandler(
                nav_buttons_filter,
                cancel_action,
            ),
        ],

        per_message=False,
    )

    # -----------------------------------------------------
    # REGISTER CONVERSATIONS FIRST
    # -----------------------------------------------------

    app.add_handler(order_conv)

    app.add_handler(track_conv)

    app.add_handler(admin_addbal_conv)

    app.add_handler(admin_broadcast_conv)

    # -----------------------------------------------------
    # GENERAL CALLBACKS
    # -----------------------------------------------------

    app.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )

    # -----------------------------------------------------
    # GENERAL MESSAGES
    # -----------------------------------------------------

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            message_handler,
        )
    )

    print("🤖 Bot started successfully...")

    app.run_polling()


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
