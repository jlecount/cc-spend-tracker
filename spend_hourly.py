"""Intra-day Claude Code spend report by completed hour.

Usage: spend_hourly.py

Reads today's hourly cost from ccost and writes report_hourly.html with one row
per completed local hour since midnight. The hour in progress is left out.
Costs are estimates (token counts x live list prices) and can differ from the
daily report, which uses ccusage.
"""
import html
import json
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPORT_PATH = HERE / "report_hourly.html"
LOCALTIME_LINK = Path("/etc/localtime")


def local_timezone_name():
    target = str(LOCALTIME_LINK.resolve())
    if "/zoneinfo/" not in target:
        raise RuntimeError(f"cannot find the IANA timezone name from {LOCALTIME_LINK} -> {target}")
    return target.split("/zoneinfo/", 1)[1]


def run_ccost(day):
    """ccost only writes JSON to a file. Its bundled price list has no cache-token prices, hence --live-pricing."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "hourly.json"
        subprocess.run(
            ["npx", "--yes", "ccost@0.5.0", "--per", "hour", "--from", str(day), "--tz", local_timezone_name(),
             "--live-pricing", "--output", "json", "--filename", str(out)],
            capture_output=True, text=True, check=True,
        )
        return json.loads(out.read_text())


def parse_hours(payload):
    """Maps hour of day (0-23) to cost. Labels look like '2026-09-21 12:00'."""
    return {int(row["label"].split(" ")[1].split(":")[0]): row["totalCost"] for row in payload["data"]}


def completed_hours(costs, now):
    """(hour, cost) from midnight through the last finished hour; idle hours are 0."""
    return [(hour, costs.get(hour, 0.0)) for hour in range(now.hour)]


def render_hourly_report(rows, now):
    total = sum(cost for _, cost in rows)
    peak = max([cost for _, cost in rows] + [0.01])
    body = "".join(
        f'<tr><td>{hour:02d}:00-{hour + 1:02d}:00</td><td>${cost:.2f}</td>'
        f'<td><span class="bar"><i style="width:{cost / peak * 100:.1f}%"></i></span></td></tr>'
        for hour, cost in rows
    )
    table = (
        f"<table><tr><th>Hour</th><th>Cost</th><th></th></tr>{body}</table>"
        if rows else "<p>No completed hours yet today.</p>"
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Claude Code Spend by Hour</title>
<style>
:root {{ color-scheme: light dark; --line: #8884; --accent: #4f7cff; }}
body {{ font: 14px system-ui; margin: 24px; max-width: 700px; }}
table {{ border-collapse: collapse; width: 100%; }}
td, th {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line); }}
td:nth-child(2) {{ font-variant-numeric: tabular-nums; }}
.bar {{ display: block; height: 8px; border-radius: 4px; background: var(--line); }}
.bar i {{ display: block; height: 100%; border-radius: 4px; background: var(--accent); }}
</style></head><body>
<h1>Claude Code spend by hour, {now:%Y-%m-%d}</h1>
<p>Completed hours only, as of {now:%H:%M}. Total <b>${total:.2f}</b>.
Estimated from token counts at list prices; can differ from the daily report.</p>
{table}
</body></html>"""


def main():
    now = datetime.now().astimezone()
    payload = run_ccost(now.date())
    rows = completed_hours(parse_hours(payload), now)
    REPORT_PATH.write_text(render_hourly_report(rows, now))
    print(f"wrote {REPORT_PATH}: {len(rows)} completed hours, ${sum(c for _, c in rows):.2f}")


if __name__ == "__main__":
    sys.exit(main())
