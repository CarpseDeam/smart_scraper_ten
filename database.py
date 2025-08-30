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
            self.db["tenipo"].create_index([("hasDetailedData", pymongo.ASCENDING)])
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

    def upsert_fast_data(self, match_id: str, fast_data: dict):
        """
        🏎️ FAST LANE: Upserts live score data while preserving any existing detailed data.
        This ensures we don't overwrite stats/H2H with empty values.
        """
        if self.client is None: return
        try:
            matches_collection = self.db["tenipo"]

            # Check if match already exists
            existing_match = matches_collection.find_one({"_id": match_id})

            if existing_match:
                # Preserve detailed data if it exists
                update_fields = {
                    "timePolled": fast_data["timePolled"],
                    "score": fast_data["score"],
                    "tournament": fast_data["tournament"],
                    "players": fast_data["players"]
                }

                # Only update detailed fields if they're currently empty
                if not existing_match.get("statistics"):
                    update_fields["statistics"] = fast_data.get("statistics", [])
                if not existing_match.get("pointByPoint"):
                    update_fields["pointByPoint"] = fast_data.get("pointByPoint", [])
                if not existing_match.get("h2h"):
                    update_fields["h2h"] = fast_data.get("h2h", [])
                if not existing_match.get("matchInfo", {}).get("court"):
                    update_fields["matchInfo"] = fast_data.get("matchInfo", {})
                if not existing_match.get("round"):
                    update_fields["round"] = fast_data.get("round")

                result = matches_collection.update_one(
                    {"_id": match_id},
                    {"$set": update_fields}
                )
                if result.modified_count > 0:
                    logging.debug(f"FAST DB: Updated live data for match {match_id}")
            else:
                # New match - insert all data
                result = matches_collection.insert_one(fast_data)
                logging.info(f"FAST DB: Inserted new match {match_id}")

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
        🐌 SLOW LANE: Finds matches that need detailed data enrichment.
        """
        if self.db is None: return []
        try:
            # Find matches without detailed data or with stale detailed data
            query = {
                "$or": [
                    {"hasDetailedData": {"$ne": True}},
                    {"statistics": {"$size": 0}},
                    {"h2h": {"$size": 0}}
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