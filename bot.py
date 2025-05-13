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

# Таймауты
PLAYLIST_TIMEOUT = 30
STREAM_TIMEOUT = 20

# Проверка базового формата плейлиста
def is_playlist_valid(lines: list[str]) -> bool:
    return (
        bool(lines) and lines[0].strip().lower().startswith("#extm3u")
        and any(line.strip().lower().startswith("#extinf") for line in lines)
    )

async def process_playlist(url: str, session: aiohttp.ClientSession) -> tuple[str, str] | None:
    """Скачивает плейлист, фильтрует по WANTED_CHANNELS и проверяет работу каналов"""
    try:
        async with session.get(url, timeout=PLAYLIST_TIMEOUT) as resp:
            if resp.status != 200:
                return None
            text = await resp.text()
    except Exception as e:
        logger.warning(f"Не удалось скачать плейлист {url}: {e}")
        return None

    lines = text.splitlines()
    if not is_playlist_valid(lines):
        return None

    # Фильтрация по названиям каналов
    entries: list[tuple[str,str]] = []  # (info_line, stream_url)
    for i, line in enumerate(lines):
        if line.strip().lower().startswith("#extinf"):
            title = line.split(",",1)[-1].strip()
            if any(key.lower() in title.lower() for key in config.WANTED_CHANNELS):
                if i+1 < len(lines) and lines[i+1].startswith(("http://","https://")):
                    entries.append((line, lines[i+1]))

    if not entries:
        # Если нет каналов по фильтру, но выдаём рабочий плейлист, проверяем любой поток
        all_streams = [lines[i+1] for i,line in enumerate(lines)
                       if line.strip().lower().startswith("#extinf") and i+1 < len(lines)]
        for s_url in all_streams:
            try:
                async with session.get(s_url, timeout=STREAM_TIMEOUT) as r:
                    if r.status == 200 and await r.content.read(256):
                        # возвращаем оригинальный плейлист без фильтрации
                        parts = url.rstrip('/').split('/')
                        folder = parts[-2] if len(parts) >= 2 else ''
                        base = parts[-1].split('?')[0]
                        filename = f"{folder}_{base}" if folder else base
                        return filename, text
            except Exception:
                continue
        return None

    # Проверяем доступность хотя бы одного потока
    for info_line, stream_url in entries:
        try:
            async with session.get(stream_url, timeout=STREAM_TIMEOUT) as r:
                if r.status == 200:
                    chunk = await r.content.read(512)
                    if chunk:
                        # Собираем новый плейлист
                        filtered_lines = ["#EXTM3U"]
                        for inf, url2 in entries:
                            filtered_lines.append(inf)
                            filtered_lines.append(url2)
                        content = "\n".join(filtered_lines)
                        # Имя файла по URL
                        parts = url.rstrip('/').split('/')
                        folder = parts[-2] if len(parts) >= 2 else ''
                        base = parts[-1].split('?')[0]
                        filename = f"{folder}_{base}" if folder else base
                        return filename, content
        except Exception:
            continue

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
    valid: list[tuple[str,str]] = []

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
