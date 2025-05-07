# bot.py
import os
import telebot
import requests
from dotenv import load_dotenv
import config

load_dotenv()
TOKEN = os.getenv('TELEGRAM_TOKEN')

if not TOKEN:
    print("Ошибка: TELEGRAM_TOKEN должен быть указан в .env")
    exit(1)

bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=['start'])
def start_handler(message):
    bot.reply_to(message, "Привет! Отправь /getplaylist, чтобы получить обновлённый IPTV-плейлист.")

@bot.message_handler(commands=['getplaylist'])
def get_playlist_handler(message):
    chat_id = message.chat.id
    bot.send_message(chat_id, "Собираю список доступных каналов. Подождите...")
    working_entries = []

    for playlist_url in config.PLAYLIST_URLS:
        try:
            res = requests.get(playlist_url, timeout=10)
        except requests.RequestException as e:
            print(f"Ошибка при скачивании {playlist_url}: {e}")
            continue
        if res.status_code != 200:
            print(f"{playlist_url} вернул статус {res.status_code}")
            continue

        lines = res.text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("#EXTINF"):
                parts = line.split(',', 1)
                if len(parts) < 2:
                    continue
                channel_name = parts[1].strip()
                if channel_name in config.CHANNEL_NAMES:
                    if i + 1 < len(lines):
                        stream_url = lines[i + 1].strip()
                        if not stream_url.startswith("http"):
                            continue
                        try:
                            head_res = requests.head(stream_url, timeout=10, allow_redirects=True)
                            if head_res.status_code != 200:
                                get_res = requests.get(stream_url, timeout=10, allow_redirects=True)
                                if get_res.status_code != 200:
                                    continue
                        except requests.RequestException:
                            continue
                        working_entries.append((line, stream_url))

    if not working_entries:
        bot.send_message(chat_id, "Не удалось найти рабочие потоки.")
        return

    m3u_lines = ["#EXTM3U"]
    for extinf, url in working_entries:
        m3u_lines.append(extinf)
        m3u_lines.append(url)

    playlist_data = "\n".join(m3u_lines)
    temp_filename = "result.m3u"
    with open(temp_filename, "w", encoding="utf-8") as f:
        f.write(playlist_data)

    try:
        with open(temp_filename, "rb") as f:
            upload_res = requests.post("https://file.io", files={"file": f})
        file_link = upload_res.json().get("link") or upload_res.json().get("url")
    except Exception as e:
        bot.send_message(chat_id, f"Ошибка загрузки файла: {e}")
        return

    if not file_link:
        bot.send_message(chat_id, "Не удалось получить ссылку.")
    else:
        bot.send_message(chat_id, f"Ваш плейлист готов: {file_link}")

if __name__ == "__main__":
    print("Бот запущен.")
    bot.infinity_polling(skip_pending=True)
