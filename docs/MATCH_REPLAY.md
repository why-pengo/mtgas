# Match Replay Logic

The match replay feature (`/match/<id>/replay/`) presents a step-through play-by-play of a recorded match, showing the card image and a human-readable description for each meaningful game event.

---

## Why ZoneTransfers, not GameActions?

The MTGA client communicates via `greToClientEvent / GREMessageType_GameStateMessage` messages. Each message contains an `actions` array — but this array represents the **legal moves available** to the active player at that moment, not the moves that were actually made.

Consequences for the stored data:

- Every game-state update re-broadcasts the full action menu, so the `GameAction` table accumulates one copy of the menu per snapshot (10 000+ rows for a single match).
- ~75 % of rows are `ActionType_Activate_Mana` (tapping mana sources), which are noise for a replay.
- The same card can appear as a "Cast" option across dozens of consecutive snapshots without ever having been cast.

**`ZoneTransfer` records are different.** They come from `AnnotationType_ZoneTransfer` annotations, which are only emitted when a card *physically moves* between zones. Each record represents exactly one real game event: a draw, a land play, a spell cast, a creature dying, etc.

The replay therefore iterates over `ZoneTransfer` rows (ordered by `game_state_id`, then `id`) and ignores `GameAction` entirely.

---

## Zone ID Resolution

MTGA assigns per-match integer IDs to each zone instance. These IDs are **not fixed** across matches — zone 28 might be the Battlefield in one match and something else in another. The `_build_zone_labels()` function infers the role of each ID from statistical patterns.

### Inference Steps

| Step | Role | Heuristic |
|------|------|-----------|
| 1 | **Battlefield** | Zone with the highest *net* named-card accumulation (arrivals − departures). Permanents enter and stay. |
| 2 | **Stack** | High-throughput transit zone with near-zero net (≤ 3). Spells arrive when cast, leave when they resolve or are countered. |
| 3 | **Opponent's Library** | Zone with the most *anonymous* (face-down) outflows. The opponent's draws are hidden, so cards leaving their library have no card reference. |
| 4 | **Opponent's Hand** | First unlabelled destination reached from the opponent's Library via a transfer that does carry a card reference (revealed on play). |
| 5 | **Player's Library** | Zone with high named outflows that feeds a **single** destination. `Library → Hand` is a 1-to-1 pipeline; a Hand zone, by contrast, fans out to Stack, Battlefield, and potentially others. |
| 6 | **All Hand zones** | Any remaining unlabelled zone that directly receives cards from a Library is marked Hand (catches both players after step 5). |
| 7 | **Graveyards** | Zones with a positive net accumulation that receive cards from the Battlefield or Stack (die/resolve destinations). |
| 8 | **Exile** | Residual low-traffic zones (≤ 2 named arrivals) not matched by prior steps. |

### Example (from a real match)

```
Zone 27 → Stack       (high throughput, net ≈ 0)
Zone 28 → Battlefield (net = +25, highest accumulation)
Zone 29 → Exile       (1 arrival, 1 departure)
Zone 31 → Hand        (opponent's hand, connected to zone 32)
Zone 32 → Library     (16 anonymous outflows — opponent draws)
Zone 33 → Graveyard   (receives cards from Battlefield/Stack)
Zone 35 → Hand        (player's hand, connected to zone 36)
Zone 36 → Library     (12 named outflows → single destination zone 35)
Zone 37 → Graveyard   (receives cards from Battlefield/Stack)
```

---

## Actor Attribution ("You" vs "Opponent")

After zones are labelled, the replay needs to know *who* performed each action.

**Key insight:** The player can see their own draws, so transfers from the player's Library to their Hand carry a named card reference. The opponent's Library sends anonymous (face-down) transfers. This distinguishes the two Library/Hand pairs:

- **Player's Hand** = the Hand zone whose paired Library sends *named* cards.
- **Opponent's Hand** = the Hand zone whose paired Library sends *anonymous* cards.

Actor rules applied per transfer:

| From zone | Attribution |
|-----------|-------------|
| Player's Hand | **You** |
| Opponent's Hand | **Opponent** |
| Battlefield / Stack | **—** (ownership not reliably determinable from zone data alone) |
| Other | **—** |

---

## Event Verbs

The `_zone_verb()` function maps `(from_role, to_role)` pairs to human-readable descriptions, or returns `None` to skip the event entirely.

| From → To | Verb | Notes |
|-----------|------|-------|
| Hand → Stack | `cast` | Spell placed on the stack |
| Hand → Battlefield | `entered the battlefield` | Land played directly |
| Stack → Battlefield | `entered the battlefield` | Permanent spell resolved |
| Stack → Graveyard | `resolved` | Instant/sorcery resolved |
| Stack → Exile | `was exiled` | Countered to exile, or adventure |
| Library → Hand | `drawn` | Card drawn from library |
| Library → Battlefield | `put onto the battlefield` | Cheat effect / special ability |
| Battlefield → Graveyard | `died` | Creature destroyed or lethal damage |
| Battlefield → Exile | `was exiled` | Exile removal |
| Battlefield → Hand | `bounced to hand` | Return-to-hand effect |
| Battlefield → Library | `shuffled into library` | Tuck effect |
| Anything else | *(skipped)* | Internal MTGA bookkeeping |

---

## Life Total Tracking

Life totals are stored in `LifeChange` records keyed by `game_state_id`. The view builds a dict mapping `game_state_id → {seat_id: life_total}` in a single pass, then maintains a running `current_life` dict that is updated each time a new `game_state_id` boundary is crossed while iterating over zone transfers. Each replay step snapshot includes the life totals current at that point.

Only the player's life changes are reliably captured (the parser tracks the player's seat). The opponent's life total defaults to 20 if no change has been recorded.

---

## Data Limitations

- **`turn_number = 0`** on some events: Zone transfers that occur during initialisation (e.g. opening-hand reveals) or in certain game-state snapshots may have `turn_number` stored as `0` rather than the actual turn. This is a parser data-quality issue, not a replay logic issue.
- **Unknown cards**: Cards with `grp_id` values not present in the local Scryfall cache show as "Unknown Card (grp_id)". Run `make download-cards` to populate the cache.
- **Opponent's cards**: Cards played from the opponent's hand only become known when they enter the battlefield. Prior to that they appear as anonymous transfers.

---

## Related Code

| Location | Purpose |
|----------|---------|
| `stats/views.py` — `_build_zone_labels()` | Infers zone roles from transfer statistics |
| `stats/views.py` — `_zone_verb()` | Maps zone-pair transitions to event verbs |
| `stats/views.py` — `match_replay()` | View: builds step list, serialises to JSON |
| `stats/templates/match_replay.html` | Template: JS step-through UI |
| `src/parser/log_parser.py` — `_process_game_state_message()` | Parser: extracts `AnnotationType_ZoneTransfer` from log |
| `stats/models.py` — `ZoneTransfer` | ORM model for zone transfer records |
