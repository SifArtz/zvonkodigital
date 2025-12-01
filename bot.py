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
import contextlib
import datetime as dt
import logging
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

import aiohttp
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


@dataclass(slots=True)
class PlaylistHit:
    artist: str
    release_title: str
    week_label: str
    playlists: List[str]


@dataclass(slots=True)
class LookupResult:
    hit: Optional[PlaylistHit]
    note: Optional[str] = None


class UpcRepository:
    """Lightweight SQLite persistence for UPC checks."""

    def __init__(self, db_path: str | Path = "upc_checks.db") -> None:
        self.db_path = Path(db_path)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS upc_checks (
                    upc TEXT PRIMARY KEY,
                    artist TEXT,
                    release_title TEXT,
                    release_date TEXT,
                    next_check TEXT,
                    attempts_remaining INTEGER
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def upsert(
        self,
        upc: str,
        artist: str,
        release_title: str,
        release_date: dt.date,
        next_check: dt.date,
        attempts_remaining: int,
    ) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO upc_checks (upc, artist, release_title, release_date, next_check, attempts_remaining)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(upc) DO UPDATE SET
                    artist=excluded.artist,
                    release_title=excluded.release_title,
                    release_date=excluded.release_date,
                    next_check=excluded.next_check,
                    attempts_remaining=excluded.attempts_remaining
                """,
                (
                    upc,
                    artist,
                    release_title,
                    release_date.isoformat(),
                    next_check.isoformat(),
                    attempts_remaining,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def delete(self, upc: str) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("DELETE FROM upc_checks WHERE upc = ?", (upc,))
            conn.commit()
        finally:
            conn.close()

    def get_due(self, today: dt.date) -> List[dict]:
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                "SELECT upc, artist, release_title, release_date, next_check, attempts_remaining FROM upc_checks WHERE next_check <= ?",
                (today.isoformat(),),
            )
            rows = cursor.fetchall()
            return [
                {
                    "upc": row[0],
                    "artist": row[1],
                    "release_title": row[2],
                    "release_date": dt.date.fromisoformat(row[3]),
                    "next_check": dt.date.fromisoformat(row[4]),
                    "attempts_remaining": row[5],
                }
                for row in rows
            ]
        finally:
            conn.close()

    def get(self, upc: str) -> Optional[dict]:
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                "SELECT upc, artist, release_title, release_date, next_check, attempts_remaining FROM upc_checks WHERE upc = ?",
                (upc,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "upc": row[0],
                "artist": row[1],
                "release_title": row[2],
                "release_date": dt.date.fromisoformat(row[3]),
                "next_check": dt.date.fromisoformat(row[4]),
                "attempts_remaining": row[5],
            }
        finally:
            conn.close()


class BotService:
    def __init__(self, bot: Bot, token_manager: TokenManager, repo: UpcRepository) -> None:
        self.bot = bot
        self.token_manager = token_manager
        self.repo = repo
        self.session: aiohttp.ClientSession | None = None
        self._background_task: asyncio.Task | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self.session = aiohttp.ClientSession(timeout=timeout)
            logger.info("Created aiohttp session")
        return self.session

    async def _get_headers(self) -> dict[str, str]:
        access_token = await asyncio.to_thread(self.token_manager.get_access_token)
        return {"Authorization": f"Bearer {access_token}"}

    def _extract_release_title(self, album: dict) -> str:
        return (
            album.get("album_name")
            or album.get("title")
            or album.get("name")
            or album.get("release_title")
            or "Релиз"
        )

    def _release_matches(self, result: dict, release_title: str) -> bool:
        normalized_release = (release_title or "").casefold()
        track_name = (result.get("track_name") or "").casefold()
        album_name = (result.get("album_name") or "").casefold()
        return normalized_release in track_name or normalized_release in album_name

    def _week_label(self, release_date: dt.date) -> str:
        week_start = release_date - dt.timedelta(days=release_date.weekday())
        week_end = week_start + dt.timedelta(days=6)
        return f"Неделя {week_start:%d.%m} - {week_end:%d.%m}"

    def _playlist_date(self, release_date: dt.date, today: dt.date) -> dt.date:
        target = release_date + dt.timedelta(days=7)
        if target > today:
            return today
        return target

    async def _fetch_playlists(
        self,
        session: aiohttp.ClientSession,
        headers: dict[str, str],
        platform_key: str,
        platform_label: str,
        artist_name: str,
        release_title: str,
        playlist_date: str,
    ) -> List[str]:
        params = {
            "platform": platform_key,
            "date": playlist_date,
            "limit": 50,
            "offset": 0,
            "q": artist_name,
        }
        logger.debug("Requesting playlists on %s for %s", platform_key, artist_name)
        try:
            response = await session.get(PLAYLIST_ENDPOINT, params=params, headers=headers)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Playlist request error on %s: %s", platform_key, exc)
            return []

        if response.status != 200:
            logger.warning("Playlist request failed for %s: %s", platform_key, response.status)
            return []

        payload = await response.json()
        results = payload.get("results", [])
        logger.info("%s playlists found for %s on %s", len(results), artist_name, platform_key)

        playlist_lines: List[str] = []
        for result in results:
            playlist_name = result.get("playlist_name")
            if not playlist_name:
                continue
            if not self._release_matches(result, release_title):
                logger.debug("Skipping playlist %s on %s: release mismatch", playlist_name, platform_key)
                continue
            position = result.get("position")
            note = f"(позиция {position})" if position is not None else "(Плейлист подборка)"
            playlist_lines.append(f"«{playlist_name}» ({platform_label}) {note}")

        return playlist_lines

    async def _fetch_album(self, upc: str, headers: dict[str, str], session: aiohttp.ClientSession) -> Optional[dict]:
        try:
            album_response = await session.get(
                ALBUM_ENDPOINT, params={"search": upc}, headers=headers, timeout=aiohttp.ClientTimeout(total=20)
            )
        except Exception as exc:  # pragma: no cover - network defensive
            logger.error("Album request error for %s: %s", upc, exc)
            return None
        if album_response.status != 200:
            text = await album_response.text()
            logger.error(
                "Album request failed for %s with status %s: %s",
                upc,
                album_response.status,
                text,
            )
            return None

        album_data = await album_response.json()
        albums = album_data.get("albums", [])
        return albums[0] if albums else None

    async def _check_playlists_for_album(
        self,
        artist_name: str,
        release_title: str,
        playlist_date: dt.date,
        headers: dict[str, str],
        session: aiohttp.ClientSession,
    ) -> List[str]:
        playlist_lines: List[str] = []
        tasks = [
            self._fetch_playlists(
                session,
                headers,
                platform_key,
                platform_label,
                artist_name,
                release_title,
                playlist_date.isoformat(),
            )
            for platform_key, platform_label in PLAYLIST_PLATFORMS.items()
        ]

        for task_result in await asyncio.gather(*tasks):
            playlist_lines.extend(task_result)
        return playlist_lines

    async def _schedule_record(
        self,
        upc: str,
        artist: str,
        release_title: str,
        release_date: dt.date,
        today: dt.date,
    ) -> Optional[str]:
        attempts_remaining = 2
        if release_date > today:
            next_check = release_date
            logger.info("Release %s not out yet; scheduling first check on %s", upc, next_check)
            await asyncio.to_thread(
                self.repo.upsert,
                upc,
                artist,
                release_title,
                release_date,
                next_check,
                attempts_remaining,
            )
            return f"{upc}: релиз ещё не вышел, проверка запланирована на {next_check:%d.%m.%Y}"

        next_check = today
        logger.info("Scheduling immediate check for %s with %s retries", upc, attempts_remaining)
        await asyncio.to_thread(
            self.repo.upsert,
            upc,
            artist,
            release_title,
            release_date,
            next_check,
            attempts_remaining,
        )
        return None

    async def _process_single_upc(self, upc: str, today: dt.date) -> LookupResult:
        logger.info("Processing UPC %s", upc)
        headers = await self._get_headers()
        session = await self._get_session()

        album = await self._fetch_album(upc, headers, session)
        if not album:
            return LookupResult(hit=None, note=f"{upc}: альбом не найден")

        artist_name = album.get("artist_name") or "Неизвестный исполнитель"
        release_title = self._extract_release_title(album)
        release_date_raw = album.get("sales_start_date") or album.get("release_date")
        if not release_date_raw:
            logger.warning("No sales_start_date for %s; skipping", upc)
            return LookupResult(hit=None, note=f"{upc}: нет даты начала продаж")
        release_date = dt.date.fromisoformat(release_date_raw[:10])

        existing = await asyncio.to_thread(self.repo.get, upc)
        if not existing:
            scheduled_note = await self._schedule_record(upc, artist_name, release_title, release_date, today)
            if scheduled_note:
                logger.info("UPC %s scheduled only: %s", upc, scheduled_note)
                return LookupResult(hit=None, note=scheduled_note)

        target_date = self._playlist_date(release_date, today)
        playlist_lines = await self._check_playlists_for_album(
            artist_name=artist_name,
            release_title=release_title,
            playlist_date=target_date,
            headers=headers,
            session=session,
        )

        if playlist_lines:
            logger.info("Playlists found for %s", upc)
            await asyncio.to_thread(self.repo.delete, upc)
            return LookupResult(
                hit=PlaylistHit(
                    artist=artist_name,
                    release_title=release_title,
                    week_label=self._week_label(release_date),
                    playlists=playlist_lines,
                )
            )

        logger.info("No playlists found for %s on %s", upc, target_date)
        cutoff_date = release_date + dt.timedelta(days=7)
        if target_date >= cutoff_date:
            logger.info("Reached post-release-week window for %s; removing from queue", upc)
            await asyncio.to_thread(self.repo.delete, upc)
            return LookupResult(hit=None, note=None)

        record = existing or {
            "upc": upc,
            "artist": artist_name,
            "release_title": release_title,
            "release_date": release_date,
            "next_check": today,
            "attempts_remaining": 2,
        }
        attempts_left = record.get("attempts_remaining", 0)
        if attempts_left <= 0:
            logger.info("Attempts exhausted for %s; removing from queue", upc)
            await asyncio.to_thread(self.repo.delete, upc)
            return LookupResult(hit=None, note=None)

        next_check = min(cutoff_date, today + dt.timedelta(days=7))
        await asyncio.to_thread(
            self.repo.upsert,
            upc,
            artist_name,
            release_title,
            release_date,
            next_check,
            attempts_left - 1,
        )
        logger.info("Scheduled next check for %s on %s (remaining %s)", upc, next_check, attempts_left - 1)
        return LookupResult(hit=None, note=None)

    async def lookup_upc(self, upc: str, today: Optional[dt.date] = None) -> LookupResult:
        today = today or dt.date.today()
        return await self._process_single_upc(upc, today)

    async def handle_message(self, message: types.Message) -> None:
        upc_codes = list(_extract_upc_codes(message.text or ""))
        if not upc_codes:
            await message.reply("Отправьте один или несколько UPC кодов через пробел или новую строку.")
            return

        await self.bot.send_chat_action(message.chat.id, types.ChatActions.TYPING)
        today = dt.date.today()
        results = await asyncio.gather(*(self.lookup_upc(code, today=today) for code in upc_codes))
        playlist_hits = [item.hit for item in results if item.hit]
        notes = [item.note for item in results if item.note]

        if not playlist_hits and notes:
            await message.reply("\n".join(notes))
            return
        if not playlist_hits:
            await message.reply("Плейлисты не найдены для переданных UPC.")
            return

        grouped = _group_by_week(playlist_hits)
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

    async def _run_scheduler(self) -> None:
        while True:  # pragma: no cover - background loop
            today = dt.date.today()
            due_items = await asyncio.to_thread(self.repo.get_due, today)
            if due_items:
                logger.info("Processing %s scheduled UPCs", len(due_items))
            for item in due_items:
                result = await self.lookup_upc(item["upc"], today=today)
                if result.hit:
                    logger.info(
                        "Background: playlists found for %s — %s", result.hit.artist, result.hit.release_title
                    )
                elif result.note:
                    logger.info("Background note for %s: %s", item["upc"], result.note)
            await asyncio.sleep(600)

    def start_scheduler(self) -> None:
        if not self._background_task:
            self._background_task = asyncio.create_task(self._run_scheduler())

    async def close(self) -> None:
        if self._background_task:
            self._background_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._background_task
        if self.session and not self.session.closed:
            await self.session.close()
            logger.info("aiohttp session closed")


def _extract_upc_codes(text: str) -> Iterable[str]:
    for token in text.replace("\n", " ").replace("\t", " ").split():
        normalized = token.strip()
        if normalized:
            yield normalized


def _group_by_week(hits: Sequence[PlaylistHit]) -> List[tuple[str, List[PlaylistHit]]]:
    grouped: dict[str, List[PlaylistHit]] = {}
    for hit in hits:
        grouped.setdefault(hit.week_label, []).append(hit)
    return list(grouped.items())


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
    service = BotService(bot, manager, repo)

    dp = Dispatcher(bot)
    dp.register_message_handler(service.handle_message)

    async def _on_startup(_: Dispatcher) -> None:  # pragma: no cover - invoked by aiogram loop
        service.start_scheduler()

    async def _on_shutdown(dispatcher: Dispatcher) -> None:  # pragma: no cover - invoked by aiogram loop
        await service.close()
        await dispatcher.storage.close()
        await dispatcher.storage.wait_closed()

    executor.start_polling(dp, skip_updates=True, on_startup=_on_startup, on_shutdown=_on_shutdown)


if __name__ == "__main__":
    main()
