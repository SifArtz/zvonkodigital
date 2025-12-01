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
from pathlib import Path
from typing import List, Optional

from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor

from upc_service import PlaylistHit, UpcRepository, UpcService, extract_upc_codes, group_by_week
from zvonkodigital_auth import TokenManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

def main() -> None:
    bot_token = os.environ.get("BOT_TOKEN")
    username = os.environ.get("ACCOUNT_USERNAME")
    password = os.environ.get("ACCOUNT_PASSWORD")
    cache_path = os.environ.get("TOKEN_CACHE")
    db_path = os.environ.get("UPC_DB", "upc_checks.db")

    if not bot_token:
        raise RuntimeError("BOT_TOKEN is required")
    if not username or not password:
        raise RuntimeError("ACCOUNT_USERNAME and ACCOUNT_PASSWORD are required")

    bot = Bot(token=bot_token, parse_mode=types.ParseMode.HTML)
    manager = TokenManager(username, password, cache_path) if cache_path else TokenManager(username, password)
    repo = UpcRepository(db_path)
    service = UpcService(manager, repo)

    dp = Dispatcher(bot)
    async def handle_message(message: types.Message) -> None:
        upc_codes = list(extract_upc_codes(message.text or ""))
        if not upc_codes:
            await message.reply("Отправьте один или несколько UPC кодов через пробел или новую строку.")
            return

        await bot.send_chat_action(message.chat.id, types.ChatActions.TYPING)
        today = dt.date.today()
        results = await service.process_upc_codes(upc_codes, today=today)
        playlist_hits = [item.hit for item in results if item.hit]
        notes = [item.note for item in results if item.note]

        if not playlist_hits and notes:
            await message.reply("\n".join(notes))
            return
        if not playlist_hits:
            await message.reply("Плейлисты не найдены для переданных UPC.")
            return

        grouped = group_by_week(playlist_hits)
        lines: List[str] = []
        lines.extend(notes)
        for week, week_hits in grouped:
            lines.append(f"{week}:")
            for hit in week_hits:
                header = f"{hit.artist} - {hit.release_title}"
                lines.append(header)
                lines.extend(hit.playlists)
            lines.append("")
        await message.reply("\n".join(lines).strip())

    dp.register_message_handler(handle_message)

    async def _on_startup(_: Dispatcher) -> None:  # pragma: no cover - invoked by aiogram loop
        service.start_scheduler()

    async def _on_shutdown(dispatcher: Dispatcher) -> None:  # pragma: no cover - invoked by aiogram loop
        await service.close()
        await dispatcher.storage.close()
        await dispatcher.storage.wait_closed()

    executor.start_polling(dp, skip_updates=True, on_startup=_on_startup, on_shutdown=_on_shutdown)


if __name__ == "__main__":
    main()
