#!/usr/bin/env python3
"""
report.py — Generate an HTML investment analysis report for PoE2 currency items.

Usage:
    python report.py                              # reads data/details/, writes report.html
    python report.py --data-dir ./data            # override data dir
    python report.py --out my.html                # override output path
    python report.py --min-vol 1.0                # override minimum avg volume filter
    python report.py --league-start 2026-05-23    # enable league day counter in header
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


def load_hourly(data_dir: Path) -> dict[str, dict[str, list[dict]]]:
    """
    Read all hourly snapshots from data/<stem>.jsonl.
    Returns {detailsId: {"exalted": [points], "divine": [points]}}
    where each point is {timestamp, rate, volumePrimaryValue}.
    primaryValue in each snapshot is divine-denominated; multiply by core.rates.exalted for ex rate.
    """
    result: dict[str, dict[str, list[dict]]] = {}
    for stem in STEMS:
        path = data_dir / f"{stem}.jsonl"
        if not path.exists():
            continue
        with open(path, encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                snap = json.loads(raw)
                ts = snap.get("ts", "")
                ex_rate = snap.get("core", {}).get("rates", {}).get("exalted")
                if not ex_rate:
                    continue
                id_map = {
                    item["id"]: item.get("detailsId") or item["id"]
                    for item in snap.get("items", [])
                    if item.get("id")
                }
                for entry in snap.get("lines", []):
                    pv = entry.get("primaryValue")
                    if not pv or pv <= 0:
                        continue
                    details_id = id_map.get(entry.get("id", ""), entry.get("id", ""))
                    vol = entry.get("volumePrimaryValue", 0)
                    bucket = result.setdefault(details_id, {"exalted": [], "divine": []})
                    bucket["exalted"].append({"timestamp": ts, "rate": pv * ex_rate, "volumePrimaryValue": vol})
                    bucket["divine"].append({"timestamp": ts, "rate": pv, "volumePrimaryValue": vol})
    return result


def merge_series(history: list[dict], extra: list[dict] | None = None) -> list[dict]:
    """Merge, sort, and deduplicate time-series points by timestamp."""
    all_pts = list(history) + (extra or [])
    seen: dict[str, dict] = {}
    for pt in all_pts:
        ts = pt.get("timestamp", "")
        if ts:
            seen[ts] = pt
    return sorted(seen.values(), key=lambda x: x.get("timestamp", ""))


def ts_to_epoch(ts_str: str) -> int:
    try:
        return int(datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp())
    except Exception:
        return 0


def series_to_compact(merged: list[dict]) -> list[dict]:
    """Convert merged series to compact {t, r, v} format, limited to last 7 days."""
    cutoff = datetime.now(timezone.utc).timestamp() - 7 * 86400
    result = []
    for p in merged:
        ts = p.get("timestamp", "")
        rate = p.get("rate")
        if not ts or not rate:
            continue
        epoch = ts_to_epoch(ts)
        if epoch < cutoff:
            continue
        result.append({"t": epoch, "r": round(rate, 6), "v": round(p.get("volumePrimaryValue", 0), 4)})
    return result


def linreg_slope(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    den = sum((x - mean_x) ** 2 for x in xs)
    return num / den if den else 0.0


def analyze_pair(history: list[dict], min_vol: float, extra: list[dict] | None = None) -> dict | None:
    sorted_h = merge_series(history, extra)
    if len(sorted_h) < 3:
        return None

    rates = [h["rate"] for h in sorted_h if "rate" in h]
    vols  = [h.get("volumePrimaryValue", 0) for h in sorted_h]

    if len(rates) < 3:
        return None

    avg_vol = sum(vols) / len(vols)
    if avg_vol < min_vol:
        return None

    mean_rate = sum(rates) / len(rates)
    if mean_rate == 0:
        return None

    timestamps = [h.get("timestamp", "") for h in sorted_h if "rate" in h]
    t0 = datetime.fromisoformat(timestamps[0].replace("Z", "+00:00"))

    def to_days(ts_str: str) -> float:
        try:
            return (datetime.fromisoformat(ts_str.replace("Z", "+00:00")) - t0).total_seconds() / 86400
        except Exception:
            return 0.0

    xs = [to_days(ts) for ts in timestamps]
    slope = linreg_slope(xs, rates)
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
        "timestamps": timestamps,
    }


def build_rows(items: list[dict], min_vol: float, data_dir: Path) -> tuple[list[dict], str, str, str]:
    hourly = load_hourly(data_dir)
    rows = []
    league = ""
    all_timestamps: list[str] = []

    for record in items:
        if not league and record.get("league"):
            league = record["league"]

        details = record.get("details", {})
        item_info = details.get("item", {})
        pair_map = {p["id"]: p for p in details.get("pairs", [])}
        item_id = record.get("id", "")
        hourly_item = hourly.get(item_id, {})

        analyses: dict[str, dict] = {}
        for base in BASE_CURRENCIES:
            pair = pair_map.get(base)
            if not pair:
                continue
            result = analyze_pair(pair.get("history", []), min_vol, extra=hourly_item.get(base))
            if result:
                analyses[base] = result
                all_timestamps.extend(result["timestamps"])

        if "exalted" not in analyses:
            continue

        ex_pair = pair_map.get("exalted")
        div_pair = pair_map.get("divine")
        series_ex = series_to_compact(merge_series(
            ex_pair.get("history", []) if ex_pair else [],
            hourly_item.get("exalted"),
        ))
        series_div = series_to_compact(merge_series(
            div_pair.get("history", []) if div_pair else [],
            hourly_item.get("divine"),
        ))

        rows.append({
            "name": item_info.get("name") or record.get("id", ""),
            "category": record.get("stem", ""),
            "series_ex": series_ex,
            "series_div": series_div,
        })

    all_timestamps = [t for t in all_timestamps if t]
    date_min = min(all_timestamps)[:10] if all_timestamps else ""
    date_max = max(all_timestamps)[:10] if all_timestamps else ""
    return rows, league, date_min, date_max


def generate_html(
    rows: list[dict],
    league: str,
    date_min: str,
    date_max: str,
    generated_at: str,
    league_start: str = "",
) -> str:
    rows_json = json.dumps(
        [{"name": r["name"], "category": r["category"],
          "series_ex": r["series_ex"], "series_div": r["series_div"]}
         for r in rows],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    league_start_json = f'"{league_start}"' if league_start else "null"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PoE2 Investment Report - {league}</title>
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
  --orange:  #fb923c;
  --blue:    #60a5fa;
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
.league-day {{
  display: inline-block;
  background: var(--card);
  border: 1px solid var(--border);
  color: var(--accent);
  font-size: 11px;
  font-weight: 600;
  padding: 2px 9px;
  border-radius: 10px;
  margin-left: 8px;
  letter-spacing: .04em;
}}
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
  width: 120px;
  cursor: pointer;
}}
.slider-val {{ color: var(--accent); min-width: 42px; display: inline-block; }}
/* ---- window toggle ---- */
.preset-btn {{
  background: transparent;
  border: 1px solid var(--accent2);
  color: var(--accent);
  padding: 4px 14px;
  font-size: 12px;
  font-family: inherit;
  font-weight: 600;
  letter-spacing: .04em;
  cursor: pointer;
  border-radius: 3px;
  transition: background .1s;
  user-select: none;
}}
.preset-btn:hover {{ background: var(--accent2); }}
.window-group {{
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--dim);
  font-size: 12px;
}}
.window-btns {{
  display: flex;
  border: 1px solid var(--border);
  border-radius: 4px;
  overflow: hidden;
}}
.window-btn {{
  background: transparent;
  border: none;
  border-right: 1px solid var(--border);
  color: var(--dim);
  padding: 4px 13px;
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  transition: background .1s, color .1s;
  user-select: none;
}}
.window-btn:last-child {{ border-right: none; }}
.window-btn:hover {{ background: var(--hover); color: var(--text); }}
.window-btn.active {{ background: var(--accent2); color: var(--accent); font-weight: 600; }}
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
th.sort-active::after {{ content: ' \2193'; font-size: 11px; }}
th.sort-asc::after  {{ content: ' \2191'; font-size: 11px; }}
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
.pos  {{ color: var(--green); }}
.neg  {{ color: var(--red); }}
.neu  {{ color: var(--dim); }}
.warn {{ color: var(--orange); }}
.regime-m {{ color: var(--accent); font-weight: 600; }}
.regime-r {{ color: var(--blue); font-weight: 600; }}
.regime-n {{ color: var(--dim); }}
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
    <span id="league-day-badge"></span>
    &nbsp;&middot;&nbsp;Data range: <strong>{date_min}</strong> &rarr; <strong>{date_max}</strong>
    &nbsp;&middot;&nbsp;Generated: {generated_at}
  </div>
</header>

<div class="cards">
  <div class="card">
    <div class="lbl">Items Analyzed</div>
    <div class="val" id="cnt-total">0</div>
  </div>
  <div class="card">
    <div class="lbl">Both Trending Up</div>
    <div class="val" id="cnt-both">0</div>
  </div>
  <div class="card">
    <div class="lbl">Momentum Plays</div>
    <div class="val" id="cnt-momentum">0</div>
  </div>
</div>

<div class="controls">
  <div class="window-group">
    <span>Preset:</span>
    <button class="preset-btn" id="preset-chase">Chase</button>
    <button class="preset-btn" id="preset-fade">Fade</button>
  </div>
  <div class="window-group">
    <span>Window:</span>
    <div class="window-btns">
      <button class="window-btn active" data-hours="168">7d</button>
      <button class="window-btn" data-hours="72">3d</button>
      <button class="window-btn" data-hours="24">24h</button>
    </div>
  </div>
  <label>
    <input type="checkbox" id="chk-both" checked>
    Only &ldquo;Both Up&rdquo;
  </label>
  <div class="window-group">
    <span>Regime:</span>
    <div class="window-btns">
      <button class="window-btn active" data-regime="M">M</button>
      <button class="window-btn active" data-regime="R">R</button>
      <button class="window-btn active" data-regime="~">~</button>
    </div>
  </div>
  <div class="slider-group">
    <span>Min vol (Ex):</span>
    <input type="range" id="vol-slider" min="0" max="100" step="0.5" value="0">
    <span class="slider-val" id="vol-val">0.0</span>
  </div>
  <div class="slider-group">
    <span>Min Sharpe:</span>
    <input type="range" id="sharpe-slider" min="-3" max="3" step="0.1" value="0">
    <span class="slider-val" id="sharpe-val">0.0</span>
  </div>
  <div class="slider-group">
    <span>Min trend (Ex):</span>
    <input type="range" id="trend-ex-slider" min="-20" max="20" step="0.1" value="-20">
    <span class="slider-val" id="trend-ex-val">-20.0%</span>
  </div>
  <div class="slider-group">
    <span>Min trend (Div):</span>
    <input type="range" id="trend-div-slider" min="-20" max="20" step="0.1" value="0">
    <span class="slider-val" id="trend-div-val">+0.0%</span>
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
  <th data-col="trend_ex" title="Log-return trend per day, recency-weighted">Trend/day (Ex)</th>
  <th data-col="trend_div" title="Divine-denominated trend = excess return vs divine inflation">Trend/day (Div) *</th>
  <th data-col="momentum" title="Recent 25% of window avg vs older 75% avg">Momentum</th>
  <th data-col="sharpe" title="Mean log-return / std log-return (Sharpe equivalent)">Sharpe</th>
  <th data-col="max_dd" title="Largest peak-to-trough drop in window">Max DD</th>
  <th data-col="regime" title="Lag-1 autocorrelation: M=momentum (trend continues), R=reversion (fades), ~=unclear">Regime</th>
  <th data-col="avg_vol_ex">Avg Vol (Ex)</th>
  <th data-col="score_ex" title="Sharpe x log(1 + avg_vol) — risk-adjusted, volume-weighted">Score</th>
  <th>Sparkline (Ex)</th>
  <th></th>
</tr>
</thead>
<tbody id="tbody"></tbody>
</table>
<div style="color:var(--dim);font-size:11px;padding:10px 0 0 2px">
  * Trend/day (Div) measures appreciation in divine terms, stripping out divine/exalted inflation. Use this as the inflation-adjusted signal.
</div>
</div>

<script>
const DATA = {rows_json};
const LEAGUE_START = {league_start_json};

// League day display
(function() {{
  if (!LEAGUE_START) return;
  const start = new Date(LEAGUE_START + 'T00:00:00Z');
  const day = Math.floor((Date.now() - start.getTime()) / 86400000) + 1;
  if (day < 1) return;
  const phase = day <= 14 ? 'Early' : day <= 45 ? 'Mid' : 'Late';
  const badge = document.getElementById('league-day-badge');
  badge.innerHTML = '<span class="league-day">Day ' + day + ' — ' + phase + ' League</span>';
}})();

let sortCol = 'score_ex';
let sortAsc = false;
let onlyBothUp = true;
let minVol = 0;
let minSharpe = 0;
let minTrendEx = -20;
let minTrendDiv = 0;
let selectedRegimes = new Set(['M', 'R', '~']);
let windowHours = 168;

// Recency-decay constant: half-life ~1.4 days.
// A 7-day-old point gets ~3% weight vs a current point.
const DECAY = 0.5;

function fmt(v) {{
  if (v == null) return '—';
  if (v === 0) return '0';
  const abs = Math.abs(v);
  if (abs >= 1000) return v.toFixed(0);
  if (abs >= 100)  return v.toFixed(1);
  if (abs >= 10)   return v.toFixed(2);
  if (abs >= 1)    return v.toFixed(3);
  return v.toPrecision(3);
}}

function fmtPct(v) {{
  if (v == null) return '—';
  return (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
}}

function fmtSharpe(v) {{
  if (v == null) return '—';
  return (v >= 0 ? '+' : '') + v.toFixed(2);
}}

function fmtDD(v) {{
  if (v == null) return '—';
  return v.toFixed(1) + '%';
}}

function trendCls(v) {{
  if (v == null) return 'neu';
  return v > 0 ? 'pos' : 'neg';
}}

function ddCls(v) {{
  if (v == null) return 'neu';
  if (v > 25) return 'neg';
  if (v > 10) return 'warn';
  return 'neu';
}}

function regimeCls(r) {{
  if (r === 'M') return 'regime-m';
  if (r === 'R') return 'regime-r';
  return 'regime-n';
}}

function makeSparklineSVG(rates) {{
  const W = 80, H = 30;
  if (!rates || rates.length < 2) return '';
  let rs = rates;
  const MAX_PTS = 80;
  if (rs.length > MAX_PTS) {{
    const step = (rs.length - 1) / (MAX_PTS - 1);
    rs = Array.from({{length: MAX_PTS}}, (_, i) => rs[Math.round(i * step)]);
  }}
  const mn = Math.min(...rs), mx = Math.max(...rs);
  const rng = mx - mn;
  const sx = i => (i / (rs.length - 1) * W).toFixed(1);
  const sy = r => (rng === 0 ? H / 2 : H - (r - mn) / rng * H).toFixed(1);
  const pts = rs.map((r, i) => sx(i) + ',' + sy(r)).join(' ');
  const color = rs[rs.length - 1] >= rs[0] ? '#4ade80' : '#f87171';
  return '<svg width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + ' ' + H +
    '" xmlns="http://www.w3.org/2000/svg"><polyline points="' + pts +
    '" fill="none" stroke="' + color + '" stroke-width="1.5"/></svg>';
}}

function analyzeWindow(series, hours) {{
  if (!series || series.length === 0) return null;
  const cutoff = Date.now() / 1000 - hours * 3600;
  const pts = series.filter(p => p.t >= cutoff);
  if (pts.length < 3) return null;

  const n = pts.length;
  const rates = pts.map(p => p.r);
  const vols  = pts.map(p => p.v);
  const avgVol = vols.reduce((a, b) => a + b, 0) / n;

  // Log prices for regression (% interpretation, scale-invariant)
  const logRates = rates.map(r => Math.log(r));
  const tLast = pts[n - 1].t;
  const t0    = pts[0].t;
  const xs    = pts.map(p => (p.t - t0) / 86400);  // days since first point

  // Recency weights: exp(-DECAY * days_from_end)
  const ws   = pts.map(p => Math.exp(-DECAY * (tLast - p.t) / 86400));
  const wSum = ws.reduce((a, b) => a + b, 0);

  // Weighted means
  const wxm = xs.reduce((s, x, i) => s + ws[i] * x, 0) / wSum;
  const wym = logRates.reduce((s, y, i) => s + ws[i] * y, 0) / wSum;

  // Weighted OLS — slope = log-return per day ~= % per day
  const num = xs.reduce((s, x, i) => s + ws[i] * (x - wxm) * (logRates[i] - wym), 0);
  const den = xs.reduce((s, x, i) => s + ws[i] * (x - wxm) * (x - wxm), 0);
  const slope = den ? num / den : 0;
  const trendPct = slope * 100;

  // Log returns for Sharpe and regime
  const logReturns = [];
  for (let i = 1; i < n; i++) {{
    if (rates[i] > 0 && rates[i - 1] > 0)
      logReturns.push(Math.log(rates[i] / rates[i - 1]));
  }}

  // Sharpe: mean / std of log returns
  let sharpe = null;
  let stdLR = 0;
  let meanLR = 0;
  if (logReturns.length >= 3) {{
    meanLR = logReturns.reduce((a, b) => a + b, 0) / logReturns.length;
    const varLR = logReturns.reduce((s, r) => s + (r - meanLR) * (r - meanLR), 0) / logReturns.length;
    stdLR = Math.sqrt(varLR);
    sharpe = stdLR > 0 ? meanLR / stdLR : 0;
  }}

  // CV (kept as fallback)
  const meanRate = rates.reduce((a, b) => a + b, 0) / n;
  const varRate  = rates.reduce((s, r) => s + (r - meanRate) * (r - meanRate), 0) / n;
  const cv = meanRate > 0 ? Math.sqrt(varRate) / meanRate : 0;

  // Max drawdown: largest peak-to-trough drop
  let peak = rates[0], maxDD = 0;
  for (const r of rates) {{
    if (r > peak) peak = r;
    const dd = (peak - r) / peak;
    if (dd > maxDD) maxDD = dd;
  }}

  // Momentum: recent 25% of points vs older 75%
  const split = Math.max(1, Math.floor(n * 0.75));
  const oldSlice = rates.slice(0, split);
  const newSlice = rates.slice(split);
  const oldAvg = oldSlice.reduce((a, b) => a + b, 0) / oldSlice.length;
  const newAvg = newSlice.reduce((a, b) => a + b, 0) / newSlice.length;
  const momentum = oldAvg > 0 ? (newAvg - oldAvg) / oldAvg * 100 : null;

  // Regime: lag-1 autocorrelation of log returns
  let autocorr = null;
  if (logReturns.length >= 6) {{
    const m = logReturns.reduce((a, b) => a + b, 0) / logReturns.length;
    const lag0 = logReturns.reduce((s, r) => s + (r - m) * (r - m), 0);
    const lag1 = logReturns.slice(0, -1).reduce((s, r, i) => s + (r - m) * (logReturns[i + 1] - m), 0);
    autocorr = lag0 > 0 ? lag1 / lag0 : 0;
  }}

  // Volume-weighted score: Sharpe * log(1 + avgVol)
  // Falls back to classic trend/cv when Sharpe is unavailable
  const volFactor = Math.log(1 + avgVol);
  const score = sharpe != null
    ? sharpe * volFactor
    : trendPct / (cv + 0.01);

  return {{
    trend_pct: trendPct,
    sharpe,
    cv,
    avg_vol: avgVol,
    score,
    current_rate: rates[n - 1],
    max_dd: maxDD * 100,
    momentum,
    autocorr,
    rates,
  }};
}}

function computeRows() {{
  return DATA.map(d => {{
    const ex = analyzeWindow(d.series_ex, windowHours);
    if (!ex) return null;
    const div = analyzeWindow(d.series_div, windowHours);

    // Regime from autocorrelation (|ac| >= 0.2 threshold to filter noise)
    let regime = '~';
    if (ex.autocorr != null) {{
      if (ex.autocorr >  0.2) regime = 'M';
      else if (ex.autocorr < -0.2) regime = 'R';
    }}

    return {{
      name:        d.name,
      category:    d.category,
      rate_ex:     ex.current_rate,
      rate_div:    div ? div.current_rate : null,
      trend_ex:    ex.trend_pct,
      trend_div:   div ? div.trend_pct : null,
      momentum:    ex.momentum,
      sharpe:      ex.sharpe,
      max_dd:      ex.max_dd,
      regime,
      avg_vol_ex:  ex.avg_vol,
      score_ex:    ex.score,
      both_up:     ex.trend_pct > 0 && div != null && div.trend_pct > 0,
      sparkline:   makeSparklineSVG(ex.rates),
    }};
  }}).filter(r => r !== null);
}}

function render() {{
  const computed = computeRows();
  let rows = computed.filter(r => {{
    if (onlyBothUp && !r.both_up) return false;
    if (r.avg_vol_ex < minVol) return false;
    if (r.sharpe == null || r.sharpe < minSharpe) return false;
    if (!selectedRegimes.has(r.regime)) return false;
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
  <td class="${{trendCls(r.trend_ex)}}">${{fmtPct(r.trend_ex)}}</td>
  <td class="${{trendCls(r.trend_div)}}">${{fmtPct(r.trend_div)}}</td>
  <td class="${{trendCls(r.momentum)}}">${{fmtPct(r.momentum)}}</td>
  <td class="${{trendCls(r.sharpe)}}">${{fmtSharpe(r.sharpe)}}</td>
  <td class="${{ddCls(r.max_dd)}}">${{fmtDD(r.max_dd)}}</td>
  <td class="${{regimeCls(r.regime)}}" title="${{r.regime === 'M' ? 'Momentum: trend tends to continue' : r.regime === 'R' ? 'Reversion: price tends to mean-revert' : 'No clear pattern'}}">${{r.regime}}</td>
  <td class="c-vol">${{r.avg_vol_ex.toFixed(2)}}</td>
  <td class="c-score">${{r.score_ex != null ? r.score_ex.toFixed(2) : '—'}}</td>
  <td>${{r.sparkline}}</td>
  <td>${{r.both_up ? '<span class="badge">Both Up</span>' : ''}}</td>
</tr>`).join('');

  document.getElementById('cnt-total').textContent = rows.length;
  document.getElementById('cnt-both').textContent = rows.filter(r => r.both_up).length;
  document.getElementById('cnt-momentum').textContent =
    rows.filter(r => r.regime === 'M' && r.trend_ex > 0).length;
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

const initialTh = document.querySelector('th[data-col="score_ex"]');
if (initialTh) initialTh.classList.add('sort-active');

document.getElementById('chk-both').addEventListener('change', e => {{
  onlyBothUp = e.target.checked;
  render();
}});

const volSlider = document.getElementById('vol-slider');
const volVal    = document.getElementById('vol-val');
volSlider.addEventListener('input', () => {{
  minVol = parseFloat(volSlider.value);
  volVal.textContent = minVol.toFixed(1);
  render();
}});

const sharpeSlider = document.getElementById('sharpe-slider');
const sharpeVal    = document.getElementById('sharpe-val');
sharpeSlider.addEventListener('input', () => {{
  minSharpe = parseFloat(sharpeSlider.value);
  sharpeVal.textContent = (minSharpe >= 0 ? '+' : '') + minSharpe.toFixed(1);
  render();
}});

const trendExSlider = document.getElementById('trend-ex-slider');
const trendExVal    = document.getElementById('trend-ex-val');
trendExSlider.addEventListener('input', () => {{
  minTrendEx = parseFloat(trendExSlider.value);
  trendExVal.textContent = (minTrendEx >= 0 ? '+' : '') + minTrendEx.toFixed(1) + '%';
  render();
}});

const trendDivSlider = document.getElementById('trend-div-slider');
const trendDivVal    = document.getElementById('trend-div-val');
trendDivSlider.addEventListener('input', () => {{
  minTrendDiv = parseFloat(trendDivSlider.value);
  trendDivVal.textContent = (minTrendDiv >= 0 ? '+' : '') + minTrendDiv.toFixed(1) + '%';
  render();
}});

document.querySelectorAll('.window-btn').forEach(btn => {{
  btn.addEventListener('click', () => {{
    windowHours = parseInt(btn.dataset.hours);
    document.querySelectorAll('.window-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    render();
  }});
}});

const PRESETS = {{
  chase: {{ onlyBothUp: true,  regimes: ['M'],       minSharpe: 0,  minTrendDiv: 0,   minTrendEx: -20, minVol: 0 }},
  fade:  {{ onlyBothUp: false, regimes: ['R'],       minSharpe: -3, minTrendDiv: -20, minTrendEx: -20, minVol: 0 }},
}};

function applyPreset(cfg) {{
  onlyBothUp = cfg.onlyBothUp;
  minSharpe  = cfg.minSharpe;
  minTrendEx = cfg.minTrendEx;
  minTrendDiv = cfg.minTrendDiv;
  minVol = cfg.minVol;
  selectedRegimes = new Set(cfg.regimes);

  document.getElementById('chk-both').checked = onlyBothUp;

  sharpeSlider.value = minSharpe;
  sharpeVal.textContent = (minSharpe >= 0 ? '+' : '') + minSharpe.toFixed(1);

  trendExSlider.value = minTrendEx;
  trendExVal.textContent = (minTrendEx >= 0 ? '+' : '') + minTrendEx.toFixed(1) + '%';

  trendDivSlider.value = minTrendDiv;
  trendDivVal.textContent = (minTrendDiv >= 0 ? '+' : '') + minTrendDiv.toFixed(1) + '%';

  volSlider.value = minVol;
  volVal.textContent = minVol.toFixed(1);

  document.querySelectorAll('[data-regime]').forEach(b => {{
    if (selectedRegimes.has(b.dataset.regime)) b.classList.add('active');
    else b.classList.remove('active');
  }});

  render();
}}

document.getElementById('preset-chase').addEventListener('click', () => applyPreset(PRESETS.chase));
document.getElementById('preset-fade').addEventListener('click',  () => applyPreset(PRESETS.fade));

document.querySelectorAll('[data-regime]').forEach(btn => {{
  btn.addEventListener('click', () => {{
    const r = btn.dataset.regime;
    if (selectedRegimes.has(r)) {{
      selectedRegimes.delete(r);
      btn.classList.remove('active');
    }} else {{
      selectedRegimes.add(r);
      btn.classList.add('active');
    }}
    render();
  }});
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
    parser.add_argument("--league-start", default="", metavar="YYYY-MM-DD",
                        help="League start date for day counter in report header")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    out_path = Path(args.out)

    print(f"Loading items from {data_dir / 'details'} ...")
    items = load_items(data_dir)
    print(f"  Loaded {len(items)} items across {len(STEMS)} categories")

    rows, league, date_min, date_max = build_rows(items, args.min_vol, data_dir)
    print(f"  Passed filters (min-vol={args.min_vol}): {len(rows)} items with series data")

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = generate_html(rows, league, date_min, date_max, generated_at, args.league_start)

    out_path.write_text(html, encoding="utf-8")
    print(f"  Report written to {out_path.resolve()}")


if __name__ == "__main__":
    main()
