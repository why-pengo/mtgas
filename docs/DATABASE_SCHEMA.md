# Database Schema Documentation

This document describes the database schema used by the MTG Arena Statistics Tracker.

## Overview

The database uses SQLite (via Django ORM) and consists of 7 main tables that track matches, decks, cards, and game events.

## Entity Relationship Diagram

```
┌──────────────────┐       ┌─────────────┐       ┌─────────────┐
│      cards       │       │    decks    │       │   matches   │
├──────────────────┤       ├─────────────┤       ├─────────────┤
│ grp_id (PK)      │◄──────│ id (PK)     │◄──────│ id (PK)     │
│ name             │       │ deck_id     │       │ match_id    │
│ mana_cost        │       │ name        │       │ deck_id(FK) │
│ cmc              │       │ format      │       │ result      │
│ type_line        │       │ created_at  │       │ opponent    │
│ colors           │       └─────────────┘       │ start_time  │
│ rarity           │              │              │ duration    │
│ is_token         │              │              └─────────────┘
│ object_type      │              │                     │
│ source_grp_id    │              ▼                     │
└──────────────────┘       ┌─────────────┐             │
        ▲                  │ deck_cards  │             │
        │                  ├─────────────┤             │
        └──────────────────│ deck_id(FK) │             │
                           │ card_grp_id │             │
                           │ quantity    │             │
                           └─────────────┘             │
                                                       │
        ┌──────────────────────────────────────────────┼──────────────────────┐
        │                                              │                      │
        ▼                                              ▼                      ▼
┌─────────────┐                              ┌─────────────┐         ┌──────────────┐
│game_actions │                              │life_changes │         │zone_transfers│
├─────────────┤                              ├─────────────┤         ├──────────────┤
│ id (PK)     │                              │ id (PK)     │         │ id (PK)      │
│ match_id(FK)│                              │ match_id(FK)│         │ match_id(FK) │
│ turn_number │                              │ turn_number │         │ turn_number  │
│ action_type │                              │ seat_id     │         │ instance_id  │
│ card_grp_id │                              │ life_total  │         │ card_grp_id  │
│ mana_cost   │                              │ change_amt  │         │ from_zone    │
└─────────────┘                              └─────────────┘         │ to_zone      │
                                                                     │ category     │
                                                                     └──────────────┘
```

## Tables

### 1. cards

Stores card metadata. Rows are populated from Scryfall bulk data for real MTG cards, and from game-state data for tokens, emblems, and other non-card game objects.

| Column | Type | Description |
|--------|------|-------------|
| `grp_id` | INTEGER (PK) | MTG Arena's card group ID |
| `name` | VARCHAR(255) | Card name (generated for tokens; e.g. `"1/1 Red Goblin Creature Token"`) |
| `mana_cost` | VARCHAR(100) | Mana cost string (e.g., `"{2}{U}{U}"`) |
| `cmc` | FLOAT | Converted mana cost / mana value |
| `type_line` | VARCHAR(255) | Card type (e.g., `"Creature — Human Wizard"`) |
| `colors` | JSON | Array of colors `["W", "U", "B", "R", "G"]` |
| `color_identity` | JSON | Color identity array |
| `set_code` | VARCHAR(10) | Set code (e.g., `"m21"`) |
| `rarity` | VARCHAR(20) | `common`, `uncommon`, `rare`, `mythic` |
| `oracle_text` | TEXT | Card rules text |
| `power` | VARCHAR(10) | Power (for creatures) |
| `toughness` | VARCHAR(10) | Toughness (for creatures) |
| `scryfall_id` | VARCHAR(50) | Scryfall UUID |
| `image_uri` | VARCHAR(500) | URL to card image |
| `updated_at` | DATETIME | Last update timestamp |
| `is_token` | BOOLEAN | `True` for tokens and emblems created by card abilities |
| `object_type` | VARCHAR(50) | Arena `GameObjectType_*` value (e.g. `"GameObjectType_Token"`); `NULL` for real cards |
| `source_grp_id` | INTEGER | `grp_id` of the card that created this token/emblem; `NULL` for real cards |

**Index:** Primary key on `grp_id`

#### Token and non-card rows

Not all rows in this table represent real MTG cards. The `object_type` column records the Arena game object type for non-card entries:

