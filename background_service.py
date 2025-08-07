import logging
import asyncio
from datetime import datetime, timezone
from typing import Dict

import config
from smart_scraper import TenipoScraper
from data_mapper import transform_match_data_to_client_format
from database import MongoManager
from monitoring import TelegramNotifier, StallMonitor


class ScrapingService:
    """
    Manages all background tasks for scraping, data processing, and monitoring.
    """

    def __init__(self, settings: config.Settings):
        self.settings = settings
        self.scraper: TenipoScraper | None = None
        self.mongo_manager: MongoManager | None = None
        self.stall_monitor: StallMonitor | None = None
        self.polling_task: asyncio.Task | None = None
        self.live_data_cache: Dict = {"data": {}, "last_updated": None}
        logging.info("ScrapingService initialized.")

    async def start(self):
        """Initializes all resources and starts the background polling task."""
        logging.info("ScrapingService starting up...")
        loop = asyncio.get_event_loop()
        try:
            self.scraper = await loop.run_in_executor(None, lambda: TenipoScraper(self.settings))
        except Exception as e:
            logging.critical(f"FATAL: Scraper initialization failed. Service will not poll. Error: {e}")
            # Do not proceed if the scraper fails
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
        if self.scraper:
            await asyncio.get_event_loop().run_in_executor(None, self.scraper.close)
        if self.mongo_manager:
            self.mongo_manager.close()
        logging.info("ScrapingService shutdown complete.")

    async def _poll_for_live_data(self):
        """The main background loop that continuously fetches and processes data."""
        loop = asyncio.get_event_loop()
        while True:
            logging.info("BACKGROUND_POLL: Starting polling cycle...")
            try:
                all_matches_summary = await loop.run_in_executor(None, self.scraper.get_live_matches_summary)

                itf_matches_summary = [
                    m for m in all_matches_summary if m and
                                                      "ITF" in m.get("tournament_name", "") and
                                                      "ATP" not in m.get("tournament_name", "")
                ]

                logging.info(f"Found {len(itf_matches_summary)} live ITF matches to process.")
                live_match_ids = [m['id'] for m in itf_matches_summary if m and 'id' in m]

                if self.mongo_manager and self.mongo_manager.client:
                    await loop.run_in_executor(None, lambda: self.mongo_manager.prune_completed_matches(live_match_ids))

                async def process_match(match_id: str):
                    try:
                        raw_data = await loop.run_in_executor(None, lambda: self.scraper.fetch_match_data(match_id))
                        if not raw_data: return None

                        formatted_data = transform_match_data_to_client_format(raw_data, match_id)

                        if self.mongo_manager and self.mongo_manager.client:
                            await loop.run_in_executor(None, lambda: self.mongo_manager.save_match_data(match_id,
                                                                                                        formatted_data))

                        return match_id, formatted_data
                    except Exception as e:
                        logging.error(f"Failed to process match ID {match_id} in parallel: {e}")
                        return None

                tasks = [process_match(mid) for mid in live_match_ids]
                results = await asyncio.gather(*tasks)

                new_cache_data = {match_id: data for match_id, data in results if match_id and data}

                self.live_data_cache["data"] = new_cache_data
                self.live_data_cache["last_updated"] = datetime.now(timezone.utc)
                logging.info(f"BACKGROUND_POLL: Cache updated with {len(new_cache_data)} matches.")

                if self.stall_monitor:
                    await self.stall_monitor.check_and_update_all(new_cache_data)

            except Exception as e:
                logging.error(f"BACKGROUND_POLL: Unhandled error during polling cycle: {e}", exc_info=True)

            await asyncio.sleep(self.settings.STALL_MONITOR_SECONDS)