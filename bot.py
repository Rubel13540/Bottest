import os
import logging
import random
import asyncio
import aiohttp
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is not set!")

PROXY_LIST = [
    "31.59.20.176:6754:fgeflkrz:iil3n7dfwc2s",
    "45.38.107.97:6014:fgeflkrz:iil3n7dfwc2s",
    "198.105.121.200:6462:fgeflkrz:iil3n7dfwc2s",
    "64.137.96.74:6641:fgeflkrz:iil3n7dfwc2s",
    "198.23.243.226:6361:fgeflkrz:iil3n7dfwc2s",
    "38.154.185.97:6370:fgeflkrz:iil3n7dfwc2s",
    "84.247.60.125:6095:fgeflkrz:iil3n7dfwc2s",
    "142.111.67.146:5611:fgeflkrz:iil3n7dfwc2s",
    "191.96.254.138:6185:fgeflkrz:iil3n7dfwc2s",
    "31.58.9.4:6077:fgeflkrz:iil3n7dfwc2s"
]

user_sessions = {}

def format_proxy(proxy_str):
    try:
        ip, port, username, password = proxy_str.split(":")
        return f"socks5://{username}:{password}@{ip}:{port}"
    except:
        return None

def get_random_proxy():
    proxy_str = random.choice(PROXY_LIST)
    return format_proxy(proxy_str)

async def visit_website(url, proxy, visit_number, total):
    headers = {
        'User-Agent': random.choice([
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'
        ]),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Connection': 'keep-alive'
    }
    
    try:
        proxy_url = f"http://{proxy.split('@')[1]}" if '@' in proxy else proxy
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, proxy=proxy_url, timeout=10) as response:
                if response.status == 200:
                    logger.info(f"✅ Visit {visit_number}/{total}: Success")
                    return True, response.status
                else:
                    logger.warning(f"⚠️ Visit {visit_number}/{total}: Status {response.status}")
                    return False, response.status
    except Exception as e:
        logger.error(f"❌ Visit {visit_number}/{total}: Error - {str(e)}")
        return False, str(e)

async def start_visit_task(user_id, url, total_visits=100):
    user_sessions[user_id] = {
        'url': url,
        'status': 'running',
        'count': 0,
        'total': total_visits,
        'success': 0,
        'failed': 0,
        'errors': []
    }
    
    for i in range(1, total_visits + 1):
        if user_sessions.get(user_id, {}).get('status') == 'stopped':
            user_sessions[user_id]['status'] = 'stopped_by_user'
            return
        
        proxy = get_random_proxy()
        if not proxy:
            proxy = get_random_proxy()
        
        success, result = await visit_website(url, proxy, i, total_visits)
        
        user_sessions[user_id]['count'] = i
        if success:
            user_sessions[user_id]['success'] += 1
        else:
            user_sessions[user_id]['failed'] += 1
            user_sessions[user_id]['errors'].append(f"Visit {i}: {result}")
        
        if i % 10 == 0:
            logger.info(f"📊 Progress: {i}/{total_visits}")
        
        await asyncio.sleep(5)
        
        if i < total_visits:
            await asyncio.sleep(5)
    
    user_sessions[user_id]['status'] = 'completed'

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_sessions[user_id] = {'status': 'idle'}
    await update.message.reply_text(
        "🤖 **Website Visit Automation Bot**\n\n"
        "📌 **How to use:**\n"
        "1️⃣ Send /visit\n"
        "2️⃣ Send website link\n"
        "3️⃣ Bot will visit 100 times\n"
        "4️⃣ Each visit waits 5 seconds\n"
        "5️⃣ Different IP each time\n\n"
        "📊 **Commands:**\n"
        "/visit - Start new visit\n"
        "/status - Check progress\n"
        "/stop - Stop visiting\n"
        "/help - Show help",
        parse_mode="Markdown"
    )

