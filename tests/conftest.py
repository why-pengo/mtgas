"""
Configuration for pytest with Django.
"""

import os
import sys
from pathlib import Path

import django

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Configure Django settings
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mtgas_project.settings")


def pytest_configure():
    """Configure Django for testing."""
    django.setup()
