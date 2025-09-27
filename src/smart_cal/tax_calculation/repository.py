"""MongoDB repository for fetching order documents."""

from __future__ import annotations

from typing import Any, Dict, Optional

from pymongo import MongoClient

from ..utils.logging import get_logger
from ..utils.config import Config

logger = get_logger(__name__)


class OrderRepository:
    def __init__(self, url: Optional[str] = None, db_name: Optional[str] = None, collection: Optional[str] = None, connection_url_env_key: Optional[str] = None) -> None:
        """Repository initialization with robust env fallbacks.

        Precedence for URL:
        1. Explicit url param
        2. Environment variable specified by connection_url_env_key
        3. Standardized environment keys: PROD_DB_CONNECTION_URL / STG_DB_CONNECTION_URL / DEV_DB_CONNECTION_URL
        4. Legacy style keys in .env: DB_CONNECTION_URL_PROD / DB_CONNECTION_URL_STG
        5. Generic DB_CONNECTION_URL
        6. Config fallback (mongo_url from Config, which itself reads DB_CONNECTION_URL)
        """
        config = Config(".env")
        import os

        # Normalize provided specific key
        candidate_urls = []
        if url:
            candidate_urls.append(url)
        if connection_url_env_key:
            candidate_urls.append(os.getenv(connection_url_env_key))

        # Standard canonical keys
        candidate_urls.append(os.getenv("PROD_DB_CONNECTION_URL"))
        candidate_urls.append(os.getenv("STG_DB_CONNECTION_URL"))
        candidate_urls.append(os.getenv("DEV_DB_CONNECTION_URL"))

        # Legacy naming (present in current .env)
        candidate_urls.append(os.getenv("DB_CONNECTION_URL_PROD"))
        candidate_urls.append(os.getenv("DB_CONNECTION_URL_STG"))

        # Generic
        candidate_urls.append(os.getenv("DB_CONNECTION_URL"))

        # Config fallback
        candidate_urls.append(config.get("mongo_url"))

        # First non-empty
        self._url = next((c for c in candidate_urls if c), None)

        # Database name precedence similar style
        candidate_dbs = [
            db_name,
            os.getenv("DB_NAME"),
            os.getenv("DB_NAME_PROD"),
            os.getenv("DB_NAME_STG"),
            config.get("mongo_db"),
        ]
        self._db = next((d for d in candidate_dbs if d), None)
        if not self._db:
            self._db = "GRUBTECH_MASTER_DATA_STG_V2"

        self._collection = collection or os.getenv("COLLECTION_NAME") or config.get("mongo_collection")

        if not self._url:
            raise ValueError("DB_CONNECTION_URL is required (no suitable environment variable found)")

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
