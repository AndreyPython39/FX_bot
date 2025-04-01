import logging
from logging.handlers import RotatingFileHandler
import requests
import os
from datetime import datetime
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from xml.etree import ElementTree as ET
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# Configure logging
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log_file = 'bot.log'
log_handler = RotatingFileHandler(log_file, maxBytes=1024*1024, backupCount=5)
log_handler.setFormatter(log_formatter)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(log_handler)

console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
logger.addHandler(console_handler)

# --- DATA COLLECTION ---

async def get_cbr_rates():
    url = 'https://www.cbr.ru/scripts/XML_daily.asp'
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        tree = ET.fromstring(response.content)
        result = {}
        for valute in tree.findall('Valute'):
            char_code = valute.find('CharCode').text
            value = float(valute.find('Value').text.replace(',', '.'))
            nominal = int(valute.find('Nominal').text)
            result[char_code] = value / nominal
        logger.info(f"Successfully fetched CBR rates: {result}")
        return result
    except Exception as e:
        logger.error(f"Error fetching CBR rates: {e}")
        return None

async def get_investing_rate(pair: str):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    urls = {
        'USD/RUB': 'https://www.investing.com/currencies/usd-rub',
        'CNY/RUB': 'https://www.investing.com/currencies/cny-rub',
        'USD/CNY': 'https://www.investing.com/currencies/usd-cny',
    }
    try:
        url = urls[pair]
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'lxml')
        tag = soup.select_one('div[data-test="instrument-price-last"]')
        if tag:
            rate = float(tag.text.replace(',', ''))
            logger.info(f"Successfully fetched {pair} rate from Investing.com: {rate}")
            return rate
    except Exception as e:
        logger.error(f"Error fetching Investing.com rate for {pair}: {e}")
    return None

async def get_profinance_rate(pair: str):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    urls = {
        'USD/RUB': 'https://www.profinance.ru/chart/usdrub/',
        'CNY/RUB': 'https://www.profinance.ru/chart/cnyrub/',
    }
    try:
        url = urls[pair]
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'lxml')
        tag = soup.select_one('span.price')
        if tag:
            rate = float(tag.text.strip())
            logger.info(f"Successfully fetched {pair} rate from Profinance: {rate}")
            return rate
    except Exception as e:
        logger.error(f"Error fetching Profinance rate for {pair}: {e}")
    return None

# --- BOT LOGIC ---

def format_rate(rate: float) -> str:
    return f"{rate:.4f}" if rate else "N/A"

async def compare(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    logger.info(f"Received /compare command from chat_id: {chat_id}")
    
    await update.message.reply_text("Fetching FX data, please wait... 🕒")

    try:
        # Fetch all rates
        cbr = await get_cbr_rates()
        usd_rub_cbr = cbr.get('USD') if cbr else None
        cny_rub_cbr = cbr.get('CNY') if cbr else None

        usd_rub_investing = await get_investing_rate('USD/RUB')
        cny_rub_investing = await get_investing_rate('CNY/RUB')
        usd_cny_investing = await get_investing_rate('USD/CNY')

        usd_rub_profinance = await get_profinance_rate('USD/RUB')
        cny_rub_profinance = await get_profinance_rate('CNY/RUB')

        # Calculate cross rate
        usd_rub_cross = usd_cny_investing * cny_rub_investing if usd_cny_investing and cny_rub_investing else None

        # Prepare response
        response = "\U0001F4B1 *FX Rate Comparison*\n\n"
        response += "*USD/RUB Rates:*\n"
        response += f"CBR: {format_rate(usd_rub_cbr)}\n"
        response += f"Investing: {format_rate(usd_rub_investing)}\n"
        response += f"Profinance: {format_rate(usd_rub_profinance)}\n"
        
        response += "\n*CNY/RUB Rates:*\n"
        response += f"CBR: {format_rate(cny_rub_cbr)}\n"
        response += f"Investing: {format_rate(cny_rub_investing)}\n"
        response += f"Profinance: {format_rate(cny_rub_profinance)}\n"

        response += f"\n*Cross Rates:*\n"
        response += f"USD/CNY: {format_rate(usd_cny_investing)}\n"
        response += f"Calculated USD/RUB: {format_rate(usd_rub_cross)}\n"

        # Arbitrage analysis
        if usd_rub_cross and usd_rub_investing:
            delta = usd_rub_cross - usd_rub_investing
            emoji = '\u2B06\uFE0F' if delta > 0 else '\u2B07\uFE0F'
            response += f"\n*Cross vs Direct:* {delta:+.4f} {emoji}"

            if abs(delta) > 0.3:
                response += "\n\n\u26A1 *Arbitrage opportunity detected!*"
                response += f"\nBuy: {'USD/RUB' if delta < 0 else 'USD/CNY + CNY/RUB'}"
                response += f"\nSell: {'USD/CNY + CNY/RUB' if delta < 0 else 'USD/RUB'}"
                response += f"\nPotential profit: {abs(delta):.4f} RUB per USD"

        await update.message.reply_markdown(response)
        logger.info(f"Successfully sent comparison for chat_id: {chat_id}")

    except Exception as e:
        error_msg = f"Error in compare command: {str(e)}"
        logger.error(error_msg)
        await update.message.reply_text("Sorry, an error occurred while fetching data. Please try again later.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    logger.info(f"New user started the bot. Chat ID: {chat_id}")
    
    welcome_text = (
        "Welcome to FX Rate Comparator Bot! \U0001F4B5\n\n"
        "Use /compare to get the current USD/RUB rate differences from:\n"
        "- CBR (Central Bank of Russia)\n"
        "- Investing.com\n"
        "- Profinance.ru\n"
        "- Calculated Cross-Rate (via USD/CNY and CNY/RUB)\n\n"
        "I'll help you find arbitrage opportunities! \U0001F52C"
    )
    await update.message.reply_text(welcome_text)

# --- MAIN ---

def main():
    load_dotenv()  # Load environment variables from .env file
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    
    if not TOKEN:
        logger.error("No TELEGRAM_BOT_TOKEN found in environment variables!")
        return

    logger.info("Starting the bot...")
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("compare", compare))

    logger.info("Bot is ready to handle requests")
    app.run_polling(timeout=60)

if __name__ == '__main__':
    main()
