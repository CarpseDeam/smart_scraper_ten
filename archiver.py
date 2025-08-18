# archiver.py
import logging
from datetime import datetime, timezone, timedelta
from typing import List
from pymongo.errors import BulkWriteError, OperationFailure

from database import MongoManager


class MongoArchiver:
    """
    Handles moving matches from the active collection to history and cleaning up stale data.
    """

    def __init__(self, mongo_manager: MongoManager):
        if mongo_manager is None or mongo_manager.db is None:
            raise ValueError("A valid and connected MongoManager instance is required.")
        self.db = mongo_manager.db
        self.active_collection = self.db["tenipo"]
        self.history_collection = self.db["tenipo_history"]
        self.stale_threshold = timedelta(minutes=15)
        logging.info(
            f"MongoArchiver initialized with a {self.stale_threshold.total_seconds() / 60:.0f} minute stale threshold.")

    def archive_matches_by_ids(self, match_ids: List[str]):
        """
        Finds matches by their IDs, archives them, and then deletes them from the active collection.
        This is the primary mechanism for cleaning up finished matches.
        """
        if not match_ids:
            return

        try:
            matches_to_archive = list(self.active_collection.find({"_id": {"$in": match_ids}}))

            if not matches_to_archive:
                logging.warning(
                    f"ARCHIVER: Received {len(match_ids)} IDs to archive, but none were found in the active collection.")
                return

            logging.info(f"ARCHIVER: Archiving {len(matches_to_archive)} matches by direct command.")
            self._process_archiving(matches_to_archive)

        except OperationFailure as e:
            logging.error(f"ARCHIVER: A database operation failed during ID-based archiving. Error: {e}")
        except Exception as e:
            logging.error(f"ARCHIVER: An unexpected error occurred during ID-based archiving. Error: {e}",
                          exc_info=True)

    def garbage_collect_stale_matches(self):
        """
        Acts as a safety net, deleting any match that hasn't been updated recently.
        This catches any edge cases the primary logic might miss.
        """
        try:
            stale_cutoff_time = datetime.now(timezone.utc) - self.stale_threshold
            stale_query = {"timePolled": {"$lt": stale_cutoff_time.isoformat()}}

            stale_matches = list(self.active_collection.find(stale_query, {"_id": 1}))

            if stale_matches:
                stale_ids = [match['_id'] for match in stale_matches]
                logging.warning(f"GARBAGE COLLECTOR: Found {len(stale_ids)} stale matches. Moving them to history.")
                self.archive_matches_by_ids(stale_ids)

        except OperationFailure as e:
            logging.error(f"ARCHIVER: A database operation failed during garbage collection. Error: {e}")
        except Exception as e:
            logging.error(f"ARCHIVER: An unexpected error occurred during garbage collection. Error: {e}",
                          exc_info=True)

    def _process_archiving(self, matches_to_archive: list):
        """
        Handles the insert-then-delete logic to safely move documents
        from the active collection to the history collection.
        """
        if not matches_to_archive:
            return

        original_ids = {match['_id'] for match in matches_to_archive}
        ids_to_delete = set()

        try:
            self.history_collection.insert_many(matches_to_archive, ordered=False)
            ids_to_delete = original_ids
            logging.info(f"ARCHIVER: Successfully inserted {len(matches_to_archive)} matches into 'tenipo_history'.")
        except BulkWriteError as bwe:
            unsafe_ids = {err['op']['_id'] for err in bwe.details['writeErrors'] if err['code'] != 11000}
            ids_to_delete = original_ids - unsafe_ids
            num_duplicates = len(original_ids) - len(unsafe_ids) - bwe.details['nInserted']
            logging.warning(
                f"ARCHIVER: Bulk write to history had errors. {num_duplicates} were duplicates. {len(ids_to_delete)} matches are safe to delete.")
        except Exception as e:
            logging.error(f"ARCHIVER: Failed to insert documents into history. Aborting delete step. Error: {e}")
            return

        if not ids_to_delete:
            logging.warning("ARCHIVER: No matches were cleared from the active collection this cycle.")
            return

        delete_result = self.active_collection.delete_many({"_id": {"$in": list(ids_to_delete)}})
        logging.info(f"ARCHIVER: Cleaned up {delete_result.deleted_count} matches from the active 'tenipo' collection.")