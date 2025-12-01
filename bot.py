"""Telegram bot for UPC lookups against zvonkodigital APIs.

The bot:
- Accepts UPC codes in messages (one or many separated by space/newline).
- Fetches album info via media.zvonkodigital.ru.
- Checks playlists across multiple platforms for the artist.
- Uses cached OAuth tokens with automatic refresh (see TokenManager).

Environment variables:
- BOT_TOKEN: Telegram bot token (required).
- ACCOUNT_USERNAME / ACCOUNT_PASSWORD: Credentials for account.zvonkodigital.com (required).
- TOKEN_CACHE (optional): Path to store OAuth tokens (defaults to token_cache.json).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
from typing import Iterable, List

import requests
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor

from zvonkodigital_auth import TokenManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

PLAYLIST_PLATFORMS = {
    "vk": "ВКонтакте",
    "yandex": "Яндекс Музыка",
    "mts": "МТС Музыка",
    "zvooq": "Звук",
}

ALBUM_ENDPOINT = "https://media.zvonkodigital.ru/api/albums_list"
PLAYLIST_ENDPOINT = "https://charts.zvonkodigital.ru/playlists/"


class BotService:
    def __init__(self, bot: Bot, token_manager: TokenManager) -> None:
        self.bot = bot
        self.token_manager = token_manager

    def _build_headers(self) -> dict[str, str]:
        access_token = self.token_manager.get_access_token()
        return {"Authorization": f"Bearer {access_token}"}

    def _extract_release_title(self, album: dict) -> str:
        return (
            album.get("album_name")
            or album.get("title")
            or album.get("name")
            or album.get("release_title")
            or "Релиз"
        )

    def lookup_upc(self, upc: str) -> str:
        logger.info("Processing UPC %s", upc)
        headers = self._build_headers()

        album_response = requests.get(ALBUM_ENDPOINT, params={"search": upc}, headers=headers, timeout=20)
        if album_response.status_code != 200:
            logger.error(
                "Album request failed for %s with status %s: %s",
                upc,
                album_response.status_code,
                album_response.text,
            )
            return f"{upc}: ошибка при получении данных альбома"

        album_data = album_response.json()
        albums = album_data.get("albums", [])
        if not albums:
            return f"{upc}: альбом не найден"

        album = albums[0]
        artist_name = album.get("artist_name") or "Неизвестный исполнитель"
        release_title = self._extract_release_title(album)

        logger.info("Found album for %s: %s — %s", upc, artist_name, release_title)

        playlist_date = dt.date.today().isoformat()
        playlist_lines: List[str] = []

        for platform_key, platform_label in PLAYLIST_PLATFORMS.items():
            params = {
                "platform": platform_key,
                "date": playlist_date,
                "limit": 50,
                "offset": 0,
                "q": artist_name,
            }
            logger.debug("Requesting playlists on %s for %s", platform_key, artist_name)
            response = requests.get(PLAYLIST_ENDPOINT, params=params, headers=headers, timeout=20)
            if response.status_code != 200:
                logger.warning("Playlist request failed for %s: %s", platform_key, response.status_code)
                continue

            payload = response.json()
            results = payload.get("results", [])
            logger.info("%s playlists found for %s on %s", len(results), artist_name, platform_key)
            for result in results:
                playlist_name = result.get("playlist_name")
                if not playlist_name:
                    continue
                playlist_lines.append(f"«{playlist_name}» ({platform_label})")

        if not playlist_lines:
            logger.info("No playlists found for %s", artist_name)
            playlist_lines.append("Плейлисты не найдены")

        header = f"{artist_name} - {release_title}"
        return "\n".join([header, *playlist_lines])

    async def handle_message(self, message: types.Message) -> None:
        upc_codes = list(_extract_upc_codes(message.text or ""))
        if not upc_codes:
            await message.reply("Отправьте один или несколько UPC кодов через пробел или новую строку.")
            return

        await self.bot.send_chat_action(message.chat.id, types.ChatActions.TYPING)
        loop = asyncio.get_event_loop()
        parts = []
        for code in upc_codes:
            result = await loop.run_in_executor(None, self.lookup_upc, code)
            parts.append(result)

        await message.reply("\n\n".join(parts))


def _extract_upc_codes(text: str) -> Iterable[str]:
    for token in text.replace("\n", " ").replace("\t", " ").split():
        normalized = token.strip()
        if normalized:
            yield normalized


def main() -> None:
    bot_token = os.environ.get("BOT_TOKEN")
    username = os.environ.get("ACCOUNT_USERNAME")
    password = os.environ.get("ACCOUNT_PASSWORD")
    cache_path = os.environ.get("TOKEN_CACHE")

    if not bot_token:
        raise RuntimeError("BOT_TOKEN is required")
    if not username or not password:
        raise RuntimeError("ACCOUNT_USERNAME and ACCOUNT_PASSWORD are required")

    bot = Bot(token=bot_token, parse_mode=types.ParseMode.HTML)
    manager = TokenManager(username, password, cache_path) if cache_path else TokenManager(username, password)
    service = BotService(bot, manager)

    dp = Dispatcher(bot)
    dp.register_message_handler(service.handle_message)

    executor.start_polling(dp, skip_updates=True)


if __name__ == "__main__":
    main()