async def visit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id in user_sessions and user_sessions[user_id].get('status') == 'running':
        await update.message.reply_text(
            "⚠️ **A visit is already running!**\n"
            f"Progress: {user_sessions[user_id].get('count', 0)}/{user_sessions[user_id].get('total', 100)}\n"
            "Use /stop to cancel",
            parse_mode="Markdown"
        )
        return
    
    user_sessions[user_id] = {'status': 'waiting_for_url'}
    await update.message.reply_text(
        "🔗 **Send website link:**\n"
        "Example: `https://example.com`\n\n"
        "❌ Cancel with /cancel",
        parse_mode="Markdown"
    )

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    url = update.message.text.strip()
    
    if user_id not in user_sessions or user_sessions[user_id].get('status') != 'waiting_for_url':
        await update.message.reply_text(
            "⚠️ First send /visit, then send URL",
            parse_mode="Markdown"
        )
        return
    
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    await update.message.reply_text(
        f"🚀 **Starting visit...**\n"
        f"🔗 URL: `{url}`\n"
        f"📊 Total: 100 visits\n"
        f"⏱️ Each visit waits 5 seconds\n"
        f"🔄 Different IP each time\n\n"
        f"⏳ Takes ~8.3 minutes\n"
        f"Check /status for progress",
        parse_mode="Markdown"
    )
    
    asyncio.create_task(start_visit_task(user_id, url, 100))

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in user_sessions:
        await update.message.reply_text(
            "ℹ️ No visit started. Send /visit",
            parse_mode="Markdown"
        )
        return
    
    session = user_sessions[user_id]
    status = session.get('status', 'unknown')
    
    if status == 'running':
        count = session.get('count', 0)
        total = session.get('total', 100)
        success = session.get('success', 0)
        failed = session.get('failed', 0)
        
        progress = int((count / total) * 100)
        bar = '█' * (progress // 5) + '░' * (20 - (progress // 5))
        
        await update.message.reply_text(
            f"🔄 **Visit running...**\n\n"
            f"📊 Progress: `{count}/{total}` ({progress}%)\n"
            f"[{bar}]\n"
            f"✅ Success: {success}\n"
            f"❌ Failed: {failed}\n"
            f"⏱️ Remaining: ~{(total - count) * 10 // 60} min\n\n"
            f"🛑 Stop with /stop",
            parse_mode="Markdown"
        )
    elif status == 'completed':
        success = session.get('success', 0)
        failed = session.get('failed', 0)
        
        await update.message.reply_text(
            f"✅ **Visit completed!**\n\n"
            f"📊 Total: 100\n"
            f"✅ Success: {success}\n"
            f"❌ Failed: {failed}\n"
            f"📈 Success rate: {int((success/100)*100)}%",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            f"ℹ️ Status: {status}",
            parse_mode="Markdown"
        )

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in user_sessions or user_sessions[user_id].get('status') != 'running':
        await update.message.reply_text(
            "ℹ️ No visit is currently running",
            parse_mode="Markdown"
        )
        return
    
    user_sessions[user_id]['status'] = 'stopped'
    await update.message.reply_text(
        "🛑 **Stopping visit...**",
        parse_mode="Markdown"
    )

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id in user_sessions and user_sessions[user_id].get('status') == 'waiting_for_url':
        user_sessions[user_id]['status'] = 'idle'
        await update.message.reply_text(
            "❌ **URL input cancelled**",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "ℹ️ No active URL input",
            parse_mode="Markdown"
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 **All Commands:**\n\n"
        "/start - Start bot\n"
        "/visit - Start new visit\n"
        "/status - Check progress\n"
        "/stop - Stop visit\n"
        "/cancel - Cancel URL input\n"
        "/help - Show help\n\n"
        "🔧 **How it works:**\n"
        "1. Send /visit\n"
        "2. Send website link\n"
        "3. Bot visits 100 times\n"
        "4. Each visit waits 5 seconds\n"
        "5. Different proxy each time\n"
        "6. Notifies when complete\n\n"
        "⏱️ **Time:** ~8.3 minutes",
        parse_mode="Markdown"
    )

def main():
    proxy = get_random_proxy()
    print(f"🔄 Using proxy: {proxy}")
    print(f"📊 Total proxies: {len(PROXY_LIST)}")
    
    try:
        application = ApplicationBuilder() \
            .token(BOT_TOKEN) \
            .proxy(proxy) \
            .get_updates_proxy(proxy) \
            .build()
        
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("visit", visit_command))
        application.add_handler(CommandHandler("status", status_command))
        application.add_handler(CommandHandler("stop", stop_command))
        application.add_handler(CommandHandler("cancel", cancel_command))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
        
        print("🤖 Bot starting...")
        application.run_polling()
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    main()
