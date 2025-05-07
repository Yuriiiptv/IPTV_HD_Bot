import os
import asyncio
import logging
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiohttp import web
import config

# Настройка логов
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота
bot = Bot(token=config.BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

# Клавиатура с инлайн-кнопками
def get_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📺 Получить плейлист", callback_data="get_playlist")]
    ])
    return keyboard

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
        
        user_ip = await get_user_ip(callback.from_user.id)
        if not user_ip:
            await callback.message.edit_text("❌ Не удалось определить ваш IP для проверки")
            return

        valid_channels = await check_channels(user_ip)
        
        if not valid_channels:
            await callback.message.edit_text("❌ Не удалось найти рабочие каналы")
            return

        # Формирование M3U с учетом IP пользователя
        m3u_content = "#EXTM3U\n"
        for title, url in valid_channels:
            m3u_content += f"#EXTINF:-1,{title}\n{url}\n"

        file = BufferedInputFile(
            m3u_content.encode("utf-8"),
            filename="personal_playlist.m3u"
        )
        
        await callback.message.answer_document(
            file,
            caption=f"✅ Ваш персональный плейлист ({len(valid_channels)} каналов)\n"
                   f"Проверено с вашего IP: {user_ip}"
        )
        await callback.answer()

    except Exception as e:
        logger.error(f"Ошибка: {str(e)}")
        await callback.message.edit_text("⚠️ Произошла ошибка. Попробуйте позже.")
        await callback.answer()

async def get_user_ip(user_id: int) -> str:
    """Получаем примерный IP пользователя через внешний сервис"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get('https://api.ipify.org?format=json') as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get('ip', '')
    except Exception as e:
        logger.error(f"Ошибка получения IP: {str(e)}")
    return ""

async def check_channels(user_ip: str) -> list:
    """Проверка каналов с учетом IP пользователя"""
    valid_channels = []
    target_channels = [name.lower() for name in config.CHANNEL_NAMES]
    
    try:
        async with aiohttp.ClientSession() as session:
            # Заголовки с IP пользователя (для некоторых прокси)
            headers = {'X-Forwarded-For': user_ip, 'X-Real-IP': user_ip}
            
            # Загрузка плейлистов
            playlists = []
            for url in config.M3U_URLS:
                try:
                    async with session.get(url, timeout=15, headers=headers) as resp:
                        if resp.status == 200:
                            playlists.append(await resp.text())
                except Exception as e:
                    logger.error(f"Ошибка загрузки {url}: {str(e)}")

            # Парсинг и проверка каналов
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
                                # Проверка доступности с учетом IP
                                try:
                                    async with session.head(
                                        channel_info["url"], 
                                        timeout=10,
                                        headers=headers
                                    ) as resp:
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
    
    # Запуск бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
