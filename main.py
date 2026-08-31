import os
import logging
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
from dotenv import load_dotenv

# এনভায়রনমেন্ট ভেরিয়েবল লোড করা
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# লগিং সেটআপ
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# কনভারসেশনের ধাপগুলোর জন্য স্টেট (State) ডিফাইন করা
FIRSTNAME, GMAIL, PASSWORD, RECOVERY = range(4)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """বট শুরু করার কমান্ড এবং ফার্স্ট নেম চাওয়া"""
    await update.message.reply_text(
        "হ্যালো! জিমেইল অ্যাকাউন্ট তৈরির প্রসেস শুরু হচ্ছে।\n\nপ্রথমে অ্যাকাউন্টধারীর **First Name** দিন:",
        parse_mode="Markdown"
    )
    return FIRSTNAME

async def get_firstname(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """ফারస్ట్ নেম সেভ করে জিমেইল চাওয়া"""
    context.user_data['firstname'] = update.message.text
    await update.message.reply_text(
        f"ধন্যবাদ! ফার্স্ট নেম নেওয়া হয়েছে: `{context.user_data['firstname']}`\n\nএখন পছন্দসই **Gmail** (username) দিন:",
        parse_mode="Markdown"
    )
    return GMAIL

async def get_gmail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """জিমেইল সেভ করে পাসওয়ার্ড চাওয়া"""
    context.user_data['gmail'] = update.message.text
    await update.message.reply_text(
        "সুন্দর! এখন এই জিমেইলের জন্য একটি শক্তিশালী **Password** দিন:",
        parse_mode="Markdown"
    )
    return PASSWORD

async def get_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """পাসওয়ার্ড সেভ করে রিকভারি জিমেইল চাওয়া"""
    context.user_data['password'] = update.message.text
    await update.message.reply_text(
        "প্রায় শেষ! এখন একটি **Recovery Gmail** দিন:",
        parse_mode="Markdown"
    )
    return RECOVERY

async def get_recovery(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """সব তথ্য সংগ্রহ করে ফাইনাল আউটপুট দেখানো"""
    context.user_data['recovery'] = update.message.text
    
    # ইউজার কী কী দিল তার সামারি তৈরি
    data = context.user_data
    summary = (
        "✅ **সকল তথ্য সফলভাবে সংগৃহীত হয়েছে!**\n\n"
        f"• **First Name:** {data.get('firstname')}\n"
        f"• **Gmail:** {data.get('gmail')}\n"
        f"• **Password:** {data.get('password')}\n"
        f"• **Recovery:** {data.get('recovery')}\n\n"
        "*(অটোমেশন স্ক্রিপ্ট এখানে যুক্ত করা যাবে)*"
    )
    
    await update.message.reply_text(summary, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """প্রক্রিয়া বাতিল করা"""
    await update.message.reply_text(
        "প্রক্রিয়াটি বাতিল করা হয়েছে। আবার শুরু করতে `/start` লিখুন।",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

def main():
    """বট রান করার ফাংশন"""
    app = ApplicationBuilder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            FIRSTNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_firstname)],
            GMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_gmail)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_password)],
            RECOVERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_recovery)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)
    print("বট সফলভাবে চালু হয়েছে...")
    app.run_polling()

if __name__ == "__main__":
    main()
