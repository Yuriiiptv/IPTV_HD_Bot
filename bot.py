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

# Таймауты и проверки
PLAYLIST_TIMEOUT = config.PLAYLIST_TIMEOUT if hasattr(config, 'PLAYLIST_TIMEOUT') else 60  # сек
STREAM_TIMEOUT = config.STREAM_TIMEOUT if hasattr(config, 'STREAM_TIMEOUT') else 10      # сек
SAMPLE_SIZE = getattr(config, 'SAMPLE_SIZE', 3)
MIN_ALIVE = getattr(config, 'MIN_ALIVE', 1)

# Проверка базового формата плейлиста

def is_playlist_valid(lines: list[str]) -> bool:
    return (
        bool(lines)
        and lines[0].strip().lower().startswith("#extm3u")
        and any(line.strip().lower().startswith("#extinf") for line in lines)
    )

async def process_playlist(url: str, session: aiohttp.ClientSession) -> tuple[str, str] | None:
    try:
        # загрузка плейлиста
        async with session.get(url, timeout=PLAYLIST_TIMEOUT) as resp:
            if resp.status != 200:
                logger.info(f"{url} вернул статус {resp.status}")
                return None
            content = await resp.text()
        lines = content.splitlines()

        # базовая валидация
        if not is_playlist_valid(lines):
            logger.info(f"{url} не является корректным M3U")
            return None

        # фильтрация нужных каналов
        filtered = ["#EXTM3U"]
        streams = []
        for i, line in enumerate(lines):
            if line.lower().startswith("#extinf") and i + 1 < len(lines):
                info_line = line
                stream_url = lines[i + 1].strip()
                if any(key.lower() in info_line.lower() for key in config.WANTED_CHANNELS):
                    filtered.append(info_line)
                    filtered.append(stream_url)
                    streams.append(stream_url)

        # если нашли нужные каналы, сразу возвращаем без дополнительной проверки
        if streams:
            name = url.rstrip('/').split('/')[-1].split('?')[0]
            filename = f"filtered_{name}.m3u"
            return filename, '\n'.join(filtered)

        # иначе возвращаем оригинальный плейлист
        name = url.rstrip('/').split('/')[-1].split('?')[0]
        filename = f"full_{name}.m3u"
        return filename, content

    except Exception as e:
        logger.error(f"Ошибка обработки {url}: {e}")
        return None

@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "Привет! Я собираю рабочие M3U-плейлисты с каналами из вашей подписки.\n"
        "Используйте /playlist, чтобы получить готовые файлы."
    )

@dp.message(Command("playlist"))
async def get_playlists(message: types.Message):
    await message.answer("⏳ Идёт обработка плейлистов...")
    urls = [u.strip() for u in sheet.col_values(2)[1:] if u.strip().startswith(("http://","https://"))]
    valid = []
    async with aiohttp.ClientSession() as session:
        tasks = [process_playlist(u, session) for u in urls]
        results = await asyncio.gather(*tasks)
        valid = [r for r in results if r]

    if not valid:
        return await message.answer("❌ Не найдено рабочих плейлистов с нужными каналами.")

    for filename, content in valid:
        file = BufferedInputFile(content.encode('utf-8'), filename=filename)
        await message.answer_document(file, caption=f"✅ {filename}")
        await asyncio.sleep(1)

    await message.answer(f"🎉 Готово! Отправлено {len(valid)}/{len(urls)} плейлистов.")

# Health-check и запуск сервиса
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
