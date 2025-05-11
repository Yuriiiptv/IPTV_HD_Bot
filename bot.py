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

# Компилируем фильтр каналов
pattern = re.compile(
    r'^(?:' + '|'.join(re.escape(name) for name in config.WANTED_CHANNELS) + r')$',
    re.IGNORECASE
)

# Инициализация бота
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

def is_playlist_valid(lines: list[str]) -> bool:
    """Проверка валидности плейлиста"""
    if not lines or not lines[0].strip().startswith("#EXTM3U"):
        return False
    return any(line.strip().startswith("#EXTINF") for line in lines)

async def process_playlist(url: str, session: aiohttp.ClientSession) -> tuple[str, str] | None:
    """Обработка одного плейлиста"""
    try:
        async with session.get(url, timeout=15) as resp:
            if resp.status != 200:
                return None
                
            content = await resp.text()
            if not is_playlist_valid(content.splitlines()):
                return None
                
            # Фильтрация каналов
            valid_entries = []
            lines = content.splitlines()
            
            for i in range(len(lines)):
                if lines[i].startswith("#EXTINF"):
                    title = lines[i].split(",", 1)[-1].strip()
                    if pattern.match(title):
                        if i+1 < len(lines) and lines[i+1].startswith('http'):
                            try:
                                async with session.head(lines[i+1], timeout=10) as channel_resp:
                                    if channel_resp.status == 200:
                                        valid_entries.extend([lines[i], lines[i+1]])
                            except:
                                continue
                                
            if not valid_entries:
                return None
                
            # Формируем имя файла из URL
            playlist_name = url.split('/')[-1].split('?')[0] or "playlist.m3u"
            return playlist_name, "\n".join(["#EXTM3U"] + valid_entries)
            
    except Exception as e:
        logger.error(f"Ошибка обработки {url}: {e}")
        return None

@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "Привет! Я могу собрать для тебя плейлисты из федеральных телеканалов.\n"
        "Используй команду /playlist — и я пришлю готовые .m3u файлы."
    )

@dp.message(Command("playlist"))
async def get_playlists(message: types.Message):
    try:
        processing_msg = await message.answer("⏳ Загружаю и проверяю плейлисты...")

        # Получаем URL из Google Sheets
        urls = sheet.col_values(2)[1:]
        
        # Обрабатываем все плейлисты
        valid_playlists = []
        async with aiohttp.ClientSession() as session:
            tasks = [process_playlist(url.strip(), session) for url in urls if url.strip()]
            results = await asyncio.gather(*tasks)
            valid_playlists = [result for result in results if result]

        if not valid_playlists:
            return await message.answer("❌ Не найдено валидных плейлистов")

        # Отправляем каждый плейлист отдельным сообщением
        success_count = 0
        for name, content in valid_playlists:
            try:
                file = BufferedInputFile(
                    content.encode("utf-8"),
                    filename=name
                )
                await message.answer_document(
                    file,
                    caption=f"✅ {name}"
                )
                success_count += 1
                await asyncio.sleep(1)  # Задержка между отправками
            except Exception as e:
                logger.error(f"Ошибка отправки {name}: {e}")

        await message.answer(
            f"🎉 Готово! Успешно обработано плейлистов: {success_count}/{len(valid_playlists)}"
        )

    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        await message.answer("⚠️ Произошла внутренняя ошибка. Попробуйте позже.")

# Health-check и запуск (без изменений)
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
