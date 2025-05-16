import os
import json
import asyncio
import logging

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiohttp import web

import gspread
from oauth2client.service_account import ServiceAccountCredentials
import config

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Google Sheets аутентификация
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]
creds_dict = json.loads(os.environ.get("GOOGLE_CREDS_JSON", "{}"))
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
gc = gspread.authorize(creds)

# Открываем лист с URL
sheet = gc.open(config.SHEET_NAME).worksheet(config.SHEET_TAB_NAME)

# Инициализация бота
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Я просто собираю все уникальные M3U-ссылки из Google Sheets и отправляю их.\n"
        "Используй /playlist чтобы получить ссылки."
    )

@dp.message(Command("playlist"))
async def cmd_playlist(message: types.Message):
    await message.answer("⏳ Сбор ссылок…")
    # Считываем столбец B, начиная со второй строки
    raw_urls = sheet.col_values(2)[1:]
    # Оставляем только валидные http(s) ссылки и убираем дубликаты
    seen = set()
    unique_urls = []
    for u in raw_urls:
        url = u.strip()
        if url and url.startswith(("http://", "https://")) and url not in seen:
            seen.add(url)
            unique_urls.append(url)

    if not unique_urls:
        return await message.answer("❌ В таблице нет ссылок на плейлисты.")

    # Отправляем каждый URL
    for url in unique_urls:
        await message.answer(f"🔗 {url}")
        await asyncio.sleep(0.1)

# Health-check сервис
async def health_check(request):
    return web.Response(text="ok")

async def start_web_app():
    app = web.Application()
    app.add_routes([web.get('/', health_check)])
    return app

async def main():
    # Запускаем web-сервис
    web_app = await start_web_app()
    runner = web.AppRunner(web_app)
    await runner.setup()
    port = int(os.environ.get('PORT', 5000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    # Запускаем бота
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
