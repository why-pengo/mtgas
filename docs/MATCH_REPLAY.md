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

## Token Events

### Token Creation

Token creation (`AnnotationType_TokenCreated`) is **not** a `ZoneTransfer` annotation in the raw log — it has no `zone_src` / `zone_dest` details. To make token creation visible in the replay, the parser emits a **synthetic zone_transfer** with `category="TokenCreated"` for each created token (see `LOG_PARSING.md` → *Token Creation*).

The replay and timeline views detect this category before applying the normal zone-label/verb pipeline:

```python
if zt.category == "TokenCreated":
    verb = "token created"
    # skip zone label lookup entirely
```

This ensures token creation always appears as a distinct step even when the token's zone has not been mapped by `_build_zone_labels()`.

### Visual Badge

Any step where `card.is_token` is `True` renders a gold **TOKEN** badge next to the card name in both the match timeline and the step-through replay UI. Token creation steps use the format:

```
[TOKEN] 1/1 Red Goblin Creature Token — token created
```

Regular token moves (e.g. a token dying) use the standard format with the badge prepended:

```
[TOKEN] 1/1 Red Goblin Creature Token — died
```

---

## Event Verbs

The `_zone_verb()` function maps `(from_role, to_role)` pairs to human-readable descriptions, or returns `None` to skip the event entirely. Token creation bypasses this function (see *Token Events* above).

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

Life totals are stored in `LifeChange` records keyed by `game_state_id`. Because life-change events and zone-transfer events use different `game_state_id` values and never share the same ID, the view builds a sorted list of life events and performs a linear scan: as each zone transfer is processed, all life changes with a `game_state_id` ≤ the transfer's are applied to a running `current_life` dict. Each replay step snapshot includes the life totals current at that point.

Both the player's and opponent's life changes are captured by the parser and stored as separate `LifeChange` rows (distinguished by `seat_id`).

---

## Data Limitations

- **`turn_number = 0`** on some events: Resolved by the parser now tracking the last-seen turn number and applying it to subsequent messages that lack `turnInfo`. Opening-hand reveals and similar pre-game transfers may still show turn 0 as expected.
- **Unknown cards**: Cards with `grp_id` values not present in the local Scryfall cache show as `"Unknown Card (N)"`. Run `make download-cards` to populate the cache. Note that tokens and other non-card game objects are **not** looked up in Scryfall — they are stored with generated names (see *Import Service: Card Classification* above).
- **Omen back-face cards**: Tarkir Dragonstorm Omen sorceries/instants (e.g. *Roost Seek*) have Arena grpIds not directly in Scryfall. The import service resolves their name from the paired front-face card (`grpId - 1`) and stores `source_grp_id` pointing to that front face. They display in the replay with their resolved name and are **not** flagged as tokens — zone transfer events (cast, resolve, etc.) show normally.
- **Opponent's cards**: Cards played from the opponent's hand only become known when they enter the battlefield. Prior to that they appear as anonymous transfers.

---

## Related Code

| Location | Purpose |
|----------|---------|
| `stats/views.py` — `_build_zone_labels()` | Infers zone roles from transfer statistics |
| `stats/views.py` — `_zone_verb()` | Maps zone-pair transitions to event verbs |
| `stats/views.py` — `match_replay()` | View: builds step list, serialises to JSON; handles `TokenCreated` category |
| `stats/views.py` — `match_detail()` | Timeline view; handles `TokenCreated` category |
| `stats/templates/match_replay.html` | Template: JS step-through UI with TOKEN badge |
| `src/parser/log_parser.py` — `_process_game_state_message()` | Parser: extracts `AnnotationType_ZoneTransfer`; emits synthetic entries for `AnnotationType_TokenCreated` |
| `stats/models.py` — `ZoneTransfer` | ORM model for zone transfer records (`category` field carries `"TokenCreated"` for synthetic entries) |
| `stats/models.py` — `Card` | `is_token`, `object_type`, `source_grp_id` fields identify token/non-card game objects |
| `src/services/import_service.py` — `_collect_card_ids()` | Splits game object IDs into real cards vs. special objects; `GameObjectType_Omen` override discards prior Card classification |
| `src/services/import_service.py` — `_generate_token_name()` | Builds human-readable token names from game-state data |
| `src/services/import_service.py` — `_ensure_cards()` Omen path | Resolves Omen back-face name via `grpId - 1` front-face lookup |
