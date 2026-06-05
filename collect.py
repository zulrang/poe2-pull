#!/usr/bin/env python3
"""
poe2_collect.py — Polls poe.ninja for PoE2 price data and appends snapshots to JSONL files.

Usage:
    python collect.py                        # runs until Ctrl-C
    python collect.py --hours 4              # collect for 4 hours then exit
    python collect.py --interval 900         # poll every 15 min (default: 30 min)
    python collect.py --league "Standard"    # different league

Output directory (./data/):
    currency.jsonl       — currency exchange snapshots
    fragments.jsonl      — fragment snapshots
    essences.jsonl       — essence snapshots
    runes.jsonl          — rune snapshots
    soul_cores.jsonl     — soul core snapshots
    idols.jsonl          — idol snapshots
    omens.jsonl          — omen snapshots
    skill_gems.jsonl     — skill gem snapshots
    unique_weapons.jsonl — unique weapon snapshots
    unique_armours.jsonl — unique armour snapshots
    unique_jewellery.jsonl
    divination_cards.jsonl

    details/<stem>.jsonl — per-item detail records (refreshed every 12 hours)

Each snapshot line is a JSON object:
    {
      "ts": "2026-06-04T14:30:00Z",   # UTC ISO timestamp of the poll
      "league": "Return of the Ancients",
      "category": "Currency",
      "lines": [ ... ],               # raw lines array from poe.ninja
      "items": [ ... ]                # item metadata (currency endpoints only)
    }

Each details line is a JSON object:
    {
      "ts": "2026-06-04T14:30:00Z",   # UTC timestamp when details were fetched
      "league": "Return of the Ancients",
      "category": "Currency",
      "id": "divine",                 # item id used to query details
      "details": { ... }              # raw response from poe.ninja details API
    }
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_LEAGUE   = "Runes of Aldur"
DEFAULT_INTERVAL = 30 * 60   # 30 minutes (poe.ninja updates ~hourly)
DATA_DIR         = Path("data")
DETAILS_DIR      = DATA_DIR / "details"
DETAILS_TTL      = 12 * 3600  # seconds before a detail record is re-fetched

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# PoE2 uses the /poe2/api/economy/ base; some category types use
# currencyexchange/overview (exchange-based) and others use itemoverview.
# Discovered via network interception — subject to change, but stable as of 2025-2026.

POE2_CURRENCY_BASE = "https://poe.ninja/poe2/api/economy/exchange/current/overview"
POE2_ITEM_BASE     = "https://poe.ninja/poe2/api/economy/stash/current/item/overview"
POE2_DETAILS_BASE  = "https://poe.ninja/poe2/api/economy/exchange/current/details"

ENDPOINTS = [
    # (filename_stem, api_base, overviewName)
    ("currency",          POE2_CURRENCY_BASE, "Currency"),
    ("fragments",         POE2_CURRENCY_BASE, "Fragments"),
    ("essences",          POE2_CURRENCY_BASE, "Essences"),
    ("runes",             POE2_CURRENCY_BASE, "Runes"),
    ("soul_cores",        POE2_CURRENCY_BASE, "SoulCores"),
    ("idols",             POE2_CURRENCY_BASE, "Idols"),
    ("omens",             POE2_CURRENCY_BASE, "Ritual"),
    ("abyssal_bones",     POE2_CURRENCY_BASE, "Abyss"),
    ("expedition",        POE2_CURRENCY_BASE, "Expedition"),
    ("liquid_emotions",   POE2_CURRENCY_BASE, "Delirium"),
    ("catalysts",         POE2_CURRENCY_BASE, "Breach"),
    ("verisium",          POE2_CURRENCY_BASE, "Verisium"),
    # ("skill_gems",        POE2_ITEM_BASE, "LineageSupportGems"),
    # ("unique_weapons",    POE2_ITEM_BASE, "UniqueWeapons"),
    # ("unique_armours",    POE2_ITEM_BASE, "UniqueArmours"),
    # ("unique_jewellery",  POE2_ITEM_BASE, "UniqueJewels"),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch(base_url: str, league: str, overview_name: str) -> dict | None:
    """Fetch one endpoint; returns parsed JSON or None on error."""
    params = {"league": league, "type": overview_name}
    try:
        r = requests.get(base_url, params=params, headers=HEADERS, timeout=30)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        print(f"  [HTTP {e.response.status_code}] {overview_name}: {e}", file=sys.stderr)
    except requests.RequestException as e:
        print(f"  [NET] {overview_name}: {e}", file=sys.stderr)
    except json.JSONDecodeError as e:
        print(f"  [JSON] {overview_name}: {e}", file=sys.stderr)
    return None


def fetch_details(league: str, type_name: str, item_id: str) -> dict | None:
    """Fetch detail page for one item; returns parsed JSON or None on error."""
    params = {"league": league, "type": type_name, "id": item_id}
    try:
        r = requests.get(POE2_DETAILS_BASE, params=params, headers=HEADERS, timeout=30)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        print(f"  [HTTP {e.response.status_code}] details/{type_name}/{item_id}: {e}", file=sys.stderr)
    except requests.RequestException as e:
        print(f"  [NET] details/{type_name}/{item_id}: {e}", file=sys.stderr)
    except json.JSONDecodeError as e:
        print(f"  [JSON] details/{type_name}/{item_id}: {e}", file=sys.stderr)
    return None


def load_details_map(details_path: Path) -> dict:
    """Return {item_id: record} from a details JSONL file."""
    records: dict = {}
    if not details_path.exists():
        return records
    with open(details_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                item_id = rec.get("id")
                if item_id:
                    records[item_id] = rec
            except json.JSONDecodeError:
                pass
    return records


def is_stale(ts_str: str) -> bool:
    """Return True if ts_str is older than DETAILS_TTL seconds."""
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - ts).total_seconds() > DETAILS_TTL
    except (ValueError, AttributeError):
        return True


def line_item_id(line: dict, id_map: dict | None = None) -> str | None:
    """Extract the slug used for the details API from a lines entry.

    Preference order:
    1. ``detailsId`` on the line itself (item-based endpoints)
    2. ``id_map`` lookup keyed by the line's short ``id`` (exchange endpoints
       where ``items`` metadata carries the real slug)
    3. Raw ``id`` as a fallback
    """
    if "detailsId" in line:
        return line["detailsId"]
    short_id = str(line["id"]) if "id" in line else None
    if short_id and id_map:
        return id_map.get(short_id, short_id)
    return short_id


def collect_details(league: str, type_name: str, lines: list, details_path: Path,
                    items: list | None = None):
    """Fetch details for new items and refresh any records older than DETAILS_TTL."""
    if not lines:
        return
    # Build short-id → detailsId map from the items metadata (exchange endpoints)
    id_map: dict = {}
    if items:
        for item in items:
            if "id" in item and "detailsId" in item:
                id_map[str(item["id"])] = item["detailsId"]
    existing = load_details_map(details_path)
    to_fetch = [
        item_id for line in lines
        if (item_id := line_item_id(line, id_map)) and (
            item_id not in existing or is_stale(existing[item_id].get("ts", ""))
        )
    ]
    if not to_fetch:
        return
    details_path.parent.mkdir(parents=True, exist_ok=True)
    ts = utc_now()
    fetched = 0
    for item_id in to_fetch:
        data = fetch_details(league, type_name, item_id)
        if data is None:
            continue
        existing[item_id] = {
            "ts":       ts,
            "league":   league,
            "category": type_name,
            "id":       item_id,
            "details":  data,
        }
        fetched += 1
        time.sleep(1)   # be polite
    if fetched:
        with open(details_path, "w", encoding="utf-8") as f:
            for rec in existing.values():
                f.write(json.dumps(rec, separators=(",", ":")) + "\n")
        print(f"    + {fetched} detail(s) refreshed -> {details_path.name}")


def append_snapshot(path: Path, ts: str, league: str, category: str, data: dict):
    """Append one JSONL record."""
    record = {
        "ts":       ts,
        "league":   league,
        "category": category,
        "lines":    data.get("lines", []),
    }
    # currency endpoints also return items metadata
    if "items" in data:
        record["items"] = data["items"]
    if "core" in data:
        record["core"] = data["core"]

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")


def run_report(data_dir: Path) -> None:
    """Regenerate report.html by invoking report.py as a subprocess."""
    report_script = Path(__file__).with_name("report.py")
    try:
        subprocess.run(
            [sys.executable, str(report_script), "--data-dir", str(data_dir)],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"  [REPORT] report.py exited with code {e.returncode}", file=sys.stderr)
    except FileNotFoundError:
        print(f"  [REPORT] report.py not found at {report_script}", file=sys.stderr)


def poll_once(league: str, data_dir: Path):
    ts = utc_now()
    print(f"\n[{ts}] Polling {len(ENDPOINTS)} endpoints for '{league}'…")
    ok = 0
    for stem, base, name in ENDPOINTS:
        data = fetch(base, league, name)
        if data is None:
            print(f"  FAIL {name}")
            continue
        lines = data.get("lines", [])
        append_snapshot(data_dir / f"{stem}.jsonl", ts, league, name, data)
        print(f"  ok  {name:25s}  {len(lines):4d} items")
        ok += 1
        time.sleep(1)   # be polite: 1 s between requests
        collect_details(league, name, lines, data_dir / "details" / f"{stem}.jsonl",
                        items=data.get("items"))
    print(f"  Snapshot complete: {ok}/{len(ENDPOINTS)} endpoints OK")
    return ok


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Poll poe.ninja PoE2 prices")
    parser.add_argument("--league",   default=DEFAULT_LEAGUE, help="League name (case-sensitive)")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL,
                        help="Seconds between polls (default: 1800 = 30 min)")
    parser.add_argument("--hours",    type=float, default=None,
                        help="Stop after N hours (default: run forever)")
    parser.add_argument("--data-dir", default=str(DATA_DIR), help="Output directory")
    parser.add_argument("--once",     action="store_true", help="Poll once and exit")
    args = parser.parse_args()

    data_dir  = Path(args.data_dir)
    stop_at   = (datetime.now(timezone.utc) + timedelta(hours=args.hours)
                 if args.hours else None)
    iteration = 0

    print("poe2-collector starting")
    print(f"  League   : {args.league}")
    print(f"  Interval : {args.interval}s ({args.interval/60:.0f} min)")
    print(f"  Duration : {'%.1f hours' % args.hours if args.hours else 'until Ctrl-C'}")
    print(f"  Data dir : {data_dir.resolve()}")

    try:
        while True:
            iteration += 1
            ok = poll_once(args.league, data_dir)

            if args.once:
                break

            if ok > 0:
                run_report(data_dir)

            if stop_at and datetime.now(timezone.utc) >= stop_at:
                print(f"\nDuration elapsed — exiting after {iteration} poll(s).")
                break

            next_poll = datetime.now(timezone.utc) + timedelta(seconds=args.interval)
            print(f"  Next poll at {next_poll.strftime('%H:%M:%S')} UTC  (Ctrl-C to stop)")
            time.sleep(args.interval)

    except KeyboardInterrupt:
        print(f"\nInterrupted — {iteration} snapshot(s) written to {data_dir.resolve()}")


if __name__ == "__main__":
    main()
