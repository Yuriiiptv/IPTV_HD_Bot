import os
import asyncio
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile
from aiohttp import web
import config

# Инициализация бота
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

async def fetch_playlist(session, url):
    """Загрузка одного плейлиста с обработкой ошибок"""
    try:
        async with session.get(url, timeout=10) as response:
            if response.status == 200:
                return await response.text()
            return None
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        print(f"Ошибка при загрузке {url}: {str(e)}")
        return None

async def check_channel(session, url):
    """Проверка доступности канала"""
    try:
        async with session.head(url, timeout=5) as resp:
            return resp.status == 200
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return False

@dp.message(Command("start"))
async def start_command(message: types.Message):
    """Обработчик команды /start"""
    await message.answer(
        "Привет! Я могу собрать плейлист из популярных каналов.\n"
        "Используй команду /playlist чтобы получить актуальный список."
    )

@dp.message(Command("playlist"))
async def get_playlist(message: types.Message):
    """Генерация и отправка плейлиста"""
    await message.answer("⏳ Загружаю и проверяю плейлисты...")
    
    target_channels = [name.lower() for name in config.CHANNEL_NAMES]
    found_channels = []
    
    async with aiohttp.ClientSession() as session:
        # Загрузка всех плейлистов
        tasks = [fetch_playlist(session, url) for url in config.M3U_URLS]
        playlists = await asyncio.gather(*tasks)
        
        # Парсинг плейлистов
        for playlist in filter(None, playlists):
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
        check_tasks = [check_channel(session, url) for _, url in found_channels]
        results = await asyncio.gather(*check_tasks)
        
        for (title, url), is_valid in zip(found_channels, results):
            if is_valid:
                valid_channels.append((title, url))
    
    # Формирование конечного плейлиста
    if not valid_channels:
        return await message.answer("❌ Не удалось найти рабочие каналы")
    
    m3u_content = "#EXTM3U\n"
    m3u_content += "\n".join(
        f"#EXTINF:-1,{title}\n{url}"
        for title, url in valid_channels
    )
    
    # Отправка пользователю
    file = BufferedInputFile(
        m3u_content.encode("utf-8"),
        filename="russian_channels.m3u"
    )
    await message.answer_document(
        file,
        caption=f"🎬 Найдено {len(valid_channels)} рабочих каналов"
    )

# Веб-сервер для Render
async def health_check(request):
    return web.Response(text="Bot is running")

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
