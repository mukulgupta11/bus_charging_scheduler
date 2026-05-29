# Architecture

## Approach

The scheduler uses a constraint-aware search rather than a fixed hand-coded charging recipe.

1. Build the directed route for each bus from scenario data.
2. Enumerate feasible charging plans where every leg is within battery range.
3. Pick an initial plan assignment that spreads expected demand across stations and time buckets.
4. Simulate the shared chargers with an event queue. Station queues are dispatched by weighted rule pressure.
5. Run a small deterministic local search over each bus's candidate plans and keep lower-scoring schedules.
6. Validate hard rules after scheduling.

This is intentionally modular: hard feasibility is kept separate from soft scoring. Changing weights changes scoring. Adding a new soft rule adds one rule object. Adding chargers, stations, buses, operators, segment distances, speed, range, or charge duration is data-only.

## Data Structure

Each scenario JSON file fully describes the world the scheduler needs:

- `route.nodes`: ordered route nodes. Terminals and charging stations are both nodes; stations carry `chargers`.
- `route.segments`: distances between adjacent nodes.
- `vehicle`: range, fixed charge duration, and speed.
- `weights`: rule weights keyed by rule name.
- `scheduler`: search limits, currently candidate plan limit and local-search passes.
- `buses`: bus id, operator, origin, destination, and departure time.

Unknown fields on buses, nodes, and segments are preserved in `attributes`, and the original JSON is retained on `scenario.raw`. That means future rules can read extra data like `priority`, tariff bands, or driver-shift ids without changing the parser.

## Anticipated Data-only Changes

| Change | How the design handles it |
| --- | --- |
| Add station E | Add a node and adjacent segments. Candidate generation discovers it. |
| Remove a station from scheduling | Set `chargers` to `0` or make it a non-station node. |
| Double chargers at B | Change `"chargers": 2`; station calendars create two charger lanes. |
| Change segment distance | Edit `route.segments`; feasibility and travel time recompute. |
| Change battery range | Edit `vehicle.battery_range_km`; candidate plans recompute. |
| Change charge duration | Edit `vehicle.charge_minutes`; simulation and validation use it. |
| Change bus speed | Edit `vehicle.speed_kmph`; travel times recompute. |
| Add or remove buses | Edit `buses`; no fixed count exists in code. |
| Add an operator | Use a new operator string; operator scoring groups dynamically. |
| Reverse or mix directions | Set origin and destination to any route nodes; route order is inferred. |
| Add scenario weights | Add a weight key matching a rule name. Missing weights default to zero for that rule. |
| Add extra bus metadata | Add fields under a bus object; they are preserved in `bus.attributes`. |
| Add station metadata | Add fields under a node object; they are preserved in `node.attributes`. |

## Weight Example

Scenario 4 emphasizes operator smoothness:

```json
"weights": {
  "individual": 1.0,
  "operator": 2.0,
  "overall": 1.0
}
```

Changing the operator value is enough. There is no second copy of that weight in the engine or UI.

## New Rule Example

Soft rules implement the same small protocol:

```python
class PriorityWaitRule:
    name = "priority"

    def score(self, scenario, result):
        bus_meta = {bus["id"]: bus for bus in scenario.raw["buses"]}
        return sum(
            schedule.total_wait_min
            for schedule in result.bus_schedules.values()
            if bus_meta[schedule.bus_id].get("priority") == "high"
        )
```

Then register it:

```python
DEFAULT_OBJECTIVE_RULES = (
    IndividualWaitRule(),
    OperatorSmoothnessRule(),
    OverallNetworkRule(),
    PriorityWaitRule(),
)
```

And put the weight in scenario JSON:

```json
"weights": {
  "individual": 1.0,
  "operator": 1.0,
  "overall": 1.0,
  "priority": 3.0
}
```

The engine still enumerates plans, simulates chargers, and evaluates rule contributions the same way.

## Current Rules

- `individual`: penalizes the worst bus wait and applies a convex pressure to avoid hiding one very delayed bus inside a good average.
- `operator`: penalizes average, max, and spread of wait within each operator, plus imbalance between operators.
- `overall`: penalizes total wait, total charging time, and the span of network completion.

The station dispatcher uses the same weight names to choose between buses already waiting at a charger. Overall weight leans toward first-come-first-served throughput, individual weight leans toward long-wait pressure, and operator weight leans toward operators with accumulated or queued pressure.

## Hard Rules

After every schedule the validator checks:

- No battery leg exceeds range.
- No two sessions overlap on the same charger.
- Every charge lasts exactly the configured charge duration.
- Every bus reaches its destination.
- Station visits follow route order.

## Assumptions

- All departures happen on the same service day; arrivals may roll past midnight.
- Travel speed is constant and configured per scenario.
- Charging always fills to full and has fixed duration.
- Endpoint slow chargers are outside the scheduling problem.
- A bus only waits at stations where it has chosen to charge.
- The route is modeled as a linear ordered route. Multiple independent route objects sharing stations would be the next parser extension; the station calendar itself already keys resources by station id.
