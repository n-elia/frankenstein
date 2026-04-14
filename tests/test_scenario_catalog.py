from __future__ import annotations

import unittest

from scenarios import SCENARIOS


class ScenarioCatalogTests(unittest.TestCase):
    def test_catalog_covers_core_sync_behaviors(self) -> None:
        names = {scenario.name for scenario in SCENARIOS}
        self.assertTrue(
            {
                "linear_full_overlap",
                "linear_preamble_only",
                "linear_postamble_only",
                "linear_preamble_postamble",
                "middle_hole_cut_detection",
            }.issubset(names)
        )

    def test_cut_detection_entry_is_real_media_backed(self) -> None:
        middle_hole = next(
            scenario for scenario in SCENARIOS if scenario.name == "middle_hole_cut_detection"
        )
        self.assertTrue(middle_hole.requires_real_media)


if __name__ == "__main__":
    unittest.main()
