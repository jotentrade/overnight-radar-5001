import tempfile
import unittest
from pathlib import Path

from app import analyze, analyze_public_signals, load_trades, normalize_branch, write_reports


class DetectorTest(unittest.TestCase):
    def test_sample_filters_and_scores(self):
        trades = load_trades(Path("sample_trades.csv"))
        rows = analyze(trades, {"凱基-台北", "美林"}, min_ratio=3, min_change=7)
        self.assertEqual([row["stock_id"] for row in rows], ["2330"])
        self.assertEqual(rows[0]["buy_ratio_pct"], 5.0)

    def test_volume_growth_can_filter_signals(self):
        trades = load_trades(Path("sample_trades.csv"))
        self.assertEqual(len(analyze(trades, {"凱基-台北"}, 3, 7, 50)), 1)
        self.assertEqual(len(analyze(trades, {"凱基-台北"}, 3, 7, 80)), 0)

    def test_reports_are_created(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = write_reports([], Path(directory))
            self.assertTrue(all(path.exists() for path in paths))

    def test_public_signal_uses_price_volume_and_institutional_buying(self):
        prices = {"2330": {"stock_name": "台積電", "close": 1000, "change_pct": 9,
                            "volume": 10000, "previous_volume": 5000, "volume_change_pct": 100}}
        rows = analyze_public_signals(prices, {"2330": 500}, 3, 7, 20)
        self.assertEqual(rows[0]["known_branches"], "三大法人")
        self.assertEqual(rows[0]["buy_ratio_pct"], 5)

    def test_branch_names_ignore_spaces_and_hyphens(self):
        self.assertEqual(normalize_branch("富邦 嘉義"), normalize_branch("富邦-嘉義"))


if __name__ == "__main__":
    unittest.main()
