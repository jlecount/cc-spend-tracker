import subprocess
import unittest
from datetime import datetime
from pathlib import Path

import spend_hourly as sh

HERE = Path(__file__).resolve().parent


class ParseHoursTest(unittest.TestCase):
    def test_maps_ccost_labels_to_hour_costs(self):
        payload = {"data": [
            {"label": "2026-09-21 12:00", "totalCost": 0.5},
            {"label": "2026-09-21 14:00", "totalCost": 1.25},
        ]}
        self.assertEqual(sh.parse_hours(payload), {12: 0.5, 14: 1.25})


class CompletedHoursTest(unittest.TestCase):
    def test_drops_in_progress_hour_and_fills_idle_hours_with_zero(self):
        costs = {9: 1.0, 11: 2.0, 13: 9.0}
        now = datetime(2026, 9, 21, 13, 40)
        rows = sh.completed_hours(costs, now)
        self.assertEqual([hour for hour, _ in rows], list(range(0, 13)))
        self.assertEqual(dict(rows)[9], 1.0)
        self.assertEqual(dict(rows)[10], 0.0)
        self.assertNotIn(13, dict(rows))

    def test_no_completed_hours_just_after_midnight(self):
        self.assertEqual(sh.completed_hours({0: 3.0}, datetime(2026, 9, 21, 0, 20)), [])


class RenderTest(unittest.TestCase):
    def test_lists_each_completed_hour_with_range_label_and_total(self):
        page = sh.render_hourly_report([(8, 1.0), (9, 2.5)], datetime(2026, 9, 21, 10, 5))
        self.assertIn("08:00-09:00", page)
        self.assertIn("09:00-10:00", page)
        self.assertIn("$3.50", page)
        self.assertNotIn("10:00-11:00", page)

    def test_says_so_when_no_hour_has_completed(self):
        page = sh.render_hourly_report([], datetime(2026, 9, 21, 0, 20))
        self.assertIn("No completed hours yet", page)


class InstallHourlyArgsTest(unittest.TestCase):
    def run_install(self, *args):
        return subprocess.run(["bash", str(HERE / "install_hourly.sh"), *args], capture_output=True, text=True)

    def test_rejects_missing_or_invalid_hours(self):
        for args in [(), ("0",), ("25",), ("abc",), ("1.5",), ("-2",)]:
            result = self.run_install(*args)
            self.assertNotEqual(result.returncode, 0, args)
            self.assertIn("usage", result.stderr.lower(), args)


if __name__ == "__main__":
    unittest.main()
