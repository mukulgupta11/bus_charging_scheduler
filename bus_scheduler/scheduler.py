from __future__ import annotations

import heapq
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import count
from typing import Any

from .formatting import format_clock
from .models import (
    Bus,
    BusSchedule,
    ChargeEvent,
    RangeLeg,
    ScheduleResult,
    Scenario,
    StationSlot,
    ValidationMessage,
)
from .rules import DEFAULT_OBJECTIVE_RULES, ObjectiveRule, evaluate_objective


@dataclass
class ChargeRequest:
    bus_id: str
    station_id: str
    arrival_min: float
    sequence: int


class RouteModel:
    def __init__(self, scenario: Scenario):
        self.scenario = scenario
        self.nodes = {node.id: node for node in scenario.nodes}
        self.order = {node.id: index for index, node in enumerate(scenario.nodes)}
        self.cumulative = self._build_cumulative_distances()

    def _build_cumulative_distances(self) -> dict[str, float]:
        cumulative = {self.scenario.nodes[0].id: 0.0}
        distance = 0.0
        for segment in self.scenario.segments:
            if segment.from_id not in cumulative:
                raise ValueError(f"Route segment starts from unknown prior node: {segment}")
            distance = cumulative[segment.from_id] + segment.distance_km
            cumulative[segment.to_id] = distance
        return cumulative

    def name(self, node_id: str) -> str:
        return self.nodes[node_id].name

    def distance(self, from_id: str, to_id: str) -> float:
        return abs(self.cumulative[to_id] - self.cumulative[from_id])

    def travel_minutes(self, from_id: str, to_id: str) -> int:
        raw_minutes = self.distance(from_id, to_id) * self.scenario.vehicle.minutes_per_km
        return int(math.ceil(raw_minutes - 1e-9))

    def direction(self, origin: str, destination: str) -> int:
        return 1 if self.order[destination] > self.order[origin] else -1

    def direction_label(self, origin: str, destination: str) -> str:
        return f"{self.name(origin)} -> {self.name(destination)}"

    def charging_stations_between(self, origin: str, destination: str) -> list[str]:
        step = self.direction(origin, destination)
        start = self.order[origin]
        end = self.order[destination]
        station_ids = []
        for node in self.scenario.nodes:
            index = self.order[node.id]
            is_between = start < index < end if step > 0 else end < index < start
            if is_between and node.kind == "station" and node.chargers > 0:
                station_ids.append(node.id)
        return station_ids if step > 0 else list(reversed(station_ids))

    def is_forward_in_direction(self, current: str, later: str, destination: str) -> bool:
        step = self.direction(current, destination)
        return step * (self.order[later] - self.order[current]) > 0

    def leg_distances(self, origin: str, plan: tuple[str, ...], destination: str) -> list[float]:
        stops = (origin, *plan, destination)
        return [self.distance(stops[index], stops[index + 1]) for index in range(len(stops) - 1)]


