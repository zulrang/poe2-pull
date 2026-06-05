#!/usr/bin/env python3
"""
report.py — Generate an HTML investment analysis report for PoE2 currency items.

Usage:
    python report.py                    # reads data/details/, writes report.html
    python report.py --data-dir ./data  # override data dir
    python report.py --out my.html      # override output path
    python report.py --min-vol 1.0      # override minimum avg volume filter
"""

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

STEMS = [
    "currency", "fragments", "essences", "runes", "soul_cores",
    "idols", "omens", "abyssal_bones", "expedition", "liquid_emotions",
    "catalysts", "verisium",
]

BASE_CURRENCIES = ["exalted", "divine"]


def load_items(data_dir: Path) -> list[dict]:
    """Load latest record per item ID from all currency category JSONL files."""
    items: dict[str, dict] = {}
    for stem in STEMS:
        path = data_dir / "details" / f"{stem}.jsonl"
        if not path.exists():
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                item_id = record.get("id", "")
                if not item_id:
                    continue
                if item_id not in items or record.get("ts", "") > items[item_id].get("ts", ""):
                    items[item_id] = {**record, "stem": stem}
    return list(items.values())


def linreg_slope(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean_x = (n - 1) / 2
    mean_y = sum(values) / n
    num = sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values))
    den = sum((i - mean_x) ** 2 for i in range(n))
    return num / den if den else 0.0


def analyze_pair(history: list[dict], min_vol: float) -> dict | None:
    if len(history) < 3:
        return None

    sorted_h = sorted(history, key=lambda x: x.get("timestamp", ""))
    rates = [h["rate"] for h in sorted_h if "rate" in h]
    vols = [h.get("volumePrimaryValue", 0) for h in sorted_h]

    if len(rates) < 3:
        return None

    avg_vol = sum(vols) / len(vols)
    if avg_vol < min_vol:
        return None

    mean_rate = sum(rates) / len(rates)
    if mean_rate == 0:
        return None

    slope = linreg_slope(rates)
    trend_pct = slope / mean_rate * 100

    variance = sum((r - mean_rate) ** 2 for r in rates) / len(rates)
    cv = math.sqrt(variance) / mean_rate

    return {
        "trend_pct": trend_pct,
        "cv": cv,
        "avg_vol": avg_vol,
        "score": trend_pct / (cv + 0.01),
        "current_rate": rates[-1],
        "rates": rates,
        "timestamps": [h.get("timestamp", "") for h in sorted_h],
    }


def make_sparkline(rates: list[float], width: int = 80, height: int = 30) -> str:
    if len(rates) < 2:
        return ""
    mn, mx = min(rates), max(rates)
    r_range = mx - mn

    def sx(i: int) -> float:
        return i / (len(rates) - 1) * width

    def sy(r: float) -> float:
        if r_range == 0:
            return height / 2
        return height - (r - mn) / r_range * height

    points = " ".join(f"{sx(i):.1f},{sy(r):.1f}" for i, r in enumerate(rates))
    color = "#4ade80" if rates[-1] >= rates[0] else "#f87171"
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="1.5"/>'
        f'</svg>'
    )


def build_rows(items: list[dict], min_vol: float) -> tuple[list[dict], str, str, str]:
    rows = []
    league = ""
    all_timestamps: list[str] = []

    for record in items:
        if not league and record.get("league"):
            league = record["league"]

        details = record.get("details", {})
        item_info = details.get("item", {})
        pair_map = {p["id"]: p for p in details.get("pairs", [])}

        analyses: dict[str, dict] = {}
        for base in BASE_CURRENCIES:
            pair = pair_map.get(base)
            if not pair:
                continue
            result = analyze_pair(pair.get("history", []), min_vol)
            if result:
                analyses[base] = result
                all_timestamps.extend(result["timestamps"])

        if "exalted" not in analyses:
            continue

        ex = analyses["exalted"]
        div = analyses.get("divine")

        both_up = ex["trend_pct"] > 0 and div is not None and div["trend_pct"] > 0

        rows.append({
            "name": item_info.get("name") or record.get("id", ""),
            "category": record.get("stem", ""),
            "rate_ex": ex["current_rate"],
            "rate_div": div["current_rate"] if div else None,
            "trend_ex": ex["trend_pct"],
            "trend_div": div["trend_pct"] if div else None,
            "cv_ex": ex["cv"],
            "avg_vol_ex": ex["avg_vol"],
            "score_ex": ex["score"],
            "sparkline": make_sparkline(ex["rates"]),
            "both_up": both_up,
        })

    all_timestamps = [t for t in all_timestamps if t]
    date_min = min(all_timestamps)[:10] if all_timestamps else ""
    date_max = max(all_timestamps)[:10] if all_timestamps else ""
    return rows, league, date_min, date_max


