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
import re

import config

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === Google Sheets Auth ===
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]
creds_dict = json.loads(os.environ["GOOGLE_CREDS_JSON"])
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
gc = gspread.authorize(creds)

# Открываем нужный лист
sheet = gc.open(config.SHEET_NAME).worksheet(config.SHEET_TAB_NAME)

# Инициализация бота
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

def is_playlist_valid(lines: list[str]) -> bool:
    """Проверка валидности плейлиста по формату"""
    return bool(lines) and lines[0].strip().lower().startswith("#extm3u") and any(line.strip().lower().startswith("#extinf") for line in lines)

async def process_playlist(url: str, session: aiohttp.ClientSession) -> tuple[str, str] | None:
    """Обработка одного плейлиста: проверка формата и фильтрация по нужным каналам.  
    Если нет совпадений по WANTED_CHANNELS, возвращается весь плейлист целиком."""
    try:
        async with session.get(url, timeout=15) as resp:
            if resp.status != 200:
                return None

            content = await resp.text()
            lines = content.splitlines()

            # Проверяем базовый формат
            if not is_playlist_valid(lines):
                return None

            # Фильтрация по WANTED_CHANNELS
            valid_entries = []
            seen_titles = set()
            for i, line in enumerate(lines):
                if line.lower().startswith("#extinf"):
                    # Получаем название канала после первой запятой
                    title = line.split(',', 1)[-1].strip()
                    if any(w.lower() in title.lower() for w in config.WANTED_CHANNELS) and title not in seen_titles:
                        # Проверяем следующий URL
                        if i + 1 < len(lines) and lines[i + 1].startswith(('http://', 'https://')):
                            stream_url = lines[i + 1]
                            try:
                                async with session.get(stream_url, timeout=10) as ch_resp:
                                    if ch_resp.status == 200:
                                        chunk = await ch_resp.content.read(256)
                                        if chunk:
                                            valid_entries.extend([line, stream_url])
                                            seen_titles.add(title)
                            except Exception as e:
                                logger.warning(f"Ошибка потока {stream_url}: {e}")

            # Если фильтрация не дала результатов, возвращаем весь плейлист
            final_lines = (["#EXTM3U"] + valid_entries) if valid_entries else lines
            playlist_name = url.split('/')[-1].split('?')[0] or "playlist.m3u8"
            return playlist_name, "\n".join(final_lines)

    except Exception as e:
        logger.error(f"Ошибка обработки {url}: {e}")
        return None

@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "Привет! Я проверяю плейлисты по формату и собираю нужные каналы.\n"
        "Используй /playlist — и я пришлю файлы, валидные или отфильтрованные."
    )

@dp.message(Command("playlist"))
async def get_playlists(message: types.Message):
    try:
        await message.answer("⏳ Идёт проверка плейлистов...")
        urls = sheet.col_values(2)[1:]
        async with aiohttp.ClientSession() as session:
            tasks = [process_playlist(url.strip(), session) for url in urls if url.strip()]
            results = await asyncio.gather(*tasks)
            valid_playlists = [res for res in results if res]

        if not valid_playlists:
            return await message.answer("❌ Не найдено валидных плейлистов")

        count = 0
        for name, content in valid_playlists:
            file = BufferedInputFile(content.encode('utf-8'), filename=name)
            await message.answer_document(file, caption=f"✅ {name}")
            count += 1
            await asyncio.sleep(1)

        await message.answer(f"🎉 Готово! Отправлено плейлистов: {count}/{len(valid_playlists)}")

    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        await message.answer("⚠️ Внутренняя ошибка. Попробуйте позже.")

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
    port = int(os.environ.get('PORT', 5000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
