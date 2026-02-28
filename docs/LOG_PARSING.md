# Log Parsing Documentation

This document explains how the MTG Arena log file is parsed to extract game data.

## Overview

MTG Arena generates a log file (`Player.log`) that contains detailed information about game events, including match states, card plays, and game outcomes. This application parses that log to extract and store game statistics.

## Log File Location

- **macOS**: `~/Library/Logs/Wizards Of The Coast/MTGA/Player.log`
- **Windows**: `%APPDATA%\..\LocalLow\Wizards Of The Coast\MTGA\Player.log`
- **Linux**: `~/.wine/drive_c/users/<user>/AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log`

## Log File Format

The log file is a text file containing a mix of:
- Plain text log messages
- JSON objects embedded in log lines
- Timestamps in various formats

### Key Event Types

#### 1. Match State Events (`matchGameRoomStateChangedEvent`)

These events signal match start, state changes, and completion.

```json
{
  "matchGameRoomStateChangedEvent": {
    "gameRoomInfo": {
      "stateType": "MatchGameRoomStateType_Playing",
      "gameRoomConfig": {
        "matchId": "abc123-def456-...",
        "reservedPlayers": [
          {
            "playerName": "Player1",
            "systemSeatId": 2,
            "userId": "user-id-123",
            "eventId": "Ladder"
          },
          {
            "playerName": "Opponent",
            "systemSeatId": 1,
            "userId": "user-id-456",
            "eventId": "Ladder"
          }
        ]
      },
      "finalMatchResult": {
        "winningTeamId": 2,
        "resultList": [...]
      }
    }
  }
}
```

**State Types:**
- `MatchGameRoomStateType_Playing`: Match in progress
- `MatchGameRoomStateType_MatchCompleted`: Match finished

#### 2. Game State Events (`greToClientEvent`)

These contain detailed game state updates including turns, actions, and card information.

```json
{
  "greToClientEvent": {
    "greToClientMessages": [
      {
        "type": "GREMessageType_GameStateMessage",
        "gameStateMessage": {
          "gameStateId": 123,
          "turnInfo": {
            "turnNumber": 5,
            "phase": "Phase_Main1",
            "step": "Step_BeginCombat",
            "activePlayer": 2
          },
          "gameInfo": {
            "superFormat": "SuperFormat_Standard",
            "type": "GameType_Duel"
          },
          "players": [
            {
              "systemSeatNumber": 1,
              "lifeTotal": 15
            },
            {
              "systemSeatNumber": 2,
              "lifeTotal": 20
            }
          ],
          "gameObjects": [...],
          "actions": [...],
          "annotations": [...]
        }
      }
    ]
  }
}
```

#### 3. Deck Events

Deck information appears in `EventSetDeckV2` or `CourseDeck` events:

```json
{
  "CourseDeckSummary": {
    "Name": "Red Deck Wins",
    "DeckId": "deck-uuid-123",
    "Attributes": [
      {"name": "Format", "value": "Standard"}
    ]
  },
  "CourseDeck": {
    "MainDeck": [
      {"cardId": 12345, "quantity": 4},
      {"cardId": 12346, "quantity": 3}
    ]
  }
}
```

## Parsing Process

### 1. File Reading

The parser reads the file line by line to handle large files efficiently:

```python
with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
    for line in f:
        # Process each line
```

### 2. JSON Extraction

JSON can appear in several forms:
- Standalone JSON object on a line
- JSON embedded at the end of a log line
- Multi-line JSON blocks

The parser uses regex and incremental parsing:

```python
# Check for JSON starting on line
if line.strip().startswith('{'):
    try:
        data = json.loads(line.strip())
    except json.JSONDecodeError:
        # Multi-line JSON, accumulate lines
        
# Extract JSON from end of line
json_match = re.search(r'(\{.*\})\s*$', line)
```

### 3. Event Classification

Events are classified by their content:

| Pattern | Event Type | Description |
|---------|------------|-------------|
| `matchGameRoomStateChangedEvent` | `match_state` | Match lifecycle events |
| `greToClientEvent` | `gre_event` | Game state updates |
| `CourseDeck` | `course_deck` | Deck information |
| `EventSetDeckV2` | `deck_set` | Deck selection |

### 4. Match Assembly

The parser maintains state across events to build complete match records:

```python
class MTGALogParser:
    def __init__(self):
        self.current_match = None
        self.completed_matches = []
    
    def _process_match_state(self, event):
        match_id = event.data['matchId']
        
        if self.current_match is None or self.current_match.match_id != match_id:
            # New match started
            if self.current_match:
                self.completed_matches.append(self.current_match)
            self.current_match = MatchData(match_id=match_id)
        
        if state_type == 'MatchCompleted':
            # Extract result
            self.current_match.result = self._determine_result(event)
```

