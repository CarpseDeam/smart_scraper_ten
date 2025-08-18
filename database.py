# database.py
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
            self.client.admin.command('ismaster')
            self.db = self.client[settings.MONGO_DB_NAME]
            logging.info("MongoDB connection successful.")
            self.ensure_indexes()
        except ConnectionFailure as e:
            logging.critical(f"FATAL: Could not connect to MongoDB. Error: {e}")
            self.client = None
        except Exception as e:
            logging.critical(f"An unexpected error occurred during MongoDB initialization: {e}")
            self.client = None

    def ensure_indexes(self):
        """Ensures that the necessary indexes exist."""
        if self.db is None: return
        try:
            logging.info("DB_SETUP: Ensuring database indexes exist...")
            self.db["tenipo"].create_index([("score.status", pymongo.ASCENDING)])
            self.db["tenipo"].create_index([("timePolled", pymongo.ASCENDING)])
            logging.info("DB_SETUP: Indexes on 'tenipo' collection are ensured.")
        except OperationFailure as e:
            logging.error(f"DB_SETUP: Failed to create indexes. Error: {e}")

    def save_match_data(self, match_id: str, data: dict):
        """Saves match data to the database using an upsert operation."""
        if self.client is None: return
        try:
            matches_collection = self.db["tenipo"]
            result = matches_collection.update_one(
                {"_id": match_id},
                {"$set": data},
                upsert=True
            )
            if result.upserted_id:
                logging.info(f"DB: INSERTED new match with ID: {match_id}")
            elif result.modified_count > 0:
                logging.info(f"DB: UPDATED existing match with ID: {match_id}")
        except Exception as e:
            logging.error(f"DB: An unexpected error occurred while saving match ID {match_id}. Error: {e}")

    def get_all_active_matches(self) -> List[Dict[str, Any]]:
        """Retrieves all full documents from the active 'tenipo' collection."""
        if self.db is None: return []
        try:
            return list(self.db["tenipo"].find({}))
        except OperationFailure as e:
            logging.error(f"DB: Failed to retrieve active matches for cache rebuild. Error: {e}")
            return []

    def get_all_active_match_ids(self) -> List[str]:
        """Efficiently retrieves only the _id of all documents in the active collection."""
        if self.db is None: return []
        try:
            return [doc['_id'] for doc in self.db["tenipo"].find({}, {"_id": 1})]
        except OperationFailure as e:
            logging.error(f"DB: Failed to retrieve active match IDs. Error: {e}")
            return []

    def close(self):
        """Closes the database connection."""
        if self.client:
            self.client.close()
            logging.info("MongoDB connection closed.")