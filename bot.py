import logging
import random
import os
import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters

# টোকেন এখানে বসান (হোস্টিং এ Environment Variable হিসেবে দিবেন)
TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# ডাটাবেস সেটআপ
conn = sqlite3.connect('bot_data.db', check_same_thread=False)
c = conn.cursor()
c.execute('CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, balance INTEGER)')
conn.commit()

# বর্তমান ক্যাপচা সংরক্ষণের জন্য ডিকশনারি (মেমোরি)
current_captcha = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    c.execute("INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, ?)", (user_id, 0))
    conn.commit()
    
    keyboard = [
        [InlineKeyboardButton("Earn 💰", callback_data='earn')],
        [InlineKeyboardButton("Balance 👤", callback_data='bal')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Welcome! Click Earn to start solving captcha.", reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'earn':
        captcha_text = str(random.randint(10000, 99999))
        current_captcha[query.from_user.id] = captcha_text
        
        # নোট: এখানে আসল ইমেজ জেনারেট করার কোড বসাতে হবে (PIL লাইব্রেরি ব্যবহার করে)।
        # আপাতত শুধু টেক্সট পাঠানো হচ্ছে যাতে লজিকটা কাজ করে।
        await query.message.reply_text(f"Type this captcha: {captcha_text}")
        
    elif query.data == 'bal':
        user_id = query.from_user.id
        c.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        balance = c.fetchone()[0]
        await query.message.reply_text(f"Your balance: {balance} BDT")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    
    if user_id in current_captcha:
        if text == current_captcha[user_id]:
            c.execute("UPDATE users SET balance = balance + 2 WHERE user_id = ?", (user_id,))
            conn.commit()
            await update.message.reply_text("Correct! You earned 2 BDT. Click Earn again.")
            del current_captcha[user_id]
        else:
            await update.message.reply_text("Wrong! Try again.")

if __name__ == '__main__':
    application = ApplicationBuilder().token(TOKEN).build()
    
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    print("Bot is running...")
    application.run_polling()
