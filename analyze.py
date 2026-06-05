#!/usr/bin/env python3
"""
analyze.py — Quick summary of collected poe.ninja snapshots.

Usage:
    python analyze.py                    # summarise all JSONL in ./data/
    python analyze.py currency           # inspect currency snapshots
    python analyze.py currency divine    # track a specific item over time
"""

import json
import sys
from pathlib import Path
from datetime import datetime, timezone

DATA_DIR = Path("data")


def load_jsonl(path: Path) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def summary():
    files = sorted(DATA_DIR.glob("*.jsonl"))
    if not files:
        print(f"No .jsonl files found in {DATA_DIR.resolve()}")
        return
    print(f"{'File':<30}  {'Snapshots':>10}  {'First':>20}  {'Last':>20}  {'Items/snap':>10}")
    print("-" * 98)
    for f in files:
        records = load_jsonl(f)
        if not records:
            continue
        first = records[0]["ts"]
        last  = records[-1]["ts"]
        avg_items = sum(len(r["lines"]) for r in records) / len(records)
        print(f"{f.name:<30}  {len(records):>10}  {first:>20}  {last:>20}  {avg_items:>10.0f}")


def inspect(stem: str, search: str | None = None):
    path = DATA_DIR / f"{stem}.jsonl"
    if not path.exists():
        print(f"File not found: {path}")
        return

    records = load_jsonl(path)
    print(f"{path}  —  {len(records)} snapshot(s)\n")

    if search:
        search_lc = search.lower()
        print(f"Tracking '{search}' over time:\n")
        print(f"  {'Timestamp':>20}  {'Value':>10}  {'Volume':>8}")
        print("  " + "-" * 42)
        for r in records:
            # currency endpoints: lines have an 'id' field; item endpoints have 'name'
            for line in r["lines"]:
                item_id   = line.get("id", "")
                item_name = _resolve_name(line, r.get("items", []))
                if search_lc in item_id.lower() or search_lc in item_name.lower():
                    val    = line.get("primaryValue") or line.get("chaosValue", "?")
                    vol    = line.get("volumePrimaryValue") or line.get("listingCount", "?")
                    print(f"  {r['ts']:>20}  {val!s:>10}  {vol!s:>8}  {item_name}")
    else:
        # Show latest snapshot items
        r = records[-1]
        print(f"Latest snapshot: {r['ts']}\n")
        name_map = {i["id"]: i["name"] for i in r.get("items", [])}
        print(f"  {'Name':<40}  {'Value':>10}  {'Volume':>8}")
        print("  " + "-" * 62)
        lines = sorted(r["lines"],
                       key=lambda x: x.get("primaryValue") or x.get("chaosValue", 0),
                       reverse=True)
        for line in lines:
            item_id   = line.get("id", "?")
            item_name = name_map.get(item_id) or line.get("name", item_id)
            val       = line.get("primaryValue") or line.get("chaosValue", "?")
            vol       = line.get("volumePrimaryValue") or line.get("listingCount", "?")
            print(f"  {item_name:<40}  {val!s:>10}  {vol!s:>8}")


def _resolve_name(line: dict, items: list) -> str:
    item_id = line.get("id", "")
    for item in items:
        if item.get("id") == item_id:
            return item.get("name", item_id)
    return line.get("name", item_id)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        summary()
    elif len(args) == 1:
        inspect(args[0])
    else:
        inspect(args[0], args[1])
