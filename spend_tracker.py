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


def empty_state():
    return {"days": {}, "session_cumulative": {}}


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
        detail = ", ".join(f"{html.escape(sid[:8])} ${cost:.2f}" for sid, cost in sessions[:5])
        rows.append(f"<tr><td>{d}</td><td>${total:.2f}</td><td>{detail}</td></tr>")

    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Claude Code Spend</title>
<style>
:root {{ color-scheme: light dark; }}
body {{ font: 14px system-ui; margin: 24px; max-width: 900px; }}
table {{ border-collapse: collapse; width: 100%; }}
td, th {{ text-align: left; padding: 4px 8px; border-bottom: 1px solid #8884; }}
</style></head><body>
<h1>Claude Code spend: {start} to {end}</h1>
<p>Total <b>${sum(totals):.2f}</b> over {len(days)} days, average ${sum(totals) / len(days):.2f}/day.
Estimated from token counts at list prices.</p>
<svg width="{width}" height="{chart_h + 45}" style="max-width:100%">{"".join(svg_bars)}</svg>
<table><tr><th>Day</th><th>Total</th><th>Top sessions (id, cost)</th></tr>{"".join(rows)}</table>
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
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True))

    start, end = report_range(state, today)
    REPORT_PATH.write_text(render_report(state, start, end))
    total = sum(state["days"].get(str(start + timedelta(days=i)), {}).get("total", 0.0) for i in range((end - start).days + 1))
    yesterday = state["days"].get(str(end), {}).get("total", 0.0)
    notify(f"Yesterday ${yesterday:.2f}. {start} to {end}: ${total:.2f}. Open {REPORT_PATH}")


if __name__ == "__main__":
    sys.exit(main())
