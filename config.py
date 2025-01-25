import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
IG_USERNAME = os.getenv("IG_USERNAME")
IG_PASSWORD = os.getenv("IG_PASSWORD")

if BOT_TOKEN is None:
    raise ValueError("BOT_TOKEN environment variable not set")

if IG_USERNAME is None or IG_PASSWORD is None:
    raise ValueError("IG_USERNAME and IG_PASSWORD environment variables must be set")