def generate_html(rows: list[dict], league: str, date_min: str, date_max: str, generated_at: str) -> str:
    total = len(rows)
    both_up_count = sum(1 for r in rows if r["both_up"])

    rows_json = json.dumps(
        [
            {
                "name": r["name"],
                "category": r["category"],
                "rate_ex": r["rate_ex"],
                "rate_div": r["rate_div"],
                "trend_ex": r["trend_ex"],
                "trend_div": r["trend_div"],
                "cv_ex": r["cv_ex"],
                "avg_vol_ex": r["avg_vol_ex"],
                "score_ex": r["score_ex"],
                "both_up": r["both_up"],
                "sparkline": r["sparkline"],
            }
            for r in rows
        ],
        ensure_ascii=False,
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PoE2 Investment Report — {league}</title>
<style>
:root {{
  --bg:      #0f0f1e;
  --bg2:     #1a1a2e;
  --bg3:     #13132a;
  --card:    #1c1c38;
  --border:  #2a2a50;
  --hover:   #22224a;
  --accent:  #c8aa6e;
  --accent2: #8a6e3a;
  --text:    #e0d8c8;
  --dim:     #7a7068;
  --green:   #4ade80;
  --red:     #f87171;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  background: var(--bg);
  color: var(--text);
  font-family: 'Segoe UI', system-ui, sans-serif;
  font-size: 13px;
  line-height: 1.45;
  min-width: 900px;
}}
/* ---- header ---- */
header {{
  background: linear-gradient(160deg, var(--bg2) 0%, var(--bg) 100%);
  border-bottom: 2px solid var(--accent2);
  padding: 20px 32px 16px;
}}
header h1 {{
  color: var(--accent);
  font-size: 22px;
  font-weight: 700;
  letter-spacing: .06em;
  text-transform: uppercase;
  margin-bottom: 4px;
}}
.meta {{ color: var(--dim); font-size: 12px; }}
.meta strong {{ color: var(--text); }}
/* ---- summary cards ---- */
.cards {{
  display: flex;
  gap: 14px;
  padding: 18px 32px;
  flex-wrap: wrap;
  background: var(--bg2);
  border-bottom: 1px solid var(--border);
}}
.card {{
  background: var(--card);
  border: 1px solid var(--border);
  border-top: 2px solid var(--accent2);
  padding: 12px 22px;
  border-radius: 4px;
  min-width: 150px;
}}
.card .lbl {{
  color: var(--dim);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: .1em;
  margin-bottom: 5px;
}}
.card .val {{
  color: var(--accent);
  font-size: 30px;
  font-weight: 700;
  line-height: 1;
}}
/* ---- controls ---- */
.controls {{
  display: flex;
  gap: 24px;
  align-items: center;
  padding: 10px 32px;
  background: var(--bg3);
  border-bottom: 1px solid var(--border);
  flex-wrap: wrap;
}}
.controls label {{
  display: flex;
  align-items: center;
  gap: 7px;
  cursor: pointer;
  color: var(--dim);
  font-size: 12px;
  user-select: none;
}}
.controls input[type=checkbox] {{
  accent-color: var(--accent);
  width: 13px;
  height: 13px;
  cursor: pointer;
}}
.slider-group {{
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--dim);
  font-size: 12px;
}}
.slider-group input[type=range] {{
  accent-color: var(--accent);
  width: 130px;
  cursor: pointer;
}}
.slider-val {{ color: var(--accent); min-width: 38px; display: inline-block; }}
/* ---- table ---- */
.table-wrap {{
  padding: 0 32px 40px;
  overflow-x: auto;
}}
table {{
  width: 100%;
  border-collapse: collapse;
  table-layout: auto;
}}
thead tr {{
  background: var(--bg3);
  position: sticky;
  top: 0;
  z-index: 10;
}}
th {{
  padding: 9px 12px;
  text-align: left;
  color: var(--dim);
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .07em;
  border-bottom: 2px solid var(--accent2);
  white-space: nowrap;
  cursor: pointer;
  user-select: none;
  transition: color .12s;
}}
th:hover {{ color: var(--accent); }}
th.sort-active {{ color: var(--accent); }}
th.sort-active::after {{ content: ' ↓'; font-size: 11px; }}
th.sort-asc::after  {{ content: ' ↑'; font-size: 11px; }}
td {{
  padding: 6px 12px;
  border-bottom: 1px solid var(--border);
  vertical-align: middle;
  white-space: nowrap;
}}
tr:hover td {{ background: var(--hover); }}
.c-name  {{ font-weight: 500; color: var(--accent); max-width: 220px; overflow: hidden; text-overflow: ellipsis; }}
.c-cat   {{ color: var(--dim); font-size: 11px; }}
.c-rate  {{ font-variant-numeric: tabular-nums; }}
.c-score {{ font-weight: 600; }}
.c-vol   {{ color: var(--dim); }}
.pos {{ color: var(--green); }}
.neg {{ color: var(--red); }}
.neu {{ color: var(--dim); }}
.badge {{
  display: inline-block;
  background: #1a3a1a;
  color: var(--green);
  border: 1px solid #2a5a2a;
  font-size: 10px;
  padding: 1px 7px;
  border-radius: 10px;
  font-weight: 600;
  letter-spacing: .05em;
  text-transform: uppercase;
}}
</style>
</head>
<body>

