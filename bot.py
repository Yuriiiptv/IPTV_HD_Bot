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

# Инициализация бота
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

# Обработчики команд
@dp.message(Command("start"))
async def start_handler(message: types.Message):
    logger.info(f"Получен /start от {message.from_user.id}")
    await message.answer("Бот активен! Используйте /playlist.")

@dp.message(Command("playlist"))
async def playlist_handler(message: types.Message):
    # Ваш код обработки плейлиста
    pass

# Веб-сервер для Render
async def web_handler(request):
    return web.Response(text="OK")

async def main():
    # Настройка веб-сервера
    app = web.Application()
    app.add_routes([web.get("/", web_handler)])
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 5000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    
    # Запуск бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
