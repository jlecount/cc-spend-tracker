import unittest
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
