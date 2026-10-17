"""Daily Claude Code spend tracker.

Usage: daily_tracker.py [YYYY-MM-DD]  (optional date backfills from that day)

Collects cost from ccusage into spend.json, writes report_daily.html covering the
start of the month (or first record, if earlier) through yesterday, and sends a
macOS notification. Costs are ccusage estimates (token counts x list prices).
"""

import html
import json
import math
import os
import re
import calendar
import subprocess
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE_PATH = HERE / "spend.json"
REPORT_PATH = HERE / "report_daily.html"
WEIGHTS_PATH = HERE / ".weights.json"
PROJECTS_DIR = Path.home() / ".claude" / "projects"
# Placeholder model name Claude Code writes on locally generated messages.
SYNTHETIC_MODEL = "<synthetic>"

# Anthropic's prompt-cache TTL: a gap this long since the session's last request
# evicts the cache (a "cache miss"). Not exposed in Claude Code's settings files
# (checked ~/.claude/settings.json and ~/.claude.json) — it's an API-level cache_control
# parameter (5m default, 1h with a beta header) the harness picks per session, so there's
# nothing to look up. Override with CLAUDE_CACHE_TTL_SECONDS if your sessions run the 1h tier.
CACHE_TTL_SECONDS = int(os.environ.get("CLAUDE_CACHE_TTL_SECONDS", 300))


def empty_state():
    return {"days": {}, "session_cumulative": {}, "session_info": {}}


def merge(state, daily_rows, session_rows):
    """Daily totals are overwritten from ccusage. Session cost is attributed to
    the local day of its last activity, as the growth since the previous run."""
    for row in daily_rows:
        day = state["days"].setdefault(row["period"], {"total": 0.0, "sessions": {}})
        day["total"] = row["totalCost"]

    cumulative = state["session_cumulative"]
    for row in session_rows:
        session_id, cost = row["period"], row["totalCost"]
        delta = cost - cumulative.get(session_id, 0.0)
        cumulative[session_id] = cost
        if delta <= 1e-9:
            continue
        last_active = datetime.fromisoformat(
            row["metadata"]["lastActivity"].replace("Z", "+00:00")
        )
        day = state["days"].setdefault(
            str(last_active.astimezone().date()), {"total": 0.0, "sessions": {}}
        )
        day["sessions"][session_id] = day["sessions"].get(session_id, 0.0) + delta


def activity_stats(timestamps, ttl_seconds=CACHE_TTL_SECONDS):
    """Splits the gaps between a session's request timestamps into active time (gap < TTL)
    and idle time (gap >= TTL); a gap >= TTL also busts the prompt cache, so it's a miss."""
    ordered = sorted(parse_timestamp(t) for t in timestamps)
    active_seconds = idle_seconds = 0.0
    cache_misses = 0
    for prev, cur in zip(ordered, ordered[1:]):
        gap = (cur - prev).total_seconds()
        if gap >= ttl_seconds:
            idle_seconds += gap
            cache_misses += 1
        else:
            active_seconds += gap
    return {
        "active_seconds": active_seconds,
        "idle_seconds": idle_seconds,
        "gap_count": max(len(ordered) - 1, 0),
        "cache_misses": cache_misses,
    }


def read_session_info(log_path):
    """Title Claude Code recorded for the session (last ai-title), else the first typed prompt."""
    title, first_prompt, project, timestamps = None, None, None, []
    model_counts = Counter()
    for line in Path(log_path).read_text().splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        timestamp = entry.get("timestamp")
        if isinstance(timestamp, str):
            timestamps.append(timestamp)
        if entry.get("type") == "assistant":
            model = entry.get("message", {}).get("model")
            if model and model != SYNTHETIC_MODEL:
                model_counts[model] += 1
        if entry.get("type") == "ai-title":
            title = entry["aiTitle"]
        elif entry.get("type") == "user":
            project = project or (Path(entry["cwd"]).name if entry.get("cwd") else None)
            content = entry.get("message", {}).get("content")
            if first_prompt is None and isinstance(content, str):
                first_prompt = content.strip().replace("\n", " ")[:80]
    timestamps.sort()
    return {
        "title": title or first_prompt or "",
        "project": project or "",
        "models": [model for model, _ in model_counts.most_common()],
        "started": timestamps[0] if timestamps else None,
        "ended": timestamps[-1] if timestamps else None,
        **activity_stats(timestamps),
    }


def format_model(model_id):
    """'claude-sonnet-4-5-20250929' -> 'Sonnet 4.5'; ids that don't match are shown as-is."""
    match = re.fullmatch(r"claude-([a-z]+)((?:-\d+)+?)(?:-\d{8})?", model_id)
    if not match:
        return model_id
    return f"{match[1].capitalize()} {match[2][1:].replace('-', '.')}"


def parse_timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()


