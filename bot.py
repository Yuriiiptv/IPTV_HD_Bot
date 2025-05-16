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

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Google Sheets аутентификация
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]
creds_dict = json.loads(os.environ.get("GOOGLE_CREDS_JSON", "{}"))
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
gc = gspread.authorize(creds)

# Открываем лист с URL
sheet = gc.open(config.SHEET_NAME).worksheet(config.SHEET_TAB_NAME)

# Инициализация бота
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

async def process_playlist(url: str, session: aiohttp.ClientSession) -> tuple[str, str] | None:
    """
    Скачивает M3U по URL, фильтрует каналы по списку ключевых слов из config.WANTED_CHANNELS и
    возвращает имя файла и содержимое нового плейлиста.
    """
    try:
        async with session.get(url, timeout=config.GET_TIMEOUT) as resp:
            if resp.status != 200:
                logger.warning(f"{url} returned status {resp.status}")
                return None
            raw = await resp.text()
    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
        return None

    lines = raw.splitlines()
    # Убедимся, что это M3U
    if not lines or not lines[0].strip().lower().startswith("#extm3u"):
        logger.info(f"{url} is not a valid M3U")
        return None

    # Построим фильтрованный плейлист
    filtered = ["#EXTM3U"]
    for i in range(len(lines) - 1):
        line = lines[i].strip()
        if line.lower().startswith("#extinf"):
            info = line
            link = lines[i+1].strip()
            # проверяем ключевые слова в info
            if any(key.lower() in info.lower() for key in config.WANTED_CHANNELS):
                filtered.append(info)
                filtered.append(link)

    # Если после фильтрации нет записей, пропускаем
    if len(filtered) == 1:
        logger.info(f"No channels matched for {url}")
        return None

    # Подготавливаем контент с правильными разделителями
    content = "\n".join(filtered) + "\n"

    # Генерируем имя файла
    base = url.rstrip('/').split('/')[-1].split('?')[0]
    filename = f"filtered_{base}.m3u"
    return filename, content

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Я собираю M3U-плейлисты из вашей таблицы и отфильтровываю каналы по списку в конфиге.\n"
        "Команда /playlist — чтобы получить готовые файлы."
    )

@dp.message(Command("playlist"))
async def cmd_playlist(message: types.Message):
    await message.answer("⏳ Загружаю и фильтрую плейлисты...")

    urls = sheet.col_values(2)[1:]
    urls = [u.strip() for u in urls if u and u.startswith(("http://","https://"))]
    if not urls:
        return await message.answer("❌ Нет ссылок в таблице.")

    valid = []
    async with aiohttp.ClientSession() as session:
        tasks = [process_playlist(u, session) for u in urls]
        results = await asyncio.gather(*tasks)
        valid = [r for r in results if r]

    if not valid:
        return await message.answer("❌ Не найдено каналов по заданным ключам.")

    # Отправляем каждый плейлист
    for filename, content in valid:
        file = BufferedInputFile(content.encode('utf-8'), filename=filename)
        await message.answer_document(file, caption=f"✅ {filename}")
        await asyncio.sleep(0.5)

# Web-сервер для health-check
async def health_check(request):
    return web.Response(text="ok")

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
