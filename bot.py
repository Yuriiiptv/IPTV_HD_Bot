import os
import asyncio
import logging
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile
from aiohttp import web

import gspread
from oauth2client.service_account import ServiceAccountCredentials
import re

import config

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === Google Sheets ===
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]
creds = ServiceAccountCredentials.from_json_keyfile_name(
    config.GOOGLE_CREDS_FILE, scope
)
gc = gspread.authorize(creds)
sheet = gc.open(config.SHEET_NAME).worksheet(config.SHEET_TAB_NAME)

# Компилируем регулярное выражение для фильтрации каналов
pattern = re.compile(
    r'^(?:' + '|'.join(re.escape(name) for name in config.WANTED_CHANNELS) + r')$',
    re.IGNORECASE
)

# Инициализация бота и диспетчера
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

# Проверка валидности .m3u-плейлиста по минимуму 1 каналу
def is_playlist_valid(lines: list[str]) -> bool:
    if not lines or not lines[0].startswith("#EXTM3U"):
        return False
    # хотя бы один канал
    return any(line.startswith("#EXTINF") for line in lines)

@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "Привет! Я могу собрать для тебя плейлист из федеральных телеканалов.\n"
        "Используй команду /playlist — и я пришлю готовый .m3u файл."
    )

@dp.message(Command("playlist"))
async def get_playlist(message: types.Message):
    try:
        await message.answer("⏳ Загружаю и обрабатываю плейлисты из Google Sheets...")

        # 1) Считываем все URL из колонки B, пропуская заголовок
        urls = sheet.col_values(2)[1:]

        found_channels: list[tuple[str,str]] = []

        async with aiohttp.ClientSession() as session:
            # 2) Загрузка каждого плейлиста и фильтрация по названиям
            for url in urls:
                url = url.strip()
                if not url.startswith("http"):
                    continue

                try:
                    async with session.get(url, timeout=15) as resp:
                        text = await resp.text() if resp.status == 200 else None
                except Exception as e:
                    logger.error(f"Ошибка загрузки {url}: {e}")
                    continue

                if not text:
                    continue

                lines = text.splitlines()
                if not is_playlist_valid(lines):
                    continue

                # парсим блоки #EXTINF + следующую за ним строку URL
                for i, line in enumerate(lines):
                    if line.startswith("#EXTINF"):
                        parts = line.split(",", 1)
                        title = parts[1].strip() if len(parts) > 1 else ""
                        stream_url = lines[i+1].strip() if i+1 < len(lines) else ""
                        # полное совпадение названия канала
                        if pattern.match(title):
                            found_channels.append((title, stream_url))

            # 3) Проверяем, какие из найденных каналов действительно доступны
            valid_channels: list[tuple[str,str]] = []
            for title, stream_url in found_channels:
                try:
                    async with session.head(stream_url, timeout=10) as resp2:
                        if resp2.status == 200:
                            valid_channels.append((title, stream_url))
                except Exception:
                    continue

        # 4) Собираем итоговый .m3u
        if not valid_channels:
            return await message.answer("❌ Не удалось найти ни одного рабочего канала.")

        m3u_lines = ["#EXTM3U"]
        for title, stream_url in valid_channels:
            m3u_lines.append(f"#EXTINF:-1,{title}")
            m3u_lines.append(stream_url)
        m3u_content = "\n".join(m3u_lines)

        # 5) Отправляем файл пользователю
file = BufferedInputFile(
            m3u_content.encode("utf-8"),
            filename="federal_channels.m3u"
        )
        await message.answer_document(
            file,
            caption=f"✅ Найдено {len(valid_channels)} рабочих каналов"
        )

    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        await message.answer("⚠️ Произошла внутренняя ошибка. Попробуйте позже.")

# Веб-сервер для Render
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

if name == "__main__":
    asyncio.run(main())