def format_duration(seconds):
    minutes = int(seconds // 60)
    if minutes < 1:
        return "<1m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def describe_span(info):
    """Returns (start text, end text, minutes), local time; wall-clock span including idle time."""
    if not info.get("started"):
        return "", "", 0.0
    started, ended = parse_timestamp(info["started"]), parse_timestamp(info["ended"])
    return (
        f"{started:%b %d %H:%M}",
        f"{ended:%b %d %H:%M}",
        (ended - started).total_seconds() / 60,
    )


def update_session_info(state, session_ids, projects_dir=PROJECTS_DIR):
    """Re-reads titles for all given sessions; titles can be written after the first run sees a session."""
    logs = {path.stem: path for path in Path(projects_dir).glob("*/*.jsonl")}
    info = state.setdefault("session_info", {})
    for session_id in session_ids:
        if session_id in logs:
            info[session_id] = read_session_info(logs[session_id])


def collection_start(state, today):
    first_record = min((date.fromisoformat(d) for d in state["days"]), default=today)
    return min(today.replace(day=1), first_record)


def report_range(state, today):
    yesterday = today - timedelta(days=1)
    return min(collection_start(state, today), yesterday), yesterday


def run_ccusage(subcommand, since):
    result = subprocess.run(
        [
            "npx",
            "--yes",
            "ccusage@latest",
            subcommand,
            "--json",
            "--since",
            since.strftime("%Y%m%d"),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)[subcommand]


def session_row(session_id, cost, biggest, info):
    start_text, end_text, minutes = describe_span(info)
    per_minute = f"${cost / minutes:.3f}" if minutes >= 1 else "-"
    gap_count = info.get("gap_count", 0)
    miss_pct = (
        f"{info.get('cache_misses', 0) / gap_count * 100:.0f}%" if gap_count else "-"
    )
    session_label = ", ".join([session_id, *map(format_model, info.get("models", []))])
    return (
        f'<div class="session"><div class="what"><span class="title">{html.escape(info.get("title") or "(no title)")}</span>'
        f"<code>{html.escape(info.get('project', ''))} &middot; {html.escape(session_label)}</code></div>"
        f'<span class="bar"><i style="width:{cost / biggest * 100:.1f}%"></i></span>'
        f'<span class="duration">{format_duration(minutes * 60) if minutes else ""}</span>'
        f'<span class="active">{format_duration(info.get("active_seconds", 0.0))}</span>'
        f'<span class="idle">{format_duration(info.get("idle_seconds", 0.0))}</span>'
        f'<span class="miss">{miss_pct}</span>'
        f'<span class="cost">${cost:.2f}</span>'
        f'<span class="start">{start_text}</span><span class="end">{end_text}</span>'
        f'<span class="per-minute">{per_minute}</span></div>'
    )


def cumulative_series(state, first, last):
    """Running total of daily cost for each day from first to last inclusive."""
    total, series = 0.0, []
    for i in range((last - first).days + 1):
        total += state["days"].get(str(first + timedelta(days=i)), {}).get("total", 0.0)
        series.append(total)
    return series


WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def load_weights(path):
    """Share of a full day's spend expected on each weekday, Monday first. Raises ValueError if the
    file is missing or invalid, so a typo can't silently change the projections."""
    if not path.exists():
        raise ValueError(
            f"{path} not found; ./setup.sh creates it with the default weights"
        )
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise ValueError(f"{path} is not valid JSON: {error}") from error
    if not isinstance(raw, dict):
        raise ValueError(
            f"{path} must be a JSON object with keys: {', '.join(WEEKDAY_KEYS)}"
        )
    missing, unknown = set(WEEKDAY_KEYS) - set(raw), set(raw) - set(WEEKDAY_KEYS)
    if missing or unknown:
        raise ValueError(
            f"{path}: missing keys {sorted(missing)}, unknown keys {sorted(unknown)}"
        )
    for key, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"{path}: {key} must be a number >= 0, got {value!r}")
    weights = [float(raw[key]) for key in WEEKDAY_KEYS]
    if not any(weights):
        raise ValueError(f"{path}: at least one day must have a weight above 0")
    return weights


def project_month(state, today, days_in_month, weights):
    """Cumulative actuals for the current month through yesterday, plus two projections to month end.
    "weekly": cost per full-weight day over up to 7 completed days, times each remaining day's weight.
    "daily": the latest completed day's cost divided by its weight, times the average weight, on every
    remaining day. Days with weight 0 are left out of both rates. None if no completed day has a
    weight above 0."""
    first, yesterday = today.replace(day=1), today - timedelta(days=1)
    if yesterday < first:
        return None
    actual = cumulative_series(state, first, yesterday)
    days = [first + timedelta(days=i) for i in range(len(actual))]
    costs = [state["days"].get(str(day), {}).get("total", 0.0) for day in days]
    working = [
        (day, cost) for day, cost in zip(days, costs) if weights[day.weekday()] > 0
    ]
    if not working:
        return None
    remaining = [today + timedelta(days=i) for i in range(days_in_month - len(actual))]

    recent = [
        (day, cost) for day, cost in working if day >= days[-1] - timedelta(days=6)
    ]
    weekly_rate = (
        sum(cost for _, cost in recent)
        / sum(weights[day.weekday()] for day, _ in recent)
        if recent
        else None
    )
    base_day, base_cost = working[-1]
    daily_amount = base_cost / weights[base_day.weekday()] * sum(weights) / len(weights)
    return {
        "actual": actual,
        "weekly": _running_total(
            actual[-1],
            [(weekly_rate or 0.0) * weights[day.weekday()] for day in remaining],
        ),
        "daily": _running_total(actual[-1], [daily_amount] * len(remaining)),
    }


def _running_total(start, amounts):
    total, series = start, [start]
    for amount in amounts:
        total += amount
        series.append(total)
    return series


# (floor, ceiling, fill) in dollars; the top band has no ceiling.
SPEND_BANDS = [(200, 400, "#f5c451"), (400, None, "#f08ab4")]


TOTAL_COLORS = {"green": "#16a34a", "yellow": "#ca8a04", "red": "#dc2626"}


def total_color(total):
    if total < SPEND_BANDS[0][0]:
        return TOTAL_COLORS["green"]
    return TOTAL_COLORS["yellow"] if total <= SPEND_BANDS[1][0] else TOTAL_COLORS["red"]


MONTHLY_TARGET = 200
DAILY_CAP = MONTHLY_TARGET / 30


def target_message(actual, low, high, target):
    """(text, color) for the target box. The reduction applies to spend still to come, since past spend is fixed.
    low and high are the two projected month totals; a projection at or under target needs 0%."""
    green, yellow = TOTAL_COLORS["green"], TOTAL_COLORS["yellow"]
    if high <= target:
        return f"On track to complete month with ${high:.0f} spend.", green
    if actual >= target:
        return (
            f"Target of ${target:.0f} already exceeded with ${actual:.0f} spent.",
            yellow,
        )
    needed = lambda projected: (
        math.ceil((projected - target) / (projected - actual) * 100)
        if projected > target
        else 0
    )
    return (
        f"Target reduction of use between apx {needed(low)}% and {needed(high)}% for the rest of the month "
        f"in order to hit ${target:.0f} by end of month",
        yellow,
    )


def render_weekly_weights(weights):
    days = " &middot; ".join(
        f"{key.capitalize()} {weight:g}" for key, weight in zip(WEEKDAY_KEYS, weights)
    )
    return f'<p class="weights"><b>Weekly projection weights:</b> {days}</p>'


def render_target(text, color):
    icon = "&#10003;" if color == TOTAL_COLORS["green"] else "!"
    return f'<div class="target" style="color:{color}"><span class="icon" aria-hidden="true"><span>{icon}</span></span><span class="text">{text}</span></div>'


def render_month_total(label, text, color):
    return (
        f'<aside class="month-total card"><div class="label"><span class="dot" style="color:{color}"></span>{label}</div>'
        f'<div class="amount">{text}</div></aside>'
    )


def nice_axis(peak, max_intervals=6):
    """(step, top) for a y axis: the smallest top that fits peak using a 1/2/2.5/5 step and at most max_intervals gridlines."""
    magnitude = 10 ** math.floor(math.log10(peak))
    candidates = []
    for scale in (magnitude / 100, magnitude / 10, magnitude, magnitude * 10):
        for multiplier in (1, 2, 2.5, 5):
            step = multiplier * scale
            top = math.ceil(peak / step - 1e-9) * step
            if round(top / step) <= max_intervals:
                candidates.append((top, -step))
    top, negative_step = min(candidates)
    return -negative_step, top


def rounded_top_bar(x, y, width, height, radius=4):
    radius = min(radius, height, width / 2)
    return (
        f"M{x:.1f},{y + height:.1f}V{y + radius:.1f}Q{x:.1f},{y:.1f} {x + radius:.1f},{y:.1f}"
        f"H{x + width - radius:.1f}Q{x + width:.1f},{y:.1f} {x + width:.1f},{y + radius:.1f}V{y + height:.1f}Z"
    )


def render_line_chart(
    title, lines, days_in_month, chart_id, marker=None, date_prefix="Day"
):
    """lines: (label, color, dashed, first_day_of_month, values). X axis is day of month, Y is cumulative $.
    marker: optional (day_of_month, label) drawn as a dashed vertical line with the label above the plot.
    date_prefix: month name the hover tooltip puts before the day number."""
    width, height, left, right, top, bottom = 640, 260, 48, 16, 30, 32
    data_peak = max(max(values) for *_, values in lines) or 0.01
    step, peak = nice_axis(data_peak)
    x_at = lambda day: (
        left + (day - 1) / max(days_in_month - 1, 1) * (width - left - right)
    )
    y_at = lambda cost: height - bottom - cost / peak * (height - top - bottom)
    parts = []
    for tick in range(round(peak / step) + 1):
        cost = step * tick
        parts.append(
            f'<line class="grid" x1="{left}" x2="{width - right}" y1="{y_at(cost):.1f}" y2="{y_at(cost):.1f}"/>'
            f'<text class="tick" x="{left - 8}" y="{y_at(cost) + 3.5:.1f}" text-anchor="end">${cost:,.0f}</text>'
        )
    for day in range(1, days_in_month + 1):
        if day == 1 or day % 5 == 0 or day == days_in_month:
            parts.append(
                f'<text class="tick" x="{x_at(day):.1f}" y="{height - bottom + 18}" text-anchor="middle">{day}</text>'
            )
    for floor, ceiling, _ in SPEND_BANDS:
        if floor <= data_peak:
            y_top = y_at(min(ceiling, peak)) if ceiling else top
            parts.append(
                f'<clipPath id="{chart_id}-band-{floor}"><rect x="{left}" y="{y_top:.1f}" width="{width - left - right}" height="{y_at(floor) - y_top:.1f}"/></clipPath>'
                f'<line class="threshold" x1="{left}" x2="{width - right}" y1="{y_at(floor):.1f}" y2="{y_at(floor):.1f}"/>'
                f'<text class="threshold-label" x="{left + 6}" y="{y_at(floor) - 5:.1f}">${floor} threshold</text>'
            )
    if marker:
        marker_day, marker_label = marker
        marker_x = x_at(marker_day)
        parts.append(
            f'<line class="today" x1="{marker_x:.1f}" x2="{marker_x:.1f}" y1="{top}" y2="{height - bottom}"/>'
            f'<rect class="today-pill" x="{marker_x - 24:.1f}" y="4" width="48" height="18" rx="9"/>'
            f'<text class="today-label" x="{marker_x:.1f}" y="16.5" text-anchor="middle">{marker_label}</text>'
        )
    legend, strokes, end_dots, series_data = [], [], [], []
    for label, color, dashed, first_day, values in lines:
        points = " ".join(
            f"{x_at(first_day + i):.1f},{y_at(v):.1f}" for i, v in enumerate(values)
        )
        dash = ' stroke-dasharray="6 5"' if dashed else ""
        area = f"{x_at(first_day):.1f},{y_at(0):.1f} {points} {x_at(first_day + len(values) - 1):.1f},{y_at(0):.1f}"
        if not dashed:
            parts.append(
                f'<polygon points="{area}" fill="{color}" fill-opacity="0.10"/>'
            )
        for floor, _, fill in SPEND_BANDS:
            if floor <= data_peak:
                parts.append(
                    f'<polygon points="{area}" fill="{fill}" fill-opacity="0.30" clip-path="url(#{chart_id}-band-{floor})"/>'
                )
        strokes.append(
            f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"{dash}>'
            f"<title>{label}: ${values[-1]:.2f}</title></polyline>"
        )
        end_dots.append(
            f'<circle cx="{x_at(first_day + len(values) - 1):.1f}" cy="{y_at(values[-1]):.1f}" r="4" fill="{color}" stroke="var(--surface)" stroke-width="2"/>'
        )
        legend.append(
            f'<span><i style="background:{color}"></i>{label}: ${values[-1]:.2f}</span>'
        )
        series_data.append(
            {
                "label": label,
                "color": color,
                "dashed": dashed,
                "first": first_day,
                "values": [round(v, 2) for v in values],
            }
        )
    geometry = {
        "width": width,
        "height": height,
        "left": left,
        "right": right,
        "top": top,
        "bottom": bottom,
        "peak": peak,
        "days": days_in_month,
        "prefix": date_prefix,
        "series": series_data,
    }
    legend_html = (
        f'<div class="legend">{"".join(legend)}</div>' if len(lines) > 1 else ""
    )
    return (
        f'<figure class="trend card"><figcaption>{title}</figcaption>'
        f'<div class="plot" data-geometry="{html.escape(json.dumps(geometry), quote=True)}">'
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title, quote=True)}">'
        f'{"".join(parts + strokes + end_dots)}<line class="crosshair" y1="{top}" y2="{height - bottom}"/></svg>'
        f'<div class="tooltip" role="status"></div></div>{legend_html}</figure>'
    )


