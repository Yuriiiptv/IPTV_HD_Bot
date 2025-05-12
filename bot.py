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
    """Проверка валидности плейлиста"""
    return bool(lines) and lines[0].strip().startswith("#EXTM3U") and any(line.strip().startswith("#EXTINF") for line in lines)

async def process_playlist(url: str, session: aiohttp.ClientSession) -> tuple[str, str] | None:
    """Обработка одного плейлиста с надёжной фильтрацией нужных каналов"""
    try:
        async with session.get(url, timeout=15) as resp:
            if resp.status != 200:
                return None

            content = await resp.text()
            lines = content.splitlines()

            if not is_playlist_valid(lines):
                return None

            valid_entries = []
            seen_titles = set()

            for i, line in enumerate(lines):
                if line.startswith("#EXTINF"):
                    title = line.split(',', 1)[-1].strip()
                    # Ищем вхождение любого из нужных каналов
                    if any(w.lower() in title.lower() for w in config.WANTED_CHANNELS) and title not in seen_titles:
                        # Проверяем следующий URL
                        if i + 1 < len(lines) and lines[i + 1].startswith(('http://', 'https://')):
                            stream_url = lines[i + 1]
                            try:
                                async with session.get(stream_url, timeout=10) as channel_resp:
                                    if channel_resp.status == 200:
                                        chunk = await channel_resp.content.read(512)
                                        if chunk:
                                            valid_entries.extend([line, stream_url])
                                            seen_titles.add(title)
                            except Exception as e:
                                logger.warning(f"Ошибка потока {stream_url}: {e}")

            if not valid_entries:
                return None

            playlist_name = url.split('/')[-1].split('?')[0] or "playlist.m3u"
            return playlist_name, "\n".join(["#EXTM3U"] + valid_entries)

    except Exception as e:
        logger.error(f"Ошибка обработки {url}: {e}")
        return None

@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "Привет! Я могу собрать для тебя плейлисты из федеральных телеканалов.\n"
        "Используй команду /playlist — и я пришлю готовые .m3u файлы."
    )

@dp.message(Command("playlist"))
async def get_playlists(message: types.Message):
    try:
        await message.answer("⏳ Загружаю и проверяю плейлисты...")

        urls = sheet.col_values(2)[1:]
        async with aiohttp.ClientSession() as session:
            tasks = [process_playlist(url.strip(), session) for url in urls if url.strip()]
            results = await asyncio.gather(*tasks)
            valid_playlists = [res for res in results if res]

        if not valid_playlists:
            return await message.answer("❌ Не найдено валидных плейлистов")

        success_count = 0
        for name, content in valid_playlists:
            try:
                file = BufferedInputFile(content.encode('utf-8'), filename=name)
                await message.answer_document(file, caption=f"✅ {name}")
                success_count += 1
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Ошибка отправки {name}: {e}")

        await message.answer(f"🎉 Готово! Успешно обработано плейлистов: {success_count}/{len(valid_playlists)}")

    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        await message.answer("⚠️ Произошла внутренняя ошибка. Попробуйте позже.")

# Health-check и запуск
async def health_check(request):
    return web.Response(text="Bot is alive!")

async def start_web_app():
    app = web.Application()
    app.add_routes([web.get('/', health_check)])
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
