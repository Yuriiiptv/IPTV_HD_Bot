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
    """Проверка базового формата M3U плейлиста"""
    return (
        bool(lines)
        and lines[0].strip().lower().startswith("#extm3u")
        and any(line.strip().lower().startswith("#extinf") for line in lines)
    )

async def process_playlist(url: str, session: aiohttp.ClientSession) -> tuple[str, str] | None:
    """Скачивает M3U, фильтрует каналы по WANTED_CHANNELS и возвращает новый контент"""
    try:
        async with session.get(url, timeout=15) as resp:
            if resp.status != 200:
                return None
            content = await resp.text()
        lines = content.splitlines()
        if not is_playlist_valid(lines):
            return None

        # Фильтрация каналов
        filtered = ["#EXTM3U"]
        for i, line in enumerate(lines):
            if line.lower().startswith("#extinf") and i + 1 < len(lines):
                info = line
                stream_url = lines[i+1]
                if any(key.lower() in info.lower() for key in config.WANTED_CHANNELS):
                    filtered.append(info)
                    filtered.append(stream_url)
        # Если после фильтрации нет каналов, пропускаем
        if len(filtered) <= 1:
            return None

        # Генерация имени файла
        parts = url.rstrip("/").split("/")
        folder = parts[-2] if len(parts) >= 2 else ""
        base = parts[-1].split("?")[0]
        name = f"filtered_{folder}_{base}" if folder else f"filtered_{base}"

        return name, "\n".join(filtered)

    except Exception as e:
        logger.error(f"Ошибка при обработке {url}: {e}")
        return None

@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "Привет! Я проверю плейлисты из Google Sheets, отфильтрую каналы по списку в config.WANTED_CHANNELS "
        "и пришлю тебе только те файлы, где есть нужные каналы. Используй /playlist."
    )

@dp.message(Command("playlist"))
async def get_playlists(message: types.Message):
    await message.answer("⏳ Получаю и фильтрую плейлисты...")
    urls = sheet.col_values(2)[1:]
    urls = [u.strip() for u in urls if u.strip().startswith(("http://","https://"))]
    if not urls:
        return await message.answer("❌ В таблице нет ссылок на плейлисты.")

    results = []
    async with aiohttp.ClientSession() as session:
        tasks = [process_playlist(u, session) for u in urls]
        fetched = await asyncio.gather(*tasks)
    results = [r for r in fetched if r]

    if not results:
        return await message.answer("❌ Не найдено каналов по заданным ключам.")

    for name, content in results:
        file = BufferedInputFile(content.encode('utf-8'), filename=name)
        await message.answer_document(file, caption=f"✅ {name}")
        await asyncio.sleep(1)

# Health-check и запуск веб-сервиса
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