| `object_type` | `is_token` | How populated |
|---------------|-----------|---------------|
| `NULL` | `False` | Real card — name and metadata from Scryfall |
| `GameObjectType_Token` | `True` | Name generated from game state (power/toughness, color, subtype) |
| `GameObjectType_Emblem` | `True` | Always stored as `"Emblem"` |
| `GameObjectType_Adventure` | `False` | Scryfall attempted; `[Adventure] (N)` fallback |
| `GameObjectType_MDFCBack` | `False` | Scryfall attempted; `[MDFCBack] (N)` fallback |
| `GameObjectType_RoomLeft/Right` | `False` | Scryfall attempted; `[Room…] (N)` fallback |

System objects (`GameObjectType_TriggerHolder`, `GameObjectType_Ability`, `GameObjectType_RevealedCard`) are **never stored** in this table.

### 2. decks

Stores deck information.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER (PK) | Auto-increment ID |
| `deck_id` | VARCHAR(100) | MTG Arena deck UUID (unique) |
| `name` | VARCHAR(255) | Deck name |
| `description` | TEXT | Optional description |
| `format` | VARCHAR(50) | Format (Standard, Historic, etc.) |
| `created_at` | DATETIME | Creation timestamp |
| `updated_at` | DATETIME | Last update timestamp |

**Index:** Unique index on `deck_id`

### 3. deck_cards

Junction table for cards in decks.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER (PK) | Auto-increment ID |
| `deck_id` | INTEGER (FK) | Reference to decks.id |
| `card_grp_id` | INTEGER (FK) | Reference to cards.grp_id |
| `quantity` | INTEGER | Number of copies (1-4 typically) |
| `is_sideboard` | BOOLEAN | True if sideboard card |

**Constraints:** 
- Foreign key to `decks(id)` with CASCADE delete
- Foreign key to `cards(grp_id)`
- Unique constraint on (deck_id, card_grp_id, is_sideboard)

### 4. matches

Stores match/game information.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER (PK) | Auto-increment ID |
| `match_id` | VARCHAR(100) | MTG Arena match UUID (unique) |
| `game_number` | INTEGER | Game number in match (for Bo3) |
| `player_seat_id` | INTEGER | Player's seat (usually 1 or 2) |
| `player_name` | VARCHAR(255) | Player's display name |
| `player_user_id` | VARCHAR(100) | Player's Arena user ID |
| `opponent_seat_id` | INTEGER | Opponent's seat |
| `opponent_name` | VARCHAR(255) | Opponent's display name |
| `opponent_user_id` | VARCHAR(100) | Opponent's Arena user ID |
| `deck_id` | INTEGER (FK) | Reference to decks.id |
| `event_id` | VARCHAR(100) | Event type (Ladder, Draft, etc.) |
| `format` | VARCHAR(50) | SuperFormat from game |
| `match_type` | VARCHAR(50) | GameType (Duel, etc.) |
| `result` | VARCHAR(20) | win, loss, draw, incomplete |
| `winning_team_id` | INTEGER | Seat ID of winner |
| `winning_reason` | VARCHAR(100) | How the game ended |
| `player_final_life` | INTEGER | Player's life at end |
| `opponent_final_life` | INTEGER | Opponent's life at end |
| `start_time` | DATETIME | Match start timestamp |
| `end_time` | DATETIME | Match end timestamp |
| `duration_seconds` | INTEGER | Match duration in seconds |
| `total_turns` | INTEGER | Total turns played |
| `imported_at` | DATETIME | When imported to database |

**Indexes:**
- Unique index on `match_id`
- Index on `start_time`
- Index on `result`
- Index on `opponent_name`

### 5. game_actions

Stores individual game actions (plays, casts, etc.).

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER (PK) | Auto-increment ID |
| `match_id` | INTEGER (FK) | Reference to matches.id |
| `game_state_id` | INTEGER | Game state sequence number |
| `turn_number` | INTEGER | Turn number |
| `phase` | VARCHAR(50) | Phase (Main1, Combat, etc.) |
| `step` | VARCHAR(50) | Step within phase |
| `active_player_seat` | INTEGER | Whose turn it is |
| `seat_id` | INTEGER | Who performed action |
| `action_type` | VARCHAR(50) | ActionType_Cast, ActionType_Play, etc. |
| `instance_id` | INTEGER | Card instance ID in game |
| `card_grp_id` | INTEGER (FK) | Reference to cards.grp_id |
| `ability_grp_id` | INTEGER | For activated abilities |
| `mana_cost` | JSON | Mana spent for action |
| `target_ids` | JSON | Target instance IDs |
| `timestamp_ms` | BIGINT | Timestamp in milliseconds |

