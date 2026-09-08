import os
import logging
import random
import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters

# Railway এর Variables থেকে টোকেন নেওয়া হচ্ছে
TOKEN = os.getenv("BOT_TOKEN")

# লগিং সেটআপ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# ডাটাবেস সেটআপ
conn = sqlite3.connect('bot_data.db', check_same_thread=False)
c = conn.cursor()
c.execute('CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, balance INTEGER)')
conn.commit()

current_captcha = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    c.execute("INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, ?)", (user_id, 0))
    conn.commit()
    
    # স্ক্রিনশটের মতো নিচের স্থায়ী কাস্টম কিবোর্ড
    main_menu = [
        [KeyboardButton("Account 🆔"), KeyboardButton("Earn 💸")],
        [KeyboardButton("Referral 📣"), KeyboardButton("Payment ✅")],
        [KeyboardButton("Support 👩‍💻"), KeyboardButton("Group 👥")]
    ]
    reply_markup = ReplyKeyboardMarkup(main_menu, resize_keyboard=True)
    
    await update.message.reply_text("Welcome! Choose an option from the menu below:", reply_markup=reply_markup)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    
    # ক্যাপচা মেলানোর কাজ
    if user_id in current_captcha:
        if text == current_captcha[user_id]:
            c.execute("UPDATE users SET balance = balance + 2 WHERE user_id = ?", (user_id,))
            conn.commit()
            await update.message.reply_text("Correct! You earned 2 BDT. Click Earn again.")
            del current_captcha[user_id]
            return
        else:
            await update.message.reply_text("Wrong captcha! Try again.")
            return

    # স্ক্রিনশটের মতো 'Payment ✅' বাটনে ক্লিক করলে ইনলাইন পেমেন্ট অপশন দেখাবে
    if text == "Payment ✅":
        payment_keyboard = [
            [InlineKeyboardButton("bKash", callback_data='pay_bkash'), InlineKeyboardButton("Nagad", callback_data='pay_nagad')],
            [InlineKeyboardButton("USDT", callback_data='pay_usdt')]
        ]
        reply_markup = InlineKeyboardMarkup(payment_keyboard)
        await update.message.reply_text("Select Payment Method ♻️", reply_markup=reply_markup)

    elif text == "Earn 💸":
        captcha_text = str(random.randint(10000, 99999))
        current_captcha[user_id] = captcha_text
        await update.message.reply_text(f"Type this captcha: {captcha_text}")

    elif text == "Account 🆔":
        c.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        balance = c.fetchone()[0]
        await update.message.reply_text(f"Your User ID: {user_id}\nYour Balance: {balance} BDT")

    elif text == "Referral 📣":
        await update.message.reply_text(f"Your referral link: https://t.me/{context.bot.username}?start={user_id}")

    elif text == "Support 👩‍💻":
        await update.message.reply_text("Contact admin for support: @YourAdminUsername")

    elif text == "Group 👥":
        await update.message.reply_text("Join our official group: https://t.me/YourGroupLink")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # পেমেন্ট মেথড সিলেক্ট করলে কি উত্তর দেবে
    if query.data == 'pay_bkash':
        await query.message.reply_text("Send payment to bKash Number: 017XXXXXXXX")
    elif query.data == 'pay_nagad':
        await query.message.reply_text("Send payment to Nagad Number: 017XXXXXXXX")
    elif query.data == 'pay_usdt':
        await query.message.reply_text("Send USDT (TRC20) to Address: TYourTRC20AddressHere")

if __name__ == '__main__':
    if not TOKEN:
        print("Error: BOT_TOKEN not found in environment variables!")
    else:
        try:
            print("Bot is starting...")
            application = ApplicationBuilder().token(TOKEN).build()
            
            application.add_handler(CommandHandler('start', start))
            application.add_handler(CallbackQueryHandler(button_handler))
            application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
            
            application.run_polling()
        except Exception as e:
            print(f"Error while starting bot: {e}")
