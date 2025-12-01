"""FastAPI web UI for managing UPC checks and viewing playlist hits."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
from pathlib import Path
from typing import List

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from upc_service import PlaylistHit, UpcRepository, UpcService
from zvonkodigital_auth import TokenManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Zvonkodigital playlist checker")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _init_service() -> UpcService:
    username = os.environ.get("ACCOUNT_USERNAME")
    password = os.environ.get("ACCOUNT_PASSWORD")
    cache_path = os.environ.get("TOKEN_CACHE")
    db_path = os.environ.get("UPC_DB", "upc_checks.db")

    if not username or not password:
        raise RuntimeError("ACCOUNT_USERNAME and ACCOUNT_PASSWORD are required")

    manager = TokenManager(username, password, cache_path) if cache_path else TokenManager(username, password)
    repo = UpcRepository(db_path)
    return UpcService(manager, repo)


service = _init_service()


@app.on_event("startup")
async def startup() -> None:  # pragma: no cover - lifecycle hook
    service.start_scheduler()


@app.on_event("shutdown")
async def shutdown() -> None:  # pragma: no cover - lifecycle hook
    await service.close()


@app.get("/")
async def index() -> RedirectResponse:
    return RedirectResponse(url="/static/index.html", status_code=302)


@app.get("/releases")
async def releases_page() -> RedirectResponse:
    return RedirectResponse(url="/static/releases.html", status_code=302)


def _serialize_hit(hit: PlaylistHit, upc: str) -> dict:
    return {
        "upc": upc,
        "artist": hit.artist,
        "release_title": hit.release_title,
        "week_label": hit.week_label,
        "release_date": hit.release_date.isoformat(),
        "playlists": hit.playlists,
    }


@app.post("/api/upcs")
async def submit_upcs(payload: dict) -> JSONResponse:
    upcs = payload.get("upcs") if isinstance(payload, dict) else None
    if not upcs or not isinstance(upcs, list):
        raise HTTPException(status_code=400, detail="Body must include list 'upcs'")

    today = dt.date.today()
    results = await service.process_upc_codes(upcs, today=today)
    hits = [_serialize_hit(item.hit, upcs[idx]) for idx, item in enumerate(results) if item.hit]
    notes = [item.note for item in results if item.note]
    return JSONResponse({"hits": hits, "notes": notes})


@app.get("/api/hits")
async def list_hits() -> JSONResponse:
    records = await asyncio.to_thread(service.repo.list_hits)
    serialized: List[dict] = []
    for record in records:
        serialized.append(
            {
                "upc": record["upc"],
                "artist": record["artist"],
                "release_title": record["release_title"],
                "week_label": record["week_label"],
                "release_date": record["release_date"].isoformat(),
                "playlists": record["playlists"],
                "found_at": record["found_at"],
            }
        )
    return JSONResponse({"hits": serialized})

