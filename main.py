import os
import sys
import subprocess
import logging
import asyncio
import random
import requests
import pytz
from bs4 import BeautifulSoup
from tenacity import retry, wait_exponential, stop_after_attempt
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
from telegram.error import TelegramError
from aiohttp import web

# Ensure required package is installed
try:
    import telegram
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "python-telegram-bot==20.0"])

# Configuration
CBSE_DOMAINS = [
    "https://cbseresults.nic.in/",
    "https://results.cbse.gov.in/",
    "https://cbse.gov.in/cbsenew/results.html"
]

BOT_TOKEN = os.environ.get("token", "").strip()
if not BOT_TOKEN:
    raise ValueError("Telegram Bot Token is missing! Set it as an environment variable.")

WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").strip()  # Public HTTPS URL without trailing slash
if not WEBHOOK_URL:
    raise ValueError("WEBHOOK_URL environment variable is not set!")

CHECK_INTERVAL = 300
FAST_CHECK_INTERVAL = 30
MAX_RETRIES = 5
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
]

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")
CHAT_ID = None  # Will be set when a user interacts with the bot

# Telegram handlers
async def start(update: Update, context: CallbackContext):
    global CHAT_ID
    # Save chat id for alerting
    CHAT_ID = update.message.chat.id
    await update.message.reply_text("Bot is alive! Send 'hi' to check.")

async def check_alive(update: Update, context: CallbackContext):
    if update.message.text.lower() == "hi":
        await update.message.reply_text("I'm alive!")

async def send_telegram_alert(app: Application, message):
    if CHAT_ID is None:
        logging.critical("CHAT_ID is None, cannot send message!")
        return
    try:
        await app.bot.send_message(chat_id=CHAT_ID, text=message)
        logging.info("Telegram alert sent.")
    except TelegramError as e:
        logging.error("Telegram send error: %s", e)

@retry(wait=wait_exponential(multiplier=1, min=10, max=60),
       stop=stop_after_attempt(MAX_RETRIES))
def fetch_page(url):
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    response = requests.get(url.strip(), headers=headers, timeout=10)
    response.raise_for_status()
    return response.text

def get_latest_results_url():
    for domain in CBSE_DOMAINS:
        try:
            html = fetch_page(domain.strip())
            soup = BeautifulSoup(html, "html.parser")
            for link in soup.find_all("a", href=True):
                href = link["href"].strip()
                if "CBSE12thLogin" in href or "class12" in href or "Senior Secondary" in link.text:
                    if not href.startswith("http"):
                        href = domain.rstrip("/") + "/" + href.lstrip("/")
                    logging.info("Found potential results URL: %s", href)
                    return href
        except Exception as e:
            logging.error("Error processing domain %s: %s", domain, e)
    logging.critical("Could not determine a valid results URL from any domain.")
    return None

async def check_results(app: Application):
    results_url = get_latest_results_url()
    if not results_url:
        return False
    logging.info("Using results URL: %s", results_url)
    try:
        html = fetch_page(results_url)
        if "Roll Number" in html or "Application Number" in html or "Result" in html:
            await send_telegram_alert(app, f"🚨 CBSE 12th Results are LIVE! Check: {results_url}")
            return True
    except Exception as e:
        logging.error("Error checking results on %s: %s", results_url, e)
    return False

async def main_loop(app: Application):
    consecutive_fails = 0
    while True:
        try:
            if await check_results(app):
                logging.info("Results found! Exiting loop.")
                break
        except Exception as e:
            logging.critical("Unexpected error in main loop: %s", e)
        interval = CHECK_INTERVAL if consecutive_fails < 3 else FAST_CHECK_INTERVAL
        await asyncio.sleep(interval)
        consecutive_fails = (consecutive_fails + 1) if not await check_results(app) else 0

# Create the Telegram Application (bot)
telegram_app = Application.builder().token(BOT_TOKEN).build()
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, check_alive))

# Aiohttp handler for incoming webhook updates from Telegram
async def webhook_handler(request: web.Request):
    try:
        data = await request.json()
    except Exception as e:
        logging.error("Error parsing JSON: %s", e)
        return web.Response(status=400)
    update = Update.de_json(data, telegram_app.bot)
    asyncio.create_task(telegram_app.process_update(update))
    return web.Response(status=200)

# Create the aiohttp web application and register the webhook route
aio_app = web.Application()
aio_app.router.add_post(f"/{BOT_TOKEN}", webhook_handler)

# on_startup: register the webhook and start background tasks
async def on_startup(app):
    webhook_url = f"{WEBHOOK_URL}/{BOT_TOKEN}"
    await telegram_app.bot.set_webhook(webhook_url)
    logging.info("Webhook set to: %s", webhook_url)
    # Do not call get_updates; rely on webhook mode.
    asyncio.create_task(main_loop(telegram_app))

# on_shutdown: delete the webhook
async def on_shutdown(app):
    await telegram_app.bot.delete_webhook()
    logging.info("Webhook deleted")

aio_app.on_startup.append(on_startup)
aio_app.on_shutdown.append(on_shutdown)

# Export the aiohttp app as "app" for Gunicorn deployment
app = aio_app

# For local testing:
if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
