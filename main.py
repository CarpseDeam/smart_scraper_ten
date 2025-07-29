import logging
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, status

import config
from smart_scraper import TenipoScraper
from data_mapper import transform_match_data_to_client_format

app_container = {}
app_settings = config.Settings()
live_data_cache = {"data": {}, "last_updated": None}
CACHE_REFRESH_INTERVAL_SECONDS = 30


async def poll_for_live_data():
    loop = asyncio.get_event_loop()

    # Initialize the blocking scraper in a thread to not block the event loop
    try:
        scraper = await loop.run_in_executor(None, lambda: TenipoScraper(app_settings))
        app_container["scraper_instance"] = scraper
    except Exception as e:
        logging.critical(f"FATAL: Scraper initialization failed. Polling cannot start. Error: {e}")
        return

    while True:
        logging.info("BACKGROUND_POLL: Starting polling cycle...")
        try:
            all_matches_summary = await loop.run_in_executor(None, scraper.get_live_matches_summary)
            itf_matches_summary = [m for m in all_matches_summary if m and "ITF" in m.get("tournament_name", "")]
            logging.info(f"Found {len(itf_matches_summary)} live ITF matches to process.")

            new_cache_data = {}
            for match_summary in itf_matches_summary:
                match_id = match_summary.get("id")
                if not match_id: continue

                raw_data = await loop.run_in_executor(None, lambda: scraper.fetch_match_data(match_id))
                if raw_data:
                    formatted_data = transform_match_data_to_client_format(raw_data)
                    new_cache_data[match_id] = formatted_data

            live_data_cache["data"] = new_cache_data
            live_data_cache["last_updated"] = datetime.now(timezone.utc)
            logging.info(f"BACKGROUND_POLL: Cache updated with {len(new_cache_data)} matches.")
        except Exception as e:
            logging.error(f"BACKGROUND_POLL: Error during polling cycle: {e}")

        await asyncio.sleep(CACHE_REFRESH_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.info("Application startup: Initializing resources...")
    polling_task = asyncio.create_task(poll_for_live_data())
    app_container["polling_task"] = polling_task
    yield
    logging.info("Application shutdown: Cleaning up resources...")
    if polling_task:
        polling_task.cancel()
    if scraper := app_container.get("scraper_instance"):
        # Run the blocking close method in an executor as well
        await asyncio.get_event_loop().run_in_executor(None, scraper.close)
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