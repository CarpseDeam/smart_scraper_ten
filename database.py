import logging
import pymongo
from pymongo.errors import ConnectionFailure, OperationFailure
from typing import List, Dict, Any

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
            self.ensure_indexes()
        except ConnectionFailure as e:
            logging.critical(
                f"FATAL: Could not connect to MongoDB. Is the service running and the URI correct? Error: {e}")
            self.client = None
        except Exception as e:
            logging.critical(f"An unexpected error occurred during MongoDB initialization: {e}")
            self.client = None

    def ensure_indexes(self):
        """
        Ensures that the necessary indexes exist in the collections for optimal performance.
        This is idempotent - it's safe to run multiple times.
        """
        if not self.db:
            return
        try:
            logging.info("DB_SETUP: Ensuring database indexes exist...")
            # Index for the archiver to quickly find completed matches
            self.db["tenipo"].create_index([("score.status", pymongo.ASCENDING)])
            logging.info("DB_SETUP: 'score.status' index on 'tenipo' collection is ensured.")
        except OperationFailure as e:
            logging.error(f"DB_SETUP: Failed to create indexes. This may impact performance. Error: {e}")

    def save_match_data(self, match_id: str, data: dict):
        """
        Saves match data to the database using an "upsert" operation.
        This updates the document if it exists, or inserts it if it's new.
        """
        if not self.client:
            logging.error("Cannot save match data: MongoDB client is not connected.")
            return

        try:
            matches_collection = self.db["tenipo"]
            result = matches_collection.update_one(
                {"_id": match_id},
                {"$set": data},
                upsert=True
            )
            if result.upserted_id:
                logging.info(f"DB: INSERTED new match with ID: {match_id} into collection 'tenipo'")
            elif result.modified_count > 0:
                logging.info(f"DB: UPDATED existing match with ID: {match_id} in collection 'tenipo'")
        except OperationFailure as e:
            logging.error(f"DB: A database operation failed for match ID {match_id}. Error: {e}")
        except Exception as e:
            logging.error(f"DB: An unexpected error occurred while saving match ID {match_id}. Error: {e}")

    def get_all_active_matches(self) -> List[Dict[str, Any]]:
        """
        Retrieves all documents from the active 'tenipo' collection.
        This is used to build the stable API cache.
        """
        if not self.db:
            return []
        try:
            return list(self.db["tenipo"].find({}))
        except OperationFailure as e:
            logging.error(f"DB: Failed to retrieve active matches for cache rebuild. Error: {e}")
            return []

    def close(self):
        """Closes the database connection."""
        if self.client:
            self.client.close()
            logging.info("MongoDB connection closed.")