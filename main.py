# main.py
import logging
import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, status

import config
from background_service import ScrapingService

# --- Service Setup ---
app_settings = config.Settings()
scraping_service = ScrapingService(app_settings)

# --- Redis Client for Leader Election ---
redis_client = redis.from_url(app_settings.REDIS_URL, encoding="utf-8", decode_responses=True)


class LeaderElector:
    """Manages a continuous, self-healing leader election process for all workers."""

    def __init__(self, settings, service, redis_client):
        self.settings = settings
        self.service = service
        self.redis = redis_client
        self.worker_id = str(uuid.uuid4())
        self._main_task = None

    async def start(self):
        """Starts the main leader election loop."""
        self._main_task = asyncio.create_task(self._election_loop())

    async def stop(self):
        """Stops the leader election loop and ensures graceful shutdown."""
        if self._main_task:
            self._main_task.cancel()
            try:
                await self._main_task
            except asyncio.CancelledError:
                pass
        # Ensure service is stopped on shutdown
        if self.service.is_running():
            await self._demote_to_follower()

    async def _election_loop(self):
        """The main loop where each worker vies for leadership."""
        logging.info(f"WORKER {self.worker_id}: Starting leader election loop.")
        while True:
            try:
                # Attempt to acquire the leader lock
                acquired = await self.redis.set(
                    self.settings.LEADER_LOCK_KEY,
                    self.worker_id,
                    nx=True,  # Set only if it does not exist
                    ex=self.settings.LEADER_LOCK_TTL_SECONDS  # Set with an expiration time
                )

                if acquired:
                    # We won the election, promote to leader and run the service
                    await self._run_as_leader()
                else:
                    # We lost, run as a follower and wait for the next chance
                    await self._run_as_follower()

            except asyncio.CancelledError:
                logging.info(f"WORKER {self.worker_id}: Election loop cancelled.")
                break
            except Exception as e:
                logging.error(f"WORKER {self.worker_id}: Unhandled error in election loop: {e}", exc_info=True)
                if self.service.is_running():
                    await self._demote_to_follower()
                await asyncio.sleep(30)  # Wait longer after an error

    async def _run_as_leader(self):
        """Promotes to leader, starts the service, and refreshes the lock until it's lost."""
        logging.warning(f"WORKER {self.worker_id}: Acquired lock. Promoting to LEADER.")
        refresh_task = None
        try:
            await self.service.start()
            refresh_task = asyncio.create_task(self._refresh_lock())
            # This will block until the refresh task exits (i.e., the lock is lost)
            await refresh_task
        finally:
            if refresh_task and not refresh_task.done():
                refresh_task.cancel()
            await self._demote_to_follower()

    async def _run_as_follower(self):
        """Logs status as follower and waits for a period before retrying for leadership."""
        leader_id = await self.redis.get(self.settings.LEADER_LOCK_KEY)
        logging.info(f"WORKER {self.worker_id}: Lock held by {leader_id}. Running as FOLLOWER. Will retry in 15s.")
        await asyncio.sleep(15)

    async def _demote_to_follower(self):
        """Stops the service and cleans up resources upon losing leadership."""
        logging.warning(f"WORKER {self.worker_id}: Demoting to FOLLOWER.")
        await self.service.stop()
        # Safely release the lock only if we are still the holder
        if await self.redis.get(self.settings.LEADER_LOCK_KEY) == self.worker_id:
            await self.redis.delete(self.settings.LEADER_LOCK_KEY)
            logging.info(f"WORKER {self.worker_id}: Released lock during demotion.")

    async def _refresh_lock(self):
        """Periodically refreshes the lock's TTL. Exits if the lock is lost."""
        while True:
            try:
                await asyncio.sleep(self.settings.LEADER_LOCK_TTL_SECONDS / 2)
                # Atomically check if we are still the owner and refresh the TTL
                if await self.redis.get(self.settings.LEADER_LOCK_KEY) == self.worker_id:
                    await self.redis.expire(self.settings.LEADER_LOCK_KEY, self.settings.LEADER_LOCK_TTL_SECONDS)
                else:
                    logging.warning(f"LEADER {self.worker_id}: Lost lock. Stopping refresh.")
                    break  # Exit loop to trigger demotion
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"LEADER {self.worker_id}: Failed to refresh lock: {e}")
                break  # Exit loop to trigger demotion


# --- Application Lifecycle ---

elector = LeaderElector(app_settings, scraping_service, redis_client)

@asynccontextmanager
asyn def lifespan(app: FastAPI):
    """Starts and stops the leader election process for the application."""
    await elector.start()
    yield
    await elector.stop()


app = FastAPI(title="Live Tennis Score API", lifespan=lifespan)


# --- API Endpoints ---

@app.get("/all_live_itf_data", status_code=status.HTTP_200_OK)
async def get_all_live_itf_data():
    """Returns all live match data from the service's in-memory cache."""
    if not elector.service.is_running():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The scraping service is not currently running (no leader elected). Please try again in a moment."
        )

    cache = scraping_service.live_data_cache
    last_updated = cache["last_updated"]

    if not last_updated or not cache["data"]:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cache is currently empty. The service may be initializing. Please try again in a moment."
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
    if not elector.service.is_running():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The scraping service is not currently running (no leader elected). Please try again in a moment."
        )

    match_data = scraping_service.live_data_cache["data"].get(match_id)

    if not match_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Data for match ID '{match_id}' not found in the live cache."
        )

    return match_data


@app.get("/investigate/{match_id}", status_code=status.HTTP_200_OK)
async def investigate_match(match_id: str):
    """A temporary debugging endpoint to find new data sources for a given match."""
    if not elector.service.is_running():
        raise HTTPException(status_code=503, detail="Scraping service not active on this worker (it's a follower).")

    logging.info(f"Received investigation request for match ID: {match_id}")
    urls = await asyncio.get_event_loop().run_in_executor(None, lambda: scraping_service.main_scraper.investigate_data_sources(match_id))

    return {
        "message": "Investigation complete. Check logs for captured URLs.",
        "match_id": match_id,
        "urls_found": len(urls)
    }
