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

# Conversation States for Admin Balance Add & Order Process
ADD_BAL_USER, ADD_BAL_AMOUNT = range(2)
ORDER_LINK, ORDER_QTY = range(2, 4)

# Database Helpers
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

# SMM Panel API Fetch
def fetch_smm_services():
    try:
        payload = {'key': SMM_API_KEY, 'action': 'services'}
        response = requests.post(SMM_API_URL, data=payload, timeout=10).json()
        if isinstance(response, list):
            return response
    except Exception as e:
        logging.error(f"API Error: {e}")
    return []

# Start Command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance = get_user_balance(user_id)
    
    keyboard = [
        [InlineKeyboardButton("🛒 সার্ভিসসমূহ ও অর্ডার", callback_data='cat_menu')],
        [InlineKeyboardButton("👤 আমার একাউন্ট", callback_data='profile'),
         InlineKeyboardButton("💳 ব্যালেন্স অ্যাড করুন", callback_data='add_balance')],
        [InlineKeyboardButton("📞 সাপোর্ট (WhatsApp)", url='https://wa.me/message/ZFPUNOUHWSWRI1')]
    ]
    
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("⚙️ অ্যাডমিন কন্ট্রোল প্যানেল", callback_data='admin_panel')])

    reply_markup = InlineKeyboardMarkup(keyboard)
    text = (
        f"🚀 **Social Growth SMM Bot-এ স্বাগতম!**\n\n"
        f"🆔 **আপনার User ID:** `{user_id}`\n"
        f"💰 **বর্তমান ব্যালেন্স:** ৳{balance:.2f} BDT\n\n"
        f"মেসেজ টাইপ করার প্রয়োজন নেই, নিচের বাটনগুলোতে ক্লিক করে ব্যবহার করুন:"
    )
    
    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.callback_query.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    return ConversationHandler.END

