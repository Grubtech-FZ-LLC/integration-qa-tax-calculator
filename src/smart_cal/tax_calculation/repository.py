"""MongoDB repository for fetching order documents."""

from __future__ import annotations

from typing import Any, Dict, Optional

from pymongo import MongoClient

from ..utils.logging import get_logger
from ..utils.config import Config

logger = get_logger(__name__)


class OrderRepository:
    def __init__(self, url: Optional[str] = None, db_name: Optional[str] = None, collection: Optional[str] = None, connection_url_env_key: Optional[str] = None) -> None:
        # Load config with .env file explicitly
        config = Config(".env")
        
        # Determine connection URL based on environment-specific key if provided
        if connection_url_env_key:
            # Get the environment-specific URL directly from os.environ
            import os
            self._url = os.getenv(connection_url_env_key) or config.get("mongo_url")
        else:
            self._url = url or config.get("mongo_url")
            
        self._db = db_name or config.get("mongo_db")
        self._collection = collection or config.get("mongo_collection")
        if not self._url:
            raise ValueError("DB_CONNECTION_URL is required")
        self._client: Optional[MongoClient] = None

    def __enter__(self) -> "OrderRepository":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.disconnect()

    def connect(self) -> None:
        if self._client is None:
            self._client = MongoClient(self._url, serverSelectionTimeoutMS=5000)

    def disconnect(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def get_order_by_internal_id(self, internal_id: str) -> Optional[Dict[str, Any]]:
        if self._client is None:
            self.connect()
        db = self._client[self._db]
        coll = db[self._collection]
        return coll.find_one({"internalId": internal_id})
