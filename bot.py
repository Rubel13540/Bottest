import os
import sqlite3
import requests
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes
)

# Environment Variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SMM_API_KEY = os.getenv("SMM_API_KEY")
SMM_API_URL = os.getenv("SMM_API_URL", "https://socialpanel.pro/api/v2")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

logging.basicConfig(level=logging.INFO)

# Database Setup
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
    # ব্যালেন্স যদি অলরেডি থাকে তবে যোগ হবে, না থাকলে নতুন তৈরি হয়ে যোগ হবে
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

# SMM Panel থেকে সার্ভিস ফেচ করা (১০ টাকা প্রফিট সহ)
def fetch_smm_services():
    try:
        payload = {'key': SMM_API_KEY, 'action': 'services'}
        response = requests.post(SMM_API_URL, data=payload, timeout=10).json()
        if isinstance(response, list):
            return response
    except Exception as e:
        logging.error(f"API Error: {e}")
    return []

# Start & Main Menu
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance = get_user_balance(user_id)
    
    keyboard = [
        [InlineKeyboardButton("🛒 সার্ভিসসমূহ ও অর্ডার", callback_data='services')],
        [InlineKeyboardButton("👤 আমার একাউন্ট", callback_data='profile'),
         InlineKeyboardButton("💳 ব্যালেন্স অ্যাড করুন", callback_data='add_balance')],
        [InlineKeyboardButton("📞 যোগাযোগ ও সাপোর্ট (WhatsApp)", url='https://wa.me/message/ZFPUNOUHWSWRI1')]
    ]
    
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("⚙️ অ্যাডমিন কন্ট্রোল প্যানেল", callback_data='admin_panel')])

    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        f"🚀 **স্বাগতম SMM Panel Bot-এ!**\n\n"
        f"🆔 **আপনার User ID:** `{user_id}`\n"
        f"💰 **বর্তমান ব্যালেন্স:** ৳{balance:.2f} BDT\n\n"
        f"নিচের বাটনগুলো থেকে আপনার প্রয়োজনীয় সেবাটি বেছে নিন:"
    )
    
    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.callback_query.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")

