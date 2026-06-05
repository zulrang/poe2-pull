# poe2-collector

Polls [poe.ninja](https://poe.ninja) for PoE2 price data every 30 minutes and writes
timestamped JSONL snapshots locally.

## Setup

```bash
pip install requests
```

## Quick start

```bash
# Collect for 4 hours (8 snapshots at 30-min intervals), then exit
python collect.py --hours 4

# Run forever until Ctrl-C
python collect.py

# One-shot probe (useful to verify connectivity)
python collect.py --once

# Different league or poll interval
python collect.py --league "Standard" --interval 1800
```

## Output

All data lands in `./data/` as JSONL files (one JSON object per line):

| File | Category |
|---|---|
| `currency.jsonl` | Currency exchange rates |
| `fragments.jsonl` | Fragments |
| `essences.jsonl` | Essences |
| `runes.jsonl` | Runes |
| `soul_cores.jsonl` | Soul Cores |
| `idols.jsonl` | Idols |
| `omens.jsonl` | Omens |
| `skill_gems.jsonl` | Skill Gems |
| `unique_weapons.jsonl` | Unique Weapons |
| `unique_armours.jsonl` | Unique Armours |
| `unique_jewellery.jsonl` | Unique Jewellery |
| `divination_cards.jsonl` | Divination Cards |

Each record:
```json
{
  "ts": "2026-06-04T14:30:00Z",
  "league": "Return of the Ancients",
  "category": "Currency",
  "core": { "version": "1.0", "timestamp": 1749047400, ... },
  "lines": [ { "id": "divine", "primaryValue": 1.0, "volumePrimaryValue": 1480 }, ... ],
  "items": [ { "id": "divine", "name": "Divine Orb", "icon": "...", "tradeId": "divine" }, ... ]
}
```

### Price interpretation (currency endpoints)

```python
# primaryValue >= 1  →  chaos per item  (e.g. Divine = 185.0 chaos)
# primaryValue <  1  →  items per chaos  (e.g. Scroll of Wisdom = 0.02 = 50 per chaos)
chaos_value = pv if pv >= 1 else 1 / pv
```

Item endpoints (`chaosValue` field) are already in chaos directly.

## Analysis

```bash
# Summary of all collected files
python analyze.py

# Show latest snapshot for a category
python analyze.py currency
python analyze.py essences

# Track a specific item over time
python analyze.py currency divine
python analyze.py unique_weapons "Atziri's Disfavour"
```

## API notes

- poe.ninja data refreshes approximately every hour; polling every 30 min gives
  ~2 samples per update window with minimal wasted requests.
- The PoE2 endpoints (`/poe2/api/economy/`) are undocumented but stable since 2025.
- Rate limit appears to be ~12 requests per 5 minutes; the 1-second delay between
  category fetches keeps a full poll well within that budget.
- Current league: **Return of the Ancients** (0.5.0, launched May 29 2026)
