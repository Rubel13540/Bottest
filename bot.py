import sqlite3
import requests
import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
)

# ==================== DIRECT CONFIGURATION ====================
SMM_API_URL = "https://socialpanel.pro/api/v2"
SMM_API_KEY = "622feadfdefb1016b06cc8f18cdfaf83"  # Your API Key
ADMIN_ID = 5293614793                             # Your Telegram ID
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# =============================================================

logging.basicConfig(level=logging.INFO)

# States for Order & Admin Balance
ADD_BAL_USER, ADD_BAL_AMOUNT = range(2)
ORDER_LINK, ORDER_QTY = range(2, 4)

def init_db():
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            balance REAL DEFAULT 0.0
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def get_user_balance(user_id):
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    if row:
        conn.close()
        return row[0]
    else:
        cursor.execute('INSERT INTO users (user_id, balance) VALUES (?, ?)', (user_id, 0.0))
        conn.commit()
        conn.close()
        return 0.0

def update_user_balance(user_id, amount):
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, 0.0)', (user_id,))
    cursor.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (amount, user_id))
    conn.commit()
    conn.close()

def get_total_users():
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM users')
    total = cursor.fetchone()[0]
    conn.close()
    return total

def fetch_smm_services():
    try:
        payload = {'key': SMM_API_KEY, 'action': 'services'}
        response = requests.post(SMM_API_URL, data=payload, timeout=10)
        data = response.json()
        if isinstance(data, list):
            return data
    except Exception as e:
        logging.error(f"API Fetch Error: {e}")
    return []

def place_smm_order(service_id, link, quantity):
    try:
        payload = {
            'key': SMM_API_KEY,
            'action': 'add',
            'service': service_id,
            'link': link,
            'quantity': quantity
        }
        response = requests.post(SMM_API_URL, data=payload, timeout=10)
        return response.json()
    except Exception as e:
        logging.error(f"Order Error: {e}")
        return None

