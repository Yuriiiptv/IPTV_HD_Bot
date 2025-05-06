import os
import asyncio
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile
from aiohttp import web
import config  # Импортируем конфиг

# Инициализация бота и диспетчера
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

# Обработчики команд
@dp.message(Command("start"))
async def start_handler(message: types.Message):
    await message.answer("Привет! Используй /получить_плейлист")

@dp.message(Command("получить_плейлист"))
async def get_playlist_handler(message: types.Message):
    await message.answer("Обрабатываю плейлисты...")
    
    channel_names = [name.lower() for name in config.CHANNEL_NAMES]
    
    async with aiohttp.ClientSession() as session:
        # Загрузка плейлистов
        playlist_texts = []
        async def fetch_playlist(url):
            try:
                async with session.get(url, timeout=10) as resp:
                    return await resp.text() if resp.status == 200 else None
            except Exception:
                return None

        tasks = [fetch_playlist(url) for url in config.M3U_URLS]
        results = await asyncio.gather(*tasks)
        playlist_texts = [text for text in results if text]

        # Фильтрация каналов
        filtered_channels = []
        for text in playlist_texts:
            lines = text.splitlines()
            it = iter(lines)
            
            try:
                first_line = next(it)
                if not first_line.startswith("#EXTM3U"):
                    it = iter(lines)
            except StopIteration:
                continue

            for line in it:
                if line.startswith("#EXTINF"):
                    title = line.split(",", 1)[-1].strip()
                    url = next(it, "").strip()
                    if title.lower() in channel_names:
                        filtered_channels.append((title, url))

        if not filtered_channels:
            await message.reply("Каналы не найдены.")
            return

        # Проверка ссылок
        valid_channels = []
        async def check_url(title, url):
            try:
                async with session.head(url, timeout=10) as resp:
                    return resp.status == 200
            except Exception:
                return False

        tasks = [check_url(title, url) for title, url in filtered_channels]
        statuses = await asyncio.gather(*tasks)
        valid_channels = [ch for ch, ok in zip(filtered_channels, statuses) if ok]

    if not valid_channels:
        await message.reply("Нет рабочих ссылок.")
        return

    # Формирование M3U
    content = "#EXTM3U\n" + "\n".join(
        f"#EXTINF:-1,{title}\n{url}" 
        for title, url in valid_channels
    )
    m3u_file = BufferedInputFile(content.encode("utf-8"), filename="playlist.m3u")
    await message.reply_document(m3u_file, caption="Ваш плейлист готов!")

# Веб-сервер для Render
async def web_handler(request):
    return web.Response(text="OK")

async def start_app():
    app = web.Application()
    app.add_routes([web.get("/", web_handler)])
    return app

async def main():
    # Запуск веб-сервера
    app = await start_app()
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 5000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    
    # Запуск бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
