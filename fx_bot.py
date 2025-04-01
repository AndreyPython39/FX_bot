import logging
from logging.handlers import TimedRotatingFileHandler
import os
from datetime import datetime
import requests
import os
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from xml.etree import ElementTree as ET
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler
import asyncio
from playwright.async_api import async_playwright

# Создаем директорию для логов если её нет
if not os.path.exists('logs'):
    os.makedirs('logs')

# Настраиваем логирование
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Форматтер для логов
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Хендлер для файла
current_time = datetime.now().strftime('%Y%m%d_%H%M%S')
file_handler = logging.FileHandler(f'logs/bot_{current_time}.log', encoding='utf-8')
file_handler.setFormatter(formatter)

# Хендлер для консоли
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)

# Добавляем хендлеры к логгеру
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# --- DATA COLLECTION ---

def get_parser():
    """Get the best available parser."""
    try:
        import lxml
        return 'lxml'
    except ImportError:
        return 'html.parser'

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
    """Получает курс с Investing.com используя Playwright"""
    urls = {
        'USD/RUB': 'https://www.investing.com/currencies/usd-rub',
        'CNY/RUB': 'https://www.investing.com/currencies/cny-rub',
        'USD/CNY': 'https://www.investing.com/currencies/usd-cny',
    }
    
    if pair not in urls:
        logger.error(f"Pair {pair} not supported by Investing.com")
        return None
    
    try:
        url = urls[pair]
        logger.info(f"Fetching {pair} from Investing.com URL: {url}")
        
        async with async_playwright() as p:
            # Запускаем браузер
            browser = await p.firefox.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            
            try:
                # Переходим на страницу с меньшим таймаутом
                await page.goto(url, wait_until='domcontentloaded', timeout=10000)
                
                # Ждем загрузки цены с меньшим таймаутом
                await page.wait_for_selector('div[data-test="instrument-price-last"]', timeout=5000)
                
                # Получаем элемент с ценой через JavaScript
                price_text = await page.evaluate('''() => {
                    const el = document.querySelector('div[data-test="instrument-price-last"]');
                    return el ? el.textContent.trim() : null;
                }''')
                
                if price_text:
                    logger.info(f"Found price text: {price_text}")
                    # Очищаем текст от всего кроме цифр, точек и запятых
                    price_text = ''.join(c for c in price_text if c.isdigit() or c in '.,')
                    rate = float(price_text.replace(',', '.'))
                    logger.info(f"Successfully parsed rate {rate} from {price_text}")
                    return rate
                else:
                    logger.error(f"Could not find price element for {pair} on Investing.com")
                    return None
                    
            except Exception as e:
                logger.error(f"Error while processing page for {pair} on Investing.com: {e}")
                return None
                
            finally:
                await browser.close()
            
    except Exception as e:
        logger.error(f"Critical error fetching Investing.com rate for {pair}: {e}")
        return None

