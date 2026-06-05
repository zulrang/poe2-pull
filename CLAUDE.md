# PoE2Pull

Collect: `python collect.py --once`
Collect continuously: `python collect.py --hours 4`
Report: `python report.py`
Deps: `pip install requests` (only external dependency)

## Non-Obvious Rules

**Windows / cp1252**: Never use non-ASCII characters in `print()` calls — the terminal codec
is cp1252 and will crash on arrows, checkmarks, etc. Use plain ASCII (`->`, `ok`, `FAIL`).

**Two data layers**: `data/<stem>.jsonl` holds hourly snapshots (many records per item, each a
full poe.ninja API response). `data/details/<stem>.jsonl` holds per-item detail records with
embedded daily `history` arrays (one record per item, deduplicated by `ts` — keep the latest).
These are different formats and serve different purposes. Do not conflate them.

**`primaryValue` is always divine-denominated**: Every stem's hourly snapshot has
`core.primary = "divine"` and `core.rates.exalted`. Convert to exalted via
`primaryValue * core.rates.exalted`. The `maxVolumeRate` field is `1 / rate_exalted` when
`maxVolumeCurrency == "exalted"`.

**Short IDs vs detailsIds**: Hourly currency snapshots use short `id` values (`alch`, `gcp`,
`annul`). The `items[]` array in each snapshot maps these to `detailsId` (e.g. `orb-of-alchemy`).
Always resolve through this map before joining to details records.

**404s from collect.py are expected**: Unique weapon/armour details return 404 for base-type
variants no longer listed in the current league. This is normal; the collector skips them.

**report.py `STEMS` list is the source of truth** for which categories the report covers.
Adding a new category requires updating `STEMS` in report.py AND ensuring the corresponding
`data/details/<stem>.jsonl` file exists.
