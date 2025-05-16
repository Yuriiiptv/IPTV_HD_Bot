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

# Открываем нужный лист
sheet = gc.open(config.SHEET_NAME).worksheet(config.SHEET_TAB_NAME)

# Инициализация бота
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

def is_playlist_valid(lines: list[str]) -> bool:
    return (
        bool(lines)
        and lines[0].strip().lower().startswith("#extm3u")
        and any(line.strip().lower().startswith("#extinf") for line in lines)
    )

async def download_playlist(url: str, session: aiohttp.ClientSession) -> tuple[str, str] | None:
    """Скачивает и проверяет плейлист без фильтрации"""
    try:
        async with session.get(url, timeout=15) as resp:
            if resp.status != 200:
                return None

            content = await resp.text()
            lines = content.splitlines()

            if not is_playlist_valid(lines):
                return None

            # Генерация имени файла
            parts = url.rstrip("/").split("/")
            folder = parts[-2] if len(parts) >= 2 else ""
            base = parts[-1].split("?")[0]
            name = f"{folder}_{base}" if folder else base

            return name, content

    except Exception as e:
        logger.error(f"Ошибка при загрузке {url}: {e}")
        return None

@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "Привет! Я проверю плейлисты из Google Sheets и пришлю те, которые корректны.\n"
        "Команда: /playlist"
    )

@dp.message(Command("playlist"))
async def get_playlists(message: types.Message):
    await message.answer("🔍 Проверяю плейлисты...")

    urls = sheet.col_values(2)[1:]  # вторая колонка, без заголовка
    urls = [u.strip() for u in urls if u.strip().startswith(("http://", "https://"))]

    async with aiohttp.ClientSession() as session:
        tasks = [download_playlist(url, session) for url in urls]
        results = await asyncio.gather(*tasks)
        valid_playlists = [res for res in results if res]

    if not valid_playlists:
        return await message.answer("❌ Не найдено валидных плейлистов.")

    for name, content in valid_playlists:
        file = BufferedInputFile(content.encode("utf-8"), filename=name)
        await message.answer_document(file, caption=f"✅ {name}")
        await asyncio.sleep(1)

# Health-check
async def health_check(request):
    return web.Response(text="Bot is alive!")

async def start_web_app():
    app = web.Application()
    app.add_routes([web.get("/", health_check)])
    return app

async def main():
    web_app = await start_web_app()
    runner = web.AppRunner(web_app)
    await runner.setup()
    port = int(os.environ.get('PORT', 5000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
