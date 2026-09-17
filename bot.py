import os
import logging
import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# .env ফাইল থেকে তথ্য লোড করা
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SMM_API_KEY = os.getenv("SMM_API_KEY")
SMM_API_URL = os.getenv("SMM_API_URL", "https://socialpanel.pro/api/v2")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "🚀 **SMM Panel Telegram Bot-এ স্বাগতম!**\n\n"
        "সহজেই যেকোনো সোশ্যাল মিডিয়া সার্ভিস অর্ডার করতে নিচের কমান্ডটি ব্যবহার করুন:\n\n"
        "📌 **কমান্ড ফরম্যাট:**\n"
        "`/order <Service_ID> <Link> <Quantity>`\n\n"
        "💡 **উদাহরণ:**\n"
        "`/order 123 https://instagram.com/username 1000`"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

async def place_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            await update.message.reply_text(f"✅ **অর্ডার সফল হয়েছে!**\n\n📦 **Order ID:** `{response['order']}`", parse_mode="Markdown")
        else:
            error_msg = response.get('error', 'অজানা কোনো সমস্যা হয়েছে।')
            await update.message.reply_text(f"❌ **অর্ডার ব্যর্থ হয়েছে!**\n\n⚠️ **কারণ:** {error_msg}")

    except Exception as e:
        await update.message.reply_text("❌ সার্ভারে কোনো সমস্যা হয়েছে। অনুগ্রহ করে কিছুক্ষণ পর আবার চেষ্টা করুন।")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("order", place_order))
    print("🤖 SMM Bot is running successfully...")
    app.run_polling()
