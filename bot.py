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

# === Google Sheets Auth ===
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]
creds_dict = json.loads(os.environ.get("GOOGLE_CREDS_JSON", "{}"))
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
gc = gspread.authorize(creds)
# табличка
sheet = gc.open(config.SHEET_NAME).worksheet(config.SHEET_TAB_NAME)

# бот
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

PLAYLIST_TIMEOUT = getattr(config, 'PLAYLIST_TIMEOUT', 60)

async def fetch_playlist(url: str, session: aiohttp.ClientSession) -> tuple[str, str] | None:
    try:
        async with session.get(url, timeout=PLAYLIST_TIMEOUT) as resp:
            text = await resp.text()
            # debug
            logger.info(f"Fetched {url}: {len(text)} chars")
            # имя файла по URL
            base = url.rstrip('/').split('/')[-1].split('?')[0] or 'playlist'
            filename = f"{base}.m3u"
            return filename, text
    except Exception as e:
        logger.error(f"Ошибка при загрузке {url}: {e}")
        return None

@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "Привет! Прогружаю плейлисты напрямую. Используй /playlist."
    )

@dp.message(Command("playlist"))
async def get_playlists(message: types.Message):
    await message.answer("⏳ Загружаю плейлисты...")
    urls = [u.strip() for u in sheet.col_values(2)[1:] if u.strip().startswith(('http://','https://'))]
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_playlist(u, session) for u in urls]
        results = await asyncio.gather(*tasks)
    valid = [r for r in results if r]
    if not valid:
        await message.answer("❌ Не удалось получить ни одного плейлиста.")
        return
    for filename, content in valid:
        await message.answer_document(BufferedInputFile(content.encode('utf-8'), filename), caption=filename)
        await asyncio.sleep(0.3)
    await message.answer(f"Готово: отправлено {len(valid)}/{len(urls)} плейлистов.")

# health-check и запуск
async def health_check(request): return web.Response(text="ok")
async def start_web_app():
    app = web.Application()
    app.add_routes([web.get('/', health_check)])
    return app

async def main():
    app = await start_web_app()
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get('PORT', 5000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
