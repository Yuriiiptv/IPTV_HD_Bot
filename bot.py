import asyncio
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile
import config

async def start_handler(message: types.Message):
    """
    Обработчик команды /start.
    Отправляет приветственное сообщение.
    """
    await message.answer("Привет! Используй команду /получить_плейлист для генерации плейлиста.")

async def get_playlist_handler(message: types.Message):
    """
    Обработчик команды /получить_плейлист:
    Загружает плейлисты, фильтрует каналы, проверяет ссылки и отправляет новый M3U-файл.
    """
    await message.answer("Начинаем обработку плейлистов...")
    # Делаем список названий каналов в нижнем регистре для сравнения
    channel_names = [name.lower() for name in config.CHANNEL_NAMES]

    async with aiohttp.ClientSession() as session:
        # Асинхронно загружаем все плейлисты
        playlist_texts = []
        async def fetch_playlist(url):
            try:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        return await resp.text()
            except Exception:
                return None

        tasks = [asyncio.create_task(fetch_playlist(url)) for url in config.M3U_URLS]
        results = await asyncio.gather(*tasks)
        # Собираем тексты плейлистов (игнорируем неудачные запросы)
        for text in results:
            if text:
                playlist_texts.append(text)

        # Парсим плейлисты и фильтруем каналы по названиям
        filtered_channels = []
        for text in playlist_texts:
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            it = iter(lines)
            first = next(it, "")
            if first.startswith("#EXTM3U"):
                pass  # пропустить заголовок
            else:
                # если файл без заголовка, вернуться к началу
                it = iter(lines)
            for line in it:
                if line.startswith("#EXTINF"):
                    parts = line.split(",", 1)
                    title = parts[1].strip() if len(parts) > 1 else ""
                    url = next(it, "").strip()  # следующий URL-стрим
                    if title and url and title.lower() in channel_names:
                        filtered_channels.append((title, url))

        if not filtered_channels:
            await message.reply("Не найдены каналы с указанными именами.")
            return

        # Проверяем статус ссылок асинхронно
        valid_channels = []
        async def check_url(title, url):
            try:
                async with session.get(url) as resp:
                    return resp.status == 200
            except Exception:
                return False

        tasks = [asyncio.create_task(check_url(title, url)) for title, url in filtered_channels]
        statuses = await asyncio.gather(*tasks)
        for (title, url), ok in zip(filtered_channels, statuses):
            if ok:
                valid_channels.append((title, url))

    if not valid_channels:
        await message.reply("Не удалось получить рабочие ссылки для указанных каналов.")
        return

    # Генерируем содержимое нового M3U-файла
    content = "#EXTM3U\n"
    for title, url in valid_channels:
        content += f"#EXTINF:-1,{title}\n{url}\n"

    # Отправляем файл пользователю
    m3u_file = BufferedInputFile(content.encode('utf-8'), filename="filtered_playlist.m3u")
    await message.reply_document(m3u_file, caption="Сформирован фильтрованный плейлист.")

async def main():
    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher()
    dp.message.register(start_handler, Command("start"))
    dp.message.register(get_playlist_handler, Command("получить_плейлист"))
    await dp.start_polling(bot)

if name == 'main':
    asyncio.run(main())
