# main.py
import logging
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, status

import config
from background_service import ScrapingService

app_settings = config.Settings()
# The single, authoritative service instance that manages all background work.
scraping_service = ScrapingService(app_settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages the application's lifecycle.
    Starts the scraping service on startup and stops it on shutdown.
    """
    logging.info("Application startup: Starting background service...")
    await scraping_service.start()

    yield

    logging.info("Application shutdown: Stopping background service...")
    await scraping_service.stop()


app = FastAPI(title="Live Tennis Score API", lifespan=lifespan)


@app.get("/all_live_itf_data", status_code=status.HTTP_200_OK)
async def get_all_live_itf_data():
    """Returns all live match data from the service's in-memory cache."""
    cache = scraping_service.live_data_cache
    last_updated = cache["last_updated"]

    if not last_updated or not cache["data"]:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cache is currently empty. Please try again in a moment."
        )

    age_seconds = (datetime.now(timezone.utc) - last_updated).total_seconds()

    return {
        "cache_last_updated_utc": last_updated.isoformat(),
        "cache_age_seconds": round(age_seconds),
        "match_count": len(cache["data"]),
        "matches": list(cache["data"].values())
    }


@app.get("/match/{match_id}", status_code=status.HTTP_200_OK)
async def get_match_data(match_id: str):
    """Returns data for a specific match from the service's cache."""
    match_data = scraping_service.live_data_cache["data"].get(match_id)

    if not match_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Data for match ID '{match_id}' not found in the live cache."
        )

    return match_data


@app.get("/investigate/{match_id}", status_code=status.HTTP_200_OK)
async def investigate_match(match_id: str):
    """
    A temporary debugging endpoint to find new data sources for a given match.
    """
    scraper = scraping_service.scraper
    if not scraper:
        raise HTTPException(status_code=503, detail="Scraper not available.")

    logging.info(f"Received investigation request for match ID: {match_id}")
    urls = await asyncio.get_event_loop().run_in_executor(None, lambda: scraper.investigate_data_sources(match_id))

    return {
        "message": "Investigation complete. Check logs for captured URLs.",
        "match_id": match_id,
        "urls_found": len(urls)
    }