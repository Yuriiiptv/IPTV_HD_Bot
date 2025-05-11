import os
import json
import asyncio
import logging
import aiohttp
import zipfile
from io import BytesIO

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, FSInputFile
from aiohttp import web

import gspread
from oauth2client.service_account import ServiceAccountCredentials
import re

import config

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === Google Sheets Auth ===
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

creds_dict = json.loads(os.environ["GOOGLE_CREDS_JSON"])
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
gc = gspread.authorize(creds)

# Открываем нужный лист
sheet = gc.open(config.SHEET_NAME).worksheet(config.SHEET_TAB_NAME)

# Компилируем фильтр каналов
wanted_pattern = re.compile(
    r'^(?:' + '|'.join(re.escape(name) for name in config.WANTED_CHANNELS) + r')$',
    re.IGNORECASE
)

# Инициализация бота
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

# Клавиатура с кнопкой
playlist_keyboard = types.ReplyKeyboardMarkup(
    keyboard=[
        [types.KeyboardButton(text="📺 Получить плейлисты")]
    ],
    resize_keyboard=True
)

async def is_playlist_valid(content: str) -> bool:
    """Проверка валидности плейлиста"""
    lines = content.splitlines()
    if not lines or not lines[0].strip().startswith("#EXTM3U"):
        return False
    return any(line.strip().startswith("#EXTINF") for line in lines)

async def process_single_playlist(url: str, session: aiohttp.ClientSession) -> tuple[str, str] | None:
    """Обработка одного плейлиста"""
    try:
        async with session.get(url, timeout=15) as resp:
            if resp.status != 200:
                return None
                
            content = await resp.text()
            if not await is_playlist_valid(content):
                return None
                
            # Фильтрация каналов
            valid_entries = []
            lines = content.splitlines()
            
            for i in range(len(lines)):
                if lines[i].startswith("#EXTINF"):
                    title = lines[i].split(",", 1)[-1].strip()
                    if wanted_pattern.match(title):
                        if i+1 < len(lines) and lines[i+1].startswith('http'):
                            # Проверка доступности канала
                            try:
                                async with session.head(lines[i+1], timeout=10) as channel_resp:
                                    if channel_resp.status == 200:
                                        valid_entries.extend([lines[i], lines[i+1]])
                            except:
                                continue
                                
            if not valid_entries:
                return None
                
            # Формирование нового плейлиста
            playlist_name = config.PLAYLIST_NAMES.get(url, "default") + ".m3u"
            return playlist_name, "\n".join(["#EXTM3U"] + valid_entries)
            
    except Exception as e:
        logger.error(f"Ошибка обработки {url}: {e}")
        return None

@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "📡 Бот для генерации проверенных IPTV-плейлистов\n"
        "Нажмите кнопку ниже, чтобы начать обработку",
        reply_markup=playlist_keyboard
    )

@dp.message(F.text == "📺 Получить плейлисты")
async def handle_playlists(message: types.Message):
    try:
        msg = await message.answer("🔄 Начинаю обработку плейлистов...")
        
        # Получаем URL из Google Sheets
        urls = sheet.col_values(2)[1:]
        
        # Собираем валидные плейлисты
        valid_playlists = []
        async with aiohttp.ClientSession() as session:
            tasks = [process_single_playlist(url.strip(), session) for url in urls if url.strip()]
            results = await asyncio.gather(*tasks)
            
            for result in results:
                if result:
                    valid_playlists.append(result)

        if not valid_playlists:
            return await message.answer("❌ Не найдено валидных плейлистов")

        # Упаковываем в ZIP-архив
        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for name, content in valid_playlists:
                zip_file.writestr(name, content.encode('utf-8'))
                
        zip_buffer.seek(0)
        
        # Отправка архива
        await message.answer_document(
            BufferedInputFile(
                zip_buffer.getvalue(),
                filename="valid_playlists.zip"
            ),
            caption=f"✅ Готово! Валидных плейлистов: {len(valid_playlists)}"
        )
        
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await message.answer("⚠️ Произошла ошибка при обработке")

# Остальной код (health check, запуск) остается без изменений
