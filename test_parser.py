#!/usr/bin/env python3
"""
Test script to verify the log parser works correctly.
Run this to check if the parser can extract data from your Player.log file.
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src.parser.log_parser import MTGALogParser


def main():
    log_path = Path(__file__).parent / "data" / "Player.log"

    if not log_path.exists():
        print(f"Log file not found: {log_path}")
        return 1

    print(f"Parsing log file: {log_path}")
    print(f"File size: {log_path.stat().st_size / 1024 / 1024:.2f} MB")
    print()

    parser = MTGALogParser(str(log_path))
    matches = parser.parse_matches()

    print(f"Found {len(matches)} matches:")
    print()

    for i, match in enumerate(matches, 1):
        print(f"Match {i}:")
        print(f"  Match ID: {match.match_id[:20]}..." if match.match_id else "  Match ID: None")
        print(f"  Player: {match.player_name} (Seat {match.player_seat_id})")
        print(f"  Opponent: {match.opponent_name} (Seat {match.opponent_seat_id})")
        print(f"  Result: {match.result or 'unknown'}")
        print(f"  Format: {match.event_id or 'unknown'}")
        print(f"  Deck: {match.deck_name or 'unknown'}")
        print(f"  Turns: {match.total_turns}")
        print(f"  Actions: {len(match.actions)}")
        print(f"  Card instances: {len(match.card_instances)}")
        if match.start_time:
            print(f"  Start: {match.start_time}")
        if match.end_time:
            print(f"  End: {match.end_time}")
        print()

    return 0


if __name__ == '__main__':
    sys.exit(main())

