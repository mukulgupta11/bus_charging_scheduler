from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Iterable

from .formatting import parse_clock
from .models import Bus, Node, Scenario, SchedulerConfig, Segment, VehicleConfig


NODE_FIELDS = {"id", "name", "kind", "chargers"}
SEGMENT_FIELDS = {"from", "to", "distance_km"}
BUS_FIELDS = {"id", "operator", "origin", "destination", "departure"}


def load_raw_scenario(path: str | Path) -> dict:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def parse_scenario(raw: dict) -> Scenario:
    route = raw["route"]
    nodes = tuple(
        Node(
            id=node["id"],
            name=node.get("name", node["id"]),
            kind=node.get("kind", "station"),
            chargers=int(node.get("chargers", 0)),
            attributes={key: value for key, value in node.items() if key not in NODE_FIELDS},
        )
        for node in route["nodes"]
    )
    segments = tuple(
        Segment(
            from_id=segment["from"],
            to_id=segment["to"],
            distance_km=float(segment["distance_km"]),
            attributes={key: value for key, value in segment.items() if key not in SEGMENT_FIELDS},
        )
        for segment in route["segments"]
    )
    vehicle_raw = raw["vehicle"]
    vehicle = VehicleConfig(
        battery_range_km=float(vehicle_raw["battery_range_km"]),
        charge_minutes=int(vehicle_raw["charge_minutes"]),
        speed_kmph=float(vehicle_raw["speed_kmph"]),
    )
    scheduler_raw = raw.get("scheduler", {})
    scheduler = SchedulerConfig(
        candidate_plan_limit=int(scheduler_raw.get("candidate_plan_limit", 24)),
        local_search_passes=int(scheduler_raw.get("local_search_passes", 3)),
    )
    buses = tuple(
        Bus(
            id=bus["id"],
            operator=bus["operator"],
            origin=bus["origin"],
            destination=bus["destination"],
            departure=bus["departure"],
            departure_min=parse_clock(bus["departure"]),
            attributes={key: value for key, value in bus.items() if key not in BUS_FIELDS},
        )
        for bus in raw["buses"]
    )
    return Scenario(
        id=raw["id"],
        name=raw["name"],
        description=raw.get("description", ""),
        nodes=nodes,
        segments=segments,
        vehicle=vehicle,
        weights={key: float(value) for key, value in raw.get("weights", {}).items()},
        scheduler=scheduler,
        buses=buses,
        raw=raw,
    )


def load_scenario(path: str | Path) -> Scenario:
    return parse_scenario(load_raw_scenario(path))


def load_scenarios(directory: str | Path) -> list[tuple[Path, Scenario]]:
    paths = sorted(Path(directory).glob("*.json"))
    return [(path, load_scenario(path)) for path in paths]


def with_weight_overrides(scenario: Scenario, weights: dict[str, float]) -> Scenario:
    return replace(scenario, weights={key: float(value) for key, value in weights.items()})


def scenario_names(scenarios: Iterable[tuple[Path, Scenario]]) -> dict[str, Path]:
    return {scenario.name: path for path, scenario in scenarios}
