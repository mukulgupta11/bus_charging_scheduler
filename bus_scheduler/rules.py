from __future__ import annotations

import math
from collections import defaultdict
from typing import Protocol

from .models import ObjectiveContribution, ScheduleResult, Scenario


class ObjectiveRule(Protocol):
    name: str

    def score(self, scenario: Scenario, result: ScheduleResult) -> float:
        ...


class IndividualWaitRule:
    name = "individual"

    def score(self, scenario: Scenario, result: ScheduleResult) -> float:
        waits = [bus.total_wait_min for bus in result.bus_schedules.values()]
        if not waits:
            return 0.0
        convex_pressure = sum(wait * wait for wait in waits) / len(waits)
        return max(waits) + 0.12 * convex_pressure


class OperatorSmoothnessRule:
    name = "operator"

    def score(self, scenario: Scenario, result: ScheduleResult) -> float:
        waits_by_operator: dict[str, list[float]] = defaultdict(list)
        for bus in result.bus_schedules.values():
            waits_by_operator[bus.operator].append(bus.total_wait_min)
        if not waits_by_operator:
            return 0.0

        operator_scores = []
        operator_averages = []
        for waits in waits_by_operator.values():
            avg_wait = sum(waits) / len(waits)
            operator_averages.append(avg_wait)
            variance = sum((wait - avg_wait) ** 2 for wait in waits) / len(waits)
            stdev = math.sqrt(variance)
            operator_scores.append(avg_wait + 0.5 * max(waits) + 0.5 * stdev)

        network_avg = sum(operator_averages) / len(operator_averages)
        between_operator_imbalance = sum(
            abs(avg - network_avg) for avg in operator_averages
        ) / len(operator_averages)
        return sum(operator_scores) / len(operator_scores) + between_operator_imbalance


class OverallNetworkRule:
    name = "overall"

    def score(self, scenario: Scenario, result: ScheduleResult) -> float:
        buses = list(result.bus_schedules.values())
        if not buses:
            return 0.0
        total_wait = sum(bus.total_wait_min for bus in buses)
        total_charge = sum(bus.total_charge_min for bus in buses)
        completed_arrivals = [bus.arrival_min for bus in buses if bus.arrival_min is not None]
        arrival_span = max(completed_arrivals) - min(bus.departure_min for bus in buses)
        return total_wait + 0.8 * total_charge + 0.05 * arrival_span


DEFAULT_OBJECTIVE_RULES: tuple[ObjectiveRule, ...] = (
    IndividualWaitRule(),
    OperatorSmoothnessRule(),
    OverallNetworkRule(),
)


def evaluate_objective(
    scenario: Scenario,
    result: ScheduleResult,
    rules: tuple[ObjectiveRule, ...] = DEFAULT_OBJECTIVE_RULES,
) -> tuple[float, list[ObjectiveContribution]]:
    contributions: list[ObjectiveContribution] = []
    total = 0.0
    for rule in rules:
        raw_score = float(rule.score(scenario, result))
        weight = float(scenario.weights.get(rule.name, 0.0))
        weighted = raw_score * weight
        total += weighted
        contributions.append(
            ObjectiveContribution(
                name=rule.name,
                weight=weight,
                raw_score=raw_score,
                weighted_score=weighted,
            )
        )
    return total, contributions
