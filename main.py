import logging
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException

import config
from smart_scraper import TenipoScraper
from data_mapper import transform_match_data_to_client_format

scraper_container = {}
app_settings = config.Settings()

live_data_cache = {
    "data": {},
    "last_updated": None
}
CACHE_EXPIRATION_SECONDS = 30


def blocking_scraper_init():
    """Initializes the scraper in a blocking way, to be run in an executor."""
    try:
        scraper = TenipoScraper(app_settings)
        scraper_container["scraper_instance"] = scraper
        return True
    except Exception as e:
        logging.critical(f"BACKGROUND_POLL: Failed to initialize scraper, polling cannot start. Error: {e}",
                         exc_info=True)
        return False


async def poll_for_live_data():
    """A background task that runs continuously to keep the cache fresh."""
    loop = asyncio.get_event_loop()
    # Run the slow, blocking scraper initialization in a separate thread
    initialized = await loop.run_in_executor(None, blocking_scraper_init)
    if not initialized:
        return

    scraper = scraper_container["scraper_instance"]

    while True:
        logging.info("BACKGROUND_POLL: Starting polling cycle...")
        try:
            # Run the blocking I/O calls in the executor
            all_matches = await loop.run_in_executor(None, scraper.get_live_matches_summary)
            itf_matches_summary = [m for m in all_matches if m and "ITF" in m.get("tournament_name", "")]

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
            logging.info(f"BACKGROUND_POLL: Cache updated with {len(new_cache_data)} live ITF matches.")

        except Exception as e:
            logging.error(f"BACKGROUND_POLL: Error during polling cycle: {e}", exc_info=True)

        await asyncio.sleep(CACHE_EXPIRATION_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.info("Application startup: Starting background polling task...")
    loop = asyncio.get_event_loop()
    task = loop.create_task(poll_for_live_data())
    scraper_container["polling_task"] = task

    yield

    logging.info("Application shutdown: Cleaning up resources...")
    if "polling_task" in scraper_container:
        scraper_container["polling_task"].cancel()
    if "scraper_instance" in scraper_container:
        scraper_container["scraper_instance"].close()
    logging.info("Shutdown cleanup complete.")


app = FastAPI(lifespan=lifespan)


@app.get("/all_live_itf_data")
async def get_all_live_itf_data():
    last_updated = live_data_cache["last_updated"]
    if not last_updated or not live_data_cache["data"]:
        raise HTTPException(status_code=503, detail="Cache is currently empty. Please try again in a moment.")

    age_seconds = (datetime.now(timezone.utc) - last_updated).total_seconds()

    return {
        "cache_last_updated_utc": last_updated.isoformat(),
        "cache_age_seconds": round(age_seconds),
        "match_count": len(live_data_cache["data"]),
        "matches": list(live_data_cache["data"].values())
    }


@app.get("/match/{match_id}")
async def get_match_data(match_id: str):
    match_data = live_data_cache["data"].get(match_id)

    if not match_data:
        raise HTTPException(status_code=404, detail=f"Data for match ID {match_id} not found in the live cache.")

    return match_data