class WeightedScheduler:
    """Candidate-plan search plus weighted event simulation."""

    def __init__(self, objective_rules: tuple[ObjectiveRule, ...] = DEFAULT_OBJECTIVE_RULES):
        self.objective_rules = objective_rules

    def schedule(self, scenario: Scenario) -> ScheduleResult:
        route = RouteModel(scenario)
        candidate_plans = self.generate_candidate_plans(scenario, route)
        assignment = self._initial_assignment(scenario, route, candidate_plans)

        best_result = self._score_result(
            scenario,
            self._simulate(scenario, route, assignment, candidate_plans),
        )
        iterations = 0

        for _ in range(scenario.scheduler.local_search_passes):
            improved = False
            bus_order = sorted(
                scenario.buses,
                key=lambda bus: (
                    best_result.bus_schedules[bus.id].total_wait_min,
                    bus.departure_min,
                    bus.id,
                ),
                reverse=True,
            )
            for bus in bus_order:
                current_plan = assignment[bus.id]
                chosen_plan = current_plan
                chosen_result = best_result
                for plan in candidate_plans[bus.id]:
                    if plan == current_plan:
                        continue
                    trial_assignment = dict(assignment)
                    trial_assignment[bus.id] = plan
                    trial_result = self._score_result(
                        scenario,
                        self._simulate(scenario, route, trial_assignment, candidate_plans),
                    )
                    iterations += 1
                    if trial_result.objective_score + 1e-9 < chosen_result.objective_score:
                        chosen_plan = plan
                        chosen_result = trial_result
                if chosen_plan != current_plan:
                    assignment = dict(assignment)
                    assignment[bus.id] = chosen_plan
                    best_result = chosen_result
                    improved = True
            if not improved:
                break

        best_result.search_iterations = iterations
        best_result.validations = self.validate(scenario, route, best_result)
        return best_result

    def generate_candidate_plans(
        self, scenario: Scenario, route: RouteModel | None = None
    ) -> dict[str, list[tuple[str, ...]]]:
        route = route or RouteModel(scenario)
        plans_by_bus: dict[str, list[tuple[str, ...]]] = {}
        for bus in scenario.buses:
            stations = route.charging_stations_between(bus.origin, bus.destination)
            plans: list[tuple[str, ...]] = []

            def dfs(current: str, remaining: list[str], plan: list[str]) -> None:
                if route.distance(current, bus.destination) <= scenario.vehicle.battery_range_km:
                    plans.append(tuple(plan))
                for index, station in enumerate(remaining):
                    if route.distance(current, station) <= scenario.vehicle.battery_range_km:
                        dfs(station, remaining[index + 1 :], [*plan, station])

            dfs(bus.origin, stations, [])
            unique_plans = sorted(set(plans), key=lambda plan: self._candidate_sort_key(scenario, route, bus, plan))
            if not unique_plans:
                raise ValueError(f"No feasible charging plan for {bus.id}")
            plans_by_bus[bus.id] = unique_plans[: scenario.scheduler.candidate_plan_limit]
        return plans_by_bus

    def _candidate_sort_key(
        self, scenario: Scenario, route: RouteModel, bus: Bus, plan: tuple[str, ...]
    ) -> tuple[Any, ...]:
        distances = route.leg_distances(bus.origin, plan, bus.destination)
        short_leg_penalty = sum(max(0.0, 80.0 - distance) for distance in distances)
        minimum_slack = min(scenario.vehicle.battery_range_km - distance for distance in distances)
        return (len(plan), short_leg_penalty, -minimum_slack, ",".join(plan))

    def _initial_assignment(
        self,
        scenario: Scenario,
        route: RouteModel,
        candidate_plans: dict[str, list[tuple[str, ...]]],
    ) -> dict[str, tuple[str, ...]]:
        assignment: dict[str, tuple[str, ...]] = {}
        station_loads: Counter[str] = Counter()
        bucket_loads: Counter[tuple[str, int]] = Counter()
        operator_bucket_loads: Counter[tuple[str, str, int]] = Counter()
        buses = sorted(scenario.buses, key=lambda bus: (bus.departure_min, bus.id))

        for bus in buses:
            scored_plans = []
            for plan in candidate_plans[bus.id]:
                projected = self._project_charge_arrivals(scenario, route, bus, plan)
                bucket_penalty = 0.0
                operator_penalty = 0.0
                total_load_penalty = 0.0
                for station_id, arrival_min in projected:
                    bucket = int(arrival_min // 20)
                    bucket_penalty += bucket_loads[(station_id, bucket)] * 9.0
                    total_load_penalty += station_loads[station_id] * 2.0
                    operator_penalty += operator_bucket_loads[(bus.operator, station_id, bucket)] * 7.0
                charge_penalty = len(plan) * scenario.vehicle.charge_minutes
                score = (
                    scenario.weights.get("overall", 0.0) * charge_penalty
                    + scenario.weights.get("individual", 0.0) * (bucket_penalty + total_load_penalty)
                    + scenario.weights.get("operator", 0.0) * operator_penalty
                )
                scored_plans.append((score, len(plan), ",".join(plan), plan))
            _, _, _, chosen = min(scored_plans)
            assignment[bus.id] = chosen
            for station_id, arrival_min in self._project_charge_arrivals(scenario, route, bus, chosen):
                bucket = int(arrival_min // 20)
                station_loads[station_id] += 1
                bucket_loads[(station_id, bucket)] += 1
                operator_bucket_loads[(bus.operator, station_id, bucket)] += 1
        return assignment

    def _project_charge_arrivals(
        self, scenario: Scenario, route: RouteModel, bus: Bus, plan: tuple[str, ...]
    ) -> list[tuple[str, float]]:
        current = bus.origin
        current_time = float(bus.departure_min)
        projected = []
        for station_id in plan:
            current_time += route.travel_minutes(current, station_id)
            projected.append((station_id, current_time))
            current_time += scenario.vehicle.charge_minutes
            current = station_id
        return projected

    def _simulate(
        self,
        scenario: Scenario,
        route: RouteModel,
        assignment: dict[str, tuple[str, ...]],
        candidate_plans: dict[str, list[tuple[str, ...]]],
    ) -> ScheduleResult:
        event_counter = count()
        request_counter = count()
        heap: list[tuple[float, int, str, dict[str, Any]]] = []

        stations = {
            node.id: node
            for node in scenario.nodes
            if node.kind == "station" and node.chargers > 0
        }
        chargers = {
            station_id: [
                {"charger_id": charger_index + 1, "available_at": 0.0}
                for charger_index in range(node.chargers)
            ]
            for station_id, node in stations.items()
        }
        queues: dict[str, list[ChargeRequest]] = {station_id: [] for station_id in stations}
        station_schedules: dict[str, list[StationSlot]] = {station_id: [] for station_id in stations}
        operator_wait_totals: Counter[str] = Counter()
        operator_charge_counts: Counter[str] = Counter()

        states: dict[str, dict[str, Any]] = {}
        for bus in scenario.buses:
            states[bus.id] = {
                "bus": bus,
                "plan": assignment[bus.id],
                "next_plan_index": 0,
                "timeline": [
                    {
                        "event": "Departure",
                        "stop": route.name(bus.origin),
                        "start_min": bus.departure_min,
                        "end_min": bus.departure_min,
                        "details": "Full charge",
                    }
                ],
                "charge_events": [],
                "range_legs": [],
                "total_wait": 0.0,
                "arrival_min": None,
            }
            self._push_next_arrival(heap, event_counter, scenario, route, states[bus.id], bus.origin, bus.departure_min)

        while heap:
            current_time = heap[0][0]
            current_events: list[tuple[float, int, str, dict[str, Any]]] = []
            while heap and abs(heap[0][0] - current_time) < 1e-9:
                current_events.append(heapq.heappop(heap))

            affected_stations: set[str] = set()
            for _, _, event_type, payload in current_events:
                if event_type == "arrival":
                    bus_id = payload["bus_id"]
                    stop_id = payload["stop_id"]
                    state = states[bus_id]
                    bus = state["bus"]
                    if stop_id == bus.destination:
                        state["arrival_min"] = current_time
                        state["timeline"].append(
                            {
                                "event": "Arrival",
                                "stop": route.name(stop_id),
                                "start_min": current_time,
                                "end_min": current_time,
                                "details": "Trip complete",
                            }
                        )
                    else:
                        queues[stop_id].append(
                            ChargeRequest(
                                bus_id=bus_id,
                                station_id=stop_id,
                                arrival_min=current_time,
                                sequence=next(request_counter),
                            )
                        )
                        affected_stations.add(stop_id)
                elif event_type == "charger_free":
                    affected_stations.add(payload["station_id"])

            for station_id in sorted(affected_stations):
                self._dispatch_station(
                    scenario,
                    route,
                    station_id,
                    current_time,
                    queues,
                    chargers,
                    station_schedules,
                    states,
                    operator_wait_totals,
                    operator_charge_counts,
                    heap,
                    event_counter,
                )

        bus_schedules: dict[str, BusSchedule] = {}
        for bus_id, state in states.items():
            bus = state["bus"]
            range_legs = state["range_legs"]
            charge_events = state["charge_events"]
            bus_schedules[bus_id] = BusSchedule(
                bus_id=bus.id,
                operator=bus.operator,
                direction=route.direction_label(bus.origin, bus.destination),
                origin=route.name(bus.origin),
                destination=route.name(bus.destination),
                departure_min=bus.departure_min,
                arrival_min=state["arrival_min"],
                charge_plan=assignment[bus.id],
                total_wait_min=state["total_wait"],
                total_charge_min=len(charge_events) * scenario.vehicle.charge_minutes,
                total_travel_min=sum(leg.end_min - leg.start_min for leg in range_legs),
                timeline=state["timeline"],
                charge_events=charge_events,
                range_legs=range_legs,
            )

        return ScheduleResult(
            scenario_id=scenario.id,
            bus_schedules=bus_schedules,
            station_schedules=station_schedules,
            candidate_plans=candidate_plans,
        )

    def _push_next_arrival(
        self,
        heap: list[tuple[float, int, str, dict[str, Any]]],
        event_counter: count,
        scenario: Scenario,
        route: RouteModel,
        state: dict[str, Any],
        from_stop: str,
        start_min: float,
    ) -> None:
        bus: Bus = state["bus"]
        plan: tuple[str, ...] = state["plan"]
        next_index = state["next_plan_index"]
        to_stop = plan[next_index] if next_index < len(plan) else bus.destination
        distance = route.distance(from_stop, to_stop)
        travel_minutes = route.travel_minutes(from_stop, to_stop)
        end_min = start_min + travel_minutes
        state["range_legs"].append(
            RangeLeg(
                from_stop=from_stop,
                to_stop=to_stop,
                distance_km=distance,
                start_min=start_min,
                end_min=end_min,
                battery_remaining_km=scenario.vehicle.battery_range_km - distance,
            )
        )
        state["timeline"].append(
            {
                "event": "Travel",
                "from": route.name(from_stop),
                "to": route.name(to_stop),
                "start_min": start_min,
                "end_min": end_min,
                "duration_min": travel_minutes,
                "details": f"{distance:g} km, battery left {scenario.vehicle.battery_range_km - distance:g} km",
            }
        )
        heapq.heappush(
            heap,
            (
                end_min,
                next(event_counter),
                "arrival",
                {"bus_id": bus.id, "stop_id": to_stop},
            ),
        )

    def _dispatch_station(
        self,
        scenario: Scenario,
        route: RouteModel,
        station_id: str,
        current_time: float,
        queues: dict[str, list[ChargeRequest]],
        chargers: dict[str, list[dict[str, float]]],
        station_schedules: dict[str, list[StationSlot]],
        states: dict[str, dict[str, Any]],
        operator_wait_totals: Counter[str],
        operator_charge_counts: Counter[str],
        heap: list[tuple[float, int, str, dict[str, Any]]],
        event_counter: count,
    ) -> None:
        queue = queues[station_id]
        while queue:
            free_chargers = [
                charger for charger in chargers[station_id] if charger["available_at"] <= current_time + 1e-9
            ]
            if not free_chargers:
                return
            charger = min(free_chargers, key=lambda item: item["charger_id"])
            request_index = self._select_request_index(
                scenario,
                current_time,
                queue,
                queues,
                states,
                operator_wait_totals,
                operator_charge_counts,
            )
            request = queue.pop(request_index)
            state = states[request.bus_id]
            bus: Bus = state["bus"]
            wait_min = current_time - request.arrival_min
            end_min = current_time + scenario.vehicle.charge_minutes

            if wait_min > 1e-9:
                state["timeline"].append(
                    {
                        "event": "Wait",
                        "stop": route.name(station_id),
                        "start_min": request.arrival_min,
                        "end_min": current_time,
                        "duration_min": wait_min,
                        "details": "Charger queue",
                    }
                )
            charge_event = ChargeEvent(
                sequence=len(state["charge_events"]) + 1,
                station_id=station_id,
                station_name=route.name(station_id),
                arrival_min=request.arrival_min,
                start_min=current_time,
                end_min=end_min,
                wait_min=wait_min,
                charger_id=int(charger["charger_id"]),
            )
            state["charge_events"].append(charge_event)
            state["timeline"].append(
                {
                    "event": "Charge",
                    "stop": route.name(station_id),
                    "start_min": current_time,
                    "end_min": end_min,
                    "duration_min": scenario.vehicle.charge_minutes,
                    "details": f"Charger {int(charger['charger_id'])}",
                }
            )
            state["total_wait"] += wait_min
            state["next_plan_index"] += 1
            operator_wait_totals[bus.operator] += wait_min
            operator_charge_counts[bus.operator] += 1

            slot = StationSlot(
                station_id=station_id,
                station_name=route.name(station_id),
                order=len(station_schedules[station_id]) + 1,
                charger_id=int(charger["charger_id"]),
                bus_id=bus.id,
                operator=bus.operator,
                direction=route.direction_label(bus.origin, bus.destination),
                arrival_min=request.arrival_min,
                start_min=current_time,
                end_min=end_min,
                wait_min=wait_min,
            )
            station_schedules[station_id].append(slot)

            charger["available_at"] = end_min
            heapq.heappush(
                heap,
                (
                    end_min,
                    next(event_counter),
                    "charger_free",
                    {"station_id": station_id, "charger_id": charger["charger_id"]},
                ),
            )
            self._push_next_arrival(heap, event_counter, scenario, route, state, station_id, end_min)

    def _select_request_index(
        self,
        scenario: Scenario,
        current_time: float,
        station_queue: list[ChargeRequest],
        all_queues: dict[str, list[ChargeRequest]],
        states: dict[str, dict[str, Any]],
        operator_wait_totals: Counter[str],
        operator_charge_counts: Counter[str],
    ) -> int:
        queued_by_operator: Counter[str] = Counter()
        for queue in all_queues.values():
            for request in queue:
                operator = states[request.bus_id]["bus"].operator
                queued_by_operator[operator] += 1

        def priority(index_and_request: tuple[int, ChargeRequest]) -> tuple[float, float, str]:
            index, request = index_and_request
            state = states[request.bus_id]
            bus: Bus = state["bus"]
            current_wait = current_time - request.arrival_min
            operator = bus.operator
            completed_avg = operator_wait_totals[operator] / max(1, operator_charge_counts[operator])
            individual_pressure = current_wait * 1.8 + state["total_wait"] * 0.6
            operator_pressure = completed_avg + queued_by_operator[operator] * 4.0
            overall_component = request.arrival_min
            score = (
                scenario.weights.get("overall", 0.0) * overall_component
                - scenario.weights.get("individual", 0.0) * individual_pressure
                - scenario.weights.get("operator", 0.0) * operator_pressure
            )
            return (score, request.arrival_min, f"{bus.departure_min:04.0f}-{bus.id}-{index}")

        return min(enumerate(station_queue), key=priority)[0]

    def _score_result(self, scenario: Scenario, result: ScheduleResult) -> ScheduleResult:
        score, contributions = evaluate_objective(scenario, result, self.objective_rules)
        result.objective_score = score
        result.objective_contributions = contributions
        return result

    def validate(
        self, scenario: Scenario, route: RouteModel, result: ScheduleResult
    ) -> list[ValidationMessage]:
        messages: list[ValidationMessage] = []

        range_failures = []
        for bus in result.bus_schedules.values():
            for leg in bus.range_legs:
                if leg.distance_km > scenario.vehicle.battery_range_km + 1e-9:
                    range_failures.append(
                        f"{bus.bus_id} {route.name(leg.from_stop)}->{route.name(leg.to_stop)} {leg.distance_km:g} km"
                    )
        messages.append(
            ValidationMessage(
                rule="Battery range",
                ok=not range_failures,
                message="All legs are within range."
                if not range_failures
                else "; ".join(range_failures),
            )
        )

        overlap_failures = []
        for station_id, slots in result.station_schedules.items():
            by_charger: dict[int, list[StationSlot]] = defaultdict(list)
            for slot in slots:
                by_charger[slot.charger_id].append(slot)
            for charger_id, charger_slots in by_charger.items():
                ordered = sorted(charger_slots, key=lambda slot: (slot.start_min, slot.end_min))
                for previous, current in zip(ordered, ordered[1:]):
                    if previous.end_min > current.start_min + 1e-9:
                        overlap_failures.append(
                            f"{route.name(station_id)} charger {charger_id}: "
                            f"{previous.bus_id} overlaps {current.bus_id}"
                        )
        messages.append(
            ValidationMessage(
                rule="Charger exclusivity",
                ok=not overlap_failures,
                message="No station charger overlaps."
                if not overlap_failures
                else "; ".join(overlap_failures),
            )
        )

        duration_failures = []
        for slots in result.station_schedules.values():
            for slot in slots:
                duration = slot.end_min - slot.start_min
                if abs(duration - scenario.vehicle.charge_minutes) > 1e-9:
                    duration_failures.append(f"{slot.bus_id} at {slot.station_name}: {duration:g} min")
        messages.append(
            ValidationMessage(
                rule="Charge duration",
                ok=not duration_failures,
                message=f"Every charge is exactly {scenario.vehicle.charge_minutes} minutes."
                if not duration_failures
                else "; ".join(duration_failures),
            )
        )

        arrival_failures = [
            bus.bus_id for bus in result.bus_schedules.values() if bus.arrival_min is None
        ]
        messages.append(
            ValidationMessage(
                rule="Trip completion",
                ok=not arrival_failures,
                message="Every bus reaches its destination."
                if not arrival_failures
                else ", ".join(arrival_failures),
            )
        )

        order_failures = []
        for bus in result.bus_schedules.values():
            planned_indices = [route.order[station_id] for station_id in bus.charge_plan]
            if len(planned_indices) > 1:
                ascending = planned_indices == sorted(planned_indices)
                descending = planned_indices == sorted(planned_indices, reverse=True)
                if not (ascending or descending):
                    order_failures.append(bus.bus_id)
        messages.append(
            ValidationMessage(
                rule="Route order",
                ok=not order_failures,
                message="All station visits follow route order."
                if not order_failures
                else ", ".join(order_failures),
            )
        )
        return messages


def result_debug_summary(result: ScheduleResult) -> str:
    waits = [bus.total_wait_min for bus in result.bus_schedules.values()]
    max_wait = max(waits) if waits else 0.0
    arrivals = [bus.arrival_min for bus in result.bus_schedules.values() if bus.arrival_min is not None]
    latest = format_clock(max(arrivals)) if arrivals else ""
    return f"objective={result.objective_score:.2f}, max_wait={max_wait:.0f}, latest={latest}"
