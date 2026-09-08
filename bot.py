import os
import io
import logging
import random
import sqlite3
import string
from PIL import Image, ImageDraw, ImageFont
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters

TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# ডাটাবেস সেটআপ (সাকসেস, ফেইল ও পেমেন্ট হিস্ট্রি সহ)
conn = sqlite3.connect('bot_data.db', check_same_thread=False)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS users 
             (user_id INTEGER PRIMARY KEY, balance INTEGER, total_tasks INTEGER, success_tasks INTEGER, failed_tasks INTEGER)''')
conn.commit()

current_captcha = {}
user_states = {}  # ইউজার কোন ধাপে আছে তা ট্র্যাক করতে

# ক্যাপচা পিকচার জেনারেটর
def generate_captcha_image(code: str):
    width, height = 220, 80
    image = Image.new('RGB', (width, height), color=(240, 240, 245))
    draw = ImageDraw.Draw(image)

    for _ in range(8):
        x1, y1 = random.randint(0, width), random.randint(0, height)
        x2, y2 = random.randint(0, width), random.randint(0, height)
        draw.line((x1, y1, x2, y2), fill=(random.randint(150, 200), random.randint(150, 200), random.randint(150, 200)), width=2)

    for _ in range(150):
        x, y = random.randint(0, width), random.randint(0, height)
        draw.point((x, y), fill=(random.randint(100, 180), random.randint(100, 180), random.randint(100, 180)))

    try:
        font = ImageFont.truetype("arial.ttf", 36)
    except IOError:
        font = ImageFont.load_default()

    for i, char in enumerate(code):
        x = 25 + i * 32 + random.randint(-3, 3)
        y = random.randint(15, 25)
        color = (random.randint(0, 150), random.randint(0, 150), random.randint(0, 150))
        draw.text((x, y), char, fill=color, font=font)

    bio = io.BytesIO()
    bio.name = 'captcha.png'
    image.save(bio, 'PNG')
    bio.seek(0)
    return bio

async def send_new_captcha(update_or_query, user_id):
    captcha_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
    current_captcha[user_id] = captcha_code

    image_bytes = generate_captcha_image(captcha_code)
    keyboard = [[InlineKeyboardButton("🔄 New Captcha", callback_data='new_captcha')]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    caption_text = "✨ **Solve this Captcha to Earn 2 BDT!**\n\nType the 5 characters shown below:"
    
    if hasattr(update_or_query, 'message'):
        await update_or_query.message.reply_photo(photo=image_bytes, caption=caption_text, parse_mode="Markdown", reply_markup=reply_markup)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    c.execute("INSERT OR IGNORE INTO users (user_id, balance, total_tasks, success_tasks, failed_tasks) VALUES (?, 0, 0, 0, 0)", (user_id,))
    conn.commit()
    
    main_menu = [
        [KeyboardButton("Account 🆔"), KeyboardButton("Earn 💸")],
        [KeyboardButton("Referral 📣"), KeyboardButton("Withdraw 💳")],
        [KeyboardButton("Support 👩‍💻"), KeyboardButton("Group 👥")]
    ]
    reply_markup = ReplyKeyboardMarkup(main_menu, resize_keyboard=True)
    await update.message.reply_text("Welcome! Select an option from the menu below:", reply_markup=reply_markup)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # ১. উইথড্র প্রসেসিং (মোবাইল নম্বর দেওয়া)
    if user_states.get(user_id) == 'WAITING_FOR_NUMBER':
        user_states[user_id] = {'method': user_states[user_id]['method'], 'number': text, 'step': 'WAITING_FOR_AMOUNT'}
        await update.message.reply_text("🔢 Enter the amount you want to withdraw (BDT):")
        return

    # ২. উইথড্র প্রসেসিং (টাকার পরিমাণ দেওয়া)
    elif isinstance(user_states.get(user_id), dict) and user_states[user_id].get('step') == 'WAITING_FOR_AMOUNT':
        try:
            amount = int(text)
            c.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
            balance = c.fetchone()[0]

            if amount > balance:
                await update.message.reply_text(f"❌ **Insufficient Balance!**\nYour current balance is {balance} BDT.")
            elif amount < 50:  # সর্বনিম্ন উইথড্র লিমিট ৫০ টাকা
                await update.message.reply_text("❌ Minimum withdrawal amount is 50 BDT.")
            else:
                c.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, user_id))
                conn.commit()
                method = user_states[user_id]['method']
                num = user_states[user_id]['number']
                
                await update.message.reply_text(f"✅ **Withdrawal Request Submitted!**\n\n📌 **Method:** {method}\n📱 **Number:** `{num}`\n💰 **Amount:** {amount} BDT\n\nStatus: Pending (Admin will approve soon).", parse_mode="Markdown")
                del user_states[user_id]
        except ValueError:
            await update.message.reply_text("❌ Invalid amount. Please enter numbers only.")
        return

    # ৩. মেনু বাটন হ্যান্ডেলিং (এখন আর ভুল করে ক্যাপচা ধরবে না)
    if text == "Withdraw 💳" or text == "Payment ✅":
        payment_keyboard = [
            [InlineKeyboardButton("bKash", callback_data='pay_bkash'), InlineKeyboardButton("Nagad", callback_data='pay_nagad')],
            [InlineKeyboardButton("USDT", callback_data='pay_usdt')]
        ]
        reply_markup = InlineKeyboardMarkup(payment_keyboard)
        await update.message.reply_text("Select Withdrawal Method ♻️", reply_markup=reply_markup)

    elif text == "Earn 💸":
        await send_new_captcha(update, user_id)

    elif text == "Account 🆔":
        c.execute("SELECT balance, total_tasks, success_tasks, failed_tasks FROM users WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        balance, total, success, failed = row[0], row[1], row[2], row[3]
        
        # সাকসেস পার্সেন্টেজ হিসাব
        accuracy = (success / total * 100) if total > 0 else 0

        account_info = (
            f"👤 **Account Dashboard**\n\n"
            f"🆔 **User ID:** `{user_id}`\n"
            f"💰 **Balance:** {balance} BDT\n\n"
            f"📊 **Work Statistics:**\n"
            f"🔹 **Total Tasks:** {total}\n"
            f"✅ **Successful:** {success}\n"
            f"❌ **Failed:** {failed}\n"
            f"📈 **Accuracy Rate:** {accuracy:.1f}%\n"
        )
        await update.message.reply_text(account_info, parse_mode="Markdown")

    elif text == "Referral 📣":
        await update.message.reply_text(f"📢 **Refer & Earn:**\nShare your link to get bonus:\nhttps://t.me/{context.bot.username}?start={user_id}")

    elif text == "Support 👩‍💻":
        await update.message.reply_text("Contact admin for support: @YourAdminUsername")

    elif text == "Group 👥":
        await update.message.reply_text("👥 Join our official group: https://t.me/YourGroupLink")

    # ৪. ক্যাপচা মেলানোর কাজ
    elif user_id in current_captcha:
        correct_code = current_captcha[user_id]
        c.execute("UPDATE users SET total_tasks = total_tasks + 1 WHERE user_id = ?", (user_id,))
        
        if text.upper() == correct_code.upper():
            c.execute("UPDATE users SET balance = balance + 2, success_tasks = success_tasks + 1 WHERE user_id = ?", (user_id,))
            conn.commit()
            
            c.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
            new_bal = c.fetchone()[0]
            
            del current_captcha[user_id]
            await update.message.reply_text(f"✅ **Correct Answer!**\n🎉 You earned **2 BDT**.\n💰 Total Balance: **{new_bal} BDT**\n\nClick **Earn 💸** for next captcha.")
        else:
            c.execute("UPDATE users SET failed_tasks = failed_tasks + 1 WHERE user_id = ?", (user_id,))
            conn.commit()
            await update.message.reply_text("❌ **Wrong Captcha!** Please try again carefully.")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if query.data == 'new_captcha':
        await send_new_captcha(query, user_id)
    elif query.data in ['pay_bkash', 'pay_nagad', 'pay_usdt']:
        method_map = {'pay_bkash': 'bKash', 'pay_nagad': 'Nagad', 'pay_usdt': 'USDT'}
        selected_method = method_map[query.data]
        
        user_states[user_id] = {'method': selected_method, 'step': 'WAITING_FOR_NUMBER'}
        await query.message.reply_text(f" Selected **{selected_method}**.\n\n📱 Please type your **Personal Account/Mobile Number**:")

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
