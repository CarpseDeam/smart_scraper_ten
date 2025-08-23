# background_service.py
import logging
import asyncio
from datetime import datetime, timezone, timedelta
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

        # --- THE QUARANTINE ZONE ---
        self.quarantine_zone: Dict[str, datetime] = {}
        self.QUARANTINE_PERIOD = timedelta(seconds=60)  # Must be absent for 60s to be archived.

        logging.info(f"ScrapingService initialized. Quarantine period is {self.QUARANTINE_PERIOD.total_seconds()}s.")

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
                f"FATAL: The main scraper instance failed to start. Service will not poll. Error: {e}",
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
        for scraper in all_scrapers_to_close:
            await loop.run_in_executor(None, scraper.close)

        if self.mongo_manager:
            self.mongo_manager.close()
        logging.info("ScrapingService shutdown complete.")

    async def _initialize_worker_pool(self):
        """Creates and starts the pool of scraper workers on demand."""
        logging.info("Initializing worker pool...")
        loop = asyncio.get_event_loop()

        if self.scraper_pool is not None: return

        created_workers = []
        try:
            pool = asyncio.Queue(maxsize=self.settings.CONCURRENT_SCRAPER_LIMIT)
            for i in range(self.settings.CONCURRENT_SCRAPER_LIMIT):
                worker = TenipoScraper(self.settings)
                await loop.run_in_executor(None, worker.start_driver)
                created_workers.append(worker)
                pool.put_nowait(worker)

            self.all_workers = created_workers
            self.scraper_pool = pool
            logging.info(f"Scraper pool fully populated with {self.scraper_pool.qsize()} workers.")
        except Exception as e:
            logging.error(f"POOL_INIT: Failed to initialize worker pool. Error: {e}", exc_info=True)
            for worker in created_workers:
                await loop.run_in_executor(None, worker.close)
            self.all_workers = []
            self.scraper_pool = None

    async def _process_single_match(self, summary_data: dict):
        """Processes a single live match."""
        match_id = summary_data.get('id')
        if not match_id: return # Should not happen with the new scraper, but a good guard.

        loop = asyncio.get_event_loop()
        worker = None
        try:
            if not self.scraper_pool: return
            worker = await self.scraper_pool.get()
            raw_data = await loop.run_in_executor(None, lambda: worker.fetch_match_data(match_id))
            if raw_data:
                formatted_data = transform_match_data_to_client_format(raw_data, summary_data)
                if formatted_data and self.mongo_manager:
                    await loop.run_in_executor(None,
                                               lambda: self.mongo_manager.save_match_data(match_id, formatted_data))
        except Exception as e:
            logging.error(f"MATCH_TASK({match_id}): Unhandled exception: {e}", exc_info=True)
        finally:
            if worker and self.scraper_pool:
                self.scraper_pool.put_nowait(worker)

    async def _poll_for_live_data(self):
        """The main background loop using the "Quarantine Zone" architecture."""
        loop = asyncio.get_event_loop()
        while True:
            logging.info("BACKGROUND_POLL: Starting polling cycle...")
            try:
                if not self.main_scraper or not self.main_scraper.driver or not self.mongo_manager or not self.archiver:
                    logging.critical("A critical component is not initialized. Cannot poll.")
                    await asyncio.sleep(self.settings.CACHE_REFRESH_INTERVAL_SECONDS)
                    continue

                # Step 1: Get the single source of truth for what is currently live.
                summary_success, all_matches_summary = await loop.run_in_executor(None,
                                                                                  self.main_scraper.get_live_matches_summary)

                if not summary_success:
                    logging.warning("Main scraper failed to get a valid summary. Skipping cycle.")
                    await asyncio.sleep(self.settings.CACHE_REFRESH_INTERVAL_SECONDS)
                    continue

                live_ids_from_feed: Set[str] = {m['id'] for m in all_matches_summary if m and 'id' in m}

                if self.scraper_pool is None and all_matches_summary:
                    await self._initialize_worker_pool()

                # Step 2: Scrape details for all currently live matches.
                if self.scraper_pool:
                    live_tasks = [self._process_single_match(match) for match in all_matches_summary]
                    if live_tasks:
                        logging.info(f"Processing {len(live_tasks)} live matches from feed.")
                        await asyncio.gather(*live_tasks)

                # --- START QUARANTINE LOGIC ---
                now = datetime.now(timezone.utc)

                # Step 3: Release any quarantined matches that have reappeared in the feed.
                reappeared_ids = live_ids_from_feed.intersection(self.quarantine_zone.keys())
                if reappeared_ids:
                    logging.info(
                        f"QUARANTINE: {len(reappeared_ids)} matches have reappeared. Releasing them: {reappeared_ids}")
                    for match_id in reappeared_ids:
                        del self.quarantine_zone[match_id]

                # Step 4: Identify newly finished/disappeared matches and place them in quarantine.
                ids_in_db = set(await loop.run_in_executor(None, self.mongo_manager.get_all_active_match_ids))
                newly_orphaned_ids = ids_in_db - live_ids_from_feed - self.quarantine_zone.keys()
                if newly_orphaned_ids:
                    logging.info(
                        f"QUARANTINE: {len(newly_orphaned_ids)} new matches disappeared. Quarantining them: {newly_orphaned_ids}")
                    for match_id in newly_orphaned_ids:
                        self.quarantine_zone[match_id] = now

                # Step 5: Find matches whose quarantine period has expired and archive them.
                ids_to_archive = []
                for match_id, quarantined_at in list(self.quarantine_zone.items()):
                    if now - quarantined_at > self.QUARANTINE_PERIOD:
                        ids_to_archive.append(match_id)

                if ids_to_archive:
                    logging.warning(
                        f"ARCHIVING: {len(ids_to_archive)} matches have expired their quarantine. Archiving them: {ids_to_archive}")
                    await loop.run_in_executor(None, self.archiver.archive_matches_by_ids, ids_to_archive)
                    for match_id in ids_to_archive:
                        del self.quarantine_zone[match_id]  # Clean up from quarantine after archiving
                # --- END QUARANTINE LOGIC ---

                # Step 6: Rebuild the API cache from the now-clean database.
                final_active_matches = await loop.run_in_executor(None, self.mongo_manager.get_all_active_matches)
                new_cache_data = {match['_id']: match for match in final_active_matches}
                self.live_data_cache["data"] = new_cache_data
                self.live_data_cache["last_updated"] = now
                logging.info(f"BACKGROUND_POLL: Cache rebuilt with {len(new_cache_data)} active matches.")

                if self.stall_monitor:
                    await self.stall_monitor.check_and_update_all(new_cache_data)

            except Exception as e:
                logging.error(f"BACKGROUND_POLL: Unhandled error in polling cycle: {e}", exc_info=True)

            await asyncio.sleep(self.settings.CACHE_REFRESH_INTERVAL_SECONDS)