## Data Extraction Details

### Player Information

Extracted from `reservedPlayers` in match state events:
- `playerName`: Display name
- `systemSeatId`: Seat number (1 or 2)
- `userId`: Account identifier
- `eventId`: Format/event type (Ladder, Draft, etc.)

**Note:** The logged-in player is typically seat 2 in their own logs.

### Match Result

Determined from `finalMatchResult`:
```python
winning_team = final_result.get('winningTeamId')
if winning_team == player_seat_id:
    result = 'win'
else:
    result = 'loss'
```

### Turn Information

Extracted from `turnInfo` in game state messages:
- `turnNumber`: Current turn (tracked as max seen)
- `phase`: Current phase (Phase_Main1, Phase_Combat, etc.)
- `step`: Current step within phase
- `activePlayer`: Whose turn it is

### Card Objects

Game objects contain card information. Every `gameObjects` entry is stored in `card_instances`, keyed by `instanceId`:

```python
{
    "instanceId": 100,          # Unique ID for this game
    "grpId": 12345,             # Card type ID (maps to Scryfall)
    "type": "GameObjectType_Card",
    "cardTypes": ["CardType_Creature"],
    "subtypes": ["SubType_Human", "SubType_Wizard"],
    "color": ["ColorType_Blue"],
    "power": {"value": 2},
    "toughness": {"value": 1},
    "ownerSeatId": 2,
    "controllerSeatId": 2,
    "objectSourceGrpId": null,  # grpId of the card that created this object (tokens)
    "zoneId": 28                # Zone the object currently occupies
}
```

#### Arena Game Object Types

Not all game objects are real MTG cards. The `type` field determines how the object is handled:

| `type` | Description | Card DB treatment |
|--------|-------------|-------------------|
| `GameObjectType_Card` | A real MTG card | Looked up in Scryfall; `Unknown Card (N)` fallback |
| `GameObjectType_Token` | Token created by a card ability | Name generated from game state (e.g. `"1/1 Red Goblin Creature Token"`); stored with `is_token=True` |
| `GameObjectType_Emblem` | Planeswalker emblem | Stored as `"Emblem"` with `is_token=True` |
| `GameObjectType_Adventure` | Adventure half of an adventure card | Scryfall lookup attempted; `[Adventure] (N)` fallback |
| `GameObjectType_MDFCBack` | Back face of a modal double-faced card | Scryfall lookup attempted; `[MDFCBack] (N)` fallback |
| `GameObjectType_RoomLeft/Right` | Room half of a Room card | Scryfall lookup attempted; `[Room…] (N)` fallback |
| `GameObjectType_Omen` | Omen card | Scryfall lookup attempted |
| `GameObjectType_TriggerHolder` | Internal engine trigger object | **Skipped entirely** — not stored in the cards table |
| `GameObjectType_Ability` | Activated/triggered ability on the stack | **Skipped entirely** |
| `GameObjectType_RevealedCard` | Revealed card from an effect | **Skipped entirely** |

The `objectSourceGrpId` field (stored as `source_grp_id` in the `cards` table) links a token or emblem back to the card that created it.

### Actions

Player actions are captured with details:
```python
{
    "seatId": 2,
    "action": {
        "actionType": "ActionType_Cast",
        "instanceId": 100,
        "grpId": 12345,
        "manaCost": [
            {"color": "ManaColor_Blue", "count": 1}
        ]
    }
}
```

**Action Types:**
- `ActionType_Cast`: Cast a spell
- `ActionType_Play`: Play a land
- `ActionType_Attack`: Declare attacker
- `ActionType_Block`: Declare blocker
- `ActionType_Activate`: Activate ability
- `ActionType_Activate_Mana`: Activate mana ability
- `ActionType_Resolution`: Spell/ability resolves

### Zone Transfers

Card movements between zones (from `AnnotationType_ZoneTransfer` annotations):
```python
{
    "type": ["AnnotationType_ZoneTransfer"],
    "affectedIds": [100],
    "details": [
        {"key": "zone_src", "valueInt32": [1]},
        {"key": "zone_dest", "valueInt32": [4]},
        {"key": "category", "valueString": ["CastSpell"]}
    ]
}
```

**Zone Types:**
- Library, Hand, Battlefield, Graveyard, Exile, Stack, etc.

### Token Creation

When a card ability creates a token, MTGA emits `AnnotationType_TokenCreated` instead of a zone transfer:

```python
{
    "type": ["AnnotationType_TokenCreated"],
    "affectorId": 301,    # ability/spell that created the token
    "affectedIds": [302]  # instanceId of the new token
}
```

