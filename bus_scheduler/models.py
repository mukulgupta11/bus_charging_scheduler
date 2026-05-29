from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Node:
    id: str
    name: str
    kind: str
    chargers: int = 0
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Segment:
    from_id: str
    to_id: str
    distance_km: float
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VehicleConfig:
    battery_range_km: float
    charge_minutes: int
    speed_kmph: float

    @property
    def minutes_per_km(self) -> float:
        return 60.0 / self.speed_kmph


@dataclass(frozen=True)
class SchedulerConfig:
    candidate_plan_limit: int = 24
    local_search_passes: int = 3


@dataclass(frozen=True)
class Bus:
    id: str
    operator: str
    origin: str
    destination: str
    departure: str
    departure_min: int
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Scenario:
    id: str
    name: str
    description: str
    nodes: tuple[Node, ...]
    segments: tuple[Segment, ...]
    vehicle: VehicleConfig
    weights: dict[str, float]
    scheduler: SchedulerConfig
    buses: tuple[Bus, ...]
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class RangeLeg:
    from_stop: str
    to_stop: str
    distance_km: float
    start_min: float
    end_min: float
    battery_remaining_km: float


@dataclass
class ChargeEvent:
    sequence: int
    station_id: str
    station_name: str
    arrival_min: float
    start_min: float
    end_min: float
    wait_min: float
    charger_id: int


@dataclass
class StationSlot:
    station_id: str
    station_name: str
    order: int
    charger_id: int
    bus_id: str
    operator: str
    direction: str
    arrival_min: float
    start_min: float
    end_min: float
    wait_min: float


@dataclass
class BusSchedule:
    bus_id: str
    operator: str
    direction: str
    origin: str
    destination: str
    departure_min: float
    arrival_min: float | None
    charge_plan: tuple[str, ...]
    total_wait_min: float
    total_charge_min: float
    total_travel_min: float
    timeline: list[dict[str, Any]]
    charge_events: list[ChargeEvent]
    range_legs: list[RangeLeg]


@dataclass
class ObjectiveContribution:
    name: str
    weight: float
    raw_score: float
    weighted_score: float


@dataclass
class ValidationMessage:
    rule: str
    ok: bool
    message: str


@dataclass
class ScheduleResult:
    scenario_id: str
    bus_schedules: dict[str, BusSchedule]
    station_schedules: dict[str, list[StationSlot]]
    objective_score: float = 0.0
    objective_contributions: list[ObjectiveContribution] = field(default_factory=list)
    validations: list[ValidationMessage] = field(default_factory=list)
    candidate_plans: dict[str, list[tuple[str, ...]]] = field(default_factory=dict)
    search_iterations: int = 0
