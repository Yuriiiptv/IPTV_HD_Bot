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
    return (
        bool(lines)
        and lines[0].strip().lower().startswith("#extm3u")
        and any(line.strip().lower().startswith("#extinf") for line in lines)
    )

async def process_playlist(url: str, session: aiohttp.ClientSession) -> tuple[str, str] | None:
    """Обработка одного плейлиста: если формат верен, возвращаем весь оригинальный контент"""
    try:
        async with session.get(url, timeout=15) as resp:
            if resp.status != 200:
                return None

            content = await resp.text()
            lines = content.splitlines()

            # Если базовый формат корректен — возвращаем весь плейлист
            if is_playlist_valid(lines):
                # Формируем понятное имя файла по URL
                parts = url.rstrip('/').split('/')
                folder = parts[-2] if len(parts) >= 2 else ''
                base = parts[-1].split('?')[0]
                playlist_name = f"{folder}_{base}" if folder else base
                 # 🔴 ВСТАВИТЬ ФИЛЬТРАЦИЮ КАНАЛОВ СРАЗУ НИЖЕ ЭТОЙ СТРОКИ 🔴
        filtered = [lines[0]]  # всегда держим заголовок #EXTM3U
        for i, line in enumerate(lines):
            if line.strip().lower().startswith("#extinf") and any(
                w.lower() in line.lower() for w in config.WANTED_CHANNELS
            ):
                filtered.append(line)               # строка #EXTINF
                if i+1 < len(lines):
                    filtered.append(lines[i+1])     # следующий URL
        content = "\n".join(filtered)
        # 🔴 КОНЕЦ БЛОКА ФИЛЬТРАЦИИ 🔴

                return playlist_name, content

            # Иначе — невалидный
            return None

    except Exception as e:
        logger.error(f"Ошибка обработки {url}: {e}")
        return None

@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "Привет! Я могу проверить M3U плейлист на валидность формата и вернуть его.\n"
        "Используй команду /playlist — и я пришлю всё, что прошло валидацию."
    )

@dp.message(Command("playlist"))
async def get_playlists(message: types.Message):
    try:
        await message.answer("⏳ Проверяю плейлисты...")

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
