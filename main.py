import logging
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException

import config
from smart_scraper import TenipoScraper
from data_mapper import transform_match_data_to_client_format

# --- Globals & Caching Setup ---
scraper_container = {}
app_settings = config.Settings()

# Cache to store the results of our scraping
# The key will be the match ID, the value will be the formatted data
live_data_cache = {
    "data": {},
    "last_updated": None
}
# How often we should perform a full re-scrape (e.g., every 30 seconds)
CACHE_EXPIRATION_SECONDS = 30


# --- Background Task ---
async def poll_for_live_data():
    """A background task that runs continuously to keep the cache fresh."""
    while True:
        scraper = scraper_container.get("scraper_instance")
        if scraper:
            logging.info("BACKGROUND_POLL: Starting polling cycle...")
            try:
                # 1. Get the list of live ITF matches
                all_matches = scraper.get_live_matches_summary()
                itf_matches_summary = [
                    m for m in all_matches if m and "ITF" in m.get("tournament_name", "")
                ]

                new_cache_data = {}
                # 2. Fetch detailed data for each match
                for match_summary in itf_matches_summary:
                    match_id = match_summary.get("id")
                    if not match_id: continue

                    raw_data = scraper.fetch_match_data(match_id)
                    if raw_data:
                        formatted_data = transform_match_data_to_client_format(raw_data)
                        new_cache_data[match_id] = formatted_data

                # 3. Update the global cache
                live_data_cache["data"] = new_cache_data
                live_data_cache["last_updated"] = datetime.now(timezone.utc)
                logging.info(f"BACKGROUND_POLL: Cache updated with {len(new_cache_data)} live ITF matches.")

            except Exception as e:
                logging.error(f"BACKGROUND_POLL: Error during polling cycle: {e}", exc_info=True)

        # Wait for the next polling interval
        await asyncio.sleep(CACHE_EXPIRATION_SECONDS)


# --- FastAPI Lifespan & App ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.info("Application startup: Initializing TenipoScraper...")
    try:
        scraper_container["scraper_instance"] = TenipoScraper(app_settings)
        logging.info("Scraper initialized successfully.")
        # Start the background polling task
        loop = asyncio.get_event_loop()
        task = loop.create_task(poll_for_live_data())
        scraper_container["polling_task"] = task
    except Exception as e:
        logging.critical(f"Failed to initialize scraper on startup: {e}", exc_info=True)

    yield

    logging.info("Application shutdown: Closing TenipoScraper...")
    if "polling_task" in scraper_container:
        scraper_container["polling_task"].cancel()
    if "scraper_instance" in scraper_container:
        scraper_container["scraper_instance"].close()
    logging.info("Scraper closed.")


app = FastAPI(lifespan=lifespan)


# --- API Endpoints (Now serving from the cache) ---

@app.get("/all_live_itf_data")
async def get_all_live_itf_data():
    """
    TOOL ENDPOINT: Returns the cached data for all live ITF matches.
    The data is updated in the background automatically.
    """
    last_updated = live_data_cache["last_updated"]
    if not last_updated or not live_data_cache["data"]:
        raise HTTPException(status_code=503, detail="Cache is currently empty. Please try again in a moment.")

    # Calculate how old the data is
    age_seconds = (datetime.now(timezone.utc) - last_updated).total_seconds()

    return {
        "cache_last_updated_utc": last_updated.isoformat(),
        "cache_age_seconds": round(age_seconds),
        "match_count": len(live_data_cache["data"]),
        "matches": list(live_data_cache["data"].values())
    }


@app.get("/match/{match_id}")
async def get_match_data(match_id: str):
    """
    TOOL ENDPOINT: Returns cached data for a specific live ITF match ID.
    """
    match_data = live_data_cache["data"].get(match_id)

    if not match_data:
        raise HTTPException(status_code=404, detail=f"Data for match ID {match_id} not found in the live cache.")

    return match_data