<header>
  <h1>PoE2 Currency Investment Report</h1>
  <div class="meta">
    League: <strong>{league}</strong>
    &nbsp;&middot;&nbsp;Data range: <strong>{date_min}</strong> &rarr; <strong>{date_max}</strong>
    &nbsp;&middot;&nbsp;Generated: {generated_at}
  </div>
</header>

<div class="cards">
  <div class="card">
    <div class="lbl">Items Analyzed</div>
    <div class="val" id="cnt-total">{total}</div>
  </div>
  <div class="card">
    <div class="lbl">Both Trending Up</div>
    <div class="val" id="cnt-both">{both_up_count}</div>
  </div>
</div>

<div class="controls">
  <label>
    <input type="checkbox" id="chk-both">
    Show only &ldquo;Both Up&rdquo; items
  </label>
  <div class="slider-group">
    <span>Min avg vol (Ex):</span>
    <input type="range" id="vol-slider" min="0" max="100" step="0.5" value="0">
    <span class="slider-val" id="vol-val">0.0</span>
  </div>
  <div class="slider-group">
    <span>Min trend/day (Ex):</span>
    <input type="range" id="trend-ex-slider" min="-20" max="20" step="0.1" value="-20">
    <span class="slider-val" id="trend-ex-val">-20.0%</span>
  </div>
  <div class="slider-group">
    <span>Min trend/day (Div):</span>
    <input type="range" id="trend-div-slider" min="-20" max="20" step="0.1" value="-20">
    <span class="slider-val" id="trend-div-val">-20.0%</span>
  </div>
</div>

<div class="table-wrap">
<table>
<thead>
<tr>
  <th data-col="name">Item</th>
  <th data-col="category">Category</th>
  <th data-col="rate_ex">Rate (Ex)</th>
  <th data-col="rate_div">Rate (Div)</th>
  <th data-col="trend_ex">Trend/day (Ex)</th>
  <th data-col="trend_div">Trend/day (Div)</th>
  <th data-col="cv_ex" data-default-asc="true">Volatility CV</th>
  <th data-col="avg_vol_ex">Avg Vol (Ex)</th>
  <th data-col="score_ex">Score</th>
  <th>Sparkline (Ex)</th>
  <th></th>
</tr>
</thead>
<tbody id="tbody"></tbody>
</table>
</div>

<script>
const DATA = {rows_json};

let sortCol = 'score_ex';
let sortAsc = false;
let onlyBothUp = false;
let minVol = 0;
let minTrendEx = -20;
let minTrendDiv = -20;

function fmt(v, digits) {{
  if (v == null) return '—';
  if (v === 0) return '0';
  const abs = Math.abs(v);
  if (abs >= 1000) return v.toFixed(0);
  if (abs >= 100)  return v.toFixed(1);
  if (abs >= 10)   return v.toFixed(2);
  if (abs >= 1)    return v.toFixed(3);
  return v.toPrecision(3);
}}

