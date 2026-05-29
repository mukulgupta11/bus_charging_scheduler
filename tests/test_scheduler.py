from __future__ import annotations

import copy
import unittest
from pathlib import Path

from bus_scheduler.scenario_io import load_raw_scenario, load_scenario, parse_scenario, with_weight_overrides
from bus_scheduler.scheduler import WeightedScheduler


SCENARIO_DIR = Path(__file__).resolve().parents[1] / "data" / "scenarios"


class SchedulerTests(unittest.TestCase):
    def test_all_shipped_scenarios_pass_hard_rules(self) -> None:
        scheduler = WeightedScheduler()
        for path in sorted(SCENARIO_DIR.glob("*.json")):
            with self.subTest(path=path.name):
                scenario = load_scenario(path)
                result = scheduler.schedule(scenario)
                self.assertTrue(all(validation.ok for validation in result.validations))
                self.assertEqual(len(result.bus_schedules), len(scenario.buses))
                for bus in result.bus_schedules.values():
                    self.assertIsNotNone(bus.arrival_min)
                    self.assertGreaterEqual(len(bus.charge_plan), 2)
                    for leg in bus.range_legs:
                        self.assertLessEqual(
                            leg.distance_km,
                            scenario.vehicle.battery_range_km,
                            f"{bus.bus_id} exceeds range on {leg.from_stop}->{leg.to_stop}",
                        )

    def test_weight_change_can_change_dispatch_order(self) -> None:
        base = load_scenario(SCENARIO_DIR / "scenario_4_operator_heavy.json")
        scheduler = WeightedScheduler()

        low_operator = scheduler.schedule(
            with_weight_overrides(base, {"individual": 1.0, "operator": 0.0, "overall": 1.0})
        )
        high_operator = scheduler.schedule(
            with_weight_overrides(base, {"individual": 1.0, "operator": 2.0, "overall": 1.0})
        )

        low_signature = [
            (slot.station_id, slot.bus_id, slot.start_min)
            for slots in low_operator.station_schedules.values()
            for slot in slots
        ]
        high_signature = [
            (slot.station_id, slot.bus_id, slot.start_min)
            for slots in high_operator.station_schedules.values()
            for slot in slots
        ]
        self.assertNotEqual(low_signature, high_signature)

    def test_more_chargers_is_data_only(self) -> None:
        raw = load_raw_scenario(SCENARIO_DIR / "scenario_5_worst_case_convergence.json")
        modified = copy.deepcopy(raw)
        for node in modified["route"]["nodes"]:
            if node["id"] == "B":
                node["chargers"] = 2
        scenario = parse_scenario(modified)
        result = WeightedScheduler().schedule(scenario)

        self.assertTrue(all(validation.ok for validation in result.validations))
        self.assertEqual(len(result.bus_schedules), len(scenario.buses))
        self.assertTrue(any(slot.charger_id == 2 for slot in result.station_schedules["B"]))


if __name__ == "__main__":
    unittest.main()
