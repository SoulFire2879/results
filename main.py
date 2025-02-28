import os
import logging
import requests
import random
import asyncio
from datetime import timedelta
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from tenacity import retry, stop_after_attempt, wait_exponential
import pytz
from quart import Quart, jsonify

# Configuration
CBSE_DOMAINS = [
    "https://cbseresults.nic.in/",
    "https://results.cbse.gov.in/",
    "https://cbse.gov.in/cbsenew/results.html"
]

BOT_TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 5000))
WEBHOOK_URL = f"{os.environ.get('RENDER_EXTERNAL_URL')}/webhook"

CHECK_INTERVAL = 300  # 5 minutes
FAST_CHECK_INTERVAL = 30  # 30 seconds
MAX_RETRIES = 5
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
]

logging.basicConfig(level=logging.INFO,
                   format="%(asctime)s - %(levelname)s - %(message)s")

# Create Quart app for health checks
quart_app = Quart(__name__)

class ResultMonitor:
    def __init__(self):
        self.chat_id = None
        self.consecutive_fails = 0
        self.scheduler = AsyncIOScheduler(timezone=pytz.UTC)
        self.tg_app = Application.builder().token(BOT_TOKEN).build()

    async def init(self):
        await self.setup_handlers()
        self.setup_scheduler()
        await self.tg_app.initialize()
        await self.tg_app.start()
        await self.tg_app.updater.start_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path="webhook",
            webhook_url=WEBHOOK_URL
        )

    async def get_chat_id(self, update: Update):
        if not self.chat_id:
            self.chat_id = update.effective_chat.id
            logging.info(f"Registered Chat ID: {self.chat_id}")

    async def start(self, update: Update, context: CallbackContext):
        await self.get_chat_id(update)
        await update.message.reply_text("CBSE Result Bot Active! Monitoring every 5 minutes.")

    async def check_alive(self, update: Update, context: CallbackContext):
        await self.get_chat_id(update)
        await update.message.reply_text("✅ Bot is running! Last check: " + 
                                      self.scheduler.get_job("result_check").next_run_time.strftime("%Y-%m-%d %H:%M:%S UTC"))

    async def send_alert(self, message: str):
        if self.chat_id:
            try:
                await self.tg_app.bot.send_message(chat_id=self.chat_id, text=message)
                logging.info("Alert sent successfully")
            except Exception as e:
                logging.error(f"Failed to send alert: {e}")

    @retry(wait=wait_exponential(multiplier=1, min=10, max=60), stop=stop_after_attempt(MAX_RETRIES))
    def fetch_page(self, url: str):
        headers = {"User-Agent": random.choice(USER_AGENTS)}
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        return response.text

    def find_results_url(self):
        for domain in CBSE_DOMAINS:
            try:
                soup = BeautifulSoup(self.fetch_page(domain), "html.parser")
                for link in soup.find_all("a", href=True):
                    href = link["href"].lower()
                    if any(kw in href for kw in ["cbse12thlogin", "class12", "senior secondary"]):
                        return href if href.startswith("http") else f"{domain.rstrip('/')}/{href.lstrip('/')}"
            except Exception as e:
                logging.error(f"Domain {domain} failed: {e}")
        return None

    async def check_results_job(self):
        try:
            results_url = self.find_results_url()
            if not results_url:
                return

            content = self.fetch_page(results_url)
            if any(kw in content.lower() for kw in ["roll number", "application number", "result"]):
                await self.send_alert(f"🚨 CBSE 12th Results 2025 ARE LIVE!\n{results_url}")
                self.consecutive_fails = 0
                self.scheduler.remove_job("result_check")
            else:
                self.consecutive_fails += 1

            interval = FAST_CHECK_INTERVAL if self.consecutive_fails >= 3 else CHECK_INTERVAL
            self.scheduler.modify_job("result_check", trigger="interval", seconds=interval)

        except Exception as e:
            logging.error(f"Check failed: {e}")
            self.consecutive_fails += 1

    def setup_scheduler(self):
        self.scheduler.add_job(
            self.check_results_job,
            "interval",
            seconds=CHECK_INTERVAL,
            id="result_check",
            max_instances=1
        )
        self.scheduler.start()
        logging.info("Scheduler started")

    async def setup_handlers(self):
        self.tg_app.add_handler(CommandHandler("start", self.start))
        self.tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.check_alive))

@quart_app.route('/')
async def health_check():
    return jsonify({"status": "ok", "service": "CBSE Result Bot"}), 200

async def main():
    # Start Quart server for health checks
    quart_task = asyncio.create_task(quart_app.run_task(host='0.0.0.0', port=PORT))
    
    # Initialize bot
    monitor = ResultMonitor()
    await monitor.init()
    
    # Keep running
    while True:
        await asyncio.sleep(3600)  # Keep event loop alive

if __name__ == "__main__":
    asyncio.run(main())
