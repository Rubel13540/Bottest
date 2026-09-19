import sqlite3
import requests
import logging
import os
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
)

# ==================== CONFIGURATION ====================
SMM_API_URL = "https://socialpanel.pro/api/v2"
SMM_API_KEY = "622feadfdefb1016b06cc8f18cdfaf83"  # API Key
ADMIN_ID = 5293614793                             # Admin Telegram ID
USD_TO_BDT = 120.0                                # USD to BDT Rate
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# =======================================================

logging.basicConfig(level=logging.INFO)

# States
ADD_BAL_USER, ADD_BAL_AMOUNT = range(2)
ORDER_LINK, ORDER_QTY = range(2, 4)
TRACK_ORDER_ID = 4
BROADCAST_MSG = 5

TRANSLATION_MAP = {
    "YouTube": "ইউটিউব", "Subscribers": "সাবস্ক্রাইবার", "Subscriber": "সাবস্ক্রাইবার",
    "Views": "ভিউ", "View": "ভিউ", "Likes": "লাইক", "Like": "লাইক",
    "Facebook": "ফেসবুক", "Followers": "ফলোয়ার", "Follower": "ফলোয়ার",
    "Page": "পেজ", "Profile": "প্রোফাইল", "Reactions": "রিঅ্যাকশন",
    "Watch Time": "ওয়াচ টাইম", "TikTok": "টিকটক", "Instagram": "ইনস্টাগ্রাম",
    "Comments": "কমেন্ট", "Share": "শেয়ার", "Shares": "শেয়ার",
    "Real": "রিয়েল", "Non Drop": "নন-ড্রপ", "Refill": "রিফিল",
    "Monetized": "মনিটাইজড", "Members": "মেম্বার", "Group": "গ্রুপ", "Telegram": "টেলিগ্রাম",
    "Traffic": "ট্রাফিক", "Website": "ওয়েবসাইট"
}

ALLOWED_PLATFORMS = ["facebook", "youtube", "tiktok", "instagram", "telegram", "traffic", "website"]

# Navigation button texts list for auto-canceling ongoing tasks
NAV_BUTTONS = [
    "🚀 সার্ভিস ক্যাটালগ", "📍 অর্ডার ট্র্যাক করুন", "👤 প্রোফাইল ও ব্যালেন্স", 
    "👤 মাই প্রোফাইল", "💳 ব্যালেন্স রিচার্জ", "💳 টাকা জমা দিন (রিচার্জ)", 
    "💬 ২৪/৭ হেল্পলাইন", "💬 কাস্টমার সাপোর্ট", "📜 শর্তাবলী ও নিয়ম", "📜 শর্তাবলী ও নিয়ম"
]

# Custom Filter for Navigation Buttons (Fixes AttributeError)
class NavButtonFilter(filters.MessageFilter):
    def filter(self, message):
        return bool(message.text and message.text in NAV_BUTTONS)

class TrackOrderFilter(filters.MessageFilter):
    def filter(self, message):
        return bool(message.text and message.text == '📍 অর্ডার ট্র্যাক করুন')

nav_buttons_filter = NavButtonFilter()
track_order_filter = TrackOrderFilter()

def translate_to_bangla(text):
    if not text:
        return text
    translated = str(text)
    for eng, bng in TRANSLATION_MAP.items():
        translated = re.sub(rf'\b{eng}\b', bng, translated, flags=re.IGNORECASE)
    return translated

def init_db():
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            balance REAL DEFAULT 0.0,
            is_banned INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def get_user(user_id):
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT balance, is_banned FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    if row:
        conn.close()
        return row
    else:
        cursor.execute('INSERT INTO users (user_id, balance, is_banned) VALUES (?, 0.0, 0)', (user_id,))
        conn.commit()
        conn.close()
        return (0.0, 0)

def update_user_balance(user_id, amount):
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO users (user_id, balance, is_banned) VALUES (?, 0.0, 0)', (user_id,))
    cursor.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (amount, user_id))
    conn.commit()
    conn.close()

def get_all_user_ids():
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM users')
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows]