def month_block(kind, tag, content):
    """Labeled container so the current and prior month read as separate groups."""
    return f'<section class="month-block {kind}"><div class="month-head"><span class="month-tag">{tag}</span></div>{content}</section>'


def render_trend_charts(state, today, weights):
    """Raises ValueError when last month has no records, since its chart would be meaningless."""
    weights_line = render_weekly_weights(weights)
    this_first = today.replace(day=1)
    last_last = this_first - timedelta(days=1)
    last_first = last_last.replace(day=1)
    if not any(last_first <= date.fromisoformat(d) <= last_last for d in state["days"]):
        raise ValueError(
            f"No spend records for {last_first:%B %Y}; backfill with ./setup.sh {last_first}"
        )
    last_month_total = cumulative_series(state, last_first, last_last)[-1]
    last_month = render_line_chart(
        f"{last_first:%B %Y} cumulative spend",
        [
            (
                "actual spend",
                "var(--series-1)",
                False,
                1,
                cumulative_series(state, last_first, last_last),
            )
        ],
        last_last.day,
        "last-month",
        date_prefix=f"{last_first:%b}",
    )
    prior_block = month_block(
        "prior",
        "Prior month",
        f'<div class="chart-row">{last_month}'
        f"{render_month_total('Month total', f'${last_month_total:.0f}', total_color(last_month_total))}</div>",
    )
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    projection = project_month(state, today, days_in_month, weights)
    if projection is None:
        empty = f'<div class="chart-row"><figure class="trend"><figcaption>{today:%B %Y} cumulative spend</figcaption><p>No completed working days yet.</p></figure></div>'
        return (
            weights_line + month_block("current", "Current month", empty) + prior_block
        )
    last_actual_day = len(projection["actual"])
    this_month = render_line_chart(
        f"{today:%B %Y} cumulative spend",
        [
            ("actual spend", "var(--series-1)", False, 1, projection["actual"]),
            (
                "weekly trend projection",
                "var(--series-2)",
                True,
                last_actual_day,
                projection["weekly"],
            ),
            (
                "daily trend projection",
                "var(--series-3)",
                True,
                last_actual_day,
                projection["daily"],
            ),
        ],
        days_in_month,
        "this-month",
        marker=(today.day, f"{today:%m/%d}"),
        date_prefix=f"{today:%b}",
    )
    low, high = sorted((projection["weekly"][-1], projection["daily"][-1]))
    this_month_total = render_month_total(
        "Projected month total", f"${low:.0f} &rarr; ${high:.0f}", total_color(high)
    )
    target = render_target(
        *target_message(projection["actual"][-1], low, high, MONTHLY_TARGET)
    )
    current_block = month_block(
        "current",
        "Current month",
        f'<div class="chart-row">{this_month}{this_month_total}</div>{target}',
    )
    return weights_line + current_block + prior_block


