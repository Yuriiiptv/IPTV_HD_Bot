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
    try:
        async with session.get(url, timeout=15) as resp:
            if resp.status != 200:
                return None

            content = await resp.text()
            lines = content.splitlines()

            if not is_playlist_valid(lines):
                return None

            filtered = ["#EXTM3U"]
            streams = []
            i = 0
            while i < len(lines):
                line = lines[i].strip()
                if line.lower().startswith("#extinf"):
                    _, info = line.split(",", 1) if "," in line else ("", line)
                    stream_url = lines[i+1].strip() if i+1 < len(lines) else ""
                    if any(key.lower() in info.lower() for key in config.WANTED_CHANNELS):
                        filtered.append(line)
                        filtered.append(stream_url)
                        streams.append(stream_url)
                    i += 2
                else:
                    i += 1

            # если нашлись нужные каналы — используем их
            if streams:
                sample_urls = random.sample(streams, min(SAMPLE_SIZE, len(streams)))
                alive_count = 0
                for s_url in sample_urls:
                    try:
                        async with session.head(s_url, timeout=5) as r:
                            if r.status == 200:
                                alive_count += 1
                    except:
                        pass

                if alive_count >= 1:
                    parts = url.rstrip("/").split("/")
                    folder = parts[-2] if len(parts) >= 2 else ""
                    base = parts[-1].split("?")[0]
                    playlist_name = f"{folder}_{base}" if folder else base
                    return playlist_name, "\n".join(filtered)
                else:
                    return None

            # fallback: нет совпадений, но пробуем проверить хотя бы один поток из оригинала
            all_streams = []
            i = 0
            while i < len(lines):
                line = lines[i].strip()
                if line.lower().startswith("#extinf"):
                    stream_url = lines[i+1].strip() if i+1 < len(lines) else ""
                    all_streams.append(stream_url)
                    i += 2
                else:
                    i += 1

            sample_urls = random.sample(all_streams, min(SAMPLE_SIZE, len(all_streams)))
            alive_count = 0
            for s_url in sample_urls:
                try:
                    async with session.head(s_url, timeout=5) as r:
                        if r.status == 200:
                            alive_count += 1
                except:
                    pass

            if alive_count >= 1:
                parts = url.rstrip("/").split("/")
                folder = parts[-2] if len(parts) >= 2 else ""
                base = parts[-1].split("?")[0]
                playlist_name = f"{folder}_{base}" if folder else base
                return playlist_name, content

            return None

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
