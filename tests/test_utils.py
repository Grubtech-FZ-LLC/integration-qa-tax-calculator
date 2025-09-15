"""Tests for utility functions.""""""Essential tests for utility modules - Config and Logging."""

import pytest

import osimport pytest

from unittest.mock import patchfrom smart_cal.utils.config import Config

from smart_cal.utils.logging import setup_logging

from smart_cal.utils.config import load_config

from smart_cal.utils.logging import setup_logging

def test_config_loads_env_file():

    """Test that config can load .env file for MongoDB connection."""

class TestConfig:    config = Config(".env")  # This is how CLI uses it

    """Test cases for configuration utilities."""    

        # Should be able to get MongoDB settings

    def test_load_config_with_env(self, mock_env):    assert config.get("mongo_url") is not None or config.get("mongo_url") == ""

        """Test loading configuration with environment variables."""    assert config.get("mongo_db") is not None

        config = load_config()    assert config.get("mongo_collection") is not None

        

        assert config['db_connection_url'] == 'mongodb://test:test@localhost:27017'

        assert config['db_name_stg'] == 'test_staging_db'def test_config_default_values():

        assert config['db_name_prod'] == 'test_production_db'    """Test config provides reasonable defaults."""

        assert config['log_level'] == 'DEBUG'    config = Config()  # No .env file

        

    def test_load_config_defaults(self):    assert config.get("mongo_db") == "GRUBTECH_MASTER_DATA_STG_V2"  # Default to staging

        """Test loading configuration with default values."""    assert config.get("mongo_collection") == "PARTNER_RESTAURANT_ORDER"

        with patch.dict(os.environ, {}, clear=True):    assert config.get("log_level") == "INFO"

            config = load_config()

            

            assert config['db_connection_url'] == ''def test_logging_setup():

            assert config['db_name_stg'] == 'GRUBTECH_MASTER_DATA_STG_V2'    """Test that logging can be set up for CLI usage."""

            assert config['db_name_prod'] == 'GRUBTECH_MASTER_DATA_PROD_V2'    logger = setup_logging(level="INFO")

            assert config['log_level'] == 'INFO'    

    assert logger.name == "smart_cal"

    assert logger.level == 20  # INFO level

class TestLogging:

    """Test cases for logging utilities."""
    
    def test_setup_logging_debug(self):
        """Test logging setup with DEBUG level."""
        logger = setup_logging('DEBUG')
        assert logger.level == 10  # DEBUG level
    
    def test_setup_logging_info(self):
        """Test logging setup with INFO level."""
        logger = setup_logging('INFO')
        assert logger.level == 20  # INFO level
    
    def test_setup_logging_default(self):
        """Test logging setup with default level."""
        logger = setup_logging()
        assert logger.level == 20  # INFO level (default)