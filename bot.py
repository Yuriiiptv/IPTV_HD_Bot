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

# Глобальная блокировка для предотвращения конфликтов
bot_lock = asyncio.Lock()

# Инициализация бота с явным указанием skip_updates
bot = Bot(token=config.BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

async def safe_start_polling():
    """Безопасный запуск поллинга с обработкой конфликтов"""
    async with bot_lock:
        await dp.start_polling(bot, skip_updates=True)

# Клавиатура с инлайн-кнопками
def get_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📺 Получить плейлист", callback_data="get_playlist")]
    ])

# Обработчик команды /start
@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "Привет! Я могу собрать плейлист из популярных каналов.\n"
        "Нажми кнопку ниже, чтобы получить актуальный список:",
        reply_markup=get_keyboard()
    )

# Обработчик инлайн-кнопки
@dp.callback_query(lambda c: c.data == "get_playlist")
async def process_playlist(callback: types.CallbackQuery):
    try:
        await callback.message.edit_text("⏳ Загружаю и проверяю плейлисты...")
        
        valid_channels = await check_channels()
        
        if not valid_channels:
            await callback.message.edit_text("❌ Не удалось найти рабочие каналы")
            return

        playlist_text = "<b>🎬 Доступные каналы:</b>\n\n"
        for title, url in valid_channels:
            playlist_text += f"🔹 <a href='{url}'>{title}</a>\n"

        max_length = 4000
        for i in range(0, len(playlist_text), max_length):
            part = playlist_text[i:i + max_length]
            if i == 0:
                await callback.message.edit_text(part)
            else:
                await callback.message.answer(part)

        await callback.answer()

    except Exception as e:
        logger.error(f"Ошибка: {str(e)}")
        await callback.message.edit_text("⚠️ Произошла ошибка. Попробуйте позже.")
        await callback.answer()

async def check_channels() -> list:
    """Проверка доступности каналов"""
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
                    logger.error(f"Ошибка загрузки {url}: {str(e)}")

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
                                    continue
                            channel_info = {}

    except Exception as e:
        logger.error(f"Ошибка проверки каналов: {str(e)}")
    
    return valid_channels

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
    
    # Безопасный запуск бота
    await safe_start_polling()

if __name__ == "__main__":
    asyncio.run(main())
