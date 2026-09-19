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
ADMIN_ID = int(os.getenv("ADMIN_ID", "0")) if os.getenv("ADMIN_ID") else 0

logging.basicConfig(level=logging.INFO)

ADD_BAL_USER, ADD_BAL_AMOUNT = range(2)

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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance = get_user_balance(user_id)
    
    keyboard = [
        [InlineKeyboardButton("🛒 সার্ভিসসমূহ ও অর্ডার করুন", callback_data='cat_menu')],
        [InlineKeyboardButton("👤 আমার একাউন্ট", callback_data='profile'),
         InlineKeyboardButton("💳 ব্যালেন্স যুক্ত করুন", callback_data='add_balance')],
        [InlineKeyboardButton("💬 কাস্টমার সাপোর্ট (WhatsApp)", url='https://wa.me/message/ZFPUNOUHWSWRI1')]
    ]
    
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("⚙️ অ্যাডমিন কন্ট্রোল প্যানেল", callback_data='admin_panel')])

    reply_markup = InlineKeyboardMarkup(keyboard)
    text = (
        f"🌟 **Social Growth SMM Panel Bot-এ স্বাগতম!** 🌟\n\n"
        f"🆔 **আপনার User ID:** `{user_id}`\n"
        f"💰 **বর্তমান ব্যালেন্স:** ৳{balance:.2f} BDT\n\n"
        f"👇 নিচের মেনু বোতামগুলো ব্যবহার করে কাজ সম্পন্ন করুন:"
    )
    
    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.callback_query.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    return ConversationHandler.END

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
            f"👤 **আপনার প্রোফাইল তথ্য**\n\n🆔 ID: `{user_id}`\n💰 ব্যালেন্স: ৳{balance:.2f} BDT",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
        
    elif query.data == 'add_balance':
        keyboard = [
            [InlineKeyboardButton("💬 অ্যাডমিন হোয়াটসঅ্যাপ", url='https://wa.me/message/ZFPUNOUHWSWRI1')],
            [InlineKeyboardButton("🏠 প্রধান মেনু", callback_data='main_menu')]
        ]
        await query.message.edit_text(
            "💳 **ব্যালেন্স রিচার্জ করার পদ্ধতি:**\n\n"
            "বিকাশ / নগদ (Personal): `01911198221`\n\n"
            "টাকা পাঠানোর পর পেমেন্ট স্ক্রিনশট এবং **User ID (`" + str(user_id) + "`)** হোয়াটসঅ্যাপে দিন।",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )

    elif query.data == 'cat_menu':
        keyboard = [
            [InlineKeyboardButton("🔥 সকল সার্ভিস তালিকা", callback_data='show_all_services')],
            [InlineKeyboardButton("🏠 প্রধান মেনু", callback_data='main_menu')]
        ]
        await query.message.edit_text("📋 **সার্ভিস দেখতে নিচের বোতামে চাপ দিন:**", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == 'show_all_services':
        services = fetch_smm_services()
        if not services:
            keyboard = [[InlineKeyboardButton("🔄 পুনরায় চেষ্টা করুন", callback_data='show_all_services'),
                         InlineKeyboardButton("🏠 প্রধান মেনু", callback_data='main_menu')]]
            await query.message.edit_text("⚠️ সার্ভিস লোড হচ্ছে না! নিশ্চিত করুন Railway-তে API Key ও URL সঠিকভাবে যুক্ত করেছেন।", reply_markup=InlineKeyboardMarkup(keyboard))
            return

        keyboard = []
        for s in services[:12]:
            s_id = s.get('service')
            orig_rate = float(s.get('rate', 0))
            final_rate = orig_rate + 10.0
            btn_text = f"🔹 {s.get('name')[:28]} - ৳{final_rate:.1f}"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"buy_{s_id}_{final_rate:.1f}")])

        keyboard.append([InlineKeyboardButton("🏠 প্রধান মেনু", callback_data='main_menu')])
        await query.message.edit_text("⚡ **আমাদের এভেইলএবল সার্ভিসসমূহ:**", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data.startswith('buy_'):
        parts = query.data.split('_')
        s_id = parts[1]
        price = parts[2]
        keyboard = [
            [InlineKeyboardButton("🔙 সার্ভিসসমূহ", callback_data='show_all_services')],
            [InlineKeyboardButton("🏠 প্রধান মেনু", callback_data='main_menu')]
        ]
        await query.message.edit_text(
            f"🛒 **অর্ডার করার তথ্য:**\n\n"
            f"🆔 সার্ভিস ID: `{s_id}`\n"
            f"💵 মূল্য: ৳{price} BDT (প্রতি ১০০০)\n\n"
            f"অর্ডার দিতে চ্যাটে টাইপ করে পাঠান:\n"
            f"`/order {s_id} <লিংক> <পরিমাণ>`",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )

    elif query.data == 'admin_panel':
        if user_id != ADMIN_ID: return
        keyboard = [
            [InlineKeyboardButton("➕ ইউজার ব্যালেন্স যুক্ত করুন", callback_data='adm_start_addbal')],
            [InlineKeyboardButton("📊 মোট ইউজার সংখ্যা", callback_data='adm_stats')],
            [InlineKeyboardButton("🏠 প্রধান মেনু", callback_data='main_menu')]
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
    
    keyboard = [[InlineKeyboardButton("❌ বাতিল করুন", callback_data='admin_panel')]]
    await query.message.edit_text("👤 **ধাপ ১/২:** যে ইউজারের একাউন্টে টাকা দেবেন তার **User ID** লিখে পাঠান:", reply_markup=InlineKeyboardMarkup(keyboard))
    return ADD_BAL_USER

async def adm_get_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['target_user'] = int(update.message.text.strip())
        keyboard = [[InlineKeyboardButton("❌ বাতিল করুন", callback_data='admin_panel')]]
        await update.message.reply_text("💵 **ধাপ ২/২:** টাকার পরিমাণ (Amount) লিখে পাঠান:", reply_markup=InlineKeyboardMarkup(keyboard))
        return ADD_BAL_AMOUNT
    except ValueError:
        await update.message.reply_text("⚠️ ভুল ID। শুধুমাত্র সংখ্যা দিয়ে আইডি লিখুন:")
        return ADD_BAL_USER

async def adm_get_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip())
        target_user = context.user_data['target_user']
        update_user_balance(target_user, amount)
        
        keyboard = [[InlineKeyboardButton("⚙️ অ্যাডমিন প্যানেল", callback_data='admin_panel'),
                     InlineKeyboardButton("🏠 প্রধান মেনু", callback_data='main_menu')]]
        await update.message.reply_text(
            f"✅ **ব্যালেন্স যুক্ত হয়েছে!**\n\n👤 User ID: `{target_user}`\n💰 যুক্ত করা হয়েছে: ৳{amount} BDT",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("⚠️ সঠিক টাকার পরিমাণ লিখুন।")
        return ADD_BAL_AMOUNT

if __name__ == '__main__':
    if not TELEGRAM_BOT_TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN Missing!")
    else:
        app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
        
        # Fixed PTBUserWarning by adding per_message=False
        bal_handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(adm_start_addbal, pattern='^adm_start_addbal$')],
            states={
                ADD_BAL_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_get_user)],
                ADD_BAL_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_get_amount)],
            },
            fallbacks=[CallbackQueryHandler(button_handler, pattern='^(admin_panel|main_menu)$')],
            per_message=False
        )
        
        app.add_handler(CommandHandler("start", start))
        app.add_handler(bal_handler)
        app.add_handler(CallbackQueryHandler(button_handler))
        
        print("🤖 Bot Running Successfully...")
        app.run_polling()
