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

# Веб-сервер для Render
async def health_check(request):
    return web.Response(text="Bot is working!")

async def start_web_server():
    """Явная настройка веб-сервера с логированием порта"""
    app = web.Application()
    app.add_routes([web.get("/", health_check)])
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"🔄 Web server starting on port {port}")  # Логирование порта
    
    try:
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        logger.info("✅ Web server started successfully")
        return runner
    except Exception as e:
        logger.error(f"❌ Web server failed: {str(e)}")
        raise

async def main():
    try:
        # Запуск веб-сервера
        web_runner = await start_web_server()
        
        # Запуск бота
        logger.info("🤖 Starting bot...")
        await dp.start_polling(bot)
        
    except Exception as e:
        logger.critical(f"🔥 Critical error: {str(e)}")
    finally:
        if web_runner:
            await web_runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
