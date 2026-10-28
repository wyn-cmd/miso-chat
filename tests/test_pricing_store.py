# Tests for the DeepSeek cost estimate in web/pricing.py and the peak and
# off-peak recording in web/store.py: the peak-hour windows, the rate shapes
# the admin override may take, the split cost math, and the columns the store
# fills by the hour a call finished. Run from the project folder:
# python -m unittest discover -s tests

import datetime
import os
import shutil
import tempfile
import unittest
from unittest import mock

try:
    from web import pricing
    from web.store import Store
    import web.store as store_mod
    IMPORT_ERROR = None
except ImportError as e:
    pricing = None
    Store = None
    store_mod = None
    IMPORT_ERROR = str(e)


def utc(y, m, d, hh, mm):
    return datetime.datetime(y, m, d, hh, mm, tzinfo=datetime.timezone.utc)


def pacific_day():
    from rp_core import pacific_date
    return pacific_date()


def stub_store(raw):
    # Whatever the deepseek_prices setting returns.
    class Stub:
        def get_setting(self, key):
            return raw
    return Stub()


FLASH_USD = {"peak": {"cache_hit": 0.006, "cache_miss": 0.30, "output": 1.20},
             "offpeak": {"cache_hit": 0.003, "cache_miss": 0.15, "output": 0.60}}

FLASH_CNY = {"peak": {"cache_hit": 0.04, "cache_miss": 2.00, "output": 8.00},
             "offpeak": {"cache_hit": 0.02, "cache_miss": 1.00, "output": 4.00}}

# A full model entry, as the table and the merged override carry it.
FLASH_ENTRY = {"usd": FLASH_USD, "cny": FLASH_CNY}


@unittest.skipUnless(pricing, "pricing imports need the web package: " + str(IMPORT_ERROR))
class PeakWindows(unittest.TestCase):
    # Which tier a completion time falls in. 2026-10-14 is a Wednesday.

    def check(self, dt, offpeak):
        self.assertEqual(pricing.is_offpeak(dt), offpeak, dt.isoformat())

    def test_peak_windows(self):
        self.check(utc(2026, 10, 14, 1, 0), False)
        self.check(utc(2026, 10, 14, 2, 0), False)
        self.check(utc(2026, 10, 14, 3, 59), False)
        self.check(utc(2026, 10, 14, 6, 0), False)
        self.check(utc(2026, 10, 14, 6, 30), False)
        self.check(utc(2026, 10, 14, 9, 59), False)
        self.check(utc(2026, 10, 16, 1, 0), False)          # Friday

    def test_offpeak_hours(self):
        self.check(utc(2026, 10, 14, 0, 59), True)
        self.check(utc(2026, 10, 14, 4, 0), True)           # the window ends before its end time
        self.check(utc(2026, 10, 14, 5, 59), True)
        self.check(utc(2026, 10, 14, 10, 0), True)
        self.check(utc(2026, 10, 14, 12, 0), True)
        self.check(utc(2026, 10, 16, 23, 0), True)

    def test_weekends_are_offpeak(self):
        # 2026-10-17 is a Saturday and 2026-10-18 a Sunday, even inside the windows.
        self.check(utc(2026, 10, 17, 2, 0), True)
        self.check(utc(2026, 10, 17, 7, 0), True)
        self.check(utc(2026, 10, 18, 8, 0), True)

    def test_naive_time_reads_as_utc(self):
        self.assertEqual(pricing.is_offpeak(datetime.datetime(2026, 10, 14, 7, 0)), False)
        self.assertEqual(pricing.is_offpeak(datetime.datetime(2026, 10, 14, 5, 0)), True)

    def test_other_timezones_convert(self):
        plus8 = datetime.timezone(datetime.timedelta(hours=8))
        self.check(datetime.datetime(2026, 10, 14, 15, 0, tzinfo=plus8), False)   # 07:00 UTC
        self.check(datetime.datetime(2026, 10, 14, 13, 0, tzinfo=plus8), True)    # 05:00 UTC

    def test_holidays_are_not_modelled(self):
        # The windows look at the weekday only, so a holiday Wednesday reads as peak.
        self.check(utc(2026, 10, 14, 7, 0), False)


