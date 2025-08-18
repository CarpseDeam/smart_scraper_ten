# archiver.py
import logging
from datetime import datetime, timezone, timedelta
from pymongo.errors import BulkWriteError, OperationFailure

from database import MongoManager


class MongoArchiver:
    """
    Handles the process of moving completed matches from the active collection
    to a permanent history collection, and cleans up stale, unresponsive matches.
    """

    def __init__(self, mongo_manager: MongoManager):
        if mongo_manager is None or mongo_manager.db is None:
            raise ValueError("A valid and connected MongoManager instance is required.")
        self.db = mongo_manager.db
        self.active_collection = self.db["tenipo"]
        self.history_collection = self.db["tenipo_history"]
        # A match is stale if it hasn't been successfully updated in 15 minutes
        self.stale_threshold = timedelta(minutes=15)
        logging.info(
            f"MongoArchiver initialized with a {self.stale_threshold.total_seconds() / 60:.0f} minute stale threshold.")

    def archive_completed_matches(self):
        """
        Primary function to clean the active collection. It performs two main tasks:
        1. Archives any match explicitly marked with status "COMPLETED".
        2. Deletes any "stale" match that hasn't been updated recently, acting as a garbage collector.
        """
        try:
            # --- Task 1: Archive normally completed matches ---
            completed_matches = list(self.active_collection.find({"score.status": "COMPLETED"}))

            if completed_matches:
                logging.info(f"ARCHIVER: Found {len(completed_matches)} completed matches to archive.")
                self._process_archiving(completed_matches)
            else:
                logging.debug("ARCHIVER: No 'COMPLETED' status matches found to archive.")

            # --- Task 2: Garbage collect stale matches ---
            stale_cutoff_time = datetime.now(timezone.utc) - self.stale_threshold

            # Find matches that are old AND are not already marked as COMPLETED (to avoid double processing)
            stale_query = {
                "timePolled": {"$lt": stale_cutoff_time.isoformat()},
                "score.status": {"$ne": "COMPLETED"}
            }
            stale_matches = list(self.active_collection.find(stale_query))

            if stale_matches:
                stale_ids = [match['_id'] for match in stale_matches]
                logging.warning(
                    f"GARBAGE COLLECTOR: Found {len(stale_ids)} stale matches that haven't been updated in over {self.stale_threshold.total_seconds() / 60:.0f} minutes. Deleting them.")

                delete_result = self.active_collection.delete_many({"_id": {"$in": stale_ids}})
                logging.info(
                    f"GARBAGE COLLECTOR: Deleted {delete_result.deleted_count} stale matches from the active 'tenipo' collection.")

        except OperationFailure as e:
            logging.error(f"ARCHIVER: A database operation failed during cleanup. Error: {e}")
        except Exception as e:
            logging.error(f"ARCHIVER: An unexpected error occurred during the archiving process. Error: {e}",
                          exc_info=True)

    def _process_archiving(self, matches_to_archive: list):
        """
        Handles the insert-then-delete logic to safely move documents
        from the active collection to the history collection.
        """
        original_ids = {match['_id'] for match in matches_to_archive}
        ids_to_delete = set()

        # Step 1: Attempt to copy all completed matches to the history collection.
        try:
            self.history_collection.insert_many(matches_to_archive, ordered=False)
            ids_to_delete = original_ids
            logging.info(
                f"ARCHIVER: Successfully inserted all {len(matches_to_archive)} matches into 'tenipo_history'.")

        except BulkWriteError as bwe:
            # This occurs if some documents failed to insert.
            # We can safely delete documents that were either successfully inserted or failed due to a "duplicate key" error (code 11000).
            unsafe_ids = {
                err['op']['_id'] for err in bwe.details['writeErrors'] if err['code'] != 11000
            }
            ids_to_delete = original_ids - unsafe_ids
            num_duplicates = len(original_ids) - len(unsafe_ids) - bwe.details['nInserted']
            logging.warning(
                f"ARCHIVER: Bulk write to history had errors. {num_duplicates} were duplicates. {len(ids_to_delete)} matches are safe to delete.")

        except Exception as e:
            logging.error(
                f"ARCHIVER: Failed to insert documents into history. Aborting archive step to prevent data loss. Error: {e}")
            return

        # Step 2: If we have a list of safe IDs, delete them from the active collection.
        if not ids_to_delete:
            logging.info("ARCHIVER: No matches were cleared from the active collection this cycle.")
            return

        delete_result = self.active_collection.delete_many({"_id": {"$in": list(ids_to_delete)}})
        logging.info(
            f"ARCHIVER: Cleaned up {delete_result.deleted_count} matches from the active 'tenipo' collection.")