import logging
import asyncio
import random
from datetime import datetime, timezone
from typing import Dict, List

import config
from smart_scraper import TenipoScraper
from data_mapper import transform_match_data_to_client_format
from database import MongoManager
from monitoring import TelegramNotifier, StallMonitor


class ScrapingService:
    """
    Manages all background tasks using a fixed pool of scraper workers.
    """

    def __init__(self, settings: config.Settings):
        self.settings = settings
        self.mongo_manager: MongoManager | None = None
        self.stall_monitor: StallMonitor | None = None
        self.polling_task: asyncio.Task | None = None
        self.live_data_cache: Dict = {"data": {}, "last_updated": None}

        # --- Worker Pool Setup ---
        self.main_scraper: TenipoScraper | None = None
        self.scraper_pool: asyncio.Queue[TenipoScraper] | None = None
        self.all_workers: List[TenipoScraper] = []
        logging.info(f"ScrapingService initialized. Concurrency limit is {self.settings.CONCURRENT_SCRAPER_LIMIT}.")

    async def start(self):
        """Initializes all resources and starts the background polling task."""
        logging.info("ScrapingService starting up...")
        loop = asyncio.get_event_loop()

        try:
            # --- Start the Main Scraper (for the summary list) ---
            self.main_scraper = TenipoScraper(self.settings)
            await loop.run_in_executor(None, self.main_scraper.start_driver)
            logging.info("Main scraper for summary list started successfully.")

            # --- Start the Worker Pool (for concurrent match details) ---
            self.scraper_pool = asyncio.Queue(maxsize=self.settings.CONCURRENT_SCRAPER_LIMIT)
            for i in range(self.settings.CONCURRENT_SCRAPER_LIMIT):
                logging.info(f"Starting worker {i + 1}/{self.settings.CONCURRENT_SCRAPER_LIMIT}...")
                worker = TenipoScraper(self.settings)
                await loop.run_in_executor(None, worker.start_driver)
                self.all_workers.append(worker)
                self.scraper_pool.put_nowait(worker)
            logging.info(f"Scraper pool fully populated with {self.scraper_pool.qsize()} workers.")

        except Exception as e:
            logging.critical(f"FATAL: A scraper instance failed to start. Service will not poll. Error: {e}",
                             exc_info=True)
            return

        self.mongo_manager = MongoManager(self.settings)
        telegram_notifier = TelegramNotifier(self.settings)
        self.stall_monitor = StallMonitor(notifier=telegram_notifier, settings=self.settings)

        self.polling_task = asyncio.create_task(self._poll_for_live_data())
        logging.info("ScrapingService started, polling task created.")

    async def stop(self):
        """Gracefully stops all background tasks and closes resources."""
        logging.info("ScrapingService shutting down...")
        if self.polling_task:
            self.polling_task.cancel()
            try:
                await self.polling_task
            except asyncio.CancelledError:
                logging.info("Polling task successfully cancelled.")

        loop = asyncio.get_event_loop()
        # Close all worker scrapers
        for worker in self.all_workers:
            await loop.run_in_executor(None, worker.close)
        # Close the main scraper
        if self.main_scraper:
            await loop.run_in_executor(None, self.main_scraper.close)

        if self.mongo_manager:
            self.mongo_manager.close()
        logging.info("ScrapingService shutdown complete.")

    async def _process_single_match(self, match_id: str) -> tuple[str, dict | None]:
        """
        Checks out a scraper from the pool, processes one match, and returns the scraper.
        """
        loop = asyncio.get_event_loop()
        worker = None
        try:
            # "Check out" a worker. This will wait if the pool is empty.
            worker = await self.scraper_pool.get()
            logging.info(f"MATCH_TASK({match_id}): Worker acquired. Processing...")

            # Add jitter here to be polite
            await asyncio.sleep(random.uniform(0.5, 1.5))

            raw_data = await loop.run_in_executor(None, lambda: worker.fetch_match_data(match_id))

            if not raw_data:
                return match_id, None

            formatted_data = transform_match_data_to_client_format(raw_data, match_id)

            if self.mongo_manager and self.mongo_manager.client:
                await loop.run_in_executor(None, lambda: self.mongo_manager.save_match_data(match_id, formatted_data))

            return match_id, formatted_data
        except Exception as e:
            logging.error(f"MATCH_TASK({match_id}): Unhandled exception during processing: {e}", exc_info=True)
            return match_id, None
        finally:
            # CRITICAL: Always return the worker to the pool.
            if worker:
                self.scraper_pool.put_nowait(worker)
                logging.info(f"MATCH_TASK({match_id}): Worker released back to pool.")

    async def _poll_for_live_data(self):
        """The main background loop that continuously fetches and processes data."""
        loop = asyncio.get_event_loop()
        while True:
            logging.info("BACKGROUND_POLL: Starting polling cycle...")
            try:
                if not self.main_scraper or not self.main_scraper.driver:
                    logging.critical("Main scraper is dead. Cannot proceed with polling cycle.")
                    await asyncio.sleep(self.settings.CACHE_REFRESH_INTERVAL_SECONDS)
                    continue

                all_matches_summary = await loop.run_in_executor(None, self.main_scraper.get_live_matches_summary)

                itf_matches_summary = [
                    m for m in all_matches_summary if m and
                                                      "ITF" in m.get("tournament_name", "") and
                                                      "ATP" not in m.get("tournament_name", "")
                ]

                logging.info(f"Found {len(itf_matches_summary)} live ITF matches to process.")
                live_match_ids = [m['id'] for m in itf_matches_summary if m and 'id' in m]

                if self.mongo_manager and self.mongo_manager.client:
                    await loop.run_in_executor(None, lambda: self.mongo_manager.prune_completed_matches(live_match_ids))

                if live_match_ids:
                    tasks = [self._process_single_match(match_id) for match_id in live_match_ids]
                    logging.info(f"Processing {len(tasks)} matches concurrently with worker pool...")
                    results = await asyncio.gather(*tasks)
                    new_cache_data = {match_id: data for match_id, data in results if data}
                else:
                    new_cache_data = {}

                self.live_data_cache["data"] = new_cache_data
                self.live_data_cache["last_updated"] = datetime.now(timezone.utc)
                logging.info(f"BACKGROUND_POLL: Cache updated with {len(new_cache_data)} matches.")

                if self.stall_monitor:
                    await self.stall_monitor.check_and_update_all(new_cache_data)

            except Exception as e:
                logging.error(f"BACKGROUND_POLL: Unhandled error during polling cycle: {e}", exc_info=True)

            await asyncio.sleep(self.settings.CACHE_REFRESH_INTERVAL_SECONDS)