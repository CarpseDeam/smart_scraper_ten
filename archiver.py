# archiver.py
import logging
from pymongo.errors import BulkWriteError, OperationFailure

from database import MongoManager


class MongoArchiver:
    """
    Handles the process of moving completed matches from the active collection
    to a permanent history collection.
    """

    def __init__(self, mongo_manager: MongoManager):
        if mongo_manager is None or mongo_manager.db is None:
            raise ValueError("A valid and connected MongoManager instance is required.")
        self.db = mongo_manager.db
        self.active_collection = self.db["tenipo"]
        self.history_collection = self.db["tenipo_history"]
        logging.info("MongoArchiver initialized.")

    def archive_completed_matches(self):
        """
        Finds completed matches, copies them to a history collection, and then
        deletes ONLY the successfully copied matches from the active collection.
        This process is designed to prevent data loss in case of partial failures.
        """
        try:
            completed_matches = list(self.active_collection.find({"score.status": "COMPLETED"}))

            if not completed_matches:
                logging.debug("ARCHIVER: No completed matches found to archive.")
                return

            logging.info(f"ARCHIVER: Found {len(completed_matches)} completed matches to archive.")
            original_ids = {match['_id'] for match in completed_matches}
            ids_to_delete = set()

            # Step 1: Attempt to copy all completed matches to the history collection.
            try:
                self.history_collection.insert_many(completed_matches, ordered=False)
                # If this succeeds without error, all matches were inserted.
                ids_to_delete = original_ids
                logging.info(
                    f"ARCHIVER: Successfully inserted all {len(completed_matches)} matches into 'tenipo_history'.")

            except BulkWriteError as bwe:
                # This occurs if some documents failed to insert.
                # We can safely delete documents that were either successfully inserted or failed due to a "duplicate key" error (code 11000).

                # Find IDs that failed for reasons OTHER than being a duplicate.
                # These are the ones we do NOT want to delete.
                unsafe_ids = {
                    err['op']['_id'] for err in bwe.details['writeErrors'] if err['code'] != 11000
                }

                # Safe IDs are all original IDs minus the ones that failed for other reasons.
                ids_to_delete = original_ids - unsafe_ids

                num_duplicates = sum(1 for err in bwe.details['writeErrors'] if err['code'] == 11000)
                logging.warning(
                    f"ARCHIVER: Bulk write to history had errors. {num_duplicates} were duplicates. {len(ids_to_delete)} matches are safe to delete.")

            except Exception as e:
                # For any other unexpected error during insertion, we abort the entire cycle.
                # We will not delete anything to be safe.
                logging.error(
                    f"ARCHIVER: Failed to insert documents into history. Aborting archive cycle to prevent data loss. Error: {e}")
                return

            # Step 2: If we have a list of safe IDs, delete them from the active collection.
            if not ids_to_delete:
                logging.info("ARCHIVER: No matches were cleared from the active collection this cycle.")
                return

            delete_result = self.active_collection.delete_many({"_id": {"$in": list(ids_to_delete)}})
            logging.info(
                f"ARCHIVER: Cleaned up {delete_result.deleted_count} matches from the active 'tenipo' collection.")

        except OperationFailure as e:
            logging.error(f"ARCHIVER: A database operation failed. Error: {e}")
        except Exception as e:
            logging.error(f"ARCHIVER: An unexpected error occurred during the archiving process. Error: {e}",
                          exc_info=True)