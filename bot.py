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
creds_dict = json.loads(os.environ["GOOGLE_CREDS_JSON"])
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
gc = gspread.authorize(creds)

# Открываем нужный лист
sheet = gc.open(config.SHEET_NAME).worksheet(config.SHEET_TAB_NAME)

# Инициализация бота
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

def is_playlist_valid(lines: list[str]) -> bool:
    """Проверка базового формата M3U плейлиста"""
    return bool(lines) and lines[0].strip().lower().startswith("#extm3u") and any(line.strip().lower().startswith("#extinf") for line in lines)

async def process_playlist(url: str, session: aiohttp.ClientSession) -> tuple[str, str] | None:
    """
    Обработка одного плейлиста:
    - Проверяем базовый формат (#EXTM3U и #EXTINF).
    - Проверяем работоспособность первого или второго потока.
    - Если один из них жив, возвращаем **весь** оригинальный плейлист.
    """
    try:
        async with session.get(url, timeout=15) as resp:
            if resp.status != 200:
                return None

            content = await resp.text()
            lines = content.splitlines()

            # Проверка формата
            if not is_playlist_valid(lines):
                return None

            # Проверяем первые два потока
            extinf_indices = [i for i, ln in enumerate(lines) if ln.lower().startswith('#extinf')]
            for idx in extinf_indices[:2]:
                if idx + 1 < len(lines) and lines[idx+1].startswith(('http://', 'https://')):
                    try:
                        async with session.get(lines[idx+1], timeout=10) as ch:
                            if ch.status == 200 and await ch.content.read(256):
                                # живая запись — возвращаем весь оригинал
                                filename = url.rstrip('/').split('/')[-1].split('?')[0] or 'playlist.m3u8'
                                return filename, content
                    except Exception:
                        continue
            return None

    except Exception as e:
        logger.error(f"Ошибка обработки {url}: {e}")
        return None

@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "Привет! Я проверяю плейлисты: валидны по формату и по первым двум потокам.\n"
        "Используй /playlist, и я пришлю **всe** рабочие плейлисты из таблицы."
    )

@dp.message(Command("playlist"))
async def get_playlists(message: types.Message):
    try:
        await message.answer("⏳ Загружаю и проверяю плейлисты...")
        urls = sheet.col_values(2)[1:]
        async with aiohttp.ClientSession() as session:
            tasks = [process_playlist(u.strip(), session) for u in urls if u.strip()]
            res = await asyncio.gather(*tasks)
            valid = [r for r in res if r]

        if not valid:
            return await message.answer("❌ Не найдено рабочих плейлистов")

        count = 0
        for name, content in valid:
            file = BufferedInputFile(content.encode('utf-8'), filename=name)
            await message.answer_document(file, caption=f"✅ {name}")
            count += 1
            await asyncio.sleep(1)

        await message.answer(f"🎉 Отправлено рабочих плейлистов: {count}/{len(valid)}")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        await message.answer("⚠️ Внутренняя ошибка. Попробуйте позже.")

# Health-check для Render
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
