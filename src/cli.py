#!/usr/bin/env python3
"""
MTG Arena Statistics Tracker CLI.

Command-line interface for importing log files and querying statistics.
"""

import argparse
import sys
import logging
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.database import init_db, get_db
from src.services.import_service import DataImportService, import_log
from src.services.scryfall import get_scryfall


def setup_logging(verbose: bool = False):
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


def cmd_import(args):
    """Import a log file into the database."""
    log_path = args.log_file

    if not Path(log_path).exists():
        print(f"Error: Log file not found: {log_path}")
        return 1

    print(f"Importing log file: {log_path}")
    count = import_log(log_path, args.database)
    print(f"Successfully imported {count} matches")
    return 0


def cmd_init(args):
    """Initialize the database."""
    db = init_db(args.database)
    print(f"Database initialized at: {db.db_path}")
    return 0


def cmd_stats(args):
    """Show overall statistics."""
    db = init_db(args.database)

    # Total matches
    cursor = db.execute("SELECT COUNT(*) as count FROM matches")
    total_matches = cursor.fetchone()['count']

    # Win/loss record
    cursor = db.execute("""
        SELECT result, COUNT(*) as count 
        FROM matches 
        WHERE result IS NOT NULL
        GROUP BY result
    """)
    results = {row['result']: row['count'] for row in cursor.fetchall()}

    wins = results.get('win', 0)
    losses = results.get('loss', 0)
    win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0

    # Top decks
    cursor = db.execute("""
        SELECT d.name, 
               COUNT(*) as games,
               SUM(CASE WHEN m.result = 'win' THEN 1 ELSE 0 END) as wins
        FROM matches m
        JOIN decks d ON m.deck_id = d.id
        GROUP BY d.id
        ORDER BY games DESC
        LIMIT 5
    """)
    top_decks = cursor.fetchall()

    # Most played opponents
    cursor = db.execute("""
        SELECT opponent_name, COUNT(*) as games,
               SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) as wins
        FROM matches
        WHERE opponent_name IS NOT NULL
        GROUP BY opponent_name
        ORDER BY games DESC
        LIMIT 5
    """)
    top_opponents = cursor.fetchall()

    # Format breakdown
    cursor = db.execute("""
        SELECT event_id, COUNT(*) as games,
               SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) as wins
        FROM matches
        WHERE event_id IS NOT NULL
        GROUP BY event_id
        ORDER BY games DESC
    """)
    formats = cursor.fetchall()

    # Print stats
    print("\n" + "=" * 50)
    print("MTG Arena Statistics")
    print("=" * 50)

    print(f"\nTotal Matches: {total_matches}")
    print(f"Record: {wins}W - {losses}L ({win_rate:.1f}% win rate)")

    print("\n--- Top Decks ---")
    for deck in top_decks:
        deck_wr = (deck['wins'] / deck['games'] * 100) if deck['games'] > 0 else 0
        print(f"  {deck['name']}: {deck['games']} games, {deck['wins']}W ({deck_wr:.1f}%)")

    print("\n--- Most Played Opponents ---")
    for opp in top_opponents:
        opp_wr = (opp['wins'] / opp['games'] * 100) if opp['games'] > 0 else 0
        print(f"  {opp['opponent_name']}: {opp['games']} games, {opp['wins']}W ({opp_wr:.1f}%)")

    print("\n--- Format Breakdown ---")
    for fmt in formats:
        fmt_wr = (fmt['wins'] / fmt['games'] * 100) if fmt['games'] > 0 else 0
        print(f"  {fmt['event_id']}: {fmt['games']} games, {fmt['wins']}W ({fmt_wr:.1f}%)")

    print()
    return 0


def cmd_matches(args):
    """List recent matches."""
    db = init_db(args.database)

    limit = args.limit or 10

    cursor = db.execute("""
        SELECT m.*, d.name as deck_name
        FROM matches m
        LEFT JOIN decks d ON m.deck_id = d.id
        ORDER BY m.start_time DESC
        LIMIT ?
    """, (limit,))

    matches = cursor.fetchall()

    print(f"\n--- Last {len(matches)} Matches ---\n")

    for match in matches:
        result_str = match['result'] or 'incomplete'
        result_emoji = {'win': '✓', 'loss': '✗', 'incomplete': '?'}.get(result_str, '?')

        time_str = match['start_time'] or 'Unknown time'
        deck_str = match['deck_name'] or 'Unknown deck'
        opp_str = match['opponent_name'] or 'Unknown'
        turns = match['total_turns'] or 0

        print(f"  [{result_emoji}] {time_str}")
        print(f"      vs {opp_str} | {deck_str} | {match['event_id'] or 'Unknown format'}")
        print(f"      {turns} turns")
        if match['duration_seconds']:
            mins = match['duration_seconds'] // 60
            secs = match['duration_seconds'] % 60
            print(f"      Duration: {mins}m {secs}s")
        print()

    return 0


