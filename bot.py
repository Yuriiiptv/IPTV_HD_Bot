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

# Подключение к Google Sheets
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]
creds_dict = json.loads(os.environ.get("GOOGLE_CREDS_JSON", "{}"))
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
gc = gspread.authorize(creds)
sheet = gc.open(config.SHEET_NAME).worksheet(config.SHEET_TAB_NAME)

# Инициализация бота
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

# In-memory store для сокращённых ссылок
link_store: dict[str, str] = {}
BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", os.environ.get("BASE_URL", "https://your-app.com"))

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Я собираю M3U-ссылки из Google Sheets, укорачиваю их и выдаю короткие ссылки.\n"
        "Используй /links, чтобы получить их."
    )

@dp.message(Command("links"))
async def cmd_links(message: types.Message):
    await message.answer("⏳ Собираю ссылки из таблицы...")
    raw_urls = sheet.col_values(2)[1:]
    urls = [u.strip() for u in raw_urls if u.strip().startswith(('http://','https://'))]
    if not urls:
        return await message.answer("❌ В таблице нет корректных ссылок.")

    # Генерируем короткие ключи
    for idx, url in enumerate(urls, start=1):
        slug = str(idx)
        link_store[slug] = url
        short_link = f"{BASE_URL}/go/{slug}"
        await message.answer(f"🔗 {short_link}")
        await asyncio.sleep(0.1)

# Web-сервер для health-check и редиректа
async def health(request):
    return web.Response(text="ok")

async def redirect_link(request):
    slug = request.match_info.get('slug')
    target = link_store.get(slug)
    if not target:
        return web.Response(status=404, text="Not found")
    raise web.HTTPFound(location=target)

async def start_web_app():
    app = web.Application()
    app.add_routes([
        web.get('/', health),
        web.get('/go/{slug}', redirect_link),
    ])
    return app

async def main():
    web_app = await start_web_app()
    runner = web.AppRunner(web_app)
    await runner.setup()
    port = int(os.environ.get('PORT', 5000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
