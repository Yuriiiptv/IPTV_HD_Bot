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
creds_json = os.environ.get("GOOGLE_CREDS_JSON", "{}")
creds_dict = json.loads(creds_json)
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

# Параметры проверки
SAMPLE_SIZE = getattr(config, 'SAMPLE_SIZE', 3)
GET_TIMEOUT = getattr(config, 'GET_TIMEOUT', 30)
HEAD_TIMEOUT = getattr(config, 'HEAD_TIMEOUT', 10)
MIN_ALIVE = getattr(config, 'MIN_ALIVE', 1)

async def process_playlist(url: str, session: aiohttp.ClientSession) -> tuple[str, str] | None:
    """Загружает M3U, логирует #EXTINF, фильтрует по ключам и проверяет выборочно потоки."""
    try:
        logger.info(f"Fetching playlist: {url}")
        async with session.get(url, timeout=GET_TIMEOUT) as resp:
            if resp.status != 200:
                logger.warning(f"{url} returned status {resp.status}")
                return None
            content = await resp.text()
        lines = content.splitlines()
        logger.info(f"Processing {url}: {len(lines)} total lines")
        # Лог всех EXTINF для отладки
        for line in lines:
            if line.lower().startswith("#extinf"):
                logger.info(f"EXTINF line: {line}")
        # Фильтрация
        filtered = ["#EXTM3U"]
        streams = []
        for i, line in enumerate(lines):
            if line.lower().startswith("#extinf") and i + 1 < len(lines):
                info = line
                stream_url = lines[i+1].strip()
                if any(key.lower() in info.lower() for key in config.WANTED_CHANNELS):
                    filtered.append(info)
                    filtered.append(stream_url)
                    streams.append(stream_url)
        if not streams:
            logger.info(f"No matching channels found in {url}")
            return None
        # Проверяем SAMPLE_SIZE потоков, требуем минимум MIN_ALIVE
        sample = random.sample(streams, min(SAMPLE_SIZE, len(streams)))
        alive = 0
        for s_url in sample:
            try:
                async with session.head(s_url, timeout=HEAD_TIMEOUT) as r:
                    if r.status == 200:
                        alive += 1
                    else:
                        logger.warning(f"Stream HEAD {s_url} returned {r.status}")
            except Exception as e:
                logger.warning(f"HEAD check failed {s_url}: {e}")
        logger.info(f"Alive streams for {url}: {alive}/{len(sample)}")
        if alive < MIN_ALIVE:
            logger.info(f"Not enough alive streams in {url}")
            return None
        # Формируем имя файла
        base = url.rstrip('/').split('/')[-1].split('?')[0] or 'playlist'
        name = f"filtered_{base}.m3u"
        return name, "\n".join(filtered)
    except Exception as e:
        logger.error(f"Error processing {url}: {e}")
        return None

@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "Привет! Я собираю M3U-плейлисты из Google Sheets, фильтрую по вашим каналам и проверяю потоки.\n"
        "Используйте /playlist для старта."
    )

@dp.message(Command("playlist"))
async def get_playlists(message: types.Message):
    await message.answer("⏳ Проверяю плейлисты...")
    raw_urls = sheet.col_values(2)[1:]
    urls = [u.strip() for u in raw_urls if u.strip().startswith(("http://","https://"))]
    if not urls:
        return await message.answer("❌ В таблице нет ссылок на плейлисты.")
    async with aiohttp.ClientSession() as session:
        tasks = [process_playlist(u, session) for u in urls]
        results = await asyncio.gather(*tasks)
    valid = [r for r in results if r]
    if not valid:
        return await message.answer("❌ Не найдено подходящих каналов по заданным ключам.")
    for name, content in valid:
        playlist_store[name] = content
        link = f"{BASE_URL}/playlist/{name}"
        await message.answer(f"✅ Плейлист готов: {link}")
        await asyncio.sleep(1)

# Web-сервер для health-check и раздачи плейлистов
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
        web.get("/playlist/{name}", serve_playlist),
    ])
    return app

async def main():
    web_app = await start_web_app()
    runner = web.AppRunner(web_app)
    await runner.setup()
    port = int(os.environ.get('PORT', 5000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