# Button Click Management
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == 'profile':
        balance = get_user_balance(user_id)
        keyboard = [[InlineKeyboardButton("🔙 প্রধান মেনু", callback_data='main_menu')]]
        await query.message.edit_text(
            f"👤 **আপনার একাউন্ট তথ্য**\n\n🆔 User ID: `{user_id}`\n💰 ব্যালেন্স: ৳{balance:.2f} BDT",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
        
    elif query.data == 'add_balance':
        keyboard = [
            [InlineKeyboardButton("💬 অ্যাডমিনের সাথে যোগাযোগ (WhatsApp)", url='https://wa.me/message/ZFPUNOUHWSWRI1')],
            [InlineKeyboardButton("🔙 প্রধান মেনু", callback_data='main_menu')]
        ]
        await query.message.edit_text(
            "💳 **টাকা এড করার নিয়ম:**\n\n"
            "আমাদের বিকাশ ও নগদ **Personal** নম্বর:\n"
            "📲 `01911198221`\n\n"
            "টাকা সেন্ড করার পর পেমেন্টের স্ক্রিনশট এবং আপনার **User ID (`" + str(user_id) + "`)** সহ নিচের WhatsApp লিংকে মেসেজ দিন। অ্যাডমিন চেক করে সাথে সাথে ব্যালেন্স অ্যাড করে দেবেন।",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
        
    elif query.data == 'services':
        services = fetch_smm_services()
        if not services:
            keyboard = [[InlineKeyboardButton("🔙 প্রধান মেনু", callback_data='main_menu')]]
            await query.message.edit_text("⚠️ দুঃখিত, বর্তমানে সার্ভিস লিস্ট লোড করা সম্ভব হচ্ছে না। কিছুক্ষণ পর চেষ্টা করুন।", reply_markup=InlineKeyboardMarkup(keyboard))
            return
        
        # প্রথম ১০টি সার্ভিস বাটন আকারে দেখানো (যাতে লিস্ট বেশি বড় না হয়)
        keyboard = []
        for s in services[:10]: 
            s_id = s.get('service')
            s_name = s.get('name')
            s_rate = float(s.get('rate', 0)) + 10  # মূল রেটের সাথে ১০ টাকা প্রফিট যোগ করা হলো
            btn_text = f"{s_name[:30]}... (৳{s_rate}/1k)"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"buy_{s_id}")])
            
        keyboard.append([InlineKeyboardButton("🔙 প্রধান মেনু", callback_data='main_menu')])
        await query.message.edit_text("📋 **প্যানেলের সেরা সার্ভিসসমূহ (প্রতি ১ হাজারে ১০ টাকা প্রফিট যুক্ত):**", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data.startswith('buy_'):
        s_id = query.data.split('_')[1]
        keyboard = [[InlineKeyboardButton("🔙 সার্ভিস লিস্ট", callback_data='services'),
                     InlineKeyboardButton("🔙 প্রধান মেনু", callback_data='main_menu')]]
        await query.message.edit_text(
            f"🛒 **অর্ডার করার নিয়ম:**\n\n"
            f"এই সার্ভিসটি (ID: `{s_id}`) অর্ডার করতে চ্যাটে এই ফরম্যাটে মেসেজ পাঠান:\n\n"
            f"`/order {s_id} <Link> <Quantity>`\n\n"
            f"**উদাহরণ:** `/order {s_id} https://instagram.com/p/xxx 1000`",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )

    elif query.data == 'admin_panel':
        if user_id != ADMIN_ID:
            return
        total_users = get_total_users()
        admin_keyboard = [
            [InlineKeyboardButton("➕ ব্যালেন্স যোগ করার কমান্ড গাইড", callback_data='adm_guide')],
            [InlineKeyboardButton("📊 মোট ইউজার", callback_data='adm_stats')],
            [InlineKeyboardButton("🔙 প্রধান মেনু", callback_data='main_menu')]
        ]
        await query.message.edit_text(
            f"⚙️ **অ্যাডমিন কন্ট্রোল প্যানেল**\n\n👥 মোট ইউজার: {total_users} জন",
            reply_markup=InlineKeyboardMarkup(admin_keyboard)
        )
        
    elif query.data == 'adm_guide':
        admin_keyboard = [[InlineKeyboardButton("🔙 অ্যাডমিন প্যানেল", callback_data='admin_panel')]]
        await query.message.edit_text(
            "💡 ব্যালেন্স যোগ করতে চ্যাটে লিখুন:\n`/addbal <USER_ID> <AMOUNT>`\nযেমন: `/addbal 5293614793 100`",
            reply_markup=InlineKeyboardMarkup(admin_keyboard)
        )
        
    elif query.data == 'adm_stats':
        admin_keyboard = [[InlineKeyboardButton("🔙 অ্যাডমিন প্যানেল", callback_data='admin_panel')]]
        await query.message.edit_text(f"📊 মোট নিবন্ধিত ইউজার: {get_total_users()} জন", reply_markup=admin_keyboard)

    elif query.data == 'main_menu':
        await start(update, context)

# সরাসরি অর্ডার হ্যান্ডেল করার কমান্ড (/order <id> <link> <qty>)
async def place_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if len(context.args) < 3:
        await update.message.reply_text("⚠️ **সঠিক নিয়ম:** `/order <Service_ID> <Link> <Quantity>`", parse_mode="Markdown")
        return

    service_id, link, quantity = context.args[0], context.args[1], context.args[2]

    payload = {
        'key': SMM_API_KEY,
        'action': 'add',
        'service': service_id,
        'link': link,
        'quantity': quantity
    }

    try:
        response = requests.post(SMM_API_URL, data=payload, timeout=10).json()
        if 'order' in response:
            await update.message.reply_text(f"✅ **অর্ডার সফলভাবে প্লেস হয়েছে!**\n\n📦 **Order ID:** `{response['order']}`", parse_mode="Markdown")
        else:
            error_msg = response.get('error', 'পর্যাপ্ত ব্যালেন্স নেই বা লিংক ভুল।')
            await update.message.reply_text(f"❌ **অর্ডার ব্যর্থ হয়েছে:** {error_msg}")
    except Exception as e:
        await update.message.reply_text("❌ সার্ভার কানেকশনে সমস্যা হয়েছে।")

# অ্যাডমিন ব্যালেন্স যোগ করার কমান্ড (/addbal)
async def add_bal_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ আপনার এই কমান্ড ব্যবহারের অনুমতি নেই।")
        return
    try:
        target_id = int(context.args[0])
        amount = float(context.args[1])
        update_user_balance(target_id, amount)
        await update.message.reply_text(f"✅ User ID `{target_id}`-এ সফলভাবে ৳{amount} যোগ করা হয়েছে।")
    except Exception:
        await update.message.reply_text("⚠️ **সঠিক নিয়ম:** `/addbal <USER_ID> <AMOUNT>`")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("order", place_order))
    app.add_handler(CommandHandler("addbal", add_bal_admin))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    print("🤖 Professional SMM Bot is running...")
    app.run_polling()
