from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SyncScenario:
    name: str
    kind: str
    requires_real_media: bool = False


SCENARIOS: tuple[SyncScenario, ...] = (
    SyncScenario(name="linear_full_overlap", kind="linear"),
    SyncScenario(name="linear_preamble_only", kind="linear"),
    SyncScenario(name="linear_postamble_only", kind="linear"),
    SyncScenario(name="linear_preamble_postamble", kind="linear"),
    SyncScenario(name="middle_hole_cut_detection", kind="cut_detection", requires_real_media=True),
)
