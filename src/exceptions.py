"""
Custom exceptions for MTG Arena Statistics Tracker.

Provides specific exception types for different error scenarios.
"""


class MTGAStatsError(Exception):
    """Base exception for all MTGA Stats errors."""
    pass


class LogParseError(MTGAStatsError):
    """Error during log file parsing."""

    def __init__(self, message: str, line_number: int = None, details: str = None):
        self.line_number = line_number
        self.details = details

        full_message = message
        if line_number:
            full_message += f" (line {line_number})"
        if details:
            full_message += f": {details}"

        super().__init__(full_message)


class InvalidLogFormatError(LogParseError):
    """Log file format is not recognized or invalid."""
    pass


class IncompleteLogError(LogParseError):
    """Log file appears to be truncated or incomplete."""
    pass


class CardLookupError(MTGAStatsError):
    """Error looking up card data."""

    def __init__(self, message: str, grp_id: int = None):
        self.grp_id = grp_id
        full_message = message
        if grp_id:
            full_message += f" (grpId: {grp_id})"
        super().__init__(full_message)


class ScryfallError(MTGAStatsError):
    """Error interacting with Scryfall data."""
    pass


class ScryfallDownloadError(ScryfallError):
    """Failed to download Scryfall bulk data."""
    pass


class ScryfallIndexError(ScryfallError):
    """Failed to build or load Scryfall index."""
    pass


class ImportError(MTGAStatsError):
    """Error during match import."""

    def __init__(self, message: str, match_id: str = None):
        self.match_id = match_id
        full_message = message
        if match_id:
            full_message += f" (matchId: {match_id[:20]}...)"
        super().__init__(full_message)


class DuplicateMatchError(ImportError):
    """Match has already been imported."""
    pass


class DatabaseError(MTGAStatsError):
    """Error with database operations."""
    pass