# Permanent Keyboard Menu with Beautiful Buttons
def main_reply_keyboard():
    keyboard = [
        [KeyboardButton("🚀 সার্ভিস ক্যাটালগ"), KeyboardButton("👤 প্রোফাইল ও ব্যালেন্স")],
        [KeyboardButton("💳 ব্যালেন্স রিচার্জ"), KeyboardButton("📊 লাইভ সার্ভিস রেট")],
        [KeyboardButton("💬 ২৪/৭ হেল্পলাইন"), KeyboardButton("❓ কিভাবে ব্যবহার করবেন")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance = get_user_balance(user_id)
    
    text = (
        f"✨ **Social Growth SMM Panel Bot-এ স্বাগতম!** ✨\n\n"
        f"🆔 **আপনার আইডি:** `{user_id}`\n"
        f"💰 **বর্তমান ব্যালেন্স:** ৳{balance:.2f} BDT\n\n"
        f"⚡ *দ্রুত ও নির্ভরযোগ্য স্যোশাল মিডিয়া প্রমোশন পেতে নিচের মেনু বোতামগুলো ব্যবহার করুন:* 🚀"
    )
    
    inline_kb = []
    if user_id == ADMIN_ID:
        inline_kb.append([InlineKeyboardButton("⚙️ অ্যাডমিন কন্ট্রোল প্যানেল", callback_data='admin_panel')])

    markup = InlineKeyboardMarkup(inline_kb) if inline_kb else None

    if update.message:
        await update.message.reply_text("👋 স্বাগতম! নিচের অপশনগুলো থেকে বেছে নিন:", reply_markup=main_reply_keyboard())
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.callback_query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    return ConversationHandler.END

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    balance = get_user_balance(user_id)

    if text in ["🚀 সার্ভিস ক্যাটালগ", "📊 লাইভ সার্ভিস রেট"]:
        services = fetch_smm_services()
        if not services:
            await update.message.reply_text("⚠️ সার্ভিসসমূহ লোড হতে সমস্যা হচ্ছে। প্যানেল এপিআই পরীক্ষা করুন।")
            return

        keyboard = []
        for s in services[:15]:
            s_id = s.get('service')
            rate = float(s.get('rate', 0.0))
            name = s.get('name', 'Service')[:30]
            btn_text = f"🔥 {name} - ৳{rate:.2f}"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"select_srv_{s_id}_{rate}")])

        await update.message.reply_text(
            "💎 **আমাদের সেরা সার্ভিসসমূহ:**\n\n"
            "অর্ডার করতে পছন্দের সার্ভিসটির উপর ক্লিক করুন 👇", 
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif text == "👤 প্রোফাইল ও ব্যালেন্স":
        await update.message.reply_text(
            f"👤 **আপনার একাউন্ট ওভারভিউ**\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 **ইউজার আইডি:** `{user_id}`\n"
            f"💵 **একউন্ট ব্যালেন্স:** ৳{balance:.2f} BDT\n"
            f"⚡ **স্ট্যাটাস:** Active User ✅\n"
            f"━━━━━━━━━━━━━━━━━━━",
            parse_mode="Markdown"
        )

    elif text == "💳 ব্যালেন্স রিচার্জ":
        await update.message.reply_text(
            f"💳 **ব্যালেন্স রিচার্জ পেমেন্ট তথ্য**\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📱 **বিকাশ (Personal):** `01911198221`\n"
            f"📱 **নগদ (Personal):** `01911198221`\n\n"
            f"📌 **টাকা পাঠানোর পর:**\n"
            f"পেমেন্টের স্ক্রিনশট এবং আপনার **User ID (`{user_id}`)** কাস্টমার সাপোর্টে পাঠালে সাথে সাথে ব্যালেন্স যুক্ত করে দেওয়া হবে।",
            parse_mode="Markdown"
        )

    elif text == "💬 ২৪/৭ হেল্পলাইন":
        keyboard = [[InlineKeyboardButton("💬 হোয়াটসঅ্যাপে মেসেজ দিন", url='https://wa.me/message/ZFPUNOUHWSWRI1')]]
        await update.message.reply_text("🛠️ **যেকোনো সাপোর্ট বা জিজ্ঞাসায় আমাদের প্রতিনিধিকে জানান:**", reply_markup=InlineKeyboardMarkup(keyboard))

    elif text == "❓ কিভাবে ব্যবহার করবেন":
        await update.message.reply_text(
            "📖 **বট ব্যবহারের নিয়মাবলী:**\n\n"
            "১. প্রথমে `💳 ব্যালেন্স রিচার্জ` অপশনে গিয়ে একাউন্টে টাকা যোগ করুন।\n"
            "২. `🚀 সার্ভিস ক্যাটালগ` এ গিয়ে কাঙ্ক্ষিত সার্ভিসটি সিলেক্ট করুন।\n"
            "৩. আপনার লিংক এবং পরিমাণ দিন।\n"
            "৪. সার্ভিস সাথে সাথে শুরু হয়ে যাবে! ⚡",
            parse_mode="Markdown"
        )

async def select_service_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split('_')
    s_id = parts[2]
    rate = parts[3]

    context.user_data['order_service_id'] = s_id
    context.user_data['order_rate'] = float(rate)

    await query.message.edit_text(
        f"🛒 **নতুন অর্ডার নির্বাচন**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 **সার্ভিস আইডি:** `{s_id}`\n"
        f"💵 **মূল্য (প্রতি ১০০০):** ৳{rate} BDT\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔗 **ধাপ ১/২:** অনুগ্রহ করে আপনার **টার্গেট লিংক (Link)** পাঠাইন:",
        parse_mode="Markdown"
    )
    return ORDER_LINK

async def order_get_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link = update.message.text.strip()
    context.user_data['order_link'] = link
    
    await update.message.reply_text(
        "🔢 **ধাপ ২/২:** আপনি কত পরিমাণ (Quantity) নিতে চান লিখুন (যেমন: 1000):"
    )
    return ORDER_QTY

async def order_get_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        qty = int(update.message.text.strip())
        user_id = update.effective_user.id
        s_id = context.user_data.get('order_service_id')
        rate = context.user_data.get('order_rate')
        link = context.user_data.get('order_link')

        cost = (qty / 1000.0) * rate
        balance = get_user_balance(user_id)

        if balance < cost:
            await update.message.reply_text(
                f"❌ **অপর্যাপ্ত ব্যালেন্স!**\n\n"
                f"💸 মোট প্রয়োজন: ৳{cost:.2f} BDT\n"
                f"💰 আপনার আছে: ৳{balance:.2f} BDT\n\n"
                f"অনুগ্রহ করে `💳 ব্যালেন্স রিচার্জ` অপশন থেকে রিচার্জ করুন।"
            )
            return ConversationHandler.END

        api_res = place_smm_order(s_id, link, qty)
        
        if api_res and 'order' in api_res:
            update_user_balance(user_id, -cost)
            order_id = api_res['order']
            new_bal = get_user_balance(user_id)
            await update.message.reply_text(
                f"🎉 **অর্ডার সফলভাবে গ্রহন করা হয়েছে!**\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"🆔 **Order ID:** `{order_id}`\n"
                f"🔗 **Link:** {link}\n"
                f"📦 **Quantity:** {qty}\n"
                f"💸 **মোট খরচ:** ৳{cost:.2f} BDT\n"
                f"💰 **অবশিষ্ট ব্যালেন্স:** ৳{new_bal:.2f} BDT\n"
                f"━━━━━━━━━━━━━━━━━━━",
                parse_mode="Markdown"
            )
        else:
            err_msg = api_res.get('error', 'অজানা সমস্যা') if api_res else 'সার্ভার রেসপন্স করছে না'
            await update.message.reply_text(f"⚠️ অর্ডার সম্পন্ন করা যায়নি। কারণ: {err_msg}")

        return ConversationHandler.END

    except ValueError:
        await update.message.reply_text("⚠️ সঠিক সংখ্যা লিখুন (যেমন: 500, 1000):")
        return ORDER_QTY

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == 'admin_panel':
        if user_id != ADMIN_ID: return
        keyboard = [
            [InlineKeyboardButton("➕ ইউজার ব্যালেন্স যুক্ত করুন", callback_data='adm_start_addbal')],
            [InlineKeyboardButton("📊 মোট ইউজার সংখ্যা", callback_data='adm_stats')]
        ]
        await query.message.edit_text(
            f"⚙️ **অ্যাডমিন কন্ট্রোল প্যানেল**\n\n👥 নিবন্ধিত মোট ইউজার: **{get_total_users()}** জন",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )

    elif query.data == 'adm_stats':
        keyboard = [[InlineKeyboardButton("🔙 অ্যাডমিন প্যানেল", callback_data='admin_panel')]]
        await query.message.edit_text(f"📊 মোট ইউজার: **{get_total_users()}** জন", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def adm_start_addbal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID: return
    await query.message.edit_text("👤 **ধাপ ১/২:** যে ইউজারের একাউন্টে টাকা দেবেন তার **User ID** লিখুন:")
    return ADD_BAL_USER

async def adm_get_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['target_user'] = int(update.message.text.strip())
        await update.message.reply_text("💵 **ধাপ ২/২:** টাকার পরিমাণ (Amount) লিখে পাঠান:")
        return ADD_BAL_AMOUNT
    except ValueError:
        await update.message.reply_text("⚠️ ভুল ID। শুধুমাত্র সংখ্যা দিয়ে আইডি লিখুন:")
        return ADD_BAL_USER

async def adm_get_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip())
        target_user = context.user_data['target_user']
        update_user_balance(target_user, amount)
        
        await update.message.reply_text(
            f"✅ **ব্যালেন্স যুক্ত হয়েছে!**\n\n👤 User ID: `{target_user}`\n💰 যুক্ত করা হয়েছে: ৳{amount} BDT",
            parse_mode="Markdown"
        )
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("⚠️ সঠিক টাকার পরিমাণ লিখুন।")
        return ADD_BAL_AMOUNT

if __name__ == '__main__':
    token = TELEGRAM_BOT_TOKEN
    if not token:
        print("Error: TELEGRAM_BOT_TOKEN missing in Railway Variables!")
    else:
        app = ApplicationBuilder().token(token).build()
        
        order_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(select_service_callback, pattern='^select_srv_')],
            states={
                ORDER_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, order_get_link)],
                ORDER_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, order_get_qty)],
            },
            fallbacks=[],
            per_message=False
        )

        admin_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(adm_start_addbal, pattern='^adm_start_addbal$')],
            states={
                ADD_BAL_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_get_user)],
                ADD_BAL_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_get_amount)],
            },
            fallbacks=[],
            per_message=False
        )
        
        app.add_handler(CommandHandler("start", start))
        app.add_handler(order_conv)
        app.add_handler(admin_conv)
        app.add_handler(CallbackQueryHandler(button_handler))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
        
        print("🤖 Bot Running Successfully...")
        app.run_polling()
