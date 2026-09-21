import json
import tempfile
import unittest
from pathlib import Path
from datetime import date

import spend_tracker as st


class MergeTest(unittest.TestCase):
    def test_daily_totals_overwrite_and_session_deltas_accumulate(self):
        state = st.empty_state()
        daily = [{"period": "2026-09-20", "totalCost": 2.0}, {"period": "2026-09-21", "totalCost": 1.0}]
        sessions = [{"period": "s1", "totalCost": 1.5, "metadata": {"lastActivity": "2026-09-21T15:00:00Z"}}]
        st.merge(state, daily, sessions)
        self.assertEqual(state["days"]["2026-09-21"]["sessions"], {"s1": 1.5})

        sessions = [{"period": "s1", "totalCost": 2.0, "metadata": {"lastActivity": "2026-09-21T16:00:00Z"}}]
        st.merge(state, daily, sessions)
        self.assertAlmostEqual(state["days"]["2026-09-21"]["sessions"]["s1"], 2.0)
        self.assertEqual(state["days"]["2026-09-20"]["total"], 2.0)


class RenderReportTest(unittest.TestCase):
    def test_lists_every_session_and_shows_session_count(self):
        sessions = {f"session{i:02d}-xxxx": 1.0 + i for i in range(8)}
        state = {"days": {"2026-09-20": {"total": 44.0, "sessions": sessions}}}
        page = st.render_report(state, date(2026, 9, 20), date(2026, 9, 20))
        self.assertIn("<th>Sessions</th>", page)
        self.assertIn("<td>8</td>", page)
        self.assertIn("<th>Avg $/session</th>", page)
        self.assertIn("<td>$5.50</td>", page)
        for sid in sessions:
            self.assertIn(sid, page)


class SessionInfoTest(unittest.TestCase):
    def write_log(self, entries):
        path = Path(tempfile.mkdtemp()) / "abc.jsonl"
        path.write_text("\n".join(json.dumps(e) for e in entries))
        return path

    def test_uses_last_ai_title_and_project_folder(self):
        path = self.write_log([
            {"type": "user", "cwd": "/Users/x/code/proj", "message": {"content": "first prompt"}},
            {"type": "ai-title", "aiTitle": "Old title"},
            {"type": "ai-title", "aiTitle": "Final title"},
        ])
        info = st.read_session_info(path)
        self.assertEqual((info["title"], info["project"]), ("Final title", "proj"))

    def test_records_first_and_last_timestamp(self):
        path = self.write_log([
            {"type": "user", "timestamp": "2026-09-16T14:00:00.000Z", "message": {"content": "hi"}},
            {"type": "assistant", "timestamp": "2026-09-16T16:45:30.000Z"},
            {"type": "ai-title", "aiTitle": "t"},
        ])
        info = st.read_session_info(path)
        self.assertEqual((info["started"], info["ended"]), ("2026-09-16T14:00:00.000Z", "2026-09-16T16:45:30.000Z"))

    def test_format_duration(self):
        self.assertEqual(st.format_duration(30), "<1m")
        self.assertEqual(st.format_duration(12 * 60), "12m")
        self.assertEqual(st.format_duration(2 * 3600 + 45 * 60), "2h 45m")

    def test_falls_back_to_first_typed_prompt(self):
        path = self.write_log([{"type": "user", "cwd": "/a/b", "message": {"content": "fix the thing " * 20}}])
        info = st.read_session_info(path)
        self.assertTrue(info["title"].startswith("fix the thing"))
        self.assertLessEqual(len(info["title"]), 81)

    def test_report_shows_title_and_project(self):
        state = {
            "days": {"2026-09-20": {"total": 1.0, "sessions": {"abc": 1.0}}},
            "session_info": {"abc": {
                "title": "Fix <b>bug", "project": "proj",
                "started": "2026-09-20T14:00:00.000Z", "ended": "2026-09-20T15:30:00.000Z",
            }},
        }
        page = st.render_report(state, date(2026, 9, 20), date(2026, 9, 20))
        self.assertIn("Fix &lt;b&gt;bug", page)
        self.assertIn("proj", page)
        self.assertIn("1h 30m", page)
        self.assertIn("<span>Cost/min</span>", page)
        self.assertIn('<span class="per-minute">$0.011</span>', page)
        self.assertRegex(page, r'<span class="start">Sep 20 \d\d:\d\d</span>')
        self.assertRegex(page, r'<span class="end">Sep 20 \d\d:\d\d</span>')


class ReportRangeTest(unittest.TestCase):
    def test_starts_at_month_start_and_ends_yesterday(self):
        state = {"days": {"2026-09-05": {"total": 1, "sessions": {}}}}
        self.assertEqual(st.report_range(state, date(2026, 9, 21)), (date(2026, 9, 1), date(2026, 9, 20)))

    def test_earlier_record_extends_start(self):
        state = {"days": {"2026-08-28": {"total": 1, "sessions": {}}}}
        self.assertEqual(st.report_range(state, date(2026, 9, 21))[0], date(2026, 8, 28))

    def test_first_of_month_reports_through_yesterday_of_prior_month(self):
        state = {"days": {"2026-08-30": {"total": 1, "sessions": {}}}}
        self.assertEqual(st.report_range(state, date(2026, 9, 1)), (date(2026, 8, 30), date(2026, 8, 31)))


if __name__ == "__main__":
    unittest.main()
