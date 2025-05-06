import asyncio
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile
import config  # Убедитесь, что в config.py есть BOT_TOKEN, M3U_URLS, CHANNEL_NAMES

async def start_handler(message: types.Message):
    """Обработчик команды /start."""
    await message.answer("Привет! Используй команду /получить_плейлист для генерации плейлиста.")

async def get_playlist_handler(message: types.Message):
    """Обработчик команды /получить_плейлист."""
    await message.answer("Начинаем обработку плейлистов...")
    channel_names = [name.lower() for name in config.CHANNEL_NAMES]

    async with aiohttp.ClientSession() as session:
        # Асинхронная загрузка плейлистов
        playlist_texts = []
        async def fetch_playlist(url):
            try:
                async with session.get(url, timeout=10) as resp:
                    return await resp.text() if resp.status == 200 else None
            except (aiohttp.ClientError, asyncio.TimeoutError):
                return None

        tasks = [fetch_playlist(url) for url in config.M3U_URLS]
        results = await asyncio.gather(*tasks)
        playlist_texts = [text for text in results if text]

        # Парсинг и фильтрация каналов
        filtered_channels = []
        for text in playlist_texts:
            lines = text.splitlines()
            it = iter(lines)
            try:
                first_line = next(it)
                if not first_line.startswith("#EXTM3U"):
                    it = iter(lines)  # Сброс итератора, если нет заголовка
            except StopIteration:
                continue

            for line in it:
                if line.startswith("#EXTINF"):
                    title = line.split(",", 1)[-1].strip()
                    url = next(it, "").strip()
                    if title.lower() in channel_names:
                        filtered_channels.append((title, url))

        if not filtered_channels:
            await message.reply("Не найдены каналы с указанными именами.")
            return

        # Проверка доступности каналов
        valid_channels = []
        async def check_url(title, url):
            try:
                async with session.head(url, timeout=10) as resp:
                    return resp.status == 200
            except (aiohttp.ClientError, asyncio.TimeoutError):
                return False

        tasks = [check_url(title, url) for title, url in filtered_channels]
        statuses = await asyncio.gather(*tasks)
        valid_channels = [ch for ch, ok in zip(filtered_channels, statuses) if ok]

    if not valid_channels:
        await message.reply("Не удалось получить рабочие ссылки.")
        return

    # Формирование и отправка M3U
    content = "#EXTM3U\n" + "\n".join(
        f"#EXTINF:-1,{title}\n{url}" 
        for title, url in valid_channels
    )
    m3u_file = BufferedInputFile(content.encode("utf-8"), filename="playlist.m3u")
    await message.reply_document(m3u_file, caption="Ваш плейлист готов!")

async def main():
    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher()
    dp.message.register(start_handler, Command("start"))
    dp.message.register(get_playlist_handler, Command("получить_плейлист"))  # Убедитесь в правильности названия команды
    await dp.start_polling(bot)

# Исправлено: добавлены недостающие __
if __name__ == '__main__':
    asyncio.run(main())
