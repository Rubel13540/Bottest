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
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # আপনার Telegram ID

logging.basicConfig(level=logging.INFO)

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
    cursor.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (user_id, amount))
    conn.commit()
    conn.close()

def get_total_users():
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM users')
    total = cursor.fetchone()[0]
    conn.close()
    return total

# Main Menu Response
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance = get_user_balance(user_id)
    
    # সাধারণ গ্রাহকদের জন্য বাটন
    keyboard = [
        [InlineKeyboardButton("🛒 সার্ভিসসমূহ (Order)", callback_data='services')],
        [InlineKeyboardButton("👤 আমার একাউন্ট (Profile)", callback_data='profile'),
         InlineKeyboardButton("💳 টাকা যোগ করুন (Add Balance)", callback_data='add_balance')],
        [InlineKeyboardButton("📞 সাপোর্ট (Support)", callback_data='support')]
    ]
    
    # আপনি (Admin) ঢুকলে অতিরিক্ত গোপন 'Admin Panel' বাটন যোগ হবে
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("⚙️ অ্যাডমিন কন্ট্রোল প্যানেল", callback_data='admin_panel')])

    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        f"👋 **স্বাগতম SMM Panel Bot-এ!**\n\n"
        f"🆔 **User ID:** `{user_id}`\n"
        f"💰 **বর্তমান ব্যালেন্স:** ৳{balance:.2f} BDT\n\n"
        f"নিচের বাটনগুলো থেকে আপনার প্রয়োজনীয় সার্ভিস বেছে নিন:"
    )
    
    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.callback_query.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")

# Button Clicks Handler
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == 'profile':
        balance = get_user_balance(user_id)
        keyboard = [[InlineKeyboardButton("🔙 প্রধান মেনু", callback_data='main_menu')]]
        await query.message.edit_text(
            f"👤 **আপনার প্রোফাইল**\n\n🆔 ID: `{user_id}`\n💰 ব্যালেন্স: ৳{balance:.2f} BDT",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
    elif query.data == 'add_balance':
        keyboard = [[InlineKeyboardButton("🔙 প্রধান মেনু", callback_data='main_menu')]]
        await query.message.edit_text(
            "💳 **টাকা যোগ করার উপায়:**\n\n"
            "বিকাশ/নগদ Personal: `017XXXXXXXX`\n\n"
            "টাকা পাঠানোর পর আপনার Telegram ID সহ অ্যাডমিনকে মেসেজ দিন।",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
    elif query.data == 'services':
        keyboard = [
            [InlineKeyboardButton("Facebook Followers (৳80/1k)", callback_data='srv_fb_fol')],
            [InlineKeyboardButton("YouTube Views (৳120/1k)", callback_data='srv_yt_views')],
            [InlineKeyboardButton("🔙 প্রধান মেনু", callback_data='main_menu')]
        ]
        await query.message.edit_text("📋 **আমাদের সার্ভিসসমূহ:**", reply_markup=InlineKeyboardMarkup(keyboard))
        
    # --- অ্যাডমিন কন্ট্রোল প্যানেল সেকশন ---
    elif query.data == 'admin_panel':
        if user_id != ADMIN_ID:
            await query.message.edit_text("❌ আপনার এই সেকশনে প্রবেশের অধিকার নেই।")
            return
            
        total_users = get_total_users()
        admin_keyboard = [
            [InlineKeyboardButton("➕ ইউজার ব্যালেন্স যোগ করুন", callback_data='adm_add_bal_guide')],
            [InlineKeyboardButton("📊 মোট ইউজার সংখ্যা", callback_data='adm_stats')],
            [InlineKeyboardButton("🔙 প্রধান মেনু", callback_data='main_menu')]
        ]
        await query.message.edit_text(
            f"⚙️ **অ্যাডমিন কন্ট্রোল প্যানেল**\n\n"
            f"👑 **Admin ID:** `{user_id}`\n"
            f"👥 **মোট ইউজার:** {total_users} জন\n\n"
            f"নিচের বাটন থেকে আপনার প্রয়োজনীয় কাজ সম্পন্ন করুন:",
            reply_markup=InlineKeyboardMarkup(admin_keyboard),
            parse_mode="Markdown"
        )
    elif query.data == 'adm_add_bal_guide':
        admin_keyboard = [[InlineKeyboardButton("🔙 অ্যাডমিন প্যানেল", callback_data='admin_panel')]]
        await query.message.edit_text(
            "💡 **ইউজারকে ব্যালেন্স দেওয়ার নিয়ম:**\n\n"
            "মেসেজ বক্সে গিয়ে টাইপ করুন:\n"
            "`/addbal <USER_ID> <AMOUNT>`\n\n"
            "**উদাহরণ:** `/addbal 123456789 500` (এটি দিলে ঐ ইউজার ৫০০ টাকা পেয়ে যাবে)।",
            reply_markup=InlineKeyboardMarkup(admin_keyboard),
            parse_mode="Markdown"
        )
    elif query.data == 'adm_stats':
        admin_keyboard = [[InlineKeyboardButton("🔙 অ্যাডমিন প্যানেল", callback_data='admin_panel')]]
        total_users = get_total_users()
        await query.message.edit_text(
            f"📊 **বটের আপডেট স্ট্যাটিস্টিকস:**\n\n"
            f"👤 মোট নিবন্ধিত ইউজার: **{total_users}** জন",
            reply_markup=InlineKeyboardMarkup(admin_keyboard),
            parse_mode="Markdown"
        )
    elif query.data == 'main_menu':
        await start(update, context)

# Admin Command: Add Balance
async def add_bal_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ আপনার এই কমান্ড ব্যবহারের অনুমতি নেই।")
        return
    
    try:
        target_id = int(context.args[0])
        amount = float(context.args[1])
        update_user_balance(target_id, amount)
        await update.message.reply_text(f"✅ User ID `{target_id}`-এ ৳{amount} সফলভাবে যোগ করা হয়েছে।")
    except Exception:
        await update.message.reply_text("⚠️ **সঠিক নিয়ম:** `/addbal <USER_ID> <AMOUNT>`")

if __name__ == '__main__':
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN পাওয়া যায়নি!")
    
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addbal", add_bal_admin))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    print("🤖 SMM Bot with Admin Control Panel is running...")
    app.run_polling()
  
