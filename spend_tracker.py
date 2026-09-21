"""Daily Claude Code spend tracker.

Usage: spend_tracker.py [YYYY-MM-DD]  (optional date backfills from that day)

Collects cost from ccusage into spend.json, writes report.html covering the
start of the month (or first record, if earlier) through yesterday, and sends a
macOS notification. Costs are ccusage estimates (token counts x list prices).
"""
import html
import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE_PATH = HERE / "spend.json"
REPORT_PATH = HERE / "report.html"
PROJECTS_DIR = Path.home() / ".claude" / "projects"


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
        last_active = datetime.fromisoformat(row["metadata"]["lastActivity"].replace("Z", "+00:00"))
        day = state["days"].setdefault(str(last_active.astimezone().date()), {"total": 0.0, "sessions": {}})
        day["sessions"][session_id] = day["sessions"].get(session_id, 0.0) + delta


def read_session_info(log_path):
    """Title Claude Code recorded for the session (last ai-title), else the first typed prompt."""
    title, first_prompt, project, started, ended = None, None, None, None, None
    for line in Path(log_path).read_text().splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        timestamp = entry.get("timestamp")
        if isinstance(timestamp, str):
            started = min(started or timestamp, timestamp)
            ended = max(ended or timestamp, timestamp)
        if entry.get("type") == "ai-title":
            title = entry["aiTitle"]
        elif entry.get("type") == "user":
            project = project or (Path(entry["cwd"]).name if entry.get("cwd") else None)
            content = entry.get("message", {}).get("content")
            if first_prompt is None and isinstance(content, str):
                first_prompt = content.strip().replace("\n", " ")[:80]
    return {"title": title or first_prompt or "", "project": project or "", "started": started, "ended": ended}


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
    return f"{started:%b %d %H:%M}", f"{ended:%b %d %H:%M}", (ended - started).total_seconds() / 60


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
        ["npx", "--yes", "ccusage@latest", subcommand, "--json", "--since", since.strftime("%Y%m%d")],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)[subcommand]


def session_row(session_id, cost, biggest, info):
    start_text, end_text, minutes = describe_span(info)
    per_minute = f"${cost / minutes:.3f}" if minutes >= 1 else "-"
    return (
        f'<div class="session"><div class="what"><span class="title">{html.escape(info.get("title") or "(no title)")}</span>'
        f'<code>{html.escape(info.get("project", ""))} &middot; {html.escape(session_id)}</code></div>'
        f'<span class="bar"><i style="width:{cost / biggest * 100:.1f}%"></i></span>'
        f'<span class="duration">{format_duration(minutes * 60) if minutes else ""}</span>'
        f'<span class="cost">${cost:.2f}</span>'
        f'<span class="start">{start_text}</span><span class="end">{end_text}</span>'
        f'<span class="per-minute">{per_minute}</span></div>'
    )


def render_report(state, start, end):
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    totals = [state["days"].get(str(d), {}).get("total", 0.0) for d in days]
    peak = max(totals + [0.01])
    bar_w, chart_h = 28, 200
    svg_bars = []
    for i, (d, total) in enumerate(zip(days, totals)):
        h = total / peak * chart_h
        x = 10 + i * (bar_w + 4)
        svg_bars.append(
            f'<rect x="{x}" y="{chart_h - h + 20}" width="{bar_w}" height="{h:.1f}" fill="#4f7cff">'
            f"<title>{d}: ${total:.2f}</title></rect>"
            f'<text x="{x + bar_w / 2}" y="{chart_h + 36}" font-size="10" text-anchor="middle" fill="currentColor">{d.day}</text>'
            f'<text x="{x + bar_w / 2}" y="{chart_h - h + 15}" font-size="9" text-anchor="middle" fill="currentColor">{total:.0f}</text>'
        )
    width = 20 + len(days) * (bar_w + 4)

    rows = []
    for d, total in reversed(list(zip(days, totals))):
        sessions = sorted(state["days"].get(str(d), {}).get("sessions", {}).items(), key=lambda kv: -kv[1])
        average = f"${total / len(sessions):.2f}" if sessions else "-"
        toggle = f'<button class="toggle" aria-expanded="false" data-day="{d}">Sessions</button>' if sessions else ""
        rows.append(
            f"<tr><td>{d}</td><td>${total:.2f}</td><td>{len(sessions)}</td><td>{average}</td><td>{toggle}</td></tr>"
        )
        if sessions:
            info = state.get("session_info", {})
            biggest = sessions[0][1] or 0.01
            header = (
                '<div class="session head"><span>Session</span><span></span><span>Duration</span><span>Cost</span>'
                "<span>Start</span><span>End</span><span>Cost/min</span></div>"
            )
            items = header + "".join(session_row(sid, cost, biggest, info.get(sid, {})) for sid, cost in sessions)
            rows.append(f'<tr class="detail" id="detail-{d}" hidden><td colspan="5"><div class="panel">{items}</div></td></tr>')

    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Claude Code Spend</title>
