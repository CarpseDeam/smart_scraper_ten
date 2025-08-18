# background_service.py
import logging
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Set

import config
from smart_scraper import TenipoScraper
from data_mapper import transform_match_data_to_client_format
from database import MongoManager
from monitoring import TelegramNotifier, StallMonitor
from archiver import MongoArchiver


class ScrapingService:
    """
    Manages all background tasks using a fixed pool of scraper workers.
    """

    def __init__(self, settings: config.Settings):
        self.settings = settings
        self.mongo_manager: MongoManager | None = None
        self.stall_monitor: StallMonitor | None = None
        self.archiver: MongoArchiver | None = None
        self.polling_task: asyncio.Task | None = None
        self.live_data_cache: Dict = {"data": {}, "last_updated": None}
        self.main_scraper: TenipoScraper | None = None
        self.scraper_pool: asyncio.Queue[TenipoScraper] | None = None
        self.all_workers: List[TenipoScraper] = []
        logging.info(f"ScrapingService initialized. Concurrency limit is {self.settings.CONCURRENT_SCRAPER_LIMIT}.")

    async def start(self):
        """Initializes the main scraper and starts the background polling task."""
        logging.info("ScrapingService starting up...")
        loop = asyncio.get_event_loop()

        self.scraper_pool = None
        self.all_workers = []

        try:
            self.main_scraper = TenipoScraper(self.settings)
            await loop.run_in_executor(None, self.main_scraper.start_driver)
            logging.info("Main scraper for summary list started successfully.")
        except Exception as e:
            logging.critical(
                f"FATAL: The main scraper instance failed to start during initialization. Service will not poll. Error: {e}",
                exc_info=True)
            return

        self.mongo_manager = MongoManager(self.settings)

        if self.mongo_manager.client is not None:
            self.archiver = MongoArchiver(self.mongo_manager)
            telegram_notifier = TelegramNotifier(self.settings)
            self.stall_monitor = StallMonitor(notifier=telegram_notifier, settings=self.settings)
            self.polling_task = asyncio.create_task(self._poll_for_live_data())
            logging.info("ScrapingService started, polling task created.")
        else:
            logging.critical("ScrapingService did not start polling task due to MongoDB connection failure.")

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
        all_scrapers_to_close = self.all_workers + ([self.main_scraper] if self.main_scraper else [])
        for i, scraper in enumerate(all_scrapers_to_close):
            logging.info(f"Closing scraper {i + 1}/{len(all_scrapers_to_close)}...")
            await loop.run_in_executor(None, scraper.close)

        if self.mongo_manager:
            self.mongo_manager.close()
        logging.info("ScrapingService shutdown complete.")

    async def _initialize_worker_pool(self):
        """Creates and starts the pool of scraper workers on demand."""
        logging.info("First poll cycle with work: Initializing worker pool...")
        loop = asyncio.get_event_loop()

        if self.scraper_pool is not None:
            logging.warning("Worker pool initialization called but pool already exists.")
            return

        created_workers = []
        try:
            pool = asyncio.Queue(maxsize=self.settings.CONCURRENT_SCRAPER_LIMIT)
            for i in range(self.settings.CONCURRENT_SCRAPER_LIMIT):
                logging.info(f"POOL_INIT: Starting worker {i + 1}/{self.settings.CONCURRENT_SCRAPER_LIMIT}...")
                worker = TenipoScraper(self.settings)
                await loop.run_in_executor(None, worker.start_driver)
                created_workers.append(worker)
                pool.put_nowait(worker)
                logging.info(f"POOL_INIT: Worker {i + 1} started and added to pool.")

            self.all_workers = created_workers
            self.scraper_pool = pool
            logging.info(f"Scraper pool fully populated with {self.scraper_pool.qsize()} workers.")
        except Exception as e:
            logging.error(f"POOL_INIT: Failed to initialize worker pool. Will retry on next poll cycle. Error: {e}",
                          exc_info=True)
            for worker in created_workers:
                await loop.run_in_executor(None, worker.close)
            self.all_workers = []
            self.scraper_pool = None

    async def _process_single_match(self, summary_data: dict) -> tuple[str, dict | None]:
        """
        Processes a single match using a worker. This is now ONLY used for live matches.
        """
        match_id = summary_data.get('id')
        tournament_name = summary_data.get('tournament_name', '')

        if "itf" not in tournament_name.lower():
            logging.debug(f"MATCH_TASK({match_id}): Pre-filtered. Tournament '{tournament_name}' is not an ITF event.")
            return match_id, None

        loop = asyncio.get_event_loop()
        worker = None
        try:
            if not self.scraper_pool:
                logging.error(f"MATCH_TASK({match_id}): Cannot process, scraper pool is not initialized.")
                return match_id, None

            worker = await self.scraper_pool.get()
            raw_data = await loop.run_in_executor(None, lambda: worker.fetch_match_data(match_id))

            if not raw_data:
                logging.warning(f"MATCH_TASK({match_id}): Scraper returned no raw data.")
                return match_id, None

            formatted_data = transform_match_data_to_client_format(raw_data, summary_data)

            if not formatted_data:
                logging.warning(f"MATCH_TASK({match_id}): Data mapper returned an empty dictionary.")
                return match_id, None

            if self.mongo_manager:
                await loop.run_in_executor(None, lambda: self.mongo_manager.save_match_data(match_id, formatted_data))
            return match_id, formatted_data

        except Exception as e:
            logging.error(f"MATCH_TASK({match_id}): Unhandled exception during processing: {e}", exc_info=True)
            return match_id, None
        finally:
            if worker and self.scraper_pool:
                self.scraper_pool.put_nowait(worker)

    async def _poll_for_live_data(self):
        """
        The main background loop that continuously fetches and processes data using the "Lighthouse" architecture.
        """
        loop = asyncio.get_event_loop()
        while True:
            logging.info("BACKGROUND_POLL: Starting polling cycle...")
            try:
                if not self.main_scraper or not self.main_scraper.driver:
                    logging.critical("Main scraper is dead. Cannot poll.")
                    await asyncio.sleep(self.settings.CACHE_REFRESH_INTERVAL_SECONDS)
                    continue

                # Step 1: Get the single source of truth for what is currently live.
                summary_success, all_matches_summary = await loop.run_in_executor(None,
                                                                                  self.main_scraper.get_live_matches_summary)

                if not summary_success:
                    logging.warning("Main scraper failed to get a valid summary. Skipping cycle.")
                    await asyncio.sleep(self.settings.CACHE_REFRESH_INTERVAL_SECONDS)
                    continue

                live_ids_from_feed: Set[str] = {match['id'] for match in all_matches_summary if match and 'id' in match}

                if self.scraper_pool is None and all_matches_summary:
                    await self._initialize_worker_pool()

                # Step 2: Scrape details for all live matches.
                if self.scraper_pool and self.mongo_manager:
                    live_tasks = [self._process_single_match(match) for match in all_matches_summary]
                    if live_tasks:
                        logging.info(f"Processing {len(live_tasks)} live matches from feed.")
                        await asyncio.gather(*live_tasks)

                    # Step 3: Identify finished matches and tell the archiver to remove them.
                    active_matches_from_db = await loop.run_in_executor(None,
                                                                        self.mongo_manager.get_all_active_match_ids)
                    ids_in_db: Set[str] = set(active_matches_from_db)

                    orphaned_ids: Set[str] = ids_in_db - live_ids_from_feed

                    if orphaned_ids:
                        logging.info(
                            f"ARCHITECTURAL CLEANUP: Found {len(orphaned_ids)} matches that are no longer in the live feed. Archiving them now.")
                        await loop.run_in_executor(None, self.archiver.archive_matches_by_ids, list(orphaned_ids))

                # Step 4: Rebuild the API cache from the now-clean database.
                final_active_matches = await loop.run_in_executor(None, self.mongo_manager.get_all_active_matches)
                new_cache_data = {match['_id']: match for match in final_active_matches}
                self.live_data_cache["data"] = new_cache_data
                self.live_data_cache["last_updated"] = datetime.now(timezone.utc)
                logging.info(f"BACKGROUND_POLL: Cache rebuilt with {len(new_cache_data)} active ITF matches.")

                # Step 5: Run monitoring and the archiver's own garbage collection as a final safety net.
                if self.stall_monitor:
                    await self.stall_monitor.check_and_update_all(new_cache_data)

                if self.archiver:
                    logging.info("BACKGROUND_POLL: Running archiver's time-based garbage collection as a safety net...")
                    await loop.run_in_executor(None, self.archiver.garbage_collect_stale_matches)

            except Exception as e:
                logging.error(f"BACKGROUND_POLL: Unhandled error during polling cycle: {e}", exc_info=True)

            await asyncio.sleep(self.settings.CACHE_REFRESH_INTERVAL_SECONDS)