# Category & Service Menu
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
            f"👤 **আপনার একাউন্ট তথ্য**\n\n🆔 ID: `{user_id}`\n💰 ব্যালেন্স: ৳{balance:.2f} BDT",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
        
    elif query.data == 'add_balance':
        keyboard = [
            [InlineKeyboardButton("💬 অ্যাডমিন সাপোর্ট (WhatsApp)", url='https://wa.me/message/ZFPUNOUHWSWRI1')],
            [InlineKeyboardButton("🏠 প্রধান মেনু", callback_data='main_menu')]
        ]
        await query.message.edit_text(
            "💳 **টাকা যুক্ত করার নিয়ম:**\n\n"
            "বিকাশ/নগদ (Personal): `01911198221`\n\n"
            "টাকা পাঠানোর পর স্ক্রিনশট ও আপনার **User ID (`" + str(user_id) + "`)** লিখে নিচের WhatsApp লিংকে পাঠান। অ্যাডমিন সাথে সাথে ব্যালেন্স যোগ করে দেবেন।",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )

    elif query.data == 'cat_menu':
        keyboard = [
            [InlineKeyboardButton("🔵 Facebook Services", callback_data='cat_facebook')],
            [InlineKeyboardButton("📸 Instagram Services", callback_data='cat_instagram')],
            [InlineKeyboardButton("🔴 YouTube Services", callback_data='cat_youtube')],
            [InlineKeyboardButton("🎵 TikTok Services", callback_data='cat_tiktok')],
            [InlineKeyboardButton("🏠 প্রধান মেনু", callback_data='main_menu')]
        ]
        await query.message.edit_text("📋 **ক্যাটাগরি বেছে নিন:**", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data.startswith('cat_'):
        cat_name = query.data.split('_')[1].capitalize()
        services = fetch_smm_services()
        
        keyboard = []
        count = 0
        for s in services:
            # ক্যাটাগরি ফিল্টারিং ও ১০ টাকা প্রফিট যুক্ত করা
            if cat_name.lower() in s.get('category', '').lower() or cat_name.lower() in s.get('name', '').lower():
                s_id = s.get('service')
                s_rate = float(s.get('rate', 0)) + 10.0
                btn_text = f"{s.get('name')[:28]}.. (৳{s_rate:.1f}/1k)"
                keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"buy_{s_id}_{s_rate}")])
                count += 1
                if count >= 8: break # ৮টির বেশি বাটন হলে টেলিগ্রাম সুন্দর দেখায় না
                
        if not keyboard:
            keyboard.append([InlineKeyboardButton("⚠️ কোনো সার্ভিস পাওয়া যায়নি", callback_data='cat_menu')])
            
        keyboard.append([InlineKeyboardButton("🔙 ক্যাটাগরি মেনু", callback_data='cat_menu'),
                         InlineKeyboardButton("🏠 প্রধান মেনু", callback_data='main_menu')])
        await query.message.edit_text(f"📦 **{cat_name} সার্ভিসসমূহ (১০টাকা লাভসহ):**", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == 'admin_panel':
        if user_id != ADMIN_ID: return
        keyboard = [
            [InlineKeyboardButton("➕ ইউজার ব্যালেন্স যুক্ত করুন", callback_data='adm_start_addbal')],
            [InlineKeyboardButton("📊 মোট ইউজার সংখ্যা", callback_data='adm_stats')],
            [InlineKeyboardButton("🏠 প্রধান মেনু", callback_data='main_menu')]
        ]
        await query.message.edit_text(
            f"⚙️ **অ্যাডমিন কন্ট্রোল প্যানেল**\n\n👥 মোট নিবন্ধিত ইউজার: **{get_total_users()}** জন",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )

    elif query.data == 'adm_stats':
        keyboard = [[InlineKeyboardButton("🔙 অ্যাডমিন প্যানেল", callback_data='admin_panel')]]
        await query.message.edit_text(f"📊 মোট ইউজার: **{get_total_users()}** জন", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# --- Step-by-Step Admin Add Balance via Buttons ---
async def adm_start_addbal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID: return
    
    keyboard = [[InlineKeyboardButton("❌ বাতিল করুন", callback_data='admin_panel')]]
    await query.message.edit_text("👤 **ধাপ ১/২:** যে ইউজারকে ব্যালেন্স দেবেন তার **User ID** লিখে মেসেজ পাঠান:", reply_markup=InlineKeyboardMarkup(keyboard))
    return ADD_BAL_USER

async def adm_get_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['target_user'] = int(update.message.text.strip())
        keyboard = [[InlineKeyboardButton("❌ বাতিল করুন", callback_data='admin_panel')]]
        await update.message.reply_text("💵 **ধাপ ২/২:** কত টাকা (Amount) যোগ করতে চান সংখ্যায় লিখুন (যেমন: 100):", reply_markup=InlineKeyboardMarkup(keyboard))
        return ADD_BAL_AMOUNT
    except ValueError:
        await update.message.reply_text("⚠️ ভ্যালিড User ID দিন (শুধু সংখ্যা)। আবার চেষ্টা করুন:")
        return ADD_BAL_USER

async def adm_get_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip())
        target_user = context.user_data['target_user']
        update_user_balance(target_user, amount)
        
        keyboard = [[InlineKeyboardButton("🔙 অ্যাডমিন প্যানেল", callback_data='admin_panel'),
                     InlineKeyboardButton("🏠 প্রধান মেনু", callback_data='main_menu')]]
        await update.message.reply_text(
            f"✅ **সফল হয়েছে!**\n\n👤 User ID: `{target_user}`\n💰 যুক্ত হয়েছে: ৳{amount} BDT",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("⚠️ সঠিক টাকার পরিমাণ লিখুন।")
        return ADD_BAL_AMOUNT

async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)
    return ConversationHandler.END

if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Conversation Handler for Admin Add Balance
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
    
    print("🤖 Ultra Advanced SMM Bot Running...")
    app.run_polling()
