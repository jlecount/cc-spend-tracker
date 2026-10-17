import json
import tempfile
import unittest
from pathlib import Path
from datetime import date

import daily_tracker as st

# Monday first.
WEIGHTS = [1.0, 1.0, 1.0, 1.0, 0.75, 0.5, 0.5]


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


class ModelNameTest(unittest.TestCase):
    def test_formats_model_ids(self):
        self.assertEqual(st.format_model("claude-sonnet-5"), "Sonnet 5")
        self.assertEqual(st.format_model("claude-sonnet-5-5"), "Sonnet 5.5")
        self.assertEqual(st.format_model("claude-sonnet-4-5-20250929"), "Sonnet 4.5")
        self.assertEqual(st.format_model("claude-haiku-4-5-20251001"), "Haiku 4.5")
        self.assertEqual(st.format_model("sonnet"), "sonnet")


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

    def test_computes_activity_stats_from_all_timestamps(self):
        path = self.write_log([
            {"type": "user", "timestamp": "2026-09-16T14:00:00.000Z", "message": {"content": "hi"}},
            {"type": "assistant", "timestamp": "2026-09-16T14:04:00.000Z"},
            {"type": "assistant", "timestamp": "2026-09-16T14:10:00.000Z"},
        ])
        info = st.read_session_info(path)
        self.assertAlmostEqual(info["active_seconds"], 4 * 60)
        self.assertAlmostEqual(info["idle_seconds"], 6 * 60)
        self.assertEqual(info["gap_count"], 2)
        self.assertEqual(info["cache_misses"], 1)

    def test_format_duration(self):
        self.assertEqual(st.format_duration(30), "<1m")
        self.assertEqual(st.format_duration(12 * 60), "12m")
        self.assertEqual(st.format_duration(2 * 3600 + 45 * 60), "2h 45m")

    def test_activity_stats_splits_gaps_at_the_cache_ttl(self):
        # gaps: 4min (active), 6min (idle, >= 5min ttl), 1min (active)
        timestamps = [
            "2026-09-16T14:00:00.000Z",
            "2026-09-16T14:04:00.000Z",
            "2026-09-16T14:10:00.000Z",
            "2026-09-16T14:11:00.000Z",
        ]
        stats = st.activity_stats(timestamps, ttl_seconds=300)
        self.assertAlmostEqual(stats["active_seconds"], 5 * 60)
        self.assertAlmostEqual(stats["idle_seconds"], 6 * 60)
        self.assertEqual(stats["gap_count"], 3)
        self.assertEqual(stats["cache_misses"], 1)

    def test_activity_stats_handles_no_or_single_timestamp(self):
        self.assertEqual(st.activity_stats([], ttl_seconds=300),
                          {"active_seconds": 0.0, "idle_seconds": 0.0, "gap_count": 0, "cache_misses": 0})
        self.assertEqual(st.activity_stats(["2026-09-16T14:00:00.000Z"], ttl_seconds=300),
                          {"active_seconds": 0.0, "idle_seconds": 0.0, "gap_count": 0, "cache_misses": 0})

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
                "active_seconds": 20 * 60, "idle_seconds": 70 * 60, "gap_count": 4, "cache_misses": 1,
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
        self.assertIn("<span>Active</span>", page)
        self.assertIn("<span>Idle</span>", page)
        self.assertIn("<span>Miss %</span>", page)
        self.assertIn('<span class="active">20m</span>', page)
        self.assertIn('<span class="idle">1h 10m</span>', page)
        self.assertIn('<span class="miss">25%</span>', page)


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


class SessionModelTest(unittest.TestCase):
    def write_log(self, entries):
        path = Path(tempfile.mkdtemp()) / "abc.jsonl"
        path.write_text("\n".join(json.dumps(e) for e in entries))
        return path

    def test_records_models_most_used_first_and_skips_synthetic(self):
        path = self.write_log([
            {"type": "assistant", "message": {"model": "claude-opus-5"}},
            {"type": "assistant", "message": {"model": "claude-sonnet-5"}},
            {"type": "assistant", "message": {"model": "claude-sonnet-5"}},
            {"type": "assistant", "message": {"model": "<synthetic>"}},
        ])
        self.assertEqual(st.read_session_info(path)["models"], ["claude-sonnet-5", "claude-opus-5"])

    def test_session_row_shows_models_after_session_id(self):
        info = {"title": "T", "project": "d1", "models": ["claude-sonnet-5", "claude-opus-5-5"]}
        row = st.session_row("sid-1", 1.0, 1.0, info)
        self.assertIn("d1 &middot; sid-1, Sonnet 5, Opus 5.5</code>", row)

    def test_session_row_without_models_is_unchanged(self):
        row = st.session_row("sid-1", 1.0, 1.0, {"project": "d1"})
        self.assertIn("d1 &middot; sid-1</code>", row)


