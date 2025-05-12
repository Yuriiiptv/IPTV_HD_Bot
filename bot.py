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
    1) Проверка базового формата (#EXTM3U и #EXTINF).
    2) Проверка работоспособности первого или второго потока.
    3) Фильтрация по WANTED_CHANNELS: собираем только совпавшие каналы.
    4) Если совпадения есть, возвращаем отфильтрованный плейлист; иначе None.
    """
    try:
        async with session.get(url, timeout=15) as resp:
            if resp.status != 200:
                return None

            content = await resp.text()
            lines = content.splitlines()

            # 1) Базовая проверка
            if not is_playlist_valid(lines):
                return None

            # 2) Проверяем первые два потока
            extinf_indices = [i for i, ln in enumerate(lines) if ln.lower().startswith('#extinf')]
            alive = False
            for idx in extinf_indices[:2]:
                if idx + 1 < len(lines) and lines[idx+1].startswith(('http://', 'https://')):
                    try:
                        async with session.get(lines[idx+1], timeout=10) as ch:
                            if ch.status == 200 and await ch.content.read(256):
                                alive = True
                                break
                    except:
                        pass
            if not alive:
                return None

            # 3) Фильтрация по названию каналов
            valid_entries = []
            seen = set()
            for idx in extinf_indices:
                title = lines[idx].split(',', 1)[-1].strip()
                if title not in seen and any(w.lower() in title.lower() for w in config.WANTED_CHANNELS):
                    # проверка потока
                    stream = lines[idx+1] if idx+1 < len(lines) else None
                    if stream and stream.startswith(('http://','https://')):
                        try:
                            async with session.get(stream, timeout=10) as ch2:
                                if ch2.status == 200 and await ch2.content.read(256):
                                    valid_entries += [lines[idx], stream]
                                    seen.add(title)
                        except:
                            pass

            # 4) Вернуть только если есть совпадения
            if not valid_entries:
                return None

            # Формируем имя файла как оригинал
            filename = url.rstrip('/').split('/')[-1].split('?')[0]
            playlist_name = filename or 'playlist.m3u8'
            final = ['#EXTM3U'] + valid_entries
            return playlist_name, '\n'.join(final)

    except Exception as e:
        logger.error(f"Ошибка обработки {url}: {e}")
        return None

@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "Привет! Я проверяю плейлисты и фильтрую только указанные каналы.\n"
        "Используй /playlist, чтобы получить результат."
    )

@dp.message(Command("playlist"))
async def get_playlists(message: types.Message):
    try:
        await message.answer("⏳ Загружаю и фильтрую плейлисты...")
        urls = sheet.col_values(2)[1:]
        async with aiohttp.ClientSession() as session:
            tasks = [process_playlist(u.strip(), session) for u in urls if u.strip()]
            res = await asyncio.gather(*tasks)
            valid = [r for r in res if r]

        if not valid:
            return await message.answer("❌ Не найдено подходящих плейлистов")

        cnt = 0
        for name, content in valid:
            file = BufferedInputFile(content.encode('utf-8'), filename=name)
            await message.answer_document(file, caption=f"✅ {name}")
            cnt += 1
            await asyncio.sleep(1)

        await message.answer(f"🎉 Отправлено: {cnt}/{len(valid)}")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        await message.answer("⚠️ Ошибка. Попробуйте позже.")

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