This annotation has no `zone_src`/`zone_dest` details. The parser handles it by:
1. Looking up the token's `zoneId` from `card_instances` (populated when game objects are processed earlier in the same game state message).
2. Emitting a **synthetic zone_transfer** with `from_zone=None`, `to_zone=<token's zoneId>`, and `category="TokenCreated"`.

This synthetic record flows through the import service and is stored in the `zone_transfers` table with `category="TokenCreated"`, making token creation events visible in the match timeline and replay.

## Import Service: Card Classification

There are two import paths; both implement the same classification logic and share constants from `src/services/import_service.py`:

| Import path | Entry point |
|---|---|
| CLI (`make import-log`) | `stats/management/commands/import_log.py` |
| Web UI (`/import/`) | `stats/views.py` |

Both import these shared symbols from `src/services/import_service.py`:

```python
from src.services.import_service import (
    _SKIP_OBJECT_TYPES,    # frozenset — engine objects to ignore entirely
    _TOKEN_OBJECT_TYPES,   # frozenset — tokens/emblems to name from game state
    generate_token_name,   # builds "1/1 Red Goblin Creature Token" etc.
)
```

### `_collect_card_ids(match_data)`

Separates game object IDs into two buckets:

- **`real_card_ids`** (`Set[int]`) — `GameObjectType_Card`, deck card IDs, and action card IDs → looked up in Scryfall.
- **`special_objects`** (`Dict[int, dict]`) — tokens, emblems, adventure faces, MDFC backs, room halves, Omens → handled without Scryfall (or with graceful fallback).

Object types in `_SKIP_OBJECT_TYPES` (`TriggerHolder`, `Ability`, `RevealedCard`) are discarded and never added to either set.

### `_ensure_cards(real_card_ids, special_objects, ...)`

**Real cards** — batch Scryfall lookup; cards not found get an `Unknown Card (N)` placeholder and an `UnknownCard` tracking record.

**Tokens / Emblems** — `generate_token_name()` builds a descriptive name from the game-state data and the row is inserted with `is_token=True`:

```
power/toughness  colors  subtypes  card_types  "Token"
→ "1/1 Red Goblin Creature Token"
→ "Treasure Artifact Token"
→ "Emblem"
```

**Other special objects** (Adventure, MDFCBack, RoomLeft, RoomRight, Omen) — Scryfall lookup attempted; on failure a `[Type] (N)` placeholder is stored with `object_type` set.

All token/emblem rows in the `cards` table have `is_token=True`, `object_type="GameObjectType_Token"` (or `"GameObjectType_Emblem"`), and `source_grp_id` pointing to the parent card.

## Error Handling

The parser implements robust error handling:

### 1. File Validation
```python
# Check file exists and is readable
if not log_path.exists():
    raise FileNotFoundError(...)

# Check file is not empty
if file_size == 0:
    raise InvalidLogFormatError("Log file is empty")

# Basic format validation
if 'Unity' not in header and 'MTGA' not in header:
    logger.warning("File may not be a valid MTGA log")
```

### 2. JSON Parse Errors
```python
try:
    data = json.loads(line)
except json.JSONDecodeError:
    # Skip malformed JSON, continue parsing
    pass
```

### 3. Missing Data
```python
# Graceful handling of missing fields
player_name = player.get('playerName', '')  # Default to empty
seat_id = player.get('systemSeatId')  # May be None
```

### 4. Error Tracking
```python
# Track non-fatal errors for reporting
self._parse_errors.append({
    'event_type': event.event_type,
    'line_number': event.line_number,
    'error': str(e)
})
```

## Performance Considerations

1. **Streaming**: File is read line-by-line, not loaded entirely into memory
2. **Generators**: Events are yielded as parsed, reducing memory usage
3. **Early Exit**: JSON parsing stops at first valid parse
4. **Deduplication**: Actions are deduplicated by (game_state_id, action_type, instance_id)

## Limitations

1. **Single Log File**: Parser processes one file at a time
2. **No Real-time**: Designed for batch import after sessions
3. **Bo3 Support**: Limited support for best-of-three match tracking
4. **Draft/Sealed**: Limited extraction of draft pick information

## Example Usage

```python
from src.parser.log_parser import MTGALogParser

# Parse log file
parser = MTGALogParser("/path/to/Player.log")
matches = parser.parse_matches()

# Process results
for match in matches:
    print(f"Match: {match.match_id}")
    print(f"  vs {match.opponent_name}")
    print(f"  Result: {match.result}")
    print(f"  Turns: {match.total_turns}")
    print(f"  Actions: {len(match.actions)}")

# Check for parse errors
errors = parser.get_parse_errors()
if errors:
    print(f"Encountered {len(errors)} non-fatal errors")
```

