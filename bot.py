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
    - Сначала проверяем базовый формат (#EXTM3U, хотя бы один #EXTINF).
    - Проверяем работоспособность первого или второго потока.
    - Если один из первых двух — рабочий, фильтруем весь плейлист по WANTED_CHANNELS.
    - Возвращаем отфильтрованный плейлист, если найдены нужные каналы.
    """
    try:
        async with session.get(url, timeout=15) as resp:
            if resp.status != 200:
                return None
            content = await resp.text()
            lines = content.splitlines()

            # Базовая валидация
            if not is_playlist_valid(lines):
                return None

            # Проверяем первые два потока
            extinf_indices = [i for i, line in enumerate(lines) if line.lower().startswith('#extinf')]
            valid_first_two = False
            for idx in extinf_indices[:2]:
                if idx + 1 < len(lines) and lines[idx+1].startswith(('http://', 'https://')):
                    try:
                        async with session.get(lines[idx+1], timeout=10) as ch_resp:
                            if ch_resp.status == 200:
                                chunk = await ch_resp.content.read(256)
                                if chunk:
                                    valid_first_two = True
                                    break
                    except Exception:
                        continue
            if not valid_first_two:
                return None

            # Фильтрация нужных каналов
            valid_entries = []
            seen_titles = set()
            for idx in extinf_indices:
                title = lines[idx].split(',', 1)[-1].strip()
                if any(w.lower() in title.lower() for w in config.WANTED_CHANNELS) and title not in seen_titles:
                    # проверяем поток
                    if idx+1 < len(lines) and lines[idx+1].startswith(('http://', 'https://')):
                        try:
                            async with session.get(lines[idx+1], timeout=10) as ch_resp:
                                if ch_resp.status == 200:
                                    chunk = await ch_resp.content.read(256)
                                    if chunk:
                                        valid_entries.extend([lines[idx], lines[idx+1]])
                                        seen_titles.add(title)
                        except Exception:
                            continue

            if not valid_entries:
                return None

            # Формируем более оригинальное имя файла на основе URL
            parts = url.rstrip('/').split('/')
            filename = parts[-1].split('?')[0]
            folder = parts[-2] if len(parts) >= 2 else 'playlist'
            playlist_name = f"{folder}_{filename}"

            filtered = ['#EXTM3U'] + valid_entries
            return playlist_name, '\n'.join(filtered)

    except Exception as e:
        logger.error(f"Ошибка обработки {url}: {e}")
        return None

@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "Привет! Я собираю плейлисты, фильтруя только те, где хотя бы первый или второй канал живой.\n"
        "Используй /playlist — и я пришлю готовые файлы."
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
            return await message.answer("❌ Не найдено подходящих плейлистов")

        sent = 0
        for name, content in valid_playlists:
            file = BufferedInputFile(content.encode('utf-8'), filename=name)
            await message.answer_document(file, caption=f"✅ {name}")
            sent += 1
            await asyncio.sleep(1)

        await message.answer(f"🎉 Отправлено плейлистов: {sent}/{len(valid_playlists)}")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        await message.answer("⚠️ Внутренняя ошибка. Попробуйте позже.")

# Health-check и запуск
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
