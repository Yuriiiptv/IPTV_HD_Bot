import os
import json
import asyncio
import logging
import random
import aiohttp

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
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

# In-memory хранилище плейлистов и базовый URL
playlist_store: dict[str, str] = {}
BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", os.environ.get("BASE_URL", "https://your-app.com"))
# Сколько потоков проверяем выборочно
SAMPLE_SIZE = 3


def is_playlist_valid(lines: list[str]) -> bool:
    """Проверка базового формата M3U плейлиста"""
    return (
        bool(lines)
        and lines[0].strip().lower().startswith("#extm3u")
        and any(line.strip().lower().startswith("#extinf") for line in lines)
    )

async def process_playlist(url: str, session: aiohttp.ClientSession) -> tuple[str, str] | None:
    """Загружает M3U, фильтрует по имени и выборочно проверяет SAMPLE_SIZE потоков"""
    try:
        async with session.get(url, timeout=15) as resp:
            if resp.status != 200:
                return None

            content = await resp.text()
            lines = content.splitlines()

            # базовая валидация
            if not is_playlist_valid(lines):
                return None

            # фильтрация по имени каналов
            filtered = ["#EXTM3U"]
            streams = []
            i = 0
            while i < len(lines):
                line = lines[i].strip()
                if line.lower().startswith("#extinf"):
                    _, info = line.split(",", 1) if "," in line else ("", line)
                    stream_url = lines[i+1].strip() if i+1 < len(lines) else ""
                    if any(key.lower() in info.lower() for key in config.WANTED_CHANNELS):
                        filtered.append(lines[i])
                        filtered.append(lines[i+1])
                        streams.append(stream_url)
                    i += 2
                else:
                    i += 1

            # если нет подходящих по имени
            if not streams:
                return None

            # выборка для проверки
            sample_urls = random.sample(streams, min(SAMPLE_SIZE, len(streams)))

                        # проверяем GET-запросом и читаем первые байты
            alive_count = 0
            for s_url in sample_urls:
                try:
                    async with session.get(s_url, timeout=5) as r:
                        if r.status == 200:
                            chunk = await r.content.read(256)
                            if chunk:
                                alive_count += 1
                                break  # достаточно одного живого
                except Exception:
                    pass

            # если ни один поток не живой — пропускаем
            if alive_count == 0:
                return None
                return None

            # собираем контент
            new_content = "\n".join(filtered)
            parts = url.rstrip("/").split("/")
            folder = parts[-2] if len(parts) >= 2 else ""
            base = parts[-1].split("?")[0]
            playlist_name = f"{folder}_{base}" if folder else base
            return playlist_name, new_content

    except Exception as e:
        logger.error(f"Ошибка обработки {url}: {e}")
        return None

@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "Привет! Я проверяю и фильтрую M3U-плейлисты по вашему списку каналов, "
        "а затем выборочно тестирую потоки. Используйте /playlist"
    )

@dp.message(Command("playlist"))
async def get_playlists(message: types.Message):
    await message.answer("⏳ Проверяю и фильтрую плейлисты...")

    urls = sheet.col_values(2)[1:]
    urls = [u.strip() for u in urls if u.strip().startswith(("http://", "https://"))]

    async with aiohttp.ClientSession() as session:
        tasks = [process_playlist(u, session) for u in urls]
        results = await asyncio.gather(*tasks)
        valid = [r for r in results if r]

    if not valid:
        return await message.answer("❌ Не найдено подходящих каналов в плейлистах.")

    for name, content in valid:
        playlist_store[name] = content
        link = f"{BASE_URL}/playlist/{name}.m3u"
        await message.answer(f"✅ Ваш плейлист: {link}")
        await asyncio.sleep(1)

# Health-check и раздача
async def health_check(request):
    return web.Response(text="Bot is alive!")

async def serve_playlist(request):
    name = request.match_info['name']
    content = playlist_store.get(name)
    if content is None:
        return web.Response(status=404, text="Not found")
    return web.Response(
        text=content,
        content_type="application/vnd.apple.mpegurl"
    )

async def start_web_app():
    app = web.Application()
    app.add_routes([
        web.get("/", health_check),
        web.get("/playlist/{name}.m3u", serve_playlist),
    ])
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