REPORT_CSS = """
:root {
  color-scheme: light dark;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --line: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10); --shadow: 0 1px 2px rgba(11,11,11,0.04), 0 4px 16px rgba(11,11,11,0.04);
  --series-1: #2a78d6; --series-2: #eb6834; --series-3: #1baf7a; --accent: var(--series-1);
}
@media (prefers-color-scheme: dark) {
  :root {
    --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
    --line: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10); --shadow: none;
    --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70;
  }
}
* { box-sizing: border-box; }
body { font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; margin: 0; padding: 32px 24px 48px;
  background: var(--page); color: var(--ink); }
main { max-width: 1320px; margin: 0 auto; }
h1 { font-size: 28px; font-weight: 650; letter-spacing: -0.02em; margin: 0 0 4px; }
.summary { padding: 16px 20px 14px; margin-bottom: 12px; display: flex; flex-direction: column; gap: 14px; }
.summary-panel[hidden] { display: none; }
.stats { display: flex; flex-wrap: wrap; gap: 12px 40px; }
.stat { min-width: 150px; }
.summary-range { margin: 12px 0 0; font-size: 13px; color: var(--ink-2); }
.stat .label, .month-total .label { font-size: 13px; color: var(--ink-2); display: flex; align-items: center; gap: 8px; }
.stat .value { font-size: 24px; font-weight: 650; letter-spacing: -0.01em; }
.weights { margin: 0 0 20px; font-size: 13px; color: var(--ink-2); white-space: nowrap; overflow-x: auto; }
.weights b { color: var(--ink); font-weight: 600; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 14px; box-shadow: var(--shadow); }
.month-block { padding: 14px; margin-bottom: 20px; border-radius: 18px; border: 1px solid var(--border); display: flex; flex-direction: column; gap: 16px; }
.month-block.current { background: color-mix(in srgb, var(--accent) 7%, var(--page)); border-color: color-mix(in srgb, var(--accent) 35%, transparent); }
.month-block.prior { background: color-mix(in srgb, var(--muted) 10%, var(--page)); border-style: dashed; }
.month-block .target { margin-bottom: 0; }
.month-tag { font-size: 11px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; padding: 3px 10px; border-radius: 999px; }
.month-block.current .month-tag { background: var(--accent); color: var(--surface); }
.month-block.prior .month-tag { background: transparent; color: var(--ink-2); border: 1px solid var(--axis); }
.charts { display: flex; flex-direction: column; gap: 16px; margin-bottom: 16px; }
.chart-row { display: grid; grid-template-columns: minmax(0, 1fr) 380px; gap: 16px; align-items: stretch; }
.trend { margin: 0; padding: 18px 20px 14px; min-width: 0; }
.trend figcaption { font-size: 15px; font-weight: 600; margin-bottom: 8px; }
.plot { position: relative; }
.plot svg { display: block; width: 100%; height: auto; overflow: visible; }
.grid { stroke: var(--line); stroke-width: 1; }
.tick { font-size: 11px; fill: var(--muted); }
.threshold { stroke: var(--muted); stroke-width: 1; stroke-dasharray: 3 4; }
.threshold-label { font-size: 11px; fill: var(--ink-2); paint-order: stroke; stroke: var(--surface); stroke-width: 3px; stroke-linejoin: round; }
.today { stroke: var(--muted); stroke-width: 1; stroke-dasharray: 3 3; }
.today-pill { fill: var(--ink); }
.today-label { font-size: 11px; font-weight: 600; fill: var(--surface); }
.crosshair { stroke: var(--muted); stroke-width: 1; display: none; pointer-events: none; }
.tooltip { position: absolute; top: 0; display: none; pointer-events: none; background: var(--surface); color: var(--ink);
  border: 1px solid var(--border); border-radius: 10px; box-shadow: 0 6px 24px rgba(0,0,0,0.14); padding: 8px 12px; font-size: 12px; white-space: nowrap; z-index: 2; }
.tooltip .when { font-weight: 600; margin-bottom: 4px; }
.tooltip .row { display: flex; align-items: center; gap: 8px; justify-content: space-between; }
.tooltip .row span:first-child { color: var(--ink-2); display: flex; align-items: center; gap: 6px; }
.tooltip .row b { font-variant-numeric: tabular-nums; font-weight: 600; }
.legend { display: flex; flex-wrap: wrap; gap: 4px 18px; font-size: 12px; color: var(--ink-2); margin-top: 8px; }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.legend i, .tooltip i { display: inline-block; width: 10px; height: 10px; border-radius: 3px; }
.month-total { padding: 20px 24px; display: flex; flex-direction: column; justify-content: center; gap: 6px; }
.month-total .amount { font-size: 48px; font-weight: 650; line-height: 1.1; letter-spacing: -0.03em; white-space: nowrap; }
.dot { width: 10px; height: 10px; border-radius: 50%; background: currentColor; display: inline-block; }
.target { display: flex; align-items: center; gap: 12px; padding: 14px 20px; margin-bottom: 24px; border-radius: 14px;
  background: var(--surface); border: 1px solid var(--border); border-left: 4px solid currentColor; font-weight: 600; font-size: 15px; }
.target .icon { flex: none; width: 22px; height: 22px; border-radius: 50%; background: currentColor; display: grid; place-items: center; }
.target .icon span { color: var(--surface); font-size: 13px; line-height: 1; }
.target .text { color: var(--ink); }
.daily { padding: 18px 20px 12px; margin: 0 0 24px; overflow-x: auto; }
.daily h2 { font-size: 15px; font-weight: 600; margin: 0 0 8px; }
.daily svg { display: block; width: 100%; height: auto; }
.bar { fill: var(--series-1); }
.bar.over { fill: #dc2626; }
.cap-line { stroke: #dc2626; stroke-width: 1; stroke-dasharray: 4 3; }
.hit { fill: transparent; }
.hit:hover + .bar, .bar:hover { opacity: 0.8; }
.table-card { padding: 4px 8px 8px; }
table { border-collapse: collapse; width: 100%; }
th { text-align: left; font-size: 12px; font-weight: 500; color: var(--muted); padding: 12px 12px 8px; border-bottom: 1px solid var(--line); }
td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line); font-variant-numeric: tabular-nums; }
tr:not(.detail):hover td { background: color-mix(in srgb, var(--muted) 8%, transparent); }
.toggle { font: inherit; font-size: 13px; color: var(--ink-2); background: none; border: 1px solid var(--border);
  border-radius: 999px; padding: 2px 12px; cursor: pointer; }
.toggle::after { content: " \\25BE"; }
.toggle[aria-expanded="true"]::after { content: " \\25B4"; }
.toggle:hover { color: var(--ink); border-color: var(--muted); }
.detail td { padding: 0; }
.filter-bar { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 8px; }
.pills { display: flex; flex-wrap: wrap; gap: 8px; }
.pill, .scope-pill { font: inherit; font-size: 13px; color: var(--ink-2); background: var(--surface); border: 1px solid var(--border);
  border-radius: 999px; padding: 4px 14px; cursor: pointer; display: inline-flex; gap: 8px; align-items: baseline; }
.pill:hover, .scope-pill:hover { color: var(--ink); border-color: var(--muted); }
.pill[aria-pressed="true"], .scope-pill[aria-pressed="true"] { background: var(--ink); color: var(--surface); border-color: var(--ink); }
.pill-total { font-variant-numeric: tabular-nums; opacity: 0.7; }
.toggle-all { font: inherit; font-size: 13px; color: var(--ink-2); background: none; border: none; cursor: pointer; padding: 4px 8px; }
.toggle-all:hover { color: var(--ink); text-decoration: underline; }
.month-summary { margin: 0 0 12px; font-size: 13px; color: var(--ink-2); }
.filtered-out { display: none; }
.panel { margin: 4px 8px 14px; padding: 8px 14px; max-height: 320px; overflow-y: auto;
  border: 1px solid var(--border); border-radius: 12px; background: var(--page); }
.session { display: grid; grid-template-columns: minmax(440px, 1fr) 90px 63px 63px 63px 56px 63px 112px 112px 77px; gap: 12px; align-items: center; padding: 4px 0; }
.session .what { display: flex; flex-direction: column; min-width: 0; }
.session .title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.session code { font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); }
.session .bar { height: 8px; border-radius: 4px; background: var(--line); }
.session .bar i { display: block; height: 100%; border-radius: 4px; background: var(--series-1); }
.session .start, .session .end, .session .per-minute, .session .duration,
.session .active, .session .idle, .session .miss { text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }
.session.head { font-size: 12px; color: var(--muted); border-bottom: 1px solid var(--line); }
.session.head span:nth-child(n+3) { text-align: right; }
.session .duration { color: var(--muted); }
.session .cost { text-align: right; font-variant-numeric: tabular-nums; }
@media (max-width: 900px) { .chart-row { grid-template-columns: 1fr; } .month-total .amount { font-size: 40px; } }
"""