class TrendTest(unittest.TestCase):
    def state(self, costs):
        return {"days": {f"2026-10-{d:02d}": {"total": c, "sessions": {}} for d, c in costs.items()}}

    def test_cumulative_series_fills_missing_days_with_zero(self):
        state = self.state({1: 1.0, 3: 2.0})
        self.assertEqual(st.cumulative_series(state, date(2026, 10, 1), date(2026, 10, 4)), [1.0, 1.0, 3.0, 3.0])

    def test_projections_scale_rate_by_weekday_weight(self):
        # Oct 1 2026 is a Thursday. Weights: Mon-Thu 1.0, Fri 0.75, Sat/Sun 0.5.
        state = self.state({1: 2.0, 2: 1.5, 3: 1.0, 4: 1.0, 5: 2.0, 6: 2.0, 7: 2.0, 8: 4.0})
        proj = st.project_month(state, date(2026, 10, 9), days_in_month=31, weights=WEIGHTS)
        self.assertEqual(proj["actual"][-1], 15.5)
        self.assertEqual(len(proj["daily"]), 31 - 7)  # starts at last actual day
        # daily amount = Oct 8 (Thu, weight 1.0) cost 4.0 * 5.75/7 on each of the 23 remaining days
        self.assertAlmostEqual(proj["daily"][1], 15.5 + 4.0 * 5.75 / 7)
        self.assertAlmostEqual(proj["daily"][-1], 15.5 + 4.0 * 5.75 / 7 * 23)
        # weekly rate = cost of Oct 2..8 (13.5) / their weights (5.75)
        self.assertAlmostEqual(proj["weekly"][-1], 15.5 + 13.5 / 5.75 * 18.5)

    def test_daily_amount_is_divided_by_yesterdays_weekday_weight(self):
        # Oct 4 2026 is a Sunday (weight 0.5): 1.0 spent -> 2.0 normalized -> 2.0 * 5.75/7 per day
        proj = st.project_month(self.state({4: 1.0}), date(2026, 10, 5), days_in_month=31, weights=WEIGHTS)
        self.assertAlmostEqual(proj["daily"][1], 1.0 + 2.0 * 5.75 / 7)

    def test_weekly_window_uses_available_days_early_in_month(self):
        state = self.state({1: 2.0, 2: 3.0})
        proj = st.project_month(state, date(2026, 10, 3), days_in_month=31, weights=WEIGHTS)
        # Oct 1 Thu (1.0) + Oct 2 Fri (0.75); Oct 3..31 carries 23.5 full-weight days
        self.assertAlmostEqual(proj["weekly"][-1], 5.0 + 5.0 / 1.75 * 23.5)

    def test_no_completed_days_returns_none(self):
        self.assertIsNone(st.project_month(self.state({}), date(2026, 10, 1), days_in_month=31, weights=WEIGHTS))

    def test_report_raises_when_last_month_has_no_records(self):
        state = self.state({1: 1.0})
        with self.assertRaises(ValueError):
            st.render_report(state, date(2026, 10, 1), date(2026, 10, 1), today=date(2026, 10, 2), weights=WEIGHTS)

    def test_report_includes_named_trend_lines(self):
        state = self.state({1: 1.0, 2: 1.0})
        state["days"]["2026-09-15"] = {"total": 5.0, "sessions": {}}
        page = st.render_report(state, date(2026, 9, 15), date(2026, 10, 2), today=date(2026, 10, 3), weights=WEIGHTS)
        for label in ("daily trend projection", "weekly trend projection", "actual spend"):
            self.assertIn(label, page)


class ThresholdBandsTest(unittest.TestCase):
    def chart(self, values):
        return st.render_line_chart("t", [("actual spend", "#4f7cff", False, 1, values)], 5, "c1")

    def test_draws_threshold_lines_and_shaded_bands_when_line_exceeds_them(self):
        page = self.chart([100.0, 250.0, 450.0, 500.0, 520.0])
        self.assertIn(">$200 threshold<", page)
        self.assertIn(">$400 threshold<", page)
        self.assertIn('fill="#f5c451"', page)  # yellow, $200-$400
        self.assertIn('fill="#f08ab4"', page)  # pink, above $400

    def test_omits_bands_the_line_never_reaches(self):
        page = self.chart([10.0, 20.0, 30.0, 40.0, 50.0])
        self.assertNotIn("threshold", page)
        self.assertNotIn("#f5c451", page)

    def test_clip_ids_are_unique_per_chart(self):
        other = st.render_line_chart("t", [("a", "#000", False, 1, [500.0] * 5)], 5, "c2")
        self.assertIn('id="c2-band-200"', other)
        self.assertNotIn("c1-band", other)


