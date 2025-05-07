import os
import asyncio
import logging
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile
from aiohttp import web
import config

# Настройка логов
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

# Обработчик команды /start
@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "Привет! Я могу собрать плейлист из популярных каналов.\n"
        "Используй команду /playlist чтобы получить актуальный список."
    )

# Обработчик команды /playlist
@dp.message(Command("playlist"))
async def get_playlist(message: types.Message):
    try:
        await message.answer("⏳ Загружаю и проверяю плейлисты...")
        target_channels = [name.lower() for name in config.CHANNEL_NAMES]
        found_channels = []
        
        async with aiohttp.ClientSession() as session:
            # Загрузка плейлистов
            playlists = []
            async def fetch_playlist(url):
                try:
                    async with session.get(url, timeout=15) as resp:
                        return await resp.text() if resp.status == 200 else None
                except Exception as e:
                    logger.error(f"Ошибка загрузки {url}: {str(e)}")
                    return None
            
            tasks = [fetch_playlist(url) for url in config.M3U_URLS]
            results = await asyncio.gather(*tasks)
            playlists = [text for text in results if text]

            # Парсинг плейлистов
            for playlist in playlists:
                lines = playlist.splitlines()
                channel_info = {}
                
                for line in lines:
                    line = line.strip()
                    if line.startswith("#EXTINF"):
                        parts = line.split(",", 1)
                        if len(parts) > 1:
                            channel_info["title"] = parts[1].strip()
                    elif line and not line.startswith("#"):
                        channel_info["url"] = line
                        if "title" in channel_info:
                            title_lower = channel_info["title"].lower()
                            if any(target in title_lower for target in target_channels):
                                found_channels.append((channel_info["title"], channel_info["url"]))
                            channel_info = {}

            # Проверка доступности каналов
            valid_channels = []
            async def check_channel(url):
                try:
                    async with session.head(url, timeout=10) as resp:
                        return resp.status == 200
                except Exception as e:
                    logger.error(f"Ошибка проверки {url}: {str(e)}")
                    return False
            
            check_tasks = [check_channel(url) for _, url in found_channels]
            statuses = await asyncio.gather(*check_tasks)
            valid_channels = [ch for ch, ok in zip(found_channels, statuses) if ok]

        # Формирование M3U
        if not valid_channels:
            return await message.answer("❌ Не удалось найти рабочие каналы")

        m3u_content = "#EXTM3U\n" + "\n".join(
            f"#EXTINF:-1,{title}\n{url}" 
            for title, url in valid_channels
        )
        
        # Отправка файла
        file = BufferedInputFile(
            m3u_content.encode("utf-8"),
            filename="russian_channels.m3u"
        )
        await message.answer_document(
            file,
            caption=f"✅ Найдено {len(valid_channels)} рабочих каналов"
        )

    except Exception as e:
        logger.error(f"Критическая ошибка: {str(e)}")
        await message.answer("⚠️ Произошла внутренняя ошибка. Попробуйте позже.")

# Веб-сервер для Render
async def health_check(request):
    return web.Response(text="Bot is alive!")

async def start_web_app():
    app = web.Application()
    app.add_routes([web.get("/", health_check)])
    return app

async def main():
    # Запуск веб-сервера
    web_app = await start_web_app()
    runner = web.AppRunner(web_app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 5000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    
    # Запуск бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
