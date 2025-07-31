import logging
import pymongo
from pymongo.errors import ConnectionFailure, OperationFailure

import config


class MongoManager:
    """Manages all interactions with the MongoDB database."""

    def __init__(self, settings: config.Settings):
        self.client = None
        self.db = None
        try:
            logging.info(f"Attempting to connect to MongoDB at {settings.MONGO_URI}...")
            self.client = pymongo.MongoClient(settings.MONGO_URI, serverSelectionTimeoutMS=5000)
            # The ismaster command is cheap and does not require auth.
            self.client.admin.command('ismaster')
            self.db = self.client[settings.MONGO_DB_NAME]
            logging.info("MongoDB connection successful.")
        except ConnectionFailure as e:
            logging.critical(
                f"FATAL: Could not connect to MongoDB. Is the service running and the URI correct? Error: {e}")
            self.client = None
        except Exception as e:
            logging.critical(f"An unexpected error occurred during MongoDB initialization: {e}")
            self.client = None

    def save_match_data(self, match_id: str, data: dict):
        """
        Saves match data to the database using an "upsert" operation.
        This updates the document if it exists, or inserts it if it's new.
        """
        if not self.client:
            logging.error("Cannot save match data: MongoDB client is not connected.")
            return

        try:
            # The collection where we'll store the match documents
            matches_collection = self.db["matches"]

            # The "smart" part: update_one with upsert=True
            # We use the unique match_id as the document's _id for efficiency.
            result = matches_collection.update_one(
                {"_id": match_id},
                {"$set": data},
                upsert=True
            )

            if result.upserted_id:
                logging.info(f"DB: INSERTED new match with ID: {match_id}")
            elif result.modified_count > 0:
                logging.info(f"DB: UPDATED existing match with ID: {match_id}")

        except OperationFailure as e:
            logging.error(f"DB: A database operation failed for match ID {match_id}. Error: {e}")
        except Exception as e:
            logging.error(f"DB: An unexpected error occurred while saving match ID {match_id}. Error: {e}")

    def prune_completed_matches(self, live_match_ids: list[str]):
        """
        Deletes matches from the database that are no longer in the live feed.
        """
        if not self.client:
            logging.error("Cannot prune matches: MongoDB client is not connected.")
            return

        try:
            matches_collection = self.db["matches"]

            # Find all documents in the collection, returning only their IDs
            stored_match_ids_cursor = matches_collection.find({}, {"_id": 1})
            stored_match_ids = {doc["_id"] for doc in stored_match_ids_cursor}

            live_ids_set = set(live_match_ids)

            # These are matches that are in the database but NOT in the latest live feed.
            ids_to_delete = list(stored_match_ids - live_ids_set)

            if not ids_to_delete:
                return

            logging.info(f"DB_PRUNE: Found {len(ids_to_delete)} completed matches to prune: {ids_to_delete}")

            # Use the $in operator to delete all matching documents in one operation.
            result = matches_collection.delete_many({"_id": {"$in": ids_to_delete}})

            logging.info(f"DB_PRUNE: Successfully deleted {result.deleted_count} documents.")

        except Exception as e:
            logging.error(f"DB_PRUNE: An unexpected error occurred during pruning. Error: {e}")

    def close(self):
        """Closes the database connection."""
        if self.client:
            self.client.close()
            logging.info("MongoDB connection closed.")