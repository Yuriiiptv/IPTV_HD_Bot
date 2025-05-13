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

# === Новое: in-memory хранилище плейлистов и базовый URL ===
playlist_store: dict[str, str] = {}
BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", os.environ.get("BASE_URL", "https://your-app.com"))
# ===========================================================

def is_playlist_valid(lines: list[str]) -> bool:
    """Проверка базового формата M3U плейлиста"""
    return (
        bool(lines)
        and lines[0].strip().lower().startswith("#extm3u")
        and any(line.strip().lower().startswith("#extinf") for line in lines)
    )

async def process_playlist(url: str, session: aiohttp.ClientSession) -> tuple[str, str] | None:
    """Загружает M3U-плейлист, проверяет валидность и фильтрует каналы по списку в config.WANTED_CHANNELS."""
    try:
        async with session.get(url, timeout=15) as resp:
            if resp.status != 200:
                return None

            content = await resp.text()
            lines = content.splitlines()

            # Базовая валидация M3U
            if not is_playlist_valid(lines):
                return None

            # Фильтрация по имени канала
            filtered = ["#EXTM3U"]
            i = 0
            while i < len(lines):
                line = lines[i].strip()
                if line.lower().startswith("#extinf"):
                    _, info = line.split(",", 1) if "," in line else ("", line)
                    if any(key.lower() in info.lower() for key in config.WANTED_CHANNELS):
                        filtered.append(lines[i])
                        if i + 1 < len(lines):
                            filtered.append(lines[i+1])
                    i += 2
                else:
                    i += 1

            if len(filtered) <= 1:
                return None

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
        "Привет! Я могу проверить M3U плейлист на валидность формата и вернуть отфильтррованные каналы.\n"
        "Используй команду /playlist — и я пришлю ссылки на те плейлисты, которые подходят по списку."
    )

@dp.message(Command("playlist"))
async def get_playlists(message: types.Message):
    await message.answer("⏳ Проверяю плейлисты...")

    urls = sheet.col_values(2)[1:]
    urls = [u.strip() for u in urls if u.strip().startswith(('http://','https://'))]

    async with aiohttp.ClientSession() as session:
        tasks = [process_playlist(url, session) for url in urls]
        results = await asyncio.gather(*tasks)
        valid_playlists = [res for res in results if res]

    if not valid_playlists:
        return await message.answer("❌ Не найдено подходящих каналов в плейлистах.")

    for name, content in valid_playlists:
        playlist_store[name] = content
        link = f"{BASE_URL}/playlist/{name}.m3u"
        await message.answer(f"✅ Ваш плейлист: {link}")
        await asyncio.sleep(1)

# Health-check и раздача плейлиста по HTTP
async def health_check(request):
    return web.Response(text="Bot is alive!")

async def serve_playlist(request):
    name = request.match_info['name']
    content = playlist_store.get(name)
    if not content:
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
    port = int(os.environ.get('PORT', 5000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
