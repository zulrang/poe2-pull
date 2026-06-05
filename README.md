# poe2-collector

Polls [poe.ninja](https://poe.ninja) for PoE2 price data and writes timestamped JSONL
snapshots locally, then generates an HTML investment analysis report.

## Setup

```bash
pip install requests
```

## Quick start

```bash
# One-shot poll (verify connectivity, refresh data)
python collect.py --once

# Collect for 4 hours (8 snapshots at 30-min intervals), then exit
python collect.py --hours 4

# Run forever until Ctrl-C
python collect.py

# Different league or poll interval
python collect.py --league "Standard" --interval 900

# Generate investment report (reads data/, writes report.html)
python report.py

# Report options
python report.py --data-dir ./data --out my.html --min-vol 1.0
```

## Output

### Hourly snapshots — `data/<stem>.jsonl`

One JSON record per poll per category. Each record has the full poe.ninja API response:

| File | Category |
|---|---|
| `currency.jsonl` | Currency exchange rates |
| `fragments.jsonl` | Fragments |
| `essences.jsonl` | Essences |
| `runes.jsonl` | Runes |
| `soul_cores.jsonl` | Soul Cores |
| `idols.jsonl` | Idols |
| `omens.jsonl` | Omens |
| `abyssal_bones.jsonl` | Abyssal Bones |
| `expedition.jsonl` | Expedition currency |
| `liquid_emotions.jsonl` | Liquid Emotions |
| `catalysts.jsonl` | Catalysts |
| `verisium.jsonl` | Verisium |
| `skill_gems.jsonl` | Skill Gems |
| `unique_weapons.jsonl` | Unique Weapons |
| `unique_armours.jsonl` | Unique Armours |
| `unique_jewellery.jsonl` | Unique Jewellery |
| `divination_cards.jsonl` | Divination Cards |

```json
{
  "ts": "2026-06-04T14:30:00Z",
  "league": "Runes of Aldur",
  "category": "Currency",
  "lines": [ { "id": "divine", "primaryValue": 1.0, "volumePrimaryValue": 82011 }, ... ],
  "items": [ { "id": "divine", "name": "Divine Orb", "detailsId": "divine-orb" }, ... ],
  "core": { "primary": "divine", "rates": { "exalted": 79.6, "chaos": 23.02 } }
}
```

All `primaryValue` fields are divine-denominated. Convert to exalted via
`primaryValue * core.rates.exalted`.

### Detail records — `data/details/<stem>.jsonl`

One record per item, fetched once per item ID and updated when new IDs appear. Contains
embedded daily price history vs each base currency:

```json
{
  "ts": "2026-06-04T14:30:00Z",
  "league": "Runes of Aldur",
  "id": "architects-orb",
  "details": {
    "item": { "name": "Architect's Orb" },
    "pairs": [
      {
        "id": "exalted",
        "rate": 39.94,
        "volumePrimaryValue": 181.6,
        "history": [
          { "timestamp": "2026-06-04T00:00:00Z", "rate": 39.94, "volumePrimaryValue": 181.1 }
        ]
      }
    ]
  }
}
```

## Investment Report

`report.py` reads both data layers for the currency-type categories, runs linear regression
on price history, and generates a self-contained `report.html` with:

- Sortable table: trend %/day, volatility (CV), score, inline sparklines
- Score = `trend_pct_per_day / (CV + 0.01)` — rewards fast, consistent appreciation
- "Both Up" badge: items trending positive vs both Exalted and Divine
- Filter controls: "Both Up" checkbox, min avg-volume slider, min trend/day sliders

## Analysis

```bash
# Summary of all collected snapshot files
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
- Rate limit is ~12 requests per 5 minutes; the 1-second delay between category
  fetches keeps a full poll well within budget.
- 404s on unique weapon/armour detail fetches are expected — base-type variants
  from prior leagues stay cached locally but are no longer listed by poe.ninja.
