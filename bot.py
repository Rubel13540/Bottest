import os
import io
import logging
import random
import sqlite3
import string
from PIL import Image, ImageDraw, ImageFont
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters

# Railway Environment Variable থেকে টোকেন নেওয়া
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

# মেমরিতে ক্যাপচা কোড রাখার ডিকশনারি
current_captcha = {}

# আকর্ষণীয় ক্যাপচা ছবি তৈরি করার ফংশন
def generate_captcha_image(code: str):
    width, height = 220, 80
    image = Image.new('RGB', (width, height), color=(240, 240, 245))
    draw = ImageDraw.Draw(image)

    # ব্যাকগ্রাউন্ডে এলোমেলো লাইন ও ডট দিয়ে ডিজাইন সুন্দর করা
    for _ in range(8):
        x1 = random.randint(0, width)
        y1 = random.randint(0, height)
        x2 = random.randint(0, width)
        y2 = random.randint(0, height)
        draw.line((x1, y1, x2, y2), fill=(random.randint(150, 200), random.randint(150, 200), random.randint(150, 200)), width=2)

    for _ in range(150):
        x = random.randint(0, width)
        y = random.randint(0, height)
        draw.point((x, y), fill=(random.randint(100, 180), random.randint(100, 180), random.randint(100, 180)))

    # লেখা আঁকা
    try:
        font = ImageFont.truetype("arial.ttf", 36)
    except IOError:
        font = ImageFont.load_default()

    for i, char in enumerate(code):
        x = 25 + i * 32 + random.randint(-3, 3)
        y = random.randint(15, 25)
        color = (random.randint(0, 150), random.randint(0, 150), random.randint(0, 150))
        draw.text((x, y), char, fill=color, font=font)

    # ছবি বাইটে রূপান্তর
    bio = io.BytesIO()
    bio.name = 'captcha.png'
    image.save(bio, 'PNG')
    bio.seek(0)
    return bio

# ক্যাপচা পাঠাবে এমন একটি সাহায্যকারী ফংশন
async def send_new_captcha(update_or_query, user_id):
    captcha_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
    current_captcha[user_id] = captcha_code

    image_bytes = generate_captcha_image(captcha_code)
    
    keyboard = [[InlineKeyboardButton("🔄 New Captcha", callback_data='new_captcha')]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    caption_text = "✨ **Solve this Captcha to Earn 2 BDT!**\n\nType the 5 characters shown in the image below:"

    if hasattr(update_or_query, 'message'):
        await update_or_query.message.reply_photo(photo=image_bytes, caption=caption_text, parse_mode="Markdown", reply_markup=reply_markup)
    else:
        await update_or_query.message.reply_photo(photo=image_bytes, caption=caption_text, parse_mode="Markdown", reply_markup=reply_markup)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    c.execute("INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, ?)", (user_id, 0))
    conn.commit()
    
    # মেনু কিবোর্ড
    main_menu = [
        [KeyboardButton("Account 🆔"), KeyboardButton("Earn 💸")],
        [KeyboardButton("Referral 📣"), KeyboardButton("Payment ✅")],
        [KeyboardButton("Support 👩‍💻"), KeyboardButton("Group 👥")]
    ]
    reply_markup = ReplyKeyboardMarkup(main_menu, resize_keyboard=True)
    
    await update.message.reply_text("Welcome! Select an option from the menu below:", reply_markup=reply_markup)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    # ক্যাপচা মেলানোর লজিক
    if user_id in current_captcha:
        correct_code = current_captcha[user_id]
        if text.upper() == correct_code.upper():
            c.execute("UPDATE users SET balance = balance + 2 WHERE user_id = ?", (user_id,))
            conn.commit()
            
            c.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
            new_bal = c.fetchone()[0]
            
            del current_captcha[user_id]
            await update.message.reply_text(f"✅ **Correct Answer!**\n🎉 You earned **2 BDT**.\n💰 Total Balance: **{new_bal} BDT**\n\nClick **Earn 💸** to solve another.")
            return
        else:
            await update.message.reply_text("❌ **Wrong Captcha!** Please try again carefully.")
            return

    # মেনু বাটন হ্যান্ডেলিং
    if text == "Payment ✅":
        payment_keyboard = [
            [InlineKeyboardButton("bKash", callback_data='pay_bkash'), InlineKeyboardButton("Nagad", callback_data='pay_nagad')],
            [InlineKeyboardButton("USDT", callback_data='pay_usdt')]
        ]
        reply_markup = InlineKeyboardMarkup(payment_keyboard)
        await update.message.reply_text("Select Payment Method ♻️", reply_markup=reply_markup)

    elif text == "Earn 💸":
        await send_new_captcha(update, user_id)

    elif text == "Account 🆔":
        c.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        balance = c.fetchone()[0]
        await update.message.reply_text(f"👤 **User ID:** `{user_id}`\n💰 **Current Balance:** {balance} BDT", parse_mode="Markdown")

    elif text == "Referral 📣":
        await update.message.reply_text(f"📢 **Refer & Earn:**\nShare your link to get bonus:\nhttps://t.me/{context.bot.username}?start={user_id}")

    elif text == "Support 👩‍💻":
        await update.message.reply_text(" Contact admin for support: @YourAdminUsername")

    elif text == "Group 👥":
        await update.message.reply_text("👥 Join our official channel: https://t.me/YourGroupLink")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'new_captcha':
        await send_new_captcha(query, query.from_user.id)
    elif query.data == 'pay_bkash':
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
    
