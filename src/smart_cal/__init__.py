"""
Smart Cal - MongoDB Order Tax Verification CLI Tool

A streamlined CLI tool for verifying tax calculations in restaurant orders
stored in MongoDB databases. Supports staging and production environments.
"""

__version__ = "0.1.0"
__author__ = "Dinuka Abeysinghe"
__email__ = "integration-qa@grubtech.com"

# Import main modules for CLI functionality
from . import tax_calculation
from . import utils

__all__ = ["tax_calculation", "utils"]