class TodayMarkerTest(unittest.TestCase):
    def test_current_month_chart_marks_today_as_mm_dd(self):
        state = {"days": {"2026-09-15": {"total": 5.0, "sessions": {}}, "2026-10-01": {"total": 1.0, "sessions": {}}}}
        page = st.render_trend_charts(state, date(2026, 10, 5), WEIGHTS)
        self.assertEqual(page.count(">10/05<"), 1)

    def test_no_marker_without_one(self):
        page = st.render_line_chart("t", [("a", "#000", False, 1, [1.0] * 5)], 5, "c1")
        self.assertNotIn('class="today"', page)


class WeightsTest(unittest.TestCase):
    def write(self, content):
        path = Path(tempfile.mkdtemp()) / ".weights.json"
        path.write_text(content)
        return path

    def full(self, **overrides):
        weights = {"mon": 1, "tue": 1, "wed": 1, "thu": 1, "fri": 1, "sat": 0, "sun": 0}
        return json.dumps({**weights, **overrides})

    def test_loads_weights_monday_first(self):
        self.assertEqual(st.load_weights(self.write(self.full(fri=0.75))), [1, 1, 1, 1, 0.75, 0, 0])

    def test_missing_file_raises(self):
        with self.assertRaisesRegex(ValueError, "setup.sh"):
            st.load_weights(Path(tempfile.mkdtemp()) / ".weights.json")

    def test_missing_day_raises(self):
        content = json.loads(self.full())
        del content["wed"]
        with self.assertRaisesRegex(ValueError, "wed"):
            st.load_weights(self.write(json.dumps(content)))

    def test_unknown_day_raises(self):
        with self.assertRaisesRegex(ValueError, "funday"):
            st.load_weights(self.write(self.full(funday=1)))

    def test_negative_or_non_number_raises(self):
        for bad in (-0.5, "1.0", True, None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                st.load_weights(self.write(self.full(mon=bad)))

    def test_all_zero_raises(self):
        zeros = {d: 0 for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}
        with self.assertRaisesRegex(ValueError, "at least one"):
            st.load_weights(self.write(json.dumps(zeros)))

    def test_invalid_json_raises(self):
        with self.assertRaises(ValueError):
            st.load_weights(self.write("{not json"))


class ZeroWeightProjectionTest(unittest.TestCase):
    # Oct 2026: 1 Thu, 2 Fri, 3 Sat, 4 Sun, 5 Mon. Weekends weigh 0.
    WEEKDAYS_ONLY = [1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0]

    def state(self, costs):
        return {"days": {f"2026-10-{d:02d}": {"total": c, "sessions": {}} for d, c in costs.items()}}

    def test_zero_weight_days_add_nothing_and_their_spend_is_left_out_of_the_rate(self):
        state = self.state({1: 2.0, 2: 4.0, 3: 100.0, 4: 100.0})
        proj = st.project_month(state, date(2026, 10, 5), days_in_month=31, weights=self.WEEKDAYS_ONLY)
        # rate = (2+4)/2 = 3 per working day; Oct 5..31 has 20 weekdays; actuals total 206
        self.assertAlmostEqual(proj["weekly"][-1], 206.0 + 3.0 * 20)
        self.assertAlmostEqual(proj["weekly"][-1] - proj["weekly"][-2], 0.0)  # Oct 31 is a Saturday

    def test_daily_base_skips_back_over_zero_weight_days(self):
        state = self.state({1: 2.0, 2: 4.0, 3: 100.0, 4: 100.0})
        proj = st.project_month(state, date(2026, 10, 5), days_in_month=31, weights=self.WEEKDAYS_ONLY)
        # base = Fri Oct 2 (4.0); average weight = 5/7
        self.assertAlmostEqual(proj["daily"][1] - proj["daily"][0], 4.0 * 5 / 7)

    def test_no_working_day_completed_returns_none(self):
        # Nov 1 2026 is a Sunday
        state = {"days": {"2026-11-01": {"total": 5.0, "sessions": {}}}}
        self.assertIsNone(st.project_month(state, date(2026, 11, 2), days_in_month=30, weights=self.WEEKDAYS_ONLY))


class WeeklyWeightsLineTest(unittest.TestCase):
    def test_lists_each_day_with_its_weight_on_one_line(self):
        line = st.render_weekly_weights([1, 1, 1, 1, 0.5, 0.25, 0])
        self.assertEqual(line.count("\n"), 0)
        self.assertIn("Mon 1 &middot; Tue 1 &middot; Wed 1 &middot; Thu 1 &middot; Fri 0.5 &middot; Sat 0.25 &middot; Sun 0", line)

    def test_charts_start_with_the_weights_line(self):
        state = {"days": {"2026-09-15": {"total": 5.0, "sessions": {}}, "2026-10-01": {"total": 1.0, "sessions": {}}}}
        page = st.render_trend_charts(state, date(2026, 10, 5), WEIGHTS)
        self.assertLess(page.index("Weekly weights"), page.index("<figure"))


class TargetTest(unittest.TestCase):
    def test_over_target_states_reduction_range_of_remaining_spend(self):
        # actual 100: low 250 needs 50 of 150 remaining (34%); high 300 needs 100 of 200 (50%)
        text, color = st.target_message(actual=100.0, low=250.0, high=300.0, target=200.0)
        self.assertEqual(text, "Target reduction of use between apx 34% and 50% for the rest of the month in order to hit $200 by end of month")
        self.assertEqual(color, st.TOTAL_COLORS["yellow"])

    def test_reduction_rounds_up(self):
        text, _ = st.target_message(actual=0.0, low=227.0, high=227.0, target=200.0)  # 11.89%
        self.assertIn("between apx 12% and 12%", text)

    def test_low_projection_under_target_needs_no_reduction(self):
        text, _ = st.target_message(actual=100.0, low=190.0, high=300.0, target=200.0)
        self.assertIn("between apx 0% and 50%", text)

    def test_at_or_under_target_is_on_track_in_green(self):
        text, color = st.target_message(actual=100.0, low=150.0, high=200.0, target=200.0)
        self.assertEqual(text, "On track to complete month with $200 spend.")
        self.assertEqual(color, st.TOTAL_COLORS["green"])

    def test_already_past_target_cannot_be_fixed_by_reduction(self):
        text, color = st.target_message(actual=250.0, low=280.0, high=300.0, target=200.0)
        self.assertEqual(text, "Target of $200 already exceeded with $250 spent.")
        self.assertEqual(color, st.TOTAL_COLORS["yellow"])

    def test_current_month_chart_shows_target_for_the_higher_projection(self):
        state = {"days": {"2026-09-15": {"total": 250.0, "sessions": {}}, "2026-10-01": {"total": 50.0, "sessions": {}}}}
        page = st.render_trend_charts(state, date(2026, 10, 5), WEIGHTS)
        self.assertIn("in order to hit $200 by end of month", page)
        self.assertIn(f'class="target" style="color:{st.TOTAL_COLORS["yellow"]}"', page)


class MonthTotalTest(unittest.TestCase):
    def test_color_by_total(self):
        self.assertEqual(st.total_color(199.99), st.TOTAL_COLORS["green"])
        self.assertEqual(st.total_color(200), st.TOTAL_COLORS["yellow"])
        self.assertEqual(st.total_color(400), st.TOTAL_COLORS["yellow"])
        self.assertEqual(st.total_color(400.01), st.TOTAL_COLORS["red"])

    def test_charts_show_month_total_and_projection_range(self):
        state = {"days": {"2026-09-15": {"total": 250.0, "sessions": {}}}}
        for d in range(1, 5):
            state["days"][f"2026-10-{d:02d}"] = {"total": 10.0 if d < 4 else 50.0, "sessions": {}}
        page = st.render_trend_charts(state, date(2026, 10, 5), WEIGHTS)
        self.assertIn(">$250<", page)
        # actual 80 through Oct 4 (Thu-Sun, weights sum 2.75); Oct 5..31 carries 22.5 full-weight days
        # weekly rate 80/2.75 -> 80+80/2.75*22.5=734.55; daily: 50/0.5*5.75/7=82.14 -> 80+82.14*27=2298
        self.assertIn(">$735 &rarr; $2298<", page)
        self.assertIn(f"color:{st.TOTAL_COLORS['yellow']}", page)
        self.assertIn(f"color:{st.TOTAL_COLORS['red']}", page)