<style>
:root {{ color-scheme: light dark; --line: #8884; --accent: #4f7cff; --muted: #8888; }}
body {{ font: 14px system-ui; margin: 24px; max-width: 1150px; }}
table {{ border-collapse: collapse; width: 100%; }}
td, th {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line); }}
.toggle {{ font: inherit; color: var(--accent); background: none; border: 1px solid var(--line);
  border-radius: 6px; padding: 2px 10px; cursor: pointer; }}
.toggle::after {{ content: " \\25BE"; }}
.toggle[aria-expanded="true"]::after {{ content: " \\25B4"; }}
.toggle:hover {{ border-color: var(--accent); }}
.detail td {{ padding: 0; border-bottom: 1px solid var(--line); }}
.panel {{ margin: 6px 8px 12px; padding: 8px 12px; max-height: 320px; overflow-y: auto;
  border: 1px solid var(--line); border-radius: 8px; background: #8881; box-shadow: 0 4px 12px #0002; }}
.session {{ display: grid; grid-template-columns: minmax(0, 1fr) 90px 4.5em 4.5em 8em 8em 5.5em; gap: 12px; align-items: center; padding: 3px 0; }}
.session .what {{ display: flex; flex-direction: column; min-width: 0; }}
.session .title {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.session code {{ font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); }}
.session .bar {{ height: 8px; border-radius: 4px; background: var(--line); }}
.session .bar i {{ display: block; height: 100%; border-radius: 4px; background: var(--accent); }}
.session .start, .session .end, .session .per-minute, .session .duration {{ text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }}
.session.head {{ font-size: 12px; color: var(--muted); border-bottom: 1px solid var(--line); }}
.session.head span:nth-child(n+3) {{ text-align: right; }}
.session .duration {{ text-align: right; color: var(--muted); font-variant-numeric: tabular-nums; }}
.session .cost {{ text-align: right; font-variant-numeric: tabular-nums; }}
</style></head><body>
<h1>Claude Code spend: {start} to {end}</h1>
<p>Total <b>${sum(totals):.2f}</b> over {len(days)} days, average ${sum(totals) / len(days):.2f}/day.
Estimated from token counts at list prices.</p>
<svg width="{width}" height="{chart_h + 45}" style="max-width:100%">{"".join(svg_bars)}</svg>
<table><tr><th>Day</th><th>Total</th><th>Sessions</th><th>Avg $/session</th><th></th></tr>{"".join(rows)}</table>
<script>
document.querySelectorAll(".toggle").forEach(function (button) {{
  button.addEventListener("click", function () {{
    var row = document.getElementById("detail-" + button.dataset.day);
    row.hidden = !row.hidden;
    button.setAttribute("aria-expanded", String(!row.hidden));
  }});
}});
</script>
</body></html>"""


def notify(message):
    subprocess.run(
        ["osascript", "-e", f'display notification {json.dumps(message)} with title "Claude Code spend"'],
        check=True,
    )


def main():
    today = date.today()
    state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else empty_state()
    since = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else collection_start(state, today)
    merge(state, run_ccusage("daily", since), run_ccusage("session", since))
    update_session_info(state, {sid for day in state["days"].values() for sid in day["sessions"]})
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True))

    start, end = report_range(state, today)
    REPORT_PATH.write_text(render_report(state, start, end))
    total = sum(state["days"].get(str(start + timedelta(days=i)), {}).get("total", 0.0) for i in range((end - start).days + 1))
    yesterday = state["days"].get(str(end), {}).get("total", 0.0)
    notify(f"Yesterday ${yesterday:.2f}. {start} to {end}: ${total:.2f}. Open {REPORT_PATH}")


if __name__ == "__main__":
    sys.exit(main())
