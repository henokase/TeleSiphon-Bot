import os
from telethon import TelegramClient
from telethon.sessions import StringSession
from dotenv import load_dotenv

load_dotenv()

API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
STRING_SESSION = os.getenv("TELEGRAM_STRING_SESSION")

if not API_ID or not API_HASH or not STRING_SESSION:
    raise ValueError("API_ID, API_HASH, and TELEGRAM_STRING_SESSION must be set in .env")

client = TelegramClient(StringSession(STRING_SESSION), int(API_ID), API_HASH)