REPORT_JS = """
document.querySelectorAll(".toggle").forEach(function (button) {
  button.addEventListener("click", function () {
    var row = document.getElementById("detail-" + button.dataset.day);
    row.hidden = !row.hidden;
    button.setAttribute("aria-expanded", String(!row.hidden));
  });
});
var selectedMonth = null;
function visibleDayRows() {
  return Array.prototype.filter.call(document.querySelectorAll("tr[data-month]:not(.detail)"),
    function (row) { return !row.classList.contains("filtered-out"); });
}
function syncExpandAll() {
  var toggles = visibleDayRows().map(function (row) { return row.querySelector(".toggle"); }).filter(Boolean);
  var allOpen = toggles.length > 0 && toggles.every(function (t) { return t.getAttribute("aria-expanded") === "true"; });
  document.getElementById("expand-all").textContent = allOpen ? "Collapse all" : "Expand all";
  return { toggles: toggles, allOpen: allOpen };
}
function selectMonth(month) {
  selectedMonth = month;
  document.querySelectorAll(".pill").forEach(function (pill) {
    pill.setAttribute("aria-pressed", String(pill.dataset.month === month));
  });
  document.querySelectorAll("tr[data-month]").forEach(function (row) {
    row.classList.toggle("filtered-out", month !== "all" && row.dataset.month !== month);
  });
  var rows = visibleDayRows(), total = 0, sessions = 0;
  rows.forEach(function (row) {
    total += parseFloat(row.cells[1].textContent.replace("$", ""));
    sessions += parseInt(row.cells[2].textContent, 10);
  });
  document.getElementById("month-summary").textContent = rows.length + " days \u00b7 " + sessions + " sessions \u00b7 $" +
    total.toFixed(2) + " total \u00b7 $" + (rows.length ? total / rows.length : 0).toFixed(2) + " per day";
  syncExpandAll();
}
document.querySelectorAll(".pill").forEach(function (pill) {
  pill.addEventListener("click", function () { selectMonth(pill.dataset.month); });
});
document.getElementById("expand-all").addEventListener("click", function () {
  var state = syncExpandAll();
  state.toggles.forEach(function (t) {
    if ((t.getAttribute("aria-expanded") === "true") === state.allOpen) t.click();
  });
});
document.querySelectorAll(".toggle").forEach(function (t) { t.addEventListener("click", syncExpandAll); });
selectMonth(document.querySelector(".pill").dataset.month);
document.querySelectorAll(".scope-pill").forEach(function (pill) {
  pill.addEventListener("click", function () {
    document.querySelectorAll(".scope-pill").forEach(function (other) {
      other.setAttribute("aria-pressed", String(other === pill));
    });
    document.querySelectorAll(".summary-panel").forEach(function (panel) {
      panel.hidden = panel.dataset.scope !== pill.dataset.scope;
    });
  });
});
var SVG_NS = "http://www.w3.org/2000/svg";
document.querySelectorAll(".plot").forEach(function (plot) {
  var g = JSON.parse(plot.dataset.geometry);
  var svg = plot.querySelector("svg"), crosshair = svg.querySelector(".crosshair"), tooltip = plot.querySelector(".tooltip");
  var plotWidth = g.width - g.left - g.right, plotHeight = g.height - g.top - g.bottom;
  var xAt = function (day) { return g.left + (day - 1) / Math.max(g.days - 1, 1) * plotWidth; };
  var yAt = function (cost) { return g.height - g.bottom - cost / g.peak * plotHeight; };
  var dots = g.series.map(function (s) {
    var dot = document.createElementNS(SVG_NS, "circle");
    dot.setAttribute("r", "4.5");
    dot.style.fill = s.color; dot.style.stroke = "var(--surface)"; dot.style.strokeWidth = "2"; dot.style.display = "none";
    dot.style.pointerEvents = "none";
    svg.appendChild(dot);
    return dot;
  });
  function hide() {
    crosshair.style.display = "none"; tooltip.style.display = "none";
    dots.forEach(function (dot) { dot.style.display = "none"; });
  }
  svg.addEventListener("pointermove", function (event) {
    var box = svg.getBoundingClientRect();
    var svgX = (event.clientX - box.left) / box.width * g.width;
    var day = Math.min(g.days, Math.max(1, Math.round((svgX - g.left) / plotWidth * (g.days - 1)) + 1));
    var rows = [];
    g.series.forEach(function (s, i) {
      var index = day - s.first;
      var hasValue = index >= 0 && index < s.values.length && !(s.dashed && index === 0);
      dots[i].style.display = hasValue ? "" : "none";
      if (!hasValue) return;
      dots[i].setAttribute("cx", xAt(day)); dots[i].setAttribute("cy", yAt(s.values[index]));
      rows.push([s, s.values[index]]);
    });
    if (!rows.length) { hide(); return; }
    crosshair.setAttribute("x1", xAt(day)); crosshair.setAttribute("x2", xAt(day)); crosshair.style.display = "block";
    tooltip.textContent = "";
    var when = document.createElement("div");
    when.className = "when"; when.textContent = g.prefix + " " + day;
    tooltip.appendChild(when);
    rows.forEach(function (pair) {
      var row = document.createElement("div"), name = document.createElement("span"), swatch = document.createElement("i"), value = document.createElement("b");
      row.className = "row"; swatch.style.background = pair[0].color;
      name.appendChild(swatch); name.appendChild(document.createTextNode(pair[0].label));
      value.textContent = "$" + pair[1].toFixed(2);
      row.appendChild(name); row.appendChild(value); tooltip.appendChild(row);
    });
    tooltip.style.display = "block";
    var px = xAt(day) / g.width * box.width;
    tooltip.style.left = (px > box.width * 0.6 ? px - tooltip.offsetWidth - 12 : px + 12) + "px";
  });
  svg.addEventListener("pointerleave", hide);
});
"""