@unittest.skipUnless(pricing, "pricing imports need the web package: " + str(IMPORT_ERROR))
class RateShapes(unittest.TestCase):
    # The shapes the deepseek_prices setting may take, and what is dropped.

    def test_clean_keeps_two_tiers(self):
        fixed = pricing.clean({"usd": FLASH_USD, "cny": {"peak": {"cache_hit": 0.04, "cache_miss": 2, "output": 8},
                                                        "offpeak": {"cache_hit": 0.02, "cache_miss": 1, "output": 4}}})
        self.assertEqual(fixed["usd"]["peak"]["cache_hit"], 0.006)
        self.assertEqual(fixed["usd"]["offpeak"]["output"], 0.60)
        self.assertEqual(fixed["cny"]["peak"]["cache_miss"], 2)

    def test_flat_shape_becomes_the_peak_tier(self):
        fixed = pricing.clean({"usd": {"cache_hit": 0.10, "cache_miss": 0.40, "output": 2.00}})
        self.assertEqual(fixed["usd"]["peak"], {"cache_hit": 0.10, "cache_miss": 0.40, "output": 2.00})
        self.assertEqual(fixed["usd"]["offpeak"], {"cache_hit": 0.05, "cache_miss": 0.20, "output": 1.00})

    def test_bad_rates_are_dropped(self):
        self.assertIsNone(pricing.clean({"usd": {"peak": {"cache_hit": "x", "cache_miss": 1, "output": 1},
                                                "offpeak": {"cache_hit": 1, "cache_miss": 1, "output": 1}}}))
        self.assertIsNone(pricing.clean({"usd": {"peak": {"cache_hit": -1, "cache_miss": 1, "output": 1},
                                                "offpeak": {"cache_hit": 1, "cache_miss": 1, "output": 1}}}))
        self.assertIsNone(pricing.clean({"usd": {"peak": {"cache_hit": 1, "cache_miss": 1},
                                                "offpeak": {"cache_hit": 1, "cache_miss": 1, "output": 1}}}))
        self.assertIsNone(pricing.clean({"usd": "no"}))
        self.assertIsNone(pricing.clean({}))

    def test_defaults_carry_both_models(self):
        prices = pricing.load(None)
        self.assertIn("deepseek-flash", prices)
        self.assertIn("deepseek-v4-pro", prices)
        self.assertEqual(prices["deepseek-flash"]["cny"]["offpeak"]["cache_hit"], 0.02)

    def test_override_merges_over_defaults(self):
        prices = pricing.load(stub_store('{"deepseek-flash": {"usd": {"cache_hit": 0.01, "cache_miss": 1, "output": 2}}}'))
        self.assertEqual(prices["deepseek-flash"]["usd"]["peak"]["cache_hit"], 0.01)
        self.assertEqual(prices["deepseek-flash"]["usd"]["offpeak"]["cache_hit"], 0.005)
        # the untouched parts stay at the defaults
        self.assertEqual(prices["deepseek-flash"]["cny"]["peak"]["cache_hit"], 0.04)
        self.assertEqual(prices["deepseek-v4-pro"]["usd"]["peak"]["cache_hit"], 0.044)

    def test_broken_override_is_ignored(self):
        self.assertEqual(pricing.load(stub_store("not json"))["deepseek-flash"]["usd"]["peak"]["cache_hit"], 0.006)
        self.assertEqual(pricing.load(stub_store('{"x": "no"}'))["deepseek-flash"]["usd"]["peak"]["cache_hit"], 0.006)


