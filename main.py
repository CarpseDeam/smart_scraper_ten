import logging
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, status

import config
from smart_scraper import TenipoClient
from data_mapper import transform_match_data_to_client_format

# --- Global State Management ---

# A simple dictionary to hold our running background task and client instance.
# This allows the lifespan manager to access and shut them down gracefully.
app_container = {}

# The in-memory cache for our tennis data.
live_data_cache = {
    "data": {},
    "last_updated": None
}

# How often (in seconds) the background task will poll for new data.
CACHE_REFRESH_INTERVAL_SECONDS = 30


# --- Core Application Logic ---

async def poll_for_live_data():
    """
    This is the main engine of our application. It runs in the background for the
    entire lifespan of the app, continuously updating our data cache.
    """
    # Initialize our custom HTTP client for Tenipo.
    client = TenipoClient(config.Settings())
    app_container["client_instance"] = client

    # This loop runs forever until the application is shut down.
    while True:
        logging.info("BACKGROUND_POLL: Starting new polling cycle...")
        try:
            # 1. Fetch the summary list of all currently live matches.
            all_matches_summary = await client.get_live_matches_summary()

            # 2. Filter this list to only include the ITF matches our client cares about.
            itf_matches_summary = [
                m for m in all_matches_summary
                if m and "ITF" in m.get("tournament_name", "")
            ]

            # 3. Build a new, fresh cache in a temporary dictionary.
            new_cache_data = {}
            for match_summary in itf_matches_summary:
                match_id = match_summary.get("id")
                if not match_id:
                    continue  # Skip if a match in the summary has no ID.

                # Fetch the detailed, decoded data for this specific match.
                raw_data = await client.fetch_match_data(match_id)
                if raw_data:
                    # 4. Transform the raw data into the clean, client-facing format.
                    formatted_data = transform_match_data_to_client_format(raw_data)
                    new_cache_data[match_id] = formatted_data

            # 5. Atomically replace the old cache with the new one.
            live_data_cache["data"] = new_cache_data
            live_data_cache["last_updated"] = datetime.now(timezone.utc)
            logging.info(f"BACKGROUND_POLL: Cache updated successfully with {len(new_cache_data)} live ITF matches.")

        except Exception as e:
            logging.error(f"BACKGROUND_POLL: An unexpected error occurred during polling cycle: {e}", exc_info=True)

        # Wait for the specified interval before starting the next cycle.
        logging.info(f"BACKGROUND_POLL: Polling cycle complete. Waiting for {CACHE_REFRESH_INTERVAL_SECONDS} seconds.")
        await asyncio.sleep(CACHE_REFRESH_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    The lifespan manager is the modern FastAPI way to handle startup and shutdown events.
    """
    logging.info("Application startup: Initializing resources...")
    # Create the background polling task when the application starts.
    polling_task = asyncio.create_task(poll_for_live_data())
    app_container["polling_task"] = polling_task

    # The 'yield' keyword passes control back to the application to run normally.
    yield

    # --- Code after the 'yield' runs on application shutdown ---
    logging.info("Application shutdown: Cleaning up resources...")
    if "polling_task" in app_container:
        app_container["polling_task"].cancel()  # Gracefully cancel the background task.
    if "client_instance" in app_container:
        await app_container["client_instance"].close()  # Close the httpx client session.
    logging.info("Shutdown cleanup complete.")


# Initialize the FastAPI application with our lifespan manager.
app = FastAPI(
    title="Live Tennis Score API",
    description="Provides live ITF tennis match data scraped from Tenipo.",
    lifespan=lifespan
)


# --- API Endpoints ---

@app.get("/all_live_itf_data", status_code=status.HTTP_200_OK)
async def get_all_live_itf_data():
    """
    Returns data for all currently live ITF matches found by the scraper.
    """
    last_updated = live_data_cache["last_updated"]

    # If the cache hasn't been populated yet, return a "Service Unavailable" error.
    if not last_updated or not live_data_cache["data"]:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cache is currently empty. Please try again in a moment."
        )

    age_seconds = (datetime.now(timezone.utc) - last_updated).total_seconds()

    return {
        "cache_last_updated_utc": last_updated.isoformat(),
        "cache_age_seconds": round(age_seconds),
        "match_count": len(live_data_cache["data"]),
        "matches": list(live_data_cache["data"].values())
    }


@app.get("/match/{match_id}", status_code=status.HTTP_200_OK)
async def get_match_data(match_id: str):
    """
    Returns cached data for a single match, identified by its ID.
    """
    match_data = live_data_cache["data"].get(match_id)

    if not match_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Data for match ID '{match_id}' not found in the live cache."
        )

    return match_data