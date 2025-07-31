import logging
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, status

import config
from smart_scraper import TenipoScraper
from data_mapper import transform_match_data_to_client_format
from database import MongoManager

app_container = {}
app_settings = config.Settings()
live_data_cache = {"data": {}, "last_updated": None}
CACHE_REFRESH_INTERVAL_SECONDS = 30


async def poll_for_live_data():
    loop = asyncio.get_event_loop()
    scraper = app_container.get("scraper_instance")
    mongo_manager = app_container.get("mongo_manager")

    if not scraper:
        logging.critical("Scraper not found in app_container. Polling cannot start.")
        return

    while True:
        logging.info("BACKGROUND_POLL: Starting polling cycle...")
        try:
            all_matches_summary = await loop.run_in_executor(None, scraper.get_live_matches_summary)

            # THE FIX: Make the filter stricter to only include pure ITF matches.
            # It now checks for "ITF" and explicitly excludes "ATP".
            itf_matches_summary = [
                m for m in all_matches_summary if m and
                                                  "ITF" in m.get("tournament_name", "") and
                                                  "ATP" not in m.get("tournament_name", "")
            ]

            logging.info(f"Found {len(itf_matches_summary)} live ITF matches to process.")

            if mongo_manager and mongo_manager.client:
                live_match_ids = [m['id'] for m in itf_matches_summary if m and 'id' in m]
                await loop.run_in_executor(None, lambda: mongo_manager.prune_completed_matches(live_match_ids))

            new_cache_data = {}
            for match_summary in itf_matches_summary:
                match_id = match_summary.get("id")
                if not match_id: continue

                raw_data = await loop.run_in_executor(None, lambda: scraper.fetch_match_data(match_id))
                if raw_data:
                    formatted_data = transform_match_data_to_client_format(raw_data, match_id)
                    new_cache_data[match_id] = formatted_data

                    if mongo_manager and mongo_manager.client:
                        await loop.run_in_executor(None,
                                                   lambda: mongo_manager.save_match_data(match_id, formatted_data))

            live_data_cache["data"] = new_cache_data
            live_data_cache["last_updated"] = datetime.now(timezone.utc)
            logging.info(f"BACKGROUND_POLL: Cache updated with {len(new_cache_data)} matches.")
        except Exception as e:
            logging.error(f"BACKGROUND_POLL: Error during polling cycle: {e}", exc_info=True)

        await asyncio.sleep(CACHE_REFRESH_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.info("Application startup: Initializing resources...")
    try:
        scraper = await asyncio.get_event_loop().run_in_executor(None, lambda: TenipoScraper(app_settings))
        app_container["scraper_instance"] = scraper
    except Exception as e:
        logging.critical(f"FATAL: Scraper initialization failed. Application will not start polling. Error: {e}")
        scraper = None

    mongo_manager = MongoManager(app_settings)
    app_container["mongo_manager"] = mongo_manager

    if scraper:
        polling_task = asyncio.create_task(poll_for_live_data())
        app_container["polling_task"] = polling_task
    else:
        polling_task = None

    yield

    logging.info("Application shutdown: Cleaning up resources...")
    if polling_task:
        polling_task.cancel()
    if scraper := app_container.get("scraper_instance"):
        await asyncio.get_event_loop().run_in_executor(None, scraper.close)
    if mongo_manager := app_container.get("mongo_manager"):
        mongo_manager.close()
    logging.info("Shutdown cleanup complete.")


app = FastAPI(title="Live Tennis Score API", lifespan=lifespan)


@app.get("/all_live_itf_data", status_code=status.HTTP_200_OK)
async def get_all_live_itf_data():
    last_updated = live_data_cache["last_updated"]
    if not last_updated or not live_data_cache["data"]:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Cache is empty. Please try again.")
    age_seconds = (datetime.now(timezone.utc) - last_updated).total_seconds()
    return {
        "cache_last_updated_utc": last_updated.isoformat(),
        "cache_age_seconds": round(age_seconds),
        "match_count": len(live_data_cache["data"]),
        "matches": list(live_data_cache["data"].values())
    }


@app.get("/match/{match_id}", status_code=status.HTTP_200_OK)
async def get_match_data(match_id: str):
    match_data = live_data_cache["data"].get(match_id)
    if not match_data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Match ID '{match_id}' not found.")
    return match_data


@app.get("/investigate/{match_id}", status_code=status.HTTP_200_OK)
async def investigate_match(match_id: str):
    """
    A temporary debugging endpoint to find new data sources for a given match.
    """
    scraper = app_container.get("scraper_instance")
    if not scraper:
        raise HTTPException(status_code=503, detail="Scraper not available.")

    logging.info(f"Received investigation request for match ID: {match_id}")
    urls = await asyncio.get_event_loop().run_in_executor(None, lambda: scraper.investigate_data_sources(match_id))

    return {"message": "Investigation complete. Check logs for captured URLs.", "match_id": match_id,
            "urls_found": len(urls)}