@unittest.skipUnless(pricing, "pricing imports need the web package: " + str(IMPORT_ERROR))
class CostMath(unittest.TestCase):
    # The split cost math, with clamping and the fallback model.

    def row(self, **kw):
        return {"model": "deepseek-flash", "requests": 1, "input_tokens": 1000,
                "cached_tokens": 600, "output_tokens": 1000, "seconds": 2.0, **kw}

    def test_peak_share_at_peak_rates(self):
        tokens = {"input_tokens": 1000, "cached_tokens": 600, "output_tokens": 1000,
                  "peak_cached_tokens": 600, "peak_miss_tokens": 0, "peak_output_tokens": 0}
        cost = pricing.row_cost(tokens, FLASH_USD)
        self.assertAlmostEqual(cost, (600 * 0.006 + 400 * 0.15 + 1000 * 0.60) / 1_000_000)

    def test_no_peak_share_prices_as_offpeak(self):
        tokens = {"input_tokens": 1000, "cached_tokens": 600, "output_tokens": 1000,
                  "peak_cached_tokens": 0, "peak_miss_tokens": 0, "peak_output_tokens": 0}
        cost = pricing.row_cost(tokens, FLASH_USD)
        self.assertAlmostEqual(cost, (600 * 0.003 + 400 * 0.15 + 1000 * 0.60) / 1_000_000)

    def test_mixed_share(self):
        tokens = {"input_tokens": 1000, "cached_tokens": 600, "output_tokens": 1000,
                  "peak_cached_tokens": 100, "peak_miss_tokens": 200, "peak_output_tokens": 300}
        cost = pricing.row_cost(tokens, FLASH_USD)
        expect = (100 * 0.006 + 200 * 0.30 + 300 * 1.20
                  + 500 * 0.003 + 200 * 0.15 + 700 * 0.60) / 1_000_000
        self.assertAlmostEqual(cost, expect)

    def test_oversized_peak_figures_are_clamped(self):
        tokens = {"input_tokens": 1000, "cached_tokens": 600, "output_tokens": 1000,
                  "peak_cached_tokens": 5000, "peak_miss_tokens": 5000, "peak_output_tokens": 5000}
        cost = pricing.row_cost(tokens, FLASH_USD)
        self.assertAlmostEqual(cost, (600 * 0.006 + 400 * 0.30 + 1000 * 1.20) / 1_000_000)

    def test_empty_row_costs_nothing(self):
        self.assertEqual(pricing.row_cost({}, FLASH_USD), 0.0)

    def test_bare_model_strips_the_prefix(self):
        self.assertEqual(pricing.bare_model("deepseek:deepseek-flash"), "deepseek-flash")
        self.assertEqual(pricing.bare_model("deepseek-flash"), "deepseek-flash")

    def test_unknown_model_falls_back(self):
        rates, fallback = pricing.rates_for({"deepseek-flash": FLASH_ENTRY}, "deepseek:deepseek-nano")
        self.assertEqual(rates, FLASH_ENTRY)
        self.assertTrue(fallback)
        rates, fallback = pricing.rates_for({"deepseek-flash": FLASH_ENTRY}, "deepseek-flash")
        self.assertFalse(fallback)

    def test_summary_totals(self):
        rows = [
            self.row(model="deepseek:deepseek-flash",
                     peak_cached_tokens=600, peak_miss_tokens=0, peak_output_tokens=0),
            self.row(model="deepseek:deepseek-nano",
                     peak_cached_tokens=0, peak_miss_tokens=0, peak_output_tokens=0),
        ]
        out = pricing.summary(rows, {"deepseek-flash": FLASH_ENTRY})
        self.assertEqual(len(out["models"]), 2)
        item = out["models"][0]
        self.assertEqual(item["model"], "deepseek-flash")
        self.assertFalse(item["fallback_rates"])
        other = out["models"][1]
        self.assertTrue(other["fallback_rates"])
        self.assertEqual(item["requests"], 1)
        self.assertEqual(item["input_tokens"], 1000)
        self.assertEqual(item["peak_tokens"], 600)
        self.assertEqual(out["totals"]["peak_tokens"], 600)
        self.assertEqual(out["totals"]["requests"], 2)
        # the total rounds the exact sum, so it can sit a rounding step away from the rounded items
        self.assertAlmostEqual(out["totals"]["cost"]["usd"], item["cost"]["usd"] + other["cost"]["usd"], places=5)

    def test_summary_marks_fallback(self):
        out = pricing.summary([self.row(model="deepseek:deepseek-nano",
                                        peak_cached_tokens=0, peak_miss_tokens=0, peak_output_tokens=0)],
                              {"deepseek-flash": FLASH_ENTRY})
        self.assertTrue(out["models"][0]["fallback_rates"])
        self.assertEqual(out["models"][0]["model"], "deepseek-nano")


