import os
import sqlite3
import requests
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
)

# Environment Variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SMM_API_KEY = os.getenv("SMM_API_KEY")
SMM_API_URL = os.getenv("SMM_API_URL", "https://socialpanel.pro/api/v2")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

logging.basicConfig(level=logging.INFO)

# Conversation States for Admin Balance Add
ADD_BAL_USER, ADD_BAL_AMOUNT = range(2)

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

# Fetch All Services from SMM Panel API
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

# Start Command / Home Menu
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance = get_user_balance(user_id)
    
    keyboard = [
        [InlineKeyboardButton("🛒 সার্ভিসসমূহ ও অর্ডার করুন", callback_data='services_list')],
        [InlineKeyboardButton("👤 আমার একাউন্ট", callback_data='profile'),
         InlineKeyboardButton("💳 ব্যালেন্স যুক্ত করুন", callback_data='add_balance')],
        [InlineKeyboardButton("📞 কাস্টমার সাপোর্ট (WhatsApp)", url='https://wa.me/message/ZFPUNOUHWSWRI1')]
    ]
    
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("⚙️ অ্যাডমিন কন্ট্রোল প্যানেল", callback_data='admin_panel')])

    reply_markup = InlineKeyboardMarkup(keyboard)
    text = (
        f"🌟 **Social Growth SMM Service Bot-এ স্বাগতম!** 🌟\n\n"
        f"🆔 **User ID:** `{user_id}`\n"
        f"💰 **বর্তমান ব্যালেন্স:** ৳{balance:.2f} BDT\n\n"
        f"👇 নিচের বোতামগুলোতে ক্লিক করে সার্ভিস বাছাই করুন:"
    )
    
    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.callback_query.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    return ConversationHandler.END