def render_daily_bars(days, totals):
    """Bar chart of each day's total spend, with the value above every non-zero bar."""
    left, top, bar_w, gap, chart_h = 44, 20, 16, 6, 180
    step, peak = nice_axis(max(totals + [DAILY_CAP]))
    y_at = lambda cost: top + chart_h - cost / peak * chart_h
    width = left + len(days) * (bar_w + gap) + 8
    parts = []
    for tick in range(round(peak / step) + 1):
        cost = step * tick
        parts.append(
            f'<line class="grid" x1="{left}" x2="{width - 8}" y1="{y_at(cost):.1f}" y2="{y_at(cost):.1f}"/>'
            f'<text class="tick" x="{left - 8}" y="{y_at(cost) + 3.5:.1f}" text-anchor="end">${cost:,.0f}</text>'
        )
    for i, (d, total) in enumerate(zip(days, totals)):
        x = left + gap / 2 + i * (bar_w + gap)
        h = total / peak * chart_h
        if i == 0 or d.day == 1:
            parts.append(
                f'<text class="tick" x="{x + bar_w / 2:.1f}" y="{top + chart_h + 32}" text-anchor="middle" style="fill:var(--ink-2);font-weight:600">{d:%b}</text>'
            )
        if i == 0 or d.day == 1 or d.day % 5 == 0 or i == len(days) - 1:
            parts.append(
                f'<text class="tick" x="{x + bar_w / 2:.1f}" y="{top + chart_h + 18}" text-anchor="middle">{d.day}</text>'
            )
        parts.append(
            f'<rect class="hit" x="{x - gap / 2:.1f}" y="{top}" width="{bar_w + gap}" height="{chart_h}"><title>{d}: ${total:.2f}</title></rect>'
        )
        title = (
            f"<title>{d}: ${total:.2f}"
            + (
                f" (${total - DAILY_CAP:.2f} over the ${DAILY_CAP:.2f} daily cap)"
                if total > DAILY_CAP
                else ""
            )
            + "</title>"
        )
        if total > DAILY_CAP:
            base_h = DAILY_CAP / peak * chart_h
            parts.append(
                f'<path class="bar" d="{rounded_top_bar(x, top + chart_h - base_h, bar_w, base_h, radius=0)}">{title}</path>'
            )
            parts.append(
                f'<path class="bar over" d="{rounded_top_bar(x, top + chart_h - h, bar_w, h - base_h)}">{title}</path>'
            )
        elif h >= 0.5:
            parts.append(
                f'<path class="bar" d="{rounded_top_bar(x, top + chart_h - h, bar_w, h)}">{title}</path>'
            )
        if total > 0:
            parts.append(
                f'<text class="tick" x="{x + bar_w / 2:.1f}" y="{top + chart_h - h - 6:.1f}" text-anchor="middle" style="fill:var(--ink)">{"&lt;$1" if total < 0.5 else f"${total:.0f}"}</text>'
            )
    parts.append(
        f'<line class="cap-line" x1="{left}" x2="{width - 8}" y1="{y_at(DAILY_CAP):.1f}" y2="{y_at(DAILY_CAP):.1f}"><title>Daily cap: ${DAILY_CAP:.2f} (${MONTHLY_TARGET} / 30)</title></line>'
    )
    return f'<svg viewBox="0 0 {width} {top + chart_h + 40}" role="img" aria-label="Daily spend">{"".join(parts)}</svg>'