function fmtTrend(v) {{
  if (v == null) return '—';
  return (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
}}

function trendCls(v) {{
  if (v == null) return 'neu';
  return v > 0 ? 'pos' : 'neg';
}}

function render() {{
  let rows = DATA.filter(r => {{
    if (onlyBothUp && !r.both_up) return false;
    if (r.avg_vol_ex < minVol) return false;
    if (r.trend_ex == null || r.trend_ex < minTrendEx) return false;
    if (minTrendDiv > -20 && (r.trend_div == null || r.trend_div < minTrendDiv)) return false;
    return true;
  }});

  rows.sort((a, b) => {{
    let av = a[sortCol], bv = b[sortCol];
    if (av == null) av = sortAsc ? Infinity : -Infinity;
    if (bv == null) bv = sortAsc ? Infinity : -Infinity;
    if (typeof av === 'string') {{ av = av.toLowerCase(); bv = (bv || '').toLowerCase(); }}
    if (av < bv) return sortAsc ? -1 : 1;
    if (av > bv) return sortAsc ? 1 : -1;
    return 0;
  }});

  document.getElementById('tbody').innerHTML = rows.map(r => `
<tr>
  <td class="c-name" title="${{r.name}}">${{r.name}}</td>
  <td class="c-cat">${{r.category}}</td>
  <td class="c-rate">${{fmt(r.rate_ex)}}</td>
  <td class="c-rate">${{fmt(r.rate_div)}}</td>
  <td class="${{trendCls(r.trend_ex)}}">${{fmtTrend(r.trend_ex)}}</td>
  <td class="${{trendCls(r.trend_div)}}">${{fmtTrend(r.trend_div)}}</td>
  <td>${{r.cv_ex.toFixed(3)}}</td>
  <td class="c-vol">${{r.avg_vol_ex.toFixed(2)}}</td>
  <td class="c-score">${{r.score_ex.toFixed(2)}}</td>
  <td>${{r.sparkline}}</td>
  <td>${{r.both_up ? '<span class="badge">Both Up</span>' : ''}}</td>
</tr>`).join('');

  document.getElementById('cnt-total').textContent = rows.length;
  document.getElementById('cnt-both').textContent = rows.filter(r => r.both_up).length;
}}

// Column sort
document.querySelectorAll('th[data-col]').forEach(th => {{
  th.addEventListener('click', () => {{
    const col = th.dataset.col;
    const defaultAsc = th.dataset.defaultAsc === 'true';
    if (sortCol === col) {{
      sortAsc = !sortAsc;
    }} else {{
      sortCol = col;
      sortAsc = defaultAsc;
    }}
    document.querySelectorAll('th').forEach(h => h.classList.remove('sort-active', 'sort-asc'));
    th.classList.add('sort-active');
    if (sortAsc) th.classList.add('sort-asc');
    render();
  }});
}});

// Mark initial sort column
const initialTh = document.querySelector('th[data-col="score_ex"]');
if (initialTh) initialTh.classList.add('sort-active');

// Checkbox
document.getElementById('chk-both').addEventListener('change', e => {{
  onlyBothUp = e.target.checked;
  render();
}});

// Volume slider
const slider = document.getElementById('vol-slider');
const volVal = document.getElementById('vol-val');
slider.addEventListener('input', () => {{
  minVol = parseFloat(slider.value);
  volVal.textContent = minVol.toFixed(1);
  render();
}});

// Trend sliders
const trendExSlider = document.getElementById('trend-ex-slider');
const trendExVal = document.getElementById('trend-ex-val');
trendExSlider.addEventListener('input', () => {{
  minTrendEx = parseFloat(trendExSlider.value);
  trendExVal.textContent = (minTrendEx >= 0 ? '+' : '') + minTrendEx.toFixed(1) + '%';
  render();
}});

const trendDivSlider = document.getElementById('trend-div-slider');
const trendDivVal = document.getElementById('trend-div-val');
trendDivSlider.addEventListener('input', () => {{
  minTrendDiv = parseFloat(trendDivSlider.value);
  trendDivVal.textContent = (minTrendDiv >= 0 ? '+' : '') + minTrendDiv.toFixed(1) + '%';
  render();
}});

render();
</script>
</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate PoE2 currency investment HTML report")
    parser.add_argument("--data-dir", default="data", help="Path to data directory (default: data)")
    parser.add_argument("--out", default="report.html", help="Output HTML file (default: report.html)")
    parser.add_argument("--min-vol", type=float, default=0.5, help="Minimum avg volume filter (default: 0.5)")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    out_path = Path(args.out)

    print(f"Loading items from {data_dir / 'details'} ...")
    items = load_items(data_dir)
    print(f"  Loaded {len(items)} items across {len(STEMS)} categories")

    rows, league, date_min, date_max = build_rows(items, args.min_vol)
    both_up = sum(1 for r in rows if r["both_up"])
    print(f"  Passed filters (min-vol={args.min_vol}): {len(rows)} items, {both_up} trending up in both Ex + Div")

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = generate_html(rows, league, date_min, date_max, generated_at)

    out_path.write_text(html, encoding="utf-8")
    print(f"  Report written to {out_path.resolve()}")


if __name__ == "__main__":
    main()
