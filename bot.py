import os
import json
import asyncio
import logging
import aiohttp

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile
from aiohttp import web

import gspread
from oauth2client.service_account import ServiceAccountCredentials
import config

# Логирование
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

# Хранилище плейлистов и базовый URL
playlist_store: dict[str, str] = {}
BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", os.environ.get("BASE_URL", "https://your-app.com"))

async def process_playlist(url: str, session: aiohttp.ClientSession) -> tuple[str, str] | None:
    """Загружает плейлист и фильтрует по ключам из config.WANTED_CHANNELS"""
    try:
        async with session.get(url, timeout=config.PLAYLIST_TIMEOUT) as resp:
            if resp.status != 200:
                logger.warning(f"{url} returned status {resp.status}")
                return None
            content = await resp.text()
    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
        return None

    lines = content.splitlines()
    filtered = ["#EXTM3U"]
    for i, line in enumerate(lines):
        if line.lower().startswith("#extinf") and i + 1 < len(lines):
            info = line
            link = lines[i+1].strip()
            if any(key.lower() in info.lower() for key in config.WANTED_CHANNELS):
                filtered.append(info)
                filtered.append(link)
    if len(filtered) <= 1:
        # ничего не найдено по ключам — пропускаем
        return None

    base = url.rstrip('/').split('/')[-1].split('?')[0] or 'playlist'
    name = f"filtered_{base}.m3u"
    return name, "\n".join(filtered)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Я собираю M3U-плейлисты из Google Sheets и фильтрую каналы по твоему списку.\n"
        "Используй /playlist, чтобы получить ссылки на готовые файлы."
    )

@dp.message(Command("playlist"))
async def cmd_playlist(message: types.Message):
    await message.answer("⏳ Запрашиваю плейлисты из таблицы...")
    urls = [u.strip() for u in sheet.col_values(2)[1:] if u.strip().startswith(('http://','https://'))]
    if not urls:
        return await message.answer("❌ В таблице нет ссылок на плейлисты.")

    results = []
    async with aiohttp.ClientSession() as session:
        tasks = [process_playlist(u, session) for u in urls]
        fetched = await asyncio.gather(*tasks)
    results = [r for r in fetched if r]
    if not results:
        return await message.answer("❌ Не найдено каналов по заданным ключам.")

    for filename, content in results:
        # сохраняем в память и выдаём ссылку
        playlist_store[filename] = content
        link = f"{BASE_URL}/playlist/{filename}"
        await message.answer(f"✅ Плейлист готов: {link}")
        await asyncio.sleep(0.5)

# Web-сервер для health-check и раздачи плейлистов
async def health(request):
    return web.Response(text="ok")

async def serve_playlist(request):
    name = request.match_info.get('name')
    text = playlist_store.get(name)
    if not text:
        return web.Response(status=404, text="Not found")
    return web.Response(text=text, content_type="application/vnd.apple.mpegurl")

async def start_web_app():
    app = web.Application()
    app.add_routes([
        web.get('/', health),
        web.get('/playlist/{name}', serve_playlist),
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
