# database.py
import logging
from datetime import datetime, timedelta, timezone

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
        """Ensures that the necessary indexes exist for optimal query performance."""
        if self.db is None: return
        try:
            logging.info("DB_SETUP: Ensuring database indexes exist...")
            self.db["tenipo"].create_index([("score.status", pymongo.ASCENDING)])
            self.db["tenipo"].create_index([("timePolled", pymongo.ASCENDING)])
            # Index to efficiently find matches needing detailed data enrichment.
            self.db["tenipo"].create_index([("detailedDataUpdated", pymongo.ASCENDING)])
            logging.info("DB_SETUP: Indexes on 'tenipo' collection are ensured.")
        except OperationFailure as e:
            logging.error(f"DB_SETUP: Failed to create indexes. Error: {e}")

    def save_match_data(self, match_id: str, data: dict):
        """Saves detailed match data to the database using an upsert operation."""
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
                logging.info(f"DB: UPDATED detailed data for match with ID: {match_id}")
        except Exception as e:
            logging.error(f"DB: An unexpected error occurred while saving match ID {match_id}. Error: {e}")

    def upsert_fast_data(self, match_id: str, fast_data: dict):
        """
        🏎️ FAST LANE: Atomically upserts live score data using a single update_one operation.
        This prevents race conditions and field conflicts by separating fields for creation and update.
        - $set: Updates live score data on every call for existing documents.
        - $setOnInsert: Initializes the document with fields that are not updated on every call, only when it's first created.
        """
        if self.client is None: return
        try:
            matches_collection = self.db["tenipo"]

            # Fields that are always updated (the "live" data)
            set_fields = {
                "timePolled": fast_data["timePolled"],
                "score": fast_data["score"],
                "tournament": fast_data["tournament"],
                "players": fast_data["players"]
            }

            # Fields that are only set on initial document creation.
            # We start with the full data and remove the keys that are in `$set` to avoid conflicts.
            set_on_insert_fields = fast_data.copy()
            for key in set_fields.keys():
                set_on_insert_fields.pop(key, None)
            
            # The _id is used in the filter, not the update operation.
            set_on_insert_fields.pop("_id", None)

            result = matches_collection.update_one(
                {"_id": match_id},
                {
                    "$set": set_fields,
                    "$setOnInsert": set_on_insert_fields
                },
                upsert=True
            )

            if result.upserted_id:
                logging.info(f"FAST DB: Inserted new match {match_id}")
            elif result.modified_count > 0:
                logging.debug(f"FAST DB: Updated live data for match {match_id}")

        except Exception as e:
            logging.error(f"FAST DB: Error upserting fast data for match {match_id}: {e}")

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

    def get_matches_needing_enrichment(self) -> List[str]:
        """
        🐌 SLOW LANE: Finds matches that need detailed data enrichment, directly in the database.
        A match needs enrichment if:
        1. It has never been enriched (no 'detailedDataUpdated' field).
        2. Its last enrichment was more than 3 minutes ago.
        """
        if self.db is None: return []
        try:
            # Documents that were last updated more than 3 minutes ago
            stale_timestamp = datetime.now(timezone.utc) - timedelta(minutes=3)

            query = {
                "$or": [
                    # Condition 1: Never been enriched
                    {"detailedDataUpdated": {"$exists": False}},
                    # Condition 2: Enriched a while ago, making it stale
                    {"detailedDataUpdated": {"$lt": stale_timestamp.isoformat()}},
                    # Condition 3: Explicitly set to None by fast lane
                    {"detailedDataUpdated": None}
                ]
            }
            matches = self.db["tenipo"].find(query, {"_id": 1})
            return [match['_id'] for match in matches]
        except OperationFailure as e:
            logging.error(f"DB: Failed to find matches needing enrichment. Error: {e}")
            return []

    def close(self):
        """Closes the database connection."""
        if self.client:
            self.client.close()
            logging.info("MongoDB connection closed.")
