"""Pytest configuration and fixtures.""""""

import pytestMinimal pytest configuration for Smart Cal CLI tool tests.

import os"""

from unittest.mock import patch

import sys

from pathlib import Path

@pytest.fixture

def mock_env():# Add src to Python path for imports

    """Mock environment variables for testing."""src_path = Path(__file__).parent.parent / "src"

    with patch.dict(os.environ, {sys.path.insert(0, str(src_path))

        'DB_CONNECTION_URL': 'mongodb://test:test@localhost:27017',

        'DB_NAME_STG': 'test_staging_db',
        'DB_NAME_PROD': 'test_production_db',
        'LOG_LEVEL': 'DEBUG'
    }):
        yield


@pytest.fixture
def sample_order_data():
    """Sample order data for testing."""
    return {
        'order_id': 'test_order_123',
        'total_amount': 100.0,
        'tax_amount': 15.0,
        'items': [
            {
                'name': 'Test Item',
                'price': 100.0,
                'quantity': 1
            }
        ]
    }