"""
Views package for the stats app.

Re-exports all URL-facing views so that urls.py can continue to use
``from . import views`` and then ``views.dashboard`` etc. unchanged.
"""

from .backup import backup_download, backup_restore
from .cards import unknown_card_fix, unknown_cards_list
from .dashboard import api_stats, dashboard
from .decks import deck_detail, deck_gallery, deck_history, decks_list
from .imports import card_data, import_log, import_sessions
from .matches import match_analysis, match_detail, match_replay, matches_list

__all__ = [
    "dashboard",
    "api_stats",
    "matches_list",
    "match_analysis",
    "match_detail",
    "match_replay",
    "decks_list",
    "deck_detail",
    "deck_gallery",
    "deck_history",
    "import_log",
    "import_sessions",
    "card_data",
    "unknown_cards_list",
    "unknown_card_fix",
    "backup_download",
    "backup_restore",
]
