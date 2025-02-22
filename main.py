import os
import sys
import subprocess
import logging
import asyncio
import random
import requests
import threading
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bs4 import BeautifulSoup
from tenacity import retry, wait_exponential, stop_after_attempt
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
from telegram.error import TelegramError

# Ensure required package is installed
try:
    import telegram
except ImportError:
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "python-telegram-bot==20.0"])

# Configuration
CBSE_DOMAINS = [
    "https://cbseresults.nic.in/", "https://results.cbse.gov.in/",
    "https://cbse.gov.in/cbsenew/results.html"
]

BOT_TOKEN = os.environ.get("token")
if not BOT_TOKEN:
    raise ValueError(
        "Telegram Bot Token is missing! Set it as an environment variable.")

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
CHAT_ID = None


async def start(update: Update, context: CallbackContext):
    await update.message.reply_text("Bot is alive! Send 'hi' to check.")


async def check_alive(update: Update, context: CallbackContext):
    if update.message.text.lower() == "hi":
        await update.message.reply_text("I'm alive!")


async def get_chat_id(application: Application):
    global CHAT_ID
    updates = await application.bot.get_updates()
    if updates:
        CHAT_ID = updates[-1].message.chat.id
        logging.info("Found Chat ID: %s", CHAT_ID)
    else:
        logging.info("No chat message yet; please send a message to the bot.")


async def send_telegram_alert(application: Application, message):
    if CHAT_ID is None:
        logging.critical("CHAT_ID is None, cannot send message!")
        return
    try:
        await application.bot.send_message(chat_id=CHAT_ID, text=message)
        logging.info("Telegram alert sent.")
    except TelegramError as e:
        logging.error("Telegram send error: %s", e)


@retry(wait=wait_exponential(multiplier=1, min=10, max=60),
       stop=stop_after_attempt(MAX_RETRIES))
def fetch_page(url):
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    response = requests.get(url, headers=headers, timeout=10)
    response.raise_for_status()
    return response.text


def get_latest_results_url():
    for domain in CBSE_DOMAINS:
        try:
            html = fetch_page(domain)
            soup = BeautifulSoup(html, "html.parser")
            for link in soup.find_all("a", href=True):
                href = link["href"]
                if "CBSE12thLogin" in href or "class12" in href or "Senior Secondary" in link.text:
                    if not href.startswith("http"):
                        href = domain.rstrip("/") + "/" + href.lstrip("/")
                    logging.info("Found potential results URL: %s", href)
                    return href
        except Exception as e:
            logging.error("Error processing domain %s: %s", domain, e)
    logging.critical(
        "Could not determine a valid results URL from any domain.")
    return None


async def check_results(application: Application):
    results_url = get_latest_results_url()
    if not results_url:
        return False
    logging.info("Using results URL: %s", results_url)
    try:
        html = fetch_page(results_url)
        if "Roll Number" in html or "Application Number" in html or "Result" in html:
            await send_telegram_alert(
                application,
                f"🚨 CBSE 12th Results are LIVE! Check: {results_url}")
            return True
    except Exception as e:
        logging.error("Error checking results on %s: %s", results_url, e)
    return False


async def main_loop(application: Application):
    consecutive_fails = 0
    while True:
        try:
            if await check_results(application):
                logging.info("Results found! Exiting loop.")
                break
        except Exception as e:
            logging.critical("Unexpected error in main loop: %s", e)
        interval = CHECK_INTERVAL if consecutive_fails < 3 else FAST_CHECK_INTERVAL
        await asyncio.sleep(interval)
        consecutive_fails = (consecutive_fails +
                             1) if not await check_results(application) else 0


def main():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, check_alive))

    scheduler = AsyncIOScheduler(
        timezone=pytz.utc)  # Change 'utc' to your preferred timezone

    application.add_handler(CommandHandler("start", start))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, check_alive))

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(get_chat_id(application))
    application.run_polling()
    logging.info("Starting continuous monitoring of CBSE results...")
    loop.run_until_complete(main_loop(application))
    loop.close()

def startfn(environ,start_response):
    start_response("Active and running",[("Content-Type","text/html")])
    threading.Thread(target=main,daemon=True).start()
    return [b"Running successfully"]