def render_month_filter(days, totals):
    """Pills that limit the day table to one month, newest first; the newest month starts selected."""
    month_totals = {}
    for d, total in zip(days, totals):
        month_totals[f"{d:%Y-%m}"] = month_totals.get(f"{d:%Y-%m}", 0.0) + total
    pills = [
        f'<button class="pill" data-month="{month}" aria-pressed="{"true" if i == 0 else "false"}">'
        f'<span class="pill-name">{date.fromisoformat(month + "-01"):%b %Y}</span><span class="pill-total">${total:,.0f}</span></button>'
        for i, (month, total) in enumerate(sorted(month_totals.items(), reverse=True))
    ]
    pills.append(
        f'<button class="pill" data-month="all" aria-pressed="false"><span class="pill-name">All</span>'
        f'<span class="pill-total">${sum(totals):,.0f}</span></button>'
    )
    return (
        '<div class="filter-bar"><div class="pills" role="group" aria-label="Filter days by month">'
        + "".join(pills)
        + '</div><button class="toggle-all" id="expand-all" type="button">Expand all</button></div>'
        '<p class="month-summary" id="month-summary"></p>'
    )


def render_summary(days, totals, today):
    """Stat card at the top of the page; pills pick current month, prior month, or all time."""
    scopes = []
    if today:
        this_month = (today.year, today.month)
        prior_last = today.replace(day=1) - timedelta(days=1)
        for key, label, month in (
            ("current", "Current month", this_month),
            ("prior", "Prior month", (prior_last.year, prior_last.month)),
        ):
            picked = [
                (d, t) for d, t in zip(days, totals) if (d.year, d.month) == month
            ]
            if picked:
                scopes.append((key, label, picked))
    scopes.append(("all", "All time", list(zip(days, totals))))
    pills, panels = [], []
    for i, (key, label, picked) in enumerate(scopes):
        selected = i == 0
        spent = sum(t for _, t in picked)
        stats = "".join(
            f'<div class="stat"><div class="label">{name}</div><div class="value">{value}</div></div>'
            for name, value in (
                ("Total spend", f"${spent:,.2f}"),
                ("Days", str(len(picked))),
                ("Average per day", f"${spent / len(picked):.2f}"),
            )
        )
        pills.append(
            f'<button class="scope-pill" data-scope="{key}" aria-pressed="{"true" if selected else "false"}">{label}</button>'
        )
        panels.append(
            f'<div class="summary-panel" data-scope="{key}"{"" if selected else " hidden"}><div class="stats">{stats}</div>'
            f'<p class="summary-range">{picked[0][0]} to {picked[-1][0]}. Estimated from token counts at list prices.</p></div>'
        )
    return (
        '<section class="summary card"><div class="pills" role="group" aria-label="Summarize spend for">'
        + "".join(pills)
        + "</div>"
        + "".join(panels)
        + "</section>"
    )


