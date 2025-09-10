# background_service.py
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Set

import config
from smart_scraper import TenipoScraper
from data_mapper import transform_match_data_to_client_format, transform_summary_only_to_client_format
from database import MongoManager
from monitoring import TelegramNotifier, StallMonitor
from archiver import MongoArchiver


class ScrapingService:
    """
    Two-speed architecture for maximum performance:
    - FAST LANE: Live scores every 3-5 seconds (summary page only)
    - SLOW LANE: Detailed data every 30-60 seconds (individual match pages)
    """

    def __init__(self, settings: config.Settings):
        self.settings = settings
        self.mongo_manager: MongoManager | None = None
        self.stall_monitor: StallMonitor | None = None
        self.archiver: MongoArchiver | None = None

        # Two separate polling tasks for speed optimization
        self.fast_polling_task: asyncio.Task | None = None
        self.slow_polling_task: asyncio.Task | None = None

        self.live_data_cache: Dict = {"data": {}, "last_updated": None}
        self.main_scraper: TenipoScraper | None = None
        self.detail_scraper_pool: asyncio.Queue[TenipoScraper] | None = None
        self.all_workers: List[TenipoScraper] = []

        # Quarantine zone for disappeared matches
        self.quarantine_zone: Dict[str, datetime] = {}
        self.QUARANTINE_PERIOD = timedelta(seconds=60)

        # Speed-optimized intervals from config
        self.FAST_POLL_INTERVAL = settings.FAST_POLL_INTERVAL_SECONDS
        self.SLOW_POLL_INTERVAL = settings.SLOW_POLL_INTERVAL_SECONDS

        logging.info(
            f"ScrapingService initialized with SPEED DEMON architecture! Fast={self.FAST_POLL_INTERVAL}s, Slow={self.SLOW_POLL_INTERVAL}s")

    async def start(self):
        """Initializes scraper and starts both speed-optimized polling tasks."""
        logging.info("🚀 ScrapingService starting with TWO-SPEED ARCHITECTURE...")
        loop = asyncio.get_event_loop()

        try:
            self.main_scraper = TenipoScraper(self.settings)
            await loop.run_in_executor(None, self.main_scraper.start_driver)
            logging.info("⚡ Main scraper ready for LIGHTNING-FAST summary polling!")
        except Exception as e:
            logging.critical(f"FATAL: Main scraper failed to start: {e}", exc_info=True)
            return

        self.mongo_manager = MongoManager(self.settings)

        if self.mongo_manager.client is not None:
            self.archiver = MongoArchiver(self.mongo_manager)
            telegram_notifier = TelegramNotifier(self.settings)
            self.stall_monitor = StallMonitor(notifier=telegram_notifier, settings=self.settings)

            # Launch both speed lanes!
            self.fast_polling_task = asyncio.create_task(self._lightning_fast_score_updates())
            self.slow_polling_task = asyncio.create_task(self._leisurely_detailed_enrichment())

            logging.info("🔥 Both FAST and SLOW lanes are now running!")
        else:
            logging.critical("ScrapingService failed - MongoDB connection issue.")

    async def stop(self):
        """Gracefully stops both polling lanes and closes all resources for a clean shutdown."""
        logging.info("ScrapingService shutting down both speed lanes...")

        for task in [self.fast_polling_task, self.slow_polling_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    logging.info("Polling task cancelled successfully.")

        # Reset tasks to signify the service is fully stopped.
        self.fast_polling_task = None
        self.slow_polling_task = None

        loop = asyncio.get_event_loop()
        all_scrapers = self.all_workers + ([self.main_scraper] if self.main_scraper else [])
        for scraper in all_scrapers:
            await loop.run_in_executor(None, scraper.close)

        if self.mongo_manager:
            self.mongo_manager.close()

        # Reset scraper resources for a clean restart
        self.main_scraper = None
        self.detail_scraper_pool = None
        self.all_workers = []

        logging.info("ScrapingService shutdown complete.")

    def is_running(self) -> bool:
        """Checks if the scraping service's main polling tasks are active."""
        return self.fast_polling_task is not None and self.slow_polling_task is not None

    async def _lightning_fast_score_updates(self):
        """
        🏎️ FAST LANE: Updates only live scores, sets, and current games.
        NO individual page navigation = MAXIMUM SPEED!
        """
        loop = asyncio.get_event_loop()
        logging.info("⚡ FAST LANE: Lightning-fast score updates started!")

        while True:
            cycle_start = datetime.now(timezone.utc)
            now = cycle_start
            try:
                if not self._components_ready():
                    await asyncio.sleep(self.FAST_POLL_INTERVAL)
                    continue

                # Get ONLY the summary - no individual match fetching!
                summary_success, all_matches_summary = await loop.run_in_executor(
                    None, self.main_scraper.get_live_matches_summary
                )

                if not summary_success:
                    logging.warning("FAST LANE: Summary fetch failed, skipping cycle")
                    await asyncio.sleep(self.FAST_POLL_INTERVAL)
                    continue

                live_ids_from_feed: Set[str] = {m['id'] for m in all_matches_summary if m and 'id' in m}

                # Process summary data ONLY (super fast!)
                for match_summary in all_matches_summary:
                    match_id = match_summary.get('id')
                    if not match_id:
                        continue

                    # Transform just summary to client format
                    fast_data = transform_summary_only_to_client_format(match_summary)
                    if fast_data:
                        await loop.run_in_executor(
                            None, lambda m_id=match_id, data=fast_data: self.mongo_manager.upsert_fast_data(m_id, data)
                        )

                # Handle quarantine and archiving
                await self._handle_quarantine_logic(live_ids_from_feed, now)

                # Rebuild cache with lightning speed
                await self._rebuild_fast_cache()

                cycle_time = (datetime.now(timezone.utc) - cycle_start).total_seconds()
                logging.info(f"⚡ FAST LANE: Updated {len(live_ids_from_feed)} matches in {cycle_time:.2f}s!")

            except Exception as e:
                logging.error(f"FAST LANE: Error in speed cycle: {e}", exc_info=True)

            await asyncio.sleep(self.FAST_POLL_INTERVAL)

    async def _leisurely_detailed_enrichment(self):
        """
        🐌 SLOW LANE: Enriches matches with detailed stats, H2H, point-by-point.
        Runs in background without impacting live score speed.
        """
        # Give fast lane time to establish baseline
        await asyncio.sleep(15)

        loop = asyncio.get_event_loop()
        logging.info("🐌 SLOW LANE: Detailed enrichment service started!")

        while True:
            cycle_start = datetime.now(timezone.utc)
            try:
                if not self.mongo_manager:
                    await asyncio.sleep(self.SLOW_POLL_INTERVAL)
                    continue

                # Initialize detail worker pool on demand
                await self._ensure_detail_worker_pool()

                # Find matches needing detailed enrichment using the efficient DB query
                matches_needing_details = await self._identify_matches_needing_enrichment()

                if matches_needing_details:
                    logging.info(f"🐌 SLOW LANE: Enriching {len(matches_needing_details)} matches with detailed data")

                    # Process details in parallel but leisurely
                    detail_tasks = [
                        self._enrich_single_match_with_details(match_id)
                        for match_id in matches_needing_details
                    ]
                    await asyncio.gather(*detail_tasks, return_exceptions=True)

                    cycle_time = (datetime.now(timezone.utc) - cycle_start).total_seconds()
                    logging.info(f"🐌 SLOW LANE: Enriched {len(matches_needing_details)} matches in {cycle_time:.1f}s")
                else:
                    logging.info("🐌 SLOW LANE: All matches have current detailed data")

            except Exception as e:
                logging.error(f"SLOW LANE: Error in enrichment cycle: {e}", exc_info=True)

            await asyncio.sleep(self.SLOW_POLL_INTERVAL)

    async def _ensure_detail_worker_pool(self):
        """Creates worker pool for detailed fetching if not exists."""
        if self.detail_scraper_pool is not None:
            return

        logging.info("🔧 Initializing detail worker pool...")
        loop = asyncio.get_event_loop()
        created_workers = []

        try:
            pool = asyncio.Queue(maxsize=self.settings.CONCURRENT_SCRAPER_LIMIT)
            for i in range(self.settings.CONCURRENT_SCRAPER_LIMIT):
                worker = TenipoScraper(self.settings)
                await loop.run_in_executor(None, worker.start_driver)
                created_workers.append(worker)
                pool.put_nowait(worker)

            self.all_workers = created_workers
            self.detail_scraper_pool = pool
            logging.info(f"🔧 Detail worker pool ready with {pool.qsize()} workers!")

        except Exception as e:
            logging.error(f"Failed to initialize detail worker pool: {e}", exc_info=True)
            for worker in created_workers:
                await loop.run_in_executor(None, worker.close)
            self.all_workers = []

    async def _identify_matches_needing_enrichment(self) -> List[str]:
        """
        Identifies which matches need detailed data refresh by calling the efficient
        database query.
        """
        if not self.mongo_manager:
            return []
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.mongo_manager.get_matches_needing_enrichment)

    async def _enrich_single_match_with_details(self, match_id: str):
        """Enriches a single match with detailed data."""
        if not self.detail_scraper_pool:
            return

        loop = asyncio.get_event_loop()
        worker = None

        try:
            worker = await self.detail_scraper_pool.get()

            # Get current match data
            current_match = await loop.run_in_executor(
                None, lambda: self.mongo_manager.db["tenipo"].find_one({"_id": match_id})
            )

            if not current_match:
                return

            # Fetch detailed data from individual match page
            raw_detailed_data = await loop.run_in_executor(
                None, lambda: worker.fetch_match_data(match_id)
            )

            if raw_detailed_data:
                # Merge detailed data with existing fast data
                enhanced_data = self._merge_detailed_with_fast_data(current_match, raw_detailed_data)
                await loop.run_in_executor(
                    None, lambda: self.mongo_manager.save_match_data(match_id, enhanced_data)
                )

        except Exception as e:
            logging.error(f"DETAIL ENRICHMENT({match_id}): Error: {e}", exc_info=True)
        finally:
            if worker and self.detail_scraper_pool:
                self.detail_scraper_pool.put_nowait(worker)

    def _merge_detailed_with_fast_data(self, fast_match_data: Dict, raw_detailed_data: Dict) -> Dict:
        """Merges detailed data into existing fast data without overwriting live scores."""
        enhanced_match = fast_match_data.copy()

        # Extract detailed components
        match_details_xml = raw_detailed_data.get("match", {})
        pbp_info = raw_detailed_data.get("point_by_point_html", [])
        stats_from_html = raw_detailed_data.get("statistics_html", [])

        # Add detailed data without touching live scores
        enhanced_match["statistics"] = self._parse_stats_from_html_or_xml(stats_from_html, match_details_xml)
        enhanced_match["pointByPoint"] = self._parse_point_by_point(pbp_info)
        enhanced_match["h2h"] = self._parse_h2h_string(match_details_xml.get("h2h", ""))

        # Enrich match info if available
        if not enhanced_match.get("matchInfo"):
            enhanced_match["matchInfo"] = {}
        if match_details_xml.get("court_name"):
            enhanced_match["matchInfo"]["court"] = match_details_xml["court_name"]
        if match_details_xml.get("round"):
            enhanced_match["round"] = match_details_xml["round"]

        # Mark that this match now has detailed data
        enhanced_match["hasDetailedData"] = True
        # Mark when detailed data was last updated
        enhanced_match["detailedDataUpdated"] = datetime.now(timezone.utc).isoformat()

        return enhanced_match

    def _parse_stats_from_html_or_xml(self, stats_html: List, xml_data: Dict) -> List:
        """Parse stats from HTML first, fallback to XML if needed."""
        if stats_html:
            return stats_html

        # Fallback to XML stats parsing (keeping existing logic)
        stats_str = xml_data.get("stats") or xml_data.get("statistics", "")
        return self._parse_stats_string(stats_str)

    def _parse_stats_string(self, stats_str: str) -> List:
        """Parses stats string from XML into client format."""
        if not isinstance(stats_str, str) or '/' not in stats_str:
            return []

        STAT_MAP = {
            1: "Aces", 2: "Double Faults", 3: "1st Serve", 4: "1st Serve Points Won",
            5: "2nd Serve Points Won", 6: "Break Points Saved", 7: "Service Games Played",
            8: "1st Serve Return Points Won", 9: "2nd Serve Return Points Won",
            10: "Break Points Converted", 11: "Return Games Played"
        }

        try:
            _, p1_stats_str, p2_stats_str = stats_str.split('/')
            p1_vals, p2_vals = p1_stats_str.split(','), p2_stats_str.split(',')
        except ValueError:
            return []

        service_stats, return_stats = [], []
        for i in range(1, 12):
            stat_name = STAT_MAP.get(i)
            if not stat_name:
                continue

            p1_val = p1_vals[i] if i < len(p1_vals) else "0"
            p2_val = p2_vals[i] if i < len(p2_vals) else "0"

            stat_item = {"name": stat_name, "home": p1_val, "away": p2_val}
            if "Serve" in stat_name or "Aces" in stat_name or "Double" in stat_name:
                service_stats.append(stat_item)
            else:
                return_stats.append(stat_item)

        return [
            {"groupName": "Service", "statisticsItems": service_stats},
            {"groupName": "Return", "statisticsItems": return_stats}
        ]

    def _parse_point_by_point(self, pbp_html_data: List) -> List:
        """Parses point-by-point data from HTML."""
        if not pbp_html_data:
            return []
        client_pbp_data = []
        for game_block in pbp_html_data:
            client_pbp_data.append({
                "game": game_block.get("game_header", ""),
                "point_progression_log": game_block.get("points_log", [])
            })
        return client_pbp_data

    def _parse_h2h_string(self, h2h_str: str) -> List:
        """Parses H2H string into structured data."""
        if not isinstance(h2h_str, str) or not h2h_str:
            return []
        meetings = []
        for part in h2h_str.split('#'):
            fields = part.split('/')
            if len(fields) < 9:
                continue
            meetings.append({
                "year": fields[8] if len(fields) > 8 else None,
                "event": fields[5] if len(fields) > 5 else None,
                "surface": fields[7] if len(fields) > 7 else None,
                "score": fields[2] if len(fields) > 2 else None,
            })
        return meetings

    async def _handle_quarantine_logic(self, live_ids_from_feed: Set[str], now: datetime):
        """Handles quarantine logic for disappeared matches."""
        now = datetime.now(timezone.utc)
        loop = asyncio.get_event_loop()

        # Release reappeared matches from quarantine
        reappeared_ids = live_ids_from_feed.intersection(self.quarantine_zone.keys())
        if reappeared_ids:
            logging.info(f"QUARANTINE: {len(reappeared_ids)} matches reappeared, releasing")
            for match_id in reappeared_ids:
                del self.quarantine_zone[match_id]

        # Quarantine newly disappeared matches
        ids_in_db = set(await loop.run_in_executor(None, self.mongo_manager.get_all_active_match_ids))
        newly_orphaned_ids = ids_in_db - live_ids_from_feed - self.quarantine_zone.keys()

        if newly_orphaned_ids:
            logging.info(f"QUARANTINE: {len(newly_orphaned_ids)} matches disappeared, quarantining")
            for match_id in newly_orphaned_ids:
                self.quarantine_zone[match_id] = now

        # Archive expired quarantine matches
        ids_to_archive = [
            match_id for match_id, quarantined_at in self.quarantine_zone.items()
            if now - quarantined_at > self.QUARANTINE_PERIOD
        ]

        if ids_to_archive:
            logging.warning(f"ARCHIVING: {len(ids_to_archive)} matches expired quarantine")
            await loop.run_in_executor(None, self.archiver.archive_matches_by_ids, ids_to_archive)
            for match_id in ids_to_archive:
                del self.quarantine_zone[match_id]

    async def _rebuild_fast_cache(self):
        """Rebuilds cache and runs monitoring checks."""
        loop = asyncio.get_event_loop()
        final_active_matches = await loop.run_in_executor(None, self.mongo_manager.get_all_active_matches)

        new_cache_data = {match['_id']: match for match in final_active_matches}
        self.live_data_cache["data"] = new_cache_data
        self.live_data_cache["last_updated"] = datetime.now(timezone.utc)

        if self.stall_monitor:
            await self.stall_monitor.check_and_update_all(new_cache_data)

    def _components_ready(self) -> bool:
        """Checks if all critical components are ready."""
        return all([
            self.main_scraper,
            self.main_scraper.driver,
            self.mongo_manager,
            self.archiver
        ])

    # Legacy method for backward compatibility
    async def _poll_for_live_data(self):
        """DEPRECATED: Replaced by two-speed architecture."""
        logging.warning("Legacy polling method called - this shouldn't happen with new architecture!")
        pass