**Indexes:**
- Index on (match_id, turn_number)
- Foreign key to `matches(id)` with CASCADE delete

### 6. life_changes

Tracks life total changes during games.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER (PK) | Auto-increment ID |
| `match_id` | INTEGER (FK) | Reference to matches.id |
| `game_state_id` | INTEGER | Game state sequence number |
| `turn_number` | INTEGER | Turn number |
| `seat_id` | INTEGER | Which player |
| `life_total` | INTEGER | Current life total |
| `change_amount` | INTEGER | Change from previous (+/-) |
| `source_instance_id` | INTEGER | What caused the change |

### 7. zone_transfers

Tracks card movements between zones.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER (PK) | Auto-increment ID |
| `match_id` | INTEGER (FK) | Reference to matches.id |
| `game_state_id` | INTEGER | Game state sequence number |
| `turn_number` | INTEGER | Turn number |
| `instance_id` | INTEGER | Card instance ID |
| `card_grp_id` | INTEGER (FK) | Reference to cards.grp_id |
| `from_zone` | VARCHAR(50) | Origin zone integer ID (`NULL` for token creation events) |
| `to_zone` | VARCHAR(50) | Destination zone integer ID |
| `category` | VARCHAR(50) | Transfer type — see below |

**`category` values:**

| Value | Source | Description |
|-------|--------|-------------|
| `CastSpell` | Arena log | Spell cast from hand |
| `Draw` | Arena log | Card drawn from library |
| `PlayLand` | Arena log | Land played from hand |
| `TokenCreated` | **Synthetic** | Token/emblem created by a card ability; `from_zone` is `NULL` |
| *(others)* | Arena log | Other zone-transfer categories emitted by MTGA |

Rows with `category = "TokenCreated"` are **synthetic** — they are generated by the parser from `AnnotationType_TokenCreated` annotations (which carry no zone information) using the token's zone from `card_instances`. They give the match timeline and replay a concrete event for token creation.

### 8. import_sessions

Tracks log file import history.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER (PK) | Auto-increment ID |
| `log_file` | VARCHAR(500) | Path to imported log file |
| `file_size` | BIGINT | File size in bytes |
| `file_modified` | DATETIME | Log file modification time |
| `matches_imported` | INTEGER | Count of imported matches |
| `matches_skipped` | INTEGER | Count of skipped (duplicate) matches |
| `started_at` | DATETIME | Import start time |
| `completed_at` | DATETIME | Import completion time |
| `status` | VARCHAR(20) | pending, running, completed, failed |
| `error_message` | TEXT | Error details if failed |

## Key Relationships

1. **Deck → Cards**: Many-to-many through `deck_cards`
2. **Match → Deck**: Many-to-one (a match uses one deck)
3. **Match → Actions**: One-to-many (cascade delete)
4. **Match → Life Changes**: One-to-many (cascade delete)
5. **Match → Zone Transfers**: One-to-many (cascade delete)
6. **Actions/Transfers → Cards**: Many-to-one (for card details)

## Common Queries

### Win Rate by Deck
```sql
SELECT d.name, 
       COUNT(*) as games,
       SUM(CASE WHEN m.result = 'win' THEN 1 ELSE 0 END) as wins,
       ROUND(SUM(CASE WHEN m.result = 'win' THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) as win_rate
FROM matches m
JOIN decks d ON m.deck_id = d.id
WHERE m.result IS NOT NULL
GROUP BY d.id
ORDER BY games DESC;
```

### Recent Match History
```sql
SELECT m.start_time, m.result, m.opponent_name, d.name as deck_name, m.total_turns
FROM matches m
LEFT JOIN decks d ON m.deck_id = d.id
ORDER BY m.start_time DESC
LIMIT 20;
```

### Cards Played Most Often
```sql
SELECT c.name, COUNT(*) as times_played
FROM game_actions ga
JOIN cards c ON ga.card_grp_id = c.grp_id
WHERE ga.action_type = 'ActionType_Cast'
GROUP BY c.grp_id
ORDER BY times_played DESC
LIMIT 20;
```

### Tokens Created in a Match
```sql
SELECT c.name as token_name, c.source_grp_id, src.name as created_by,
       zt.turn_number
FROM zone_transfers zt
JOIN cards c ON zt.card_grp_id = c.grp_id
LEFT JOIN cards src ON c.source_grp_id = src.grp_id
WHERE zt.category = 'TokenCreated'
  AND zt.match_id = ?
ORDER BY zt.turn_number;
```