# Button Click Navigation
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == 'main_menu':
        await start(update, context)

    elif query.data == 'profile':
        balance = get_user_balance(user_id)
        keyboard = [[InlineKeyboardButton("🏠 প্রধান মেনু", callback_data='main_menu')]]
        await query.message.edit_text(
            f"👤 **আপনার একাউন্ট ড্যাশবোর্ড**\n\n🆔 User ID: `{user_id}`\n💰 মোট ব্যালেন্স: ৳{balance:.2f} BDT",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
        
    elif query.data == 'add_balance':
        keyboard = [
            [InlineKeyboardButton("💬 সরাসরি সাপোর্ট (WhatsApp)", url='https://wa.me/message/ZFPUNOUHWSWRI1')],
            [InlineKeyboardButton("🏠 প্রধান মেনু", callback_data='main_menu')]
        ]
        await query.message.edit_text(
            "💳 **টাকা যুক্ত করার নিয়মাবলী:**\n\n"
            "আমাদের বিকাশ ও নগদ Personal নাম্বার:\n"
            "📱 `01911198221`\n\n"
            "টাকা সেন্ড মানি করার পর পেমেন্টের স্ক্রিনশট এবং আপনার **User ID (`" + str(user_id) + "`)** সহ WhatsApp-এ পাঠান। অ্যাডমিন সাথে সাথে ব্যালেন্স যোগ করে দেবে।",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )

    elif query.data == 'services_list':
        services = fetch_smm_services()
        if not services:
            keyboard = [[InlineKeyboardButton("🔄 পুনরায় চেষ্টা করুন", callback_data='services_list'),
                         InlineKeyboardButton("🏠 প্রধান মেনু", callback_data='main_menu')]]
            await query.message.edit_text("⚠️ সার্ভিসসমূহ লোড করা যাচ্ছে না। API Key এবং URL পরীক্ষা করে নিন।", reply_markup=InlineKeyboardMarkup(keyboard))
            return

        keyboard = []
        # সেরা সার্ভিসসমূহ বাটন আকারে লোড করা (১০ টাকা ব্যাকএন্ডে যোগ করে মূল রেট হিসেবে দেখানো)
        for s in services[:10]:
            s_id = s.get('service')
            original_rate = float(s.get('rate', 0))
            final_rate = original_rate + 10.0  # ১০ টাকা ব্যাকএন্ড প্রফিট
            btn_text = f"🔥 {s.get('name')[:30]} | ৳{final_rate:.1f}"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"buy_{s_id}_{final_rate:.1f}")])

        keyboard.append([InlineKeyboardButton("🏠 প্রধান মেনু", callback_data='main_menu')])
        await query.message.edit_text("📋 **আমাদের জনপ্রিয় সার্ভিস তালিকা (রেট প্রতি ১ হাজারে):**", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data.startswith('buy_'):
        parts = query.data.split('_')
        s_id = parts[1]
        price = parts[2]
        keyboard = [
            [InlineKeyboardButton("🔙 সার্ভিস তালিকা", callback_data='services_list')],
            [InlineKeyboardButton("🏠 প্রধান মেনু", callback_data='main_menu')]
        ]
        await query.message.edit_text(
            f"🛒 **অর্ডার নির্দেশিকা:**\n\n"
            f"🔹 সার্ভিস ID: `{s_id}`\n"
            f"🔹 মূল্য: ৳{price} BDT (প্রতি ১০০০)\n\n"
            f"অর্ডার করতে চ্যাটে লিখুন:\n"
            f"`/order {s_id} <আপনার_লিংক> <পরিমাণ>`\n\n"
            f"**উদাহরণ:** `/order {s_id} https://facebook.com/page 1000`",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )

    elif query.data == 'admin_panel':
        if user_id != ADMIN_ID: return
        keyboard = [
            [InlineKeyboardButton("➕ ইউজার ব্যালেন্স যোগ করুন", callback_data='adm_start_addbal')],
            [InlineKeyboardButton("📊 মোট ইউজার সংখ্যা", callback_data='adm_stats')],
            [InlineKeyboardButton("🏠 প্রধান মেনু", callback_data='main_menu')]
        ]
        await query.message.edit_text(
            f"⚙️ **অ্যাডমিন কন্ট্রোল প্যানেল**\n\n👥 নিবন্ধিত ইউজার: **{get_total_users()}** জন",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )

    elif query.data == 'adm_stats':
        keyboard = [[InlineKeyboardButton("🔙 অ্যাডমিন প্যানেল", callback_data='admin_panel')]]
        await query.message.edit_text(f"📊 মোট নিবন্ধিত ইউজার: **{get_total_users()}** জন", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# --- Step-by-Step Admin Balance Addition ---
async def adm_start_addbal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID: return
    
    keyboard = [[InlineKeyboardButton("❌ বাতিল করুন", callback_data='admin_panel')]]
    await query.message.edit_text("👤 **ধাপ ১/২:** ইউজার যাকে ব্যালেন্স দেবেন তার **User ID** চ্যাটে লিখুন:", reply_markup=InlineKeyboardMarkup(keyboard))
    return ADD_BAL_USER

async def adm_get_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['target_user'] = int(update.message.text.strip())
        keyboard = [[InlineKeyboardButton("❌ বাতিল করুন", callback_data='admin_panel')]]
        await update.message.reply_text("💵 **ধাপ ২/২:** কত টাকা (Amount) যোগ করবেন তা লিখুন:", reply_markup=InlineKeyboardMarkup(keyboard))
        return ADD_BAL_AMOUNT
    except ValueError:
        await update.message.reply_text("⚠️ আইডি শুধুমাত্র সংখ্যা দিয়ে লিখুন। আবার চেষ্টা করুন:")
        return ADD_BAL_USER

async def adm_get_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip())
        target_user = context.user_data['target_user']
        update_user_balance(target_user, amount)
        
        keyboard = [[InlineKeyboardButton("⚙️ অ্যাডমিন প্যানেল", callback_data='admin_panel'),
                     InlineKeyboardButton("🏠 প্রধান মেনু", callback_data='main_menu')]]
        await update.message.reply_text(
            f"✅ **ব্যালেন্স সফলভাবে যুক্ত হয়েছে!**\n\n👤 User ID: `{target_user}`\n💰 পরিমাণ: ৳{amount} BDT",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("⚠️ সঠিক টাকার পরিমাণ লিখুন।")
        return ADD_BAL_AMOUNT

if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    bal_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(adm_start_addbal, pattern='^adm_start_addbal$')],
        states={
            ADD_BAL_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_get_user)],
            ADD_BAL_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_get_amount)],
        },
        fallbacks=[CallbackQueryHandler(button_handler, pattern='^(admin_panel|main_menu)$')]
    )
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(bal_handler)
    app.add_handler(CallbackQueryHandler(button_handler))
    
    print("🤖 SMM Bot Loaded Successfully...")
    app.run_polling()
