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
import re

import config

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === Google Sheets Auth (из переменной окружения) ===
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

# Читаем JSON из Environment и создаём creds
creds_dict = json.loads(os.environ["GOOGLE_CREDS_JSON"])
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
gc = gspread.authorize(creds)

# Открываем нужный лист
sheet = gc.open(config.SHEET_NAME).worksheet(config.SHEET_TAB_NAME)

# Компилируем фильтр по списку каналов
pattern = re.compile(
    r'^(?:' + '|'.join(re.escape(name) for name in config.WANTED_CHANNELS) + r')$',
    re.IGNORECASE
)

# Инициализация бота
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

def is_playlist_valid(lines: list[str]) -> bool:
    """
    Проверка минимум на наличие заголовка и хотя бы одного канала.
    """
    if not lines or not lines[0].strip().startswith("#EXTM3U"):
        return False
    return any(line.strip().startswith("#EXTINF") for line in lines)


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

        # 1) Читаем все URL из колонки B, пропуская заголовок
        urls = sheet.col_values(2)[1:]
        found_channels: list[tuple[str, str]] = []

        async with aiohttp.ClientSession() as session:
            # 2) Загружаем и фильтруем
            for raw_url in urls:
                url = raw_url.strip()
                if not url.lower().startswith("http"):
                    continue

                # Загрузка плейлиста
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

                # Ищем блоки #EXTINF + URL
                for idx, line in enumerate(lines):
                    if line.strip().startswith("#EXTINF"):
                        parts = line.split(",", 1)
                        title = parts[1].strip() if len(parts) > 1 else ""
                        # следующий line — URL
                        if idx + 1 < len(lines):
                            stream_url = lines[idx + 1].strip()
                        else:
                            stream_url = ""
                        if pattern.match(title):
                            found_channels.append((title, stream_url))

            # 3) Проверяем доступность потоков
            valid_channels: list[tuple[str, str]] = []
            for title, stream_url in found_channels:
                try:
                    async with session.head(stream_url, timeout=10) as resp2:
                        if resp2.status == 200:
                            valid_channels.append((title, stream_url))
                except Exception:
                    continue

        # 4) Формируем итоговый M3U
        if not valid_channels:
            return await message.answer("❌ Не удалось найти ни одного рабочего канала.")

        m3u_lines = ["#EXTM3U"]
        for title, stream_url in valid_channels:

m3u_lines.append(f"#EXTINF:-1,{title}")
            m3u_lines.append(stream_url)
        m3u_content = "\n".join(m3u_lines)

        # 5) Отправляем как файл
        file = BufferedInputFile(
            m3u_content.encode("utf-8"),
            filename="federal_channels.m3u"
        )
        await message.answer_document(
            file,
            caption=f"✅ Найдено {len(valid_channels)} рабочих каналов"
        )

    except Exception as e:
        logger.error(f"Критическая ошибка в /playlist: {e}")
        await message.answer("⚠️ Произошла внутренняя ошибка. Попробуйте позже.")


# Health-check для Render
async def health_check(request):
    return web.Response(text="Bot is alive!")


async def start_web_app():
    app = web.Application()
    app.add_routes([web.get("/", health_check)])
    return app


async def main():
    # Запускаем веб-сервер
    web_app = await start_web_app()
    runner = web.AppRunner(web_app)
    await runner.setup()
    port = int(os.environ.get("PORT", 5000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    # Запускаем polling
    await dp.start_polling(bot)


if name == "__main__":
    asyncio.run(main())
