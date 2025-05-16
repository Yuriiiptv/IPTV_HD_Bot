import os
import json
import asyncio
import logging
import random
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

# Таймауты и параметры
PLAYLIST_TIMEOUT = getattr(config, 'PLAYLIST_TIMEOUT', 60)  # сек для загрузки плейлиста
STREAM_TIMEOUT = getattr(config, 'STREAM_TIMEOUT', 10)     # сек для HEAD-запросов
SAMPLE_SIZE = getattr(config, 'SAMPLE_SIZE', 3)
MIN_ALIVE = getattr(config, 'MIN_ALIVE', 1)

async def process_playlist(url: str, session: aiohttp.ClientSession) -> tuple[str, str] | None:
    try:
        async with session.get(url, timeout=PLAYLIST_TIMEOUT) as resp:
            if resp.status != 200:
                logger.warning(f"{url} returned status {resp.status}")
                return None
            content = await resp.text()
        lines = content.splitlines()

        # Если указаны ключевые каналы, фильтруем
        filtered = ["#EXTM3U"]
        streams = []
        for i, line in enumerate(lines):
            if line.lower().startswith("#extinf") and i + 1 < len(lines):
                info_line = line
                stream_url = lines[i + 1].strip()
                if hasattr(config, 'WANTED_CHANNELS') and config.WANTED_CHANNELS:
                    if any(key.lower() in info_line.lower() for key in config.WANTED_CHANNELS):
                        filtered.append(info_line)
                        filtered.append(stream_url)
                        streams.append(stream_url)
        # Возвращаем отфильтрованный, если есть совпадения
        base = url.rstrip('/').split('/')[-1].split('?')[0]
        if streams:
            filename = f"filtered_{base}.m3u"
            return filename, '\n'.join(filtered)
        # Иначе возвращаем полный
        filename = f"full_{base}.m3u"
        return filename, content
    except Exception as e:
        logger.error(f"Error processing {url}: {e}")
        return None

@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "Привет! Я собираю все M3U-плейлисты из вашей таблицы и возвращаю их.\n"
        "Используйте /playlist, чтобы получить файлы."
    )

@dp.message(Command("playlist"))
async def get_playlists(message: types.Message):
    await message.answer("⏳ Обработка плейлистов...")
    urls = [u.strip() for u in sheet.col_values(2)[1:] if u.strip().startswith(("http://","https://"))]
    results = []
    async with aiohttp.ClientSession() as session:
        tasks = [process_playlist(u, session) for u in urls]
        results = await asyncio.gather(*tasks)
    valid = [r for r in results if r]
    if not valid:
        await message.answer("❌ Ни один плейлист не удалось получить.")
        return
    for filename, content in valid:
        file = BufferedInputFile(content.encode('utf-8'), filename=filename)
        await message.answer_document(file, caption=f"✅ {filename}")
        await asyncio.sleep(0.5)
    await message.answer(f"🎉 Готово! Отправлено {len(valid)}/{len(urls)} плейлистов.")

# Запуск веб-сервиса для health-check
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
    port = int(os.environ.get("PORT", 5000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