def render_report(state, start, end, today=None, weights=None):
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    totals = [state["days"].get(str(d), {}).get("total", 0.0) for d in days]
    rows = []
    for d, total in reversed(list(zip(days, totals))):
        sessions = sorted(
            state["days"].get(str(d), {}).get("sessions", {}).items(),
            key=lambda kv: -kv[1],
        )
        average = f"${total / len(sessions):.2f}" if sessions else "-"
        toggle = (
            f'<button class="toggle" aria-expanded="false" data-day="{d}">Sessions</button>'
            if sessions
            else ""
        )
        month = f"{d:%Y-%m}"
        rows.append(
            f'<tr data-month="{month}"><td>{d}</td><td>${total:.2f}</td><td>{len(sessions)}</td><td>{average}</td><td>{toggle}</td></tr>'
        )
        if sessions:
            info = state.get("session_info", {})
            biggest = sessions[0][1] or 0.01
            header = (
                '<div class="session head"><span>Session</span><span></span><span>Duration</span>'
                "<span>Active</span><span>Idle</span><span>Miss %</span><span>Cost</span>"
                "<span>Start</span><span>End</span><span>Cost/min</span></div>"
            )
            items = header + "".join(
                session_row(sid, cost, biggest, info.get(sid, {}))
                for sid, cost in sessions
            )
            rows.append(
                f'<tr class="detail" data-month="{month}" id="detail-{d}" hidden><td colspan="5"><div class="panel">{items}</div></td></tr>'
            )

    if today and weights is None:
        raise ValueError("weights are required to render trend charts")
    trend_charts = render_trend_charts(state, today, weights) if today else ""
    summary = render_summary(days, totals, today)
    month_filter = render_month_filter(days, totals)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Claude Code Spend</title>
<style>{REPORT_CSS}</style></head><body><main>
<h1>Claude Code spend</h1>
{summary}
<div class="charts">{trend_charts}</div>
<section class="daily card"><h2>Daily spend</h2>{render_daily_bars(days, totals)}</section>
{month_filter}
<div class="table-card card"><table><tr><th>Day</th><th>Total</th><th>Sessions</th><th>Avg $/session</th><th></th></tr>{"".join(rows)}</table></div>
<script>{REPORT_JS}</script>
</main></body></html>"""


def notify(message):
    subprocess.run(
        [
            "osascript",
            "-e",
            f'display notification {json.dumps(message)} with title "Claude Code spend"',
        ],
        check=True,
    )


def main():
    today = date.today()
    state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else empty_state()
    since = (
        date.fromisoformat(sys.argv[1])
        if len(sys.argv) > 1
        else collection_start(state, today)
    )
    merge(state, run_ccusage("daily", since), run_ccusage("session", since))
    update_session_info(
        state, {sid for day in state["days"].values() for sid in day["sessions"]}
    )
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True))

    start, end = report_range(state, today)
    REPORT_PATH.write_text(
        render_report(state, start, end, today, load_weights(WEIGHTS_PATH))
    )
    total = sum(
        state["days"].get(str(start + timedelta(days=i)), {}).get("total", 0.0)
        for i in range((end - start).days + 1)
    )
    yesterday = state["days"].get(str(end), {}).get("total", 0.0)
    notify(
        f"Yesterday ${yesterday:.2f}. {start} to {end}: ${total:.2f}. Open {REPORT_PATH}"
    )


if __name__ == "__main__":
    sys.exit(main())