async def get_profinance_rate(pair: str):
    """Получает курс с ProFinance используя Playwright"""
    urls = {
        'USD/RUB': 'https://www.profinance.ru/chart/usdrub/',
        'CNY/RUB': 'https://www.profinance.ru/chart/cnyrub/',
    }
    
    if pair not in urls:
        logger.error(f"Pair {pair} not supported by ProFinance")
        return None
        
    try:
        url = urls[pair]
        logger.info(f"Fetching {pair} from ProFinance URL: {url}")
        
        async with async_playwright() as p:
            # Запускаем браузер с меньшим таймаутом
            browser = await p.firefox.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            
            try:
                # Переходим на страницу с меньшим таймаутом
                await page.goto(url, wait_until='domcontentloaded', timeout=10000)
                
                # Пробуем разные селекторы с меньшим таймаутом
                selectors = [
                    '#last', 
                    '.price-large',
                    '.price',
                    '.last',
                    '#last_last'
                ]
                
                for selector in selectors:
                    try:
                        logger.info(f"Trying selector {selector}")
                        # Ждем появления элемента не более 5 секунд
                        await page.wait_for_selector(selector, timeout=5000)
                        
                        # Получаем цену через JavaScript
                        price_text = await page.evaluate(f'''() => {{
                            const elements = document.querySelectorAll('{selector}');
                            for (const el of elements) {{
                                const text = el.textContent.trim();
                                if (/^[0-9.,]+$/.test(text)) {{
                                    return text;
                                }}
                            }}
                            return null;
                        }}''')
                        
                        if price_text:
                            logger.info(f"Found price text with selector {selector}: {price_text}")
                            # Очищаем текст от всего кроме цифр, точек и запятых
                            price_text = ''.join(c for c in price_text if c.isdigit() or c in '.,')
                            rate = float(price_text.replace(',', '.'))
                            logger.info(f"Successfully parsed rate {rate} from {price_text}")
                            return rate
                            
                    except Exception as e:
                        logger.info(f"Selector {selector} failed: {e}")
                        continue
                
                logger.error(f"Could not find valid price element for {pair} on ProFinance")
                return None
                    
            except Exception as e:
                logger.error(f"Error while processing page for {pair} on ProFinance: {e}")
                return None
                
            finally:
                await browser.close()
            
    except Exception as e:
        logger.error(f"Critical error fetching ProFinance rate for {pair}: {e}")
        return None

# --- BOT LOGIC ---

def format_rate(rate: float) -> str:
    return f"{rate:.4f}" if rate else "N/A"

async def compare(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message or update.callback_query.message
    chat_id = message.chat_id
    logger.info(f"Received /compare command from chat_id: {chat_id}")
    
    status_message = await message.reply_text("Fetching FX data, please wait... 🕒")

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

        # Create keyboard with Compare button
        keyboard = [[InlineKeyboardButton("Compare Again 🔄", callback_data='compare')]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Delete the "please wait" message
        await status_message.delete()
        
        # Send the response with the new Compare button
        await message.reply_markdown(response, reply_markup=reply_markup)
        logger.info(f"Successfully sent comparison for chat_id: {chat_id}")

    except Exception as e:
        error_msg = f"Error in compare command: {str(e)}"
        logger.error(error_msg)
        await status_message.edit_text("Sorry, an error occurred while fetching data. Please try again later.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    logger.info(f"New user started the bot. Chat ID: {chat_id}")
    
    # Create keyboard with Compare button
    keyboard = [[InlineKeyboardButton("Compare Rates 💱", callback_data='compare')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    welcome_text = (
        "Welcome to FX Rate Comparator Bot! \U0001F4B5\n\n"
        "Click the button below or use /compare to get the current USD/RUB rate differences from:\n"
        "- CBR (Central Bank of Russia)\n"
        "- Investing.com\n"
        "- Profinance.ru\n"
        "- Calculated Cross-Rate (via USD/CNY and CNY/RUB)\n\n"
        "I'll help you find arbitrage opportunities! \U0001F52C"
    )
    await update.message.reply_text(welcome_text, reply_markup=reply_markup)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button presses."""
    query = update.callback_query
    await query.answer()  # Answer the callback query to remove the loading state

    if query.data == 'compare':
        # Remove the inline keyboard
        await query.message.edit_reply_markup(reply_markup=None)
        # Call the compare function
        await compare(query, context)

def main():
    load_dotenv()  # Load environment variables from .env file
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    
    if not TOKEN:
        logger.error("No TELEGRAM_BOT_TOKEN found in environment variables!")
        return

    logger.info("Starting the bot...")
    app = ApplicationBuilder().token(TOKEN).build()

    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("compare", compare))
    app.add_handler(CallbackQueryHandler(button))  # Add handler for button presses

    logger.info("Bot is ready to handle requests")
    app.run_polling(timeout=60)

if __name__ == '__main__':
    main()