def fetch_smm_services():
    try:
        payload = {'key': SMM_API_KEY, 'action': 'services'}
        res = requests.post(SMM_API_URL, data=payload, timeout=10).json()
        if isinstance(res, list):
            return res
    except Exception as e:
        logging.error(f"API Fetch Error: {e}")
    return []

def place_smm_order(service_id, link, quantity):
    try:
        payload = {'key': SMM_API_KEY, 'action': 'add', 'service': service_id, 'link': link, 'quantity': quantity}
        return requests.post(SMM_API_URL, data=payload, timeout=10).json()
    except Exception as e:
        logging.error(f"Order Error: {e}")
        return None

def get_smm_order_status(order_id):
    try:
        payload = {'key': SMM_API_KEY, 'action': 'status', 'order': order_id}
        return requests.post(SMM_API_URL, data=payload, timeout=10).json()
    except Exception as e:
        logging.error(f"Status Error: {e}")
        return None

def main_reply_keyboard():
    keyboard = [
        [KeyboardButton("🚀 সার্ভিস ক্যাটালগ"), KeyboardButton("📍 অর্ডার ট্র্যাক করুন")],
        [KeyboardButton("👤 প্রোফাইল ও ব্যালেন্স"), KeyboardButton("💳 ব্যালেন্স রিচার্জ")],
        [KeyboardButton("💬 ২৪/৭ হেল্পলাইন"), KeyboardButton("📜 শর্তাবলী ও নিয়ম")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name
    balance, is_banned = get_user(user_id)

    if is_banned:
        if update.message:
            await update.message.reply_text("❌ আপনার একাউন্টটি স্থগিত করা হয়েছে।")
        return ConversationHandler.END

    text = (
        f"👋 **আসসালামু আলাইকুম, {name}!**\n"
        f"স্যোশাল মিডিয়া সার্ভিস বোটে আপনাকে স্বাগতম! 🌟\n\n"
        f"🆔 **ইউজার আইডি:** `{user_id}`\n"
        f"💰 **আপনার ব্যালেন্স:** ৳{balance:.2f} BDT\n\n"
        f"👇 **মেনু থেকে অপশন সিলেক্ট করুন:**"
    )

    inline_kb = []
    if user_id == ADMIN_ID:
        inline_kb.append([InlineKeyboardButton("⚙️ অ্যাডমিন কন্ট্রোল প্যানেল", callback_data='admin_panel')])

    markup = InlineKeyboardMarkup(inline_kb) if inline_kb else None

    if update.message:
        await update.message.reply_text("✨ সেবা পেতে নিচের মেনু ব্যবহার করুন:", reply_markup=main_reply_keyboard())
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.callback_query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    return ConversationHandler.END

async def cancel_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text("❌ প্রক্রিয়া বাতিল করা হয়েছে।")
    else:
        await update.message.reply_text("❌ প্রক্রিয়া বাতিল করা হয়েছে।", reply_markup=main_reply_keyboard())
    return ConversationHandler.END

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    balance, is_banned = get_user(user_id)

    if is_banned:
        await update.message.reply_text("❌ আপনার একাউন্ট স্থগিত আছে।")
        return

    if text == "🚀 সার্ভিস ক্যাটালগ":
        services = fetch_smm_services()
        if not services:
            await update.message.reply_text("⚠️ সার্ভিসসমূহ লোড করা যাচ্ছে না। কিছুক্ষণ পর চেষ্টা করুন।")
            return

        categories = list(set(s.get('category', 'General') for s in services))
        filtered_categories = [
            cat for cat in categories 
            if any(platform in cat.lower() for platform in ALLOWED_PLATFORMS)
        ]
        filtered_categories.sort()

        keyboard = []
        for cat in filtered_categories:
            bangla_cat = translate_to_bangla(cat)
            emoji = "📁"
            if "youtube" in cat.lower():
                emoji = "🔴"
            elif "facebook" in cat.lower():
                emoji = "🔵"
            elif "tiktok" in cat.lower():
                emoji = "🎵"
            elif "instagram" in cat.lower():
                emoji = "📸"
            elif "telegram" in cat.lower():
                emoji = "✈️"
            elif "traffic" in cat.lower() or "website" in cat.lower():
                emoji = "🌐"

            keyboard.append([InlineKeyboardButton(f"{emoji} {bangla_cat[:35]}", callback_data=f"cat_{cat[:30]}")])

        await update.message.reply_text("🏷️ **পছন্দের প্ল্যাটফর্ম নির্বাচন করুন:**", reply_markup=InlineKeyboardMarkup(keyboard))

    elif text in ["👤 প্রোফাইল ও ব্যালেন্স", "👤 মাই প্রোফাইল"]:
        await update.message.reply_text(
            f"👤 **আপনার প্রোফাইল**\n━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 **আইডি:** `{user_id}`\n"
            f"💵 **ব্যালেন্স:** ৳{balance:.2f} BDT\n"
            f"⚡ **স্ট্যাটাস:** Active ✅\n━━━━━━━━━━━━━━━━━━━",
            parse_mode="Markdown"
        )

    elif text in ["💳 ব্যালেন্স রিচার্জ", "💳 টাকা জমা দিন (রিচার্জ)"]:
        await update.message.reply_text(
            f"💳 **ব্যালেন্স রিচার্জ পেমেন্ট ডিটেইলস**\n━━━━━━━━━━━━━━━━━━━\n"
            f"📱 **বিকাশ (Personal):** `01911198221`\n"
            f"📱 **নগদ (Personal):** `01911198221`\n\n"
            f"📌 **টাকা পাঠানোর পর:**\n"
            f"পেমেন্ট স্ক্রিনশট এবং আপনার **User ID (`{user_id}`)** কাস্টমার সাপোর্টে পাঠান। মিনিমাম রিচার্জ ৫০ টাকা।",
            parse_mode="Markdown"
        )

    elif text in ["💬 ২৪/৭ হেল্পলাইন", "💬 কাস্টমার সাপোর্ট"]:
        keyboard = [[InlineKeyboardButton("💬 সরাসরি সাপোর্ট মেসেজ দিন", url='https://wa.me/message/ZFPUNOUHWSWRI1')]]
        await update.message.reply_text("🛠️ **যেকোনো সমস্যায় আমাদের সাপোর্ট টিমের সাথে কথা বলুন:**", reply_markup=InlineKeyboardMarkup(keyboard))

    elif text in ["📜 শর্তাবলী ও নিয়ম", "📜 শর্তাবলী ও নিয়ম"]:
        await update.message.reply_text(
            "📜 **নিয়ম ও শর্তাবলী:**\n\n"
            "১. ভুল লিংক বা প্রাইভেট প্রোফাইল লিংকে অর্ডার করলে টাকা রিফান্ড প্রযোজ্য হবে না।\n"
            "২. একটি অর্ডারের কাজ শেষ হওয়ার আগে একই লিংকে পুনরায় অর্ডার করবেন না।\n"
            "৩. রিফান্ড সরাসরি আপনার ওয়ালেট ব্যালেন্সে যোগ করে দেওয়া হবে।\n"
            "৪. যেকোনো সমস্যার সমাধান সর্বোচ্চ ২৪ ঘণ্টার মধ্যে করা হবে।",
            parse_mode="Markdown"
        )

async def category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    selected_cat = query.data.replace('cat_', '')
    services = fetch_smm_services()
    
    keyboard = []
    for s in services:
        if s.get('category', 'General')[:30] == selected_cat:
            s_id = s.get('service')
            bdt_rate = float(s.get('rate', 0.0)) * USD_TO_BDT
            bangla_name = translate_to_bangla(s.get('name', 'Service'))[:30]
            keyboard.append([InlineKeyboardButton(f"🔥 {bangla_name} - ৳{bdt_rate:.2f}", callback_data=f"select_srv_{s_id}_{bdt_rate:.4f}")])

    keyboard.append([InlineKeyboardButton("🔙 প্রধান মেনু", callback_data='cancel_flow')])
    await query.message.edit_text(f"💎 **ক্যাটাগরি: {translate_to_bangla(selected_cat)}**", reply_markup=InlineKeyboardMarkup(keyboard[:25]))

async def select_service_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split('_')
    context.user_data['order_service_id'] = parts[2]
    context.user_data['order_rate'] = float(parts[3])

    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ অর্ডার বাতিল করুন", callback_data='cancel_flow')]])

    await query.message.edit_text(
        f"🛒 **অর্ডার প্রক্রিয়া**\n🆔 **সার্ভিস আইডি:** `{parts[2]}`\n💵 **মূল্য (১০০০ টি):** ৳{float(parts[3]):.2f} BDT\n\n"
        f"🔗 **ধাপ ১/২:** টার্গেট লিংক (Link) দিন:",
        reply_markup=cancel_kb,
        parse_mode="Markdown"
    )
    return ORDER_LINK

async def order_get_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['order_link'] = update.message.text.strip()
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ অর্ডার বাতিল করুন", callback_data='cancel_flow')]])
    await update.message.reply_text("🔢 **ধাপ ২/২:** পরিমাণ (Quantity) লিখুন:", reply_markup=cancel_kb)
    return ORDER_QTY

async def order_get_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        qty = int(update.message.text.strip())
        user_id = update.effective_user.id
        s_id = context.user_data.get('order_service_id')
        rate = context.user_data.get('order_rate')
        link = context.user_data.get('order_link')

        cost = (qty / 1000.0) * rate
        balance, _ = get_user(user_id)

        if balance < cost:
            await update.message.reply_text(f"❌ **অপর্যাপ্ত ব্যালেন্স!**\nপ্রয়োজন: ৳{cost:.2f} BDT | আছে: ৳{balance:.2f} BDT", reply_markup=main_reply_keyboard())
            return ConversationHandler.END

        api_res = place_smm_order(s_id, link, qty)
        if api_res and 'order' in api_res:
            update_user_balance(user_id, -cost)
            order_id = api_res['order']
            new_bal, _ = get_user(user_id)
            await update.message.reply_text(
                f"🎉 **অর্ডার গৃহিত হয়েছে!**\n━━━━━━━━━━━━━━━━━━━\n"
                f"🆔 **Order ID:** `{order_id}`\n📦 **পরিমাণ:** {qty}\n💸 **খরচ:** ৳{cost:.2f} BDT\n"
                f"💰 **অবশিষ্ট ব্যালেন্স:** ৳{new_bal:.2f} BDT\n━━━━━━━━━━━━━━━━━━━",
                reply_markup=main_reply_keyboard(),
                parse_mode="Markdown"
            )
        else:
            err = api_res.get('error', 'অজানা ত্রুটি') if api_res else 'সার্ভার রেসপন্স করছে না'
            await update.message.reply_text(f"⚠️ অর্ডার সম্পন্ন হয়নি। কারণ: {err}", reply_markup=main_reply_keyboard())

        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("⚠️ সঠিক সংখ্যা লিখুন (যেমন: 1000):")
        return ORDER_QTY

async def start_track_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ বাতিল করুন", callback_data='cancel_flow')]])
    await update.message.reply_text("🔎 আপনার **Order ID** নম্বরটি লিখুন:", reply_markup=cancel_kb)
    return TRACK_ORDER_ID

async def process_track_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    order_id = update.message.text.strip()
    res = get_smm_order_status(order_id)

    if res and 'status' in res:
        status_map = {
            'Pending': 'অপেক্ষমান ⏳',
            'In progress': 'কাজ চলছে ⚡',
            'Completed': 'সম্পন্ন হয়েছে ✅',
            'Partial': 'আংশিক সম্পন্ন ⚠️',
            'Canceled': 'বাতিল হয়েছে ❌'
        }
        raw_status = res.get('status', 'Unknown')
        bng_status = status_map.get(raw_status, raw_status)
        
        await update.message.reply_text(
            f"📍 **অর্ডার স্ট্যাটাস রিপোর্ট**\n━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 **Order ID:** `{order_id}`\n"
            f"📊 **অবস্থা:** {bng_status}\n"
            f"📦 **শুরু সংখ্যা:** {res.get('start_counter', '0')}\n"
            f"⏳ **বাকি পরিমাণ:** {res.get('remains', '0')}\n━━━━━━━━━━━━━━━━━━━",
            reply_markup=main_reply_keyboard(),
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("⚠️ সঠিক Order ID পাওয়া যায়নি বা সার্ভার ত্রুটি।", reply_markup=main_reply_keyboard())
    return ConversationHandler.END

# --- Admin Handlers ---
async def admin_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return

    keyboard = [
        [InlineKeyboardButton("➕ ইউজার ব্যালেন্স যোগ", callback_data='adm_addbal')],
        [InlineKeyboardButton("📢 ব্রডকাস্ট মেসেজ (All)", callback_data='adm_broadcast')],
        [InlineKeyboardButton("📊 মোট ইউজার সংখ্যা", callback_data='adm_stats')]
    ]
    await query.message.edit_text("⚙️ **অ্যাডমিন কন্ট্রোল প্যানেল**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def adm_start_addbal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return
    await query.message.edit_text("👤 **ইউজার ID দিন:**")
    return ADD_BAL_USER

async def adm_get_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['target_user'] = int(update.message.text.strip())
        await update.message.reply_text("💵 **টাকার পরিমাণ লিখুন:**")
        return ADD_BAL_AMOUNT
    except ValueError:
        await update.message.reply_text("⚠️ সঠিক ID দিন:")
        return ADD_BAL_USER

async def adm_get_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip())
        target = context.user_data['target_user']
        update_user_balance(target, amount)
        await update.message.reply_text(f"✅ User ID `{target}`-এ ৳{amount} BDT যোগ করা হয়েছে।", parse_mode="Markdown")
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("⚠️ সঠিক পরিমাণ লিখুন:")
        return ADD_BAL_AMOUNT

async def adm_start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return
    await query.message.edit_text("📢 **সকল ইউজারের কাছে পাঠানোর মতো বার্তাটি লিখুন:**")
    return BROADCAST_MSG

async def adm_send_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    user_ids = get_all_user_ids()
    count = 0
    for uid in user_ids:
        try:
            await context.bot.send_message(chat_id=uid, text=f"📢 **অফিশিয়াল নোটিশ:**\n\n{msg}", parse_mode="Markdown")
            count += 1
        except Exception:
            pass
    await update.message.reply_text(f"✅ মোট {count} জন ইউজারের কাছে নোটিশ পাঠানো হয়েছে।")
    return ConversationHandler.END

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'admin_panel':
        await admin_panel_callback(update, context)
    elif query.data == 'adm_stats':
        await query.message.edit_text(f"📊 মোট ইউজার: **{len(get_all_user_ids())}** জন", parse_mode="Markdown")
    elif query.data == 'cancel_flow':
        await cancel_action(update, context)

if __name__ == '__main__':
    token = TELEGRAM_BOT_TOKEN
    if not token:
        print("Error: TELEGRAM_BOT_TOKEN missing!")
    else:
        app = ApplicationBuilder().token(token).build()

        order_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(select_service_callback, pattern='^select_srv_')],
            states={
                ORDER_LINK: [
                    MessageHandler(nav_buttons_filter, cancel_action),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, order_get_link)
                ],
                ORDER_QTY: [
                    MessageHandler(nav_buttons_filter, cancel_action),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, order_get_qty)
                ]
            },
            fallbacks=[
                CallbackQueryHandler(cancel_action, pattern='^cancel_flow$'),
                MessageHandler(nav_buttons_filter, cancel_action)
            ], 
            per_message=False
        )

        track_conv = ConversationHandler(
            entry_points=[MessageHandler(track_order_filter, start_track_order)],
            states={
                TRACK_ORDER_ID: [
                    MessageHandler(nav_buttons_filter, cancel_action),
                    MessageHandler(filters.TEXT & ~filters.COM
