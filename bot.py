import os
import asyncio
import logging
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiohttp import web
import config

# Настройка логов
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота
bot = Bot(token=config.BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

# Блокировка для защиты от параллельных запусков
bot_lock = asyncio.Lock()

# Кнопка "Получить плейлист"
def get_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📺 Получить плейлист", callback_data="get_playlist")]
    ])

# Стартовая команда
@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "Привет! Я могу собрать плейлист из популярных каналов.\n"
        "Нажми кнопку ниже, чтобы получить актуальный список:",
        reply_markup=get_keyboard()
    )

# Обработка нажатия кнопки
@dp.callback_query(lambda c: c.data == "get_playlist")
async def process_playlist(callback: types.CallbackQuery):
    try:
        await callback.message.edit_text("⏳ Загружаю и проверяю плейлисты...")

        valid_channels = await check_channels()

        if not valid_channels:
            await callback.message.edit_text("❌ Не удалось найти рабочие каналы")
            return

        # Создание текста .m3u
        m3u_text = "#EXTM3U\n"
        for title, url in valid_channels:
            m3u_text += f"#EXTINF:-1,{title}\n{url}\n"

        # Загрузка на transfer.sh
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://transfer.sh/playlist.m3u",
                data=m3u_text.encode("utf-8"),
                headers={"Content-Type": "text/plain"}
            ) as response:
                if response.status == 200:
                    link = await response.text()
                    await callback.message.edit_text(
                        f"✅ Плейлист готов:\n\n<a href='{link.strip()}'>📥 Скачать (.m3u)</a>",
                        disable_web_page_preview=True
                    )
                else:
                    await callback.message.edit_text("❌ Не удалось загрузить плейлист. Попробуйте позже.")

        await callback.answer()

    except Exception as e:
        logger.error(f"Ошибка: {str(e)}")
        await callback.message.edit_text("⚠️ Произошла ошибка. Попробуйте позже.")
        await callback.answer()

# Проверка рабочих каналов
async def check_channels() -> list:
    valid_channels = []
    target_channels = [name.lower() for name in config.CHANNEL_NAMES]

    try:
        async with aiohttp.ClientSession() as session:
            playlists = []

            for url in config.M3U_URLS:
                try:
                    async with session.get(url, timeout=15) as resp:
                        if resp.status == 200:
                            playlists.append(await resp.text())
                except Exception as e:
                    logger.warning(f"Ошибка загрузки {url}: {str(e)}")

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
                                try:
                                    async with session.head(channel_info["url"], timeout=5) as resp:
                                        if resp.status == 200:
                                            valid_channels.append((channel_info["title"], channel_info["url"]))
                                except Exception:
                                    pass
                            channel_info = {}

    except Exception as e:
        logger.error(f"Ошибка при проверке каналов: {str(e)}")

    return valid_channels

# Health-check для Render
async def health_check(request):
    return web.Response(text="Bot is alive!")

# Веб-приложение
async def start_web_app():
    app = web.Application()
    app.add_routes([web.get("/", health_check)])
    return app

# Запуск бота
async def main():
    web_app = await start_web_app()
    runner = web.AppRunner(web_app)
    await runner.setup()

    port = int(os.environ.get("PORT", 5000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    # Поллинг
    async with bot_lock:
        await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
