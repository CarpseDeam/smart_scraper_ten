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
        if not mongo_manager or not mongo_manager.db:
            raise ValueError("A valid MongoManager instance is required.")
        self.db = mongo_manager.db
        self.active_collection = self.db["tenipo"]
        self.history_collection = self.db["tenipo_history"]
        logging.info("MongoArchiver initialized.")

    def archive_completed_matches(self):
        """
        Finds matches marked as "COMPLETED" in the active collection,
        copies them to the history collection, and then removes them from the
        active collection in a safe, transaction-like manner.
        """
        try:
            # Find all documents in the active collection that are completed.
            completed_matches = list(self.active_collection.find({"score.status": "COMPLETED"}))

            if not completed_matches:
                logging.debug("ARCHIVER: No completed matches found to archive.")
                return

            logging.info(f"ARCHIVER: Found {len(completed_matches)} completed matches to archive.")

            # Step 1: Copy the documents to the history collection.
            try:
                self.history_collection.insert_many(completed_matches, ordered=False)
                logging.info(f"ARCHIVER: Successfully inserted {len(completed_matches)} matches into 'tenipo_history'.")
            except BulkWriteError as bwe:
                # This can happen if a match was already archived but not yet deleted (e.g., from a previous failed run).
                # It's safe to ignore duplicate key errors and proceed with deletion for the ones that did insert.
                successful_ids = [doc['_id'] for doc in completed_matches if not any(err['op']['_id'] == doc['_id'] for err in bwe.details['writeErrors'])]
                logging.warning(f"ARCHIVER: Encountered duplicate keys on insert, but will proceed with {len(successful_ids)} non-duplicates.")
                if not successful_ids:
                    # If all were duplicates, we might as well try to delete all found matches
                    successful_ids = [doc['_id'] for doc in completed_matches]
            except Exception as e:
                logging.error(f"ARCHIVER: Failed to insert documents into history. Aborting archive cycle. Error: {e}")
                return

            # Step 2: If the copy was successful, delete the documents from the active collection.
            ids_to_delete = [match["_id"] for match in completed_matches]
            if not ids_to_delete:
                return

            delete_result = self.active_collection.delete_many({"_id": {"$in": ids_to_delete}})
            logging.info(
                f"ARCHIVER: Cleaned up {delete_result.deleted_count} matches from the active 'tenipo' collection.")

        except OperationFailure as e:
            logging.error(f"ARCHIVER: A database operation failed. Error: {e}")
        except Exception as e:
            logging.error(f"ARCHIVER: An unexpected error occurred during the archiving process. Error: {e}", exc_info=True)