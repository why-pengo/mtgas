-- MTG Arena Statistics Database Schema
-- Database: SQLite

-- Stores information about each deck
CREATE TABLE IF NOT EXISTS decks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id TEXT UNIQUE NOT NULL,           -- UUID from MTG Arena
    name TEXT NOT NULL,
    description TEXT,
    format TEXT,                             -- Standard, Historic, etc.
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Stores the cards in each deck
CREATE TABLE IF NOT EXISTS deck_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id INTEGER NOT NULL,
    card_grp_id INTEGER NOT NULL,           -- Arena's card ID
    quantity INTEGER NOT NULL DEFAULT 1,
    is_sideboard BOOLEAN DEFAULT FALSE,
    FOREIGN KEY (deck_id) REFERENCES decks(id) ON DELETE CASCADE,
    UNIQUE(deck_id, card_grp_id, is_sideboard)
);

-- Card information cache (populated from Scryfall or log data)
CREATE TABLE IF NOT EXISTS cards (
    grp_id INTEGER PRIMARY KEY,              -- Arena's card group ID
    name TEXT,
    mana_cost TEXT,
    cmc REAL,                                -- Converted mana cost
    type_line TEXT,
    colors TEXT,                             -- JSON array of colors
    color_identity TEXT,                     -- JSON array
    set_code TEXT,
    rarity TEXT,
    oracle_text TEXT,
    power TEXT,
    toughness TEXT,
    scryfall_id TEXT,
    image_uri TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- Token / non-card game object metadata
    is_token BOOLEAN NOT NULL DEFAULT FALSE,
    object_type TEXT,                        -- Arena GameObjectType (e.g. GameObjectType_Token)
    source_grp_id INTEGER                    -- grpId of the card that created this token/emblem
);

-- Stores information about each match/game
CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id TEXT UNIQUE NOT NULL,           -- UUID from MTG Arena
    game_number INTEGER DEFAULT 1,           -- Game number within match (Bo3)

    -- Player info (the user)
    player_seat_id INTEGER,
    player_name TEXT,
    player_user_id TEXT,

    -- Opponent info
    opponent_seat_id INTEGER,
    opponent_name TEXT,
    opponent_user_id TEXT,

    -- Match details
    deck_id INTEGER,                         -- FK to decks table
    event_id TEXT,                           -- "Ladder", "Traditional_Standard", etc.
    format TEXT,                             -- SuperFormat from game
    match_type TEXT,                         -- GameType_Duel, etc.

    -- Result
    result TEXT CHECK(result IN ('win', 'loss', 'draw', 'incomplete')),
    winning_team_id INTEGER,
    winning_reason TEXT,

    -- Player states at end
    player_final_life INTEGER,
    opponent_final_life INTEGER,

    -- Timing
    start_time TIMESTAMP,
    end_time TIMESTAMP,
    duration_seconds INTEGER,
    total_turns INTEGER,

    -- Metadata
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (deck_id) REFERENCES decks(id)
);

-- Stores each action/play during a game
CREATE TABLE IF NOT EXISTS game_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL,

    -- Action context
    game_state_id INTEGER,                   -- For ordering
    turn_number INTEGER,
    phase TEXT,                              -- Phase_Main1, Phase_Combat, etc.
    step TEXT,                               -- Step_Upkeep, Step_Draw, etc.
    active_player_seat INTEGER,

    -- Action details
    seat_id INTEGER,                         -- Who performed the action
    action_type TEXT NOT NULL,               -- ActionType_Cast, ActionType_Play, etc.
    instance_id INTEGER,                     -- Card instance in this game
    card_grp_id INTEGER,                     -- Reference to cards table
    ability_grp_id INTEGER,                  -- For activated abilities

    -- Mana information
    mana_cost TEXT,                          -- JSON of mana cost

    -- Targeting (if applicable)
    target_ids TEXT,                         -- JSON array of target instance IDs

    -- Timestamp from log
    timestamp_ms INTEGER,

    FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE,
    FOREIGN KEY (card_grp_id) REFERENCES cards(grp_id)
);

-- Stores life total changes during the game
CREATE TABLE IF NOT EXISTS life_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL,
    game_state_id INTEGER,
    turn_number INTEGER,
    seat_id INTEGER NOT NULL,
    life_total INTEGER NOT NULL,
    change_amount INTEGER,                   -- Positive or negative
    source_instance_id INTEGER,              -- What caused the change

    FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE
);

-- Stores zone transfers (cards moving between zones)
CREATE TABLE IF NOT EXISTS zone_transfers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL,
    game_state_id INTEGER,
    turn_number INTEGER,

    instance_id INTEGER,
    card_grp_id INTEGER,
    from_zone TEXT,                          -- ZoneType_Hand, ZoneType_Library, etc.
    to_zone TEXT,
    category TEXT,                           -- CastSpell, Draw, PlayLand, etc.

    FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_matches_start_time ON matches(start_time);
CREATE INDEX IF NOT EXISTS idx_matches_result ON matches(result);
CREATE INDEX IF NOT EXISTS idx_matches_deck_id ON matches(deck_id);
CREATE INDEX IF NOT EXISTS idx_matches_opponent ON matches(opponent_name);
CREATE INDEX IF NOT EXISTS idx_matches_format ON matches(format);

CREATE INDEX IF NOT EXISTS idx_game_actions_match ON game_actions(match_id);
CREATE INDEX IF NOT EXISTS idx_game_actions_turn ON game_actions(match_id, turn_number);
CREATE INDEX IF NOT EXISTS idx_game_actions_card ON game_actions(card_grp_id);

CREATE INDEX IF NOT EXISTS idx_deck_cards_deck ON deck_cards(deck_id);
CREATE INDEX IF NOT EXISTS idx_deck_cards_card ON deck_cards(card_grp_id);

CREATE INDEX IF NOT EXISTS idx_life_changes_match ON life_changes(match_id);
CREATE INDEX IF NOT EXISTS idx_zone_transfers_match ON zone_transfers(match_id);

