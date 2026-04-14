from __future__ import annotations

import unittest

from frankenstein.cli import _format_confidence_message


class ConfidenceMessageTests(unittest.TestCase):
    def test_high_confidence_reports_normally(self) -> None:
        message = _format_confidence_message(0.8, 1.0, 0.0, has_cuts=False)
        self.assertIn("Sync confidence: 0.80", message)

    def test_low_confidence_noop_alignment_is_not_called_sync_failure(self) -> None:
        message = _format_confidence_message(0.03, 1.0, 0.0, has_cuts=False)
        self.assertIn("Weak feature match (0.03)", message)
        self.assertIn("essentially no correction", message)
        self.assertNotIn("low sync confidence", message)

    def test_low_confidence_cut_detection_asks_for_spot_check(self) -> None:
        message = _format_confidence_message(0.03, None, None, has_cuts=True)
        self.assertIn("fully reliable cut detection", message)


if __name__ == "__main__":
    unittest.main()
