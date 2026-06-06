"""Make the add-on's application package importable as ``app`` during tests."""
import os
import sys

ADDON_DIR = os.path.join(os.path.dirname(__file__), "..", "gitlab_config_sync")
sys.path.insert(0, os.path.abspath(ADDON_DIR))