@unittest.skipUnless(Store, "the store needs pytz and rp_core imports: " + str(IMPORT_ERROR))
class StoreRecording(unittest.TestCase):
    # What the store writes for a call, by the hour it finished.

    PEAK = utc(2026, 10, 14, 7, 30)       # Wednesday, inside 06:00-10:00 UTC
    OFFPEAK = utc(2026, 10, 14, 5, 0)     # Wednesday, between the windows

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "data", "miso.db")
        self.store = Store(self.path)

    def write(self, now, **kw):
        kw.setdefault("provider", "deepseek")
        kw.setdefault("model", "deepseek:deepseek-flash")
        with mock.patch.object(store_mod, "utcnow", return_value=now):
            self.store.add_usage(1, requests=1, tokens=kw.get("tokens", 1500),
                                 provider=kw["provider"],
                                 input_tokens=kw.get("input_tokens", 1000),
                                 cached_tokens=kw.get("cached_tokens", 400),
                                 output_tokens=kw.get("output_tokens", 500),
                                 seconds=1.5, model=kw["model"])

    def models_row(self, provider="deepseek"):
        rows = self.store.usage_model_totals([pacific_day()], provider)
        self.assertEqual(len(rows), 1)
        return rows[0]

    def test_peak_call_lands_in_the_peak_buckets(self):
        self.write(self.PEAK)
        row = self.models_row()
        self.assertEqual(row["peak_cached_tokens"], 400)
        self.assertEqual(row["peak_miss_tokens"], 600)
        self.assertEqual(row["peak_output_tokens"], 500)

    def test_offpeak_call_leaves_the_buckets_empty(self):
        self.write(self.OFFPEAK)
        row = self.models_row()
        self.assertEqual(row["peak_cached_tokens"], 0)
        self.assertEqual(row["peak_miss_tokens"], 0)
        self.assertEqual(row["peak_output_tokens"], 0)
        self.assertEqual(row["input_tokens"], 1000)
        self.assertEqual(row["cached_tokens"], 400)
        self.assertEqual(row["output_tokens"], 500)

    def test_other_providers_are_never_split(self):
        self.write(self.PEAK, provider="gemini", model="gemini:gemini-2.5-flash")
        row = self.models_row("gemini")
        self.assertEqual(row["peak_cached_tokens"], 0)
        self.assertEqual(row["peak_miss_tokens"], 0)
        self.assertEqual(row["peak_output_tokens"], 0)

    def test_calls_accumulate(self):
        self.write(self.PEAK)
        self.write(self.OFFPEAK)
        row = self.models_row()
        self.assertEqual(row["requests"], 2)
        self.assertEqual(row["input_tokens"], 2000)
        self.assertEqual(row["cached_tokens"], 800)
        self.assertEqual(row["peak_cached_tokens"], 400)
        self.assertEqual(row["peak_miss_tokens"], 600)
        self.assertEqual(row["peak_output_tokens"], 500)

    def test_the_daily_table_carries_the_split_too(self):
        self.write(self.PEAK)
        rows = self.store.usage_breakdown([pacific_day()], "deepseek")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["peak_cached_tokens"], 400)
        self.assertEqual(rows[0]["peak_miss_tokens"], 600)
        self.assertEqual(rows[0]["peak_output_tokens"], 500)

    def test_summary_reads_what_the_store_recorded(self):
        self.write(self.PEAK)
        self.write(self.OFFPEAK)
        out = pricing.summary(self.store.usage_model_totals([pacific_day()], "deepseek"), pricing.load(None))
        item = out["models"][0]
        self.assertEqual(item["peak_tokens"], 1500)
        expect = (400 * 0.006 + 600 * 0.30 + 500 * 1.20
                  + 400 * 0.003 + 600 * 0.15 + 500 * 0.60) / 1_000_000
        self.assertEqual(item["cost"]["usd"], round(expect, 6))

    def test_columns_survive_a_second_open(self):
        # A restart reopens the database; the migration must not choke on the columns it added.
        self.write(self.PEAK)
        again = Store(self.path)
        rows = again.usage_model_totals([pacific_day()], "deepseek")
        self.assertEqual(rows[0]["peak_cached_tokens"], 400)
