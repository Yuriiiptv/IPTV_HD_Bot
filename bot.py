import asyncio
import aiohttp
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.types import InputFile
from aiogram.utils import executor
from config import BOT_TOKEN, CHANNEL_NAMES, M3U_URLS

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

async def fetch_m3u(session, url):
    try:
        async with session.get(url, timeout=10) as response:
            if response.status == 200:
                return await response.text()
    except Exception as e:
        logging.warning(f"Failed to fetch {url}: {e}")
    return ''

def parse_m3u(content):
    lines = content.splitlines()
    channels = []
    for i in range(len(lines)):
        if lines[i].startswith('#EXTINF'):
            if i + 1 < len(lines):
                channels.append((lines[i], lines[i + 1]))
    return channels

def filter_channels(channels, names):
    filtered = []
    for extinf, url in channels:
        for name in names:
            if name.lower() in extinf.lower():
                filtered.append((extinf, url))
                break
    return filtered

async def check_url(session, url):
    try:
        async with session.head(url, timeout=5) as response:
            return response.status == 200
    except:
        return False

async def validate_channels(channels):
    valid = []
    async with aiohttp.ClientSession() as session:
        tasks = []
        for extinf, url in channels:
            tasks.append(check_url(session, url))
        results = await asyncio.gather(*tasks)
        for i, result in enumerate(results):
            if result:
                valid.append(channels[i])
    return valid

@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    await message.reply("Привет! Отправь команду /получить_плейлист, чтобы получить актуальный плейлист.")

@dp.message_handler(commands=['получить_плейлист'])
async def get_playlist(message: types.Message):
    await message.reply("Собираю плейлист, подождите...")
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_m3u(session, url) for url in M3U_URLS]
        contents = await asyncio.gather(*tasks)
    all_channels = []
    for content in contents:
        all_channels.extend(parse_m3u(content))
    filtered = filter_channels(all_channels, CHANNEL_NAMES)
    valid = await validate_channels(filtered)
    if not valid:
        await message.reply("Не удалось найти рабочие каналы.")
        return
    playlist = '#EXTM3U\n' + '\n'.join([f"{extinf}\n{url}" for extinf, url in valid])
    with open('playlist.m3u', 'w', encoding='utf-8') as f:
        f.write(playlist)
    await message.reply_document(InputFile('playlist.m3u'))

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
