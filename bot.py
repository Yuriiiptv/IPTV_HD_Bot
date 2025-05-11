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
from urllib.parse import urlparse

import config

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Инициализация Google Sheets
def init_google_sheets():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds_dict = json.loads(os.environ["GOOGLE_CREDS_JSON"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return gspread.authorize(creds)

gc = init_google_sheets()
sheet = gc.open(config.SHEET_NAME).worksheet(config.SHEET_TAB_NAME)

# Компиляция паттерна для фильтрации каналов
channel_pattern = re.compile(
    r'^(?:' + '|'.join(re.escape(name) for name in config.WANTED_CHANNELS) + r')$',
    re.IGNORECASE
)

# Инициализация бота
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

def is_playlist_valid(content: str) -> bool:
    """Проверка базовой валидности M3U плейлиста"""
    lines = content.splitlines()
    return len(lines) > 1 and lines[0].strip() == "#EXTM3U"

async def process_playlist(url: str, session: aiohttp.ClientSession) -> tuple[str, str] | None:
    """Обработка одного плейлиста с улучшенной обработкой ошибок"""
    try:
        # Нормализация URL
        url = url.strip()
        if not url.startswith(('http://', 'https://')):
            return None

        async with session.get(url, timeout=20) as response:
            if response.status != 200:
                return None
                
            content = await response.text()
            
        if not is_playlist_valid(content):
            return None

        # Парсинг и фильтрация каналов
        valid_entries = []
        lines = content.splitlines()
        
        for i in range(len(lines)):
            line = lines[i].strip()
            if line.startswith("#EXTINF"):
                try:
                    title = line.split(',', 1)[1].strip()
                except IndexError:
                    continue
                
                if not channel_pattern.match(title):
                    continue
                
                if i+1 >= len(lines) or not lines[i+1].startswith('http'):
                    continue
                
                stream_url = lines[i+1].strip()
                
                # Проверка доступности канала
                try:
                    async with session.head(stream_url, timeout=10) as resp:
                        if resp.status == 200:
                            valid_entries.extend([line, stream_url])
                except Exception as e:
                    logger.debug(f"Ошибка проверки {stream_url}: {str(e)}")
                    continue

        if not valid_entries:
            return None
            
        # Генерация имени файла
        parsed_url = urlparse(url)
        filename = f"{parsed_url.netloc}_{parsed_url.path.split('/')[-1]}"[:50] + ".m3u"
        
        return filename, "\n".join(["#EXTM3U"] + valid_entries)

    except Exception as e:
        logger.error(f"Ошибка обработки {url}: {str(e)}")
        return None

@dp.message(Command("start"))
async def start_handler(message: types.Message):
    """Обработчик команды /start"""
    await message.answer(
        "📺 IPTV Playlist Processor\n\n"
        "Используйте /playlist для получения отфильтрованных плейлистов"
    )

@dp.message(Command("playlist"))
async def playlist_handler(message: types.Message):
    """Основной обработчик для генерации плейлистов"""
    try:
        status_msg = await message.answer("🔄 Начинаю обработку плейлистов...")
        
        # Получение URL из Google Sheets
        urls = sheet.col_values(2)[1:]  # Колонка B, пропускаем заголовок
        
        # Параллельная обработка плейлистов
        valid_playlists = []
        async with aiohttp.ClientSession() as session:
            tasks = []
            for url in urls:
                if url.strip():
                    tasks.append(process_playlist(url.strip(), session))
            
            results = await asyncio.gather(*tasks)
            valid_playlists = [res for res in results if res is not None]

        # Отправка результатов
        if not valid_playlists:
            return await message.answer("❌ Не найдено валидных плейлистов")
            
        success_count = 0
        for filename, content in valid_playlists:
            try:
                await message.answer_document(
                    BufferedInputFile(
                        content.encode('utf-8'),
                        filename=filename
                    ),
                    caption=f"✅ {filename}"
                )
                success_count += 1
                await asyncio.sleep(0.5)  # Защита от флуда
            except Exception as e:
                logger.error(f"Ошибка отправки {filename}: {str(e)}")

        await message.answer(
            f"🎉 Обработка завершена!\n"
            f"Успешно отправлено: {success_count}/{len(valid_playlists)} плейлистов"
        )

    except Exception as e:
        logger.error(f"Критическая ошибка: {str(e)}")
        await message.answer("⚠️ Произошла внутренняя ошибка. Попробуйте позже.")

# Web Server для Render
async def health_check(request):
    return web.Response(text="Service is operational")

async def init_web_app():
    app = web.Application()
    app.add_routes([web.get('/', health_check)])
    return app

async def main():
    # Запуск веб-сервера
    web_app = await init_web_app()
    runner = web.AppRunner(web_app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 5000))
    site = web.TCPSite(runner, host='0.0.0.0', port=port)
    await site.start()

    # Запуск бота
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