def cmd_deck(args):
    """Show deck details."""
    db = init_db(args.database)

    # Find deck
    cursor = db.execute("""
        SELECT * FROM decks WHERE name LIKE ? OR deck_id = ?
    """, (f"%{args.deck_name}%", args.deck_name))

    deck = cursor.fetchone()
    if not deck:
        print(f"Deck not found: {args.deck_name}")
        return 1

    print(f"\n--- Deck: {deck['name']} ---")
    print(f"Format: {deck['format']}")

    # Get deck cards with names
    cursor = db.execute("""
        SELECT dc.quantity, c.name, c.mana_cost, c.type_line, c.rarity
        FROM deck_cards dc
        LEFT JOIN cards c ON dc.card_grp_id = c.grp_id
        WHERE dc.deck_id = ?
        ORDER BY c.cmc, c.name
    """, (deck['id'],))

    cards = cursor.fetchall()

    print(f"\nMain Deck ({sum(c['quantity'] for c in cards)} cards):")
    for card in cards:
        name = card['name'] or 'Unknown'
        mana = card['mana_cost'] or ''
        print(f"  {card['quantity']}x {name} {mana}")

    # Match stats with this deck
    cursor = db.execute("""
        SELECT COUNT(*) as games,
               SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) as wins
        FROM matches WHERE deck_id = ?
    """, (deck['id'],))

    stats = cursor.fetchone()
    if stats['games'] > 0:
        wr = (stats['wins'] / stats['games'] * 100)
        print(f"\nStats: {stats['games']} games, {stats['wins']}W ({wr:.1f}%)")

    return 0


def cmd_cards(args):
    """Download and index card data from Scryfall."""
    print("Downloading card data from Scryfall...")
    scryfall = get_scryfall()

    if args.full:
        # Download full bulk data
        bulk_file = scryfall.download_bulk_data()
        if bulk_file:
            count = scryfall.build_arena_id_index(bulk_file)
            print(f"Indexed {count} cards with Arena IDs")
    else:
        print("Use --full to download complete card database (~350MB)")
        print("Otherwise, cards will be looked up individually as needed.")

    return 0


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description='MTG Arena Statistics Tracker',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Enable verbose logging')
    parser.add_argument('-d', '--database', type=str, default=None,
                        help='Path to database file')

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # Init command
    init_parser = subparsers.add_parser('init', help='Initialize database')

    # Import command
    import_parser = subparsers.add_parser('import', help='Import log file')
    import_parser.add_argument('log_file', help='Path to Player.log file')

    # Stats command
    stats_parser = subparsers.add_parser('stats', help='Show statistics')

    # Matches command
    matches_parser = subparsers.add_parser('matches', help='List recent matches')
    matches_parser.add_argument('-n', '--limit', type=int, default=10,
                                help='Number of matches to show')

    # Deck command
    deck_parser = subparsers.add_parser('deck', help='Show deck details')
    deck_parser.add_argument('deck_name', help='Deck name or ID')

    # Cards command
    cards_parser = subparsers.add_parser('cards', help='Download card data')
    cards_parser.add_argument('--full', action='store_true',
                              help='Download full Scryfall bulk data')

    args = parser.parse_args()
    setup_logging(args.verbose)

    if args.command == 'init':
        return cmd_init(args)
    elif args.command == 'import':
        return cmd_import(args)
    elif args.command == 'stats':
        return cmd_stats(args)
    elif args.command == 'matches':
        return cmd_matches(args)
    elif args.command == 'deck':
        return cmd_deck(args)
    elif args.command == 'cards':
        return cmd_cards(args)
    else:
        parser.print_help()
        return 0


if __name__ == '__main__':
    sys.exit(main())

