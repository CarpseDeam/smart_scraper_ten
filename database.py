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

    def close(self):
        """Closes the database connection."""
        if self.client:
            self.client.close()
            logging.info("MongoDB connection closed.")