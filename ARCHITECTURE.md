<![CDATA[# 📐 Architecture — Bus Charging Scheduler

> Deep-dive into every component, data model, algorithm, and design decision.

---

## Table of Contents

- [System Overview](#system-overview)
- [High-Level Data Flow](#high-level-data-flow)
- [Module Dependency Graph](#module-dependency-graph)
- [Component Breakdown](#component-breakdown)
  - [Data Models (models.py)](#1-data-models--modelspy)
  - [Scenario I/O (scenario_io.py)](#2-scenario-io--scenario_iopy)
  - [Formatting Utilities (formatting.py)](#3-formatting-utilities--formattingpy)
  - [Objective Rules (rules.py)](#4-objective-rules--rulespy)
  - [Scheduling Engine (scheduler.py)](#5-scheduling-engine--schedulerpy)
  - [Streamlit Dashboard (app.py)](#6-streamlit-dashboard--apppy)
- [Scheduling Algorithm Deep-Dive](#scheduling-algorithm-deep-dive)
- [Data Structure — Scenario JSON](#data-structure--scenario-json)
- [Data Model Relationships](#data-model-relationships)
- [Scoring System Internals](#scoring-system-internals)
- [Hard-Rule Validation](#hard-rule-validation)
- [Extensibility Design](#extensibility-design)
- [Data-Only Changes Matrix](#data-only-changes-matrix)
- [Design Decisions & Tradeoffs](#design-decisions--tradeoffs)
- [Assumptions](#assumptions)

---

## System Overview

The Bus Charging Scheduler is a **constraint-aware search engine** that assigns electric buses to shared en-route charging stations. It uses a modular pipeline architecture with clean separation between:

- **Feasibility** — hard constraints that must never be violated
- **Optimality** — soft scoring rules with configurable weights
- **Presentation** — an interactive dashboard decoupled from the engine

```mermaid
graph TB
    subgraph SYSTEM["🔧 Bus Charging Scheduler"]
        direction TB
        subgraph DATA["Data Layer"]
            JSON["📄 Scenario JSON"]
            PARSE["📥 scenario_io.py"]
            MODELS["📦 models.py"]
        end

        subgraph CORE["Scheduling Core"]
            ROUTE["🗺️ RouteModel"]
            PLANGEN["🔍 Candidate Plan Generator"]
            ASSIGN["📋 Initial Assignment"]
            SIMENG["⚡ Event Simulation"]
            SEARCH["🔄 Local Search"]
        end

        subgraph EVAL["Evaluation Layer"]
            RULES["📊 Objective Rules"]
            VALIDATOR["✅ Hard-Rule Validator"]
        end

        subgraph UI["Presentation Layer"]
            STREAMLIT["🖥️ Streamlit Dashboard"]
            TABS["📑 4 Interactive Tabs"]
            SIDEBAR["🎚️ Weight Controls"]
        end
    end

    JSON --> PARSE --> MODELS
    MODELS --> ROUTE --> PLANGEN --> ASSIGN --> SIMENG --> SEARCH
    SEARCH --> RULES
    SIMENG --> VALIDATOR
    RULES --> STREAMLIT
    VALIDATOR --> STREAMLIT
    STREAMLIT --> TABS
    STREAMLIT --> SIDEBAR

    style DATA fill:#DBEAFE,stroke:#3B82F6,color:#1E3A5F
    style CORE fill:#FEF3C7,stroke:#F59E0B,color:#78350F
    style EVAL fill:#D1FAE5,stroke:#10B981,color:#064E3B
    style UI fill:#F3E8FF,stroke:#8B5CF6,color:#4C1D95
```

---

## High-Level Data Flow

```mermaid
sequenceDiagram
    participant User as 👤 User
    participant UI as 🖥️ Streamlit
    participant IO as 📥 scenario_io
    participant Engine as ⚙️ WeightedScheduler
    participant Rules as 📊 Rules
    participant Validator as ✅ Validator

    User->>UI: Select scenario + weights
    UI->>IO: load_raw_scenario(path)
    IO-->>UI: Raw JSON dict
    UI->>IO: parse_scenario(raw)
    IO-->>UI: Scenario dataclass
    UI->>IO: with_weight_overrides(scenario, weights)
    IO-->>UI: Modified Scenario
    UI->>Engine: schedule(scenario)

    Note over Engine: 1. Build RouteModel
    Note over Engine: 2. Generate candidate plans (DFS)
    Note over Engine: 3. Initial assignment (load-balanced)
    Note over Engine: 4. Event simulation (min-heap)
    Note over Engine: 5. Local search (plan swaps)

    Engine->>Rules: evaluate_objective(scenario, result)
    Rules-->>Engine: (score, contributions)
    Engine->>Validator: validate(scenario, route, result)
    Validator-->>Engine: [ValidationMessage, ...]
    Engine-->>UI: ScheduleResult
    UI-->>User: Dashboard with metrics, tables, audit
```

---

## Module Dependency Graph

Every import relationship in the codebase:

```mermaid
graph TD
    APP["app.py<br/><i>313 lines · Streamlit UI</i>"]
    INIT["__init__.py<br/><i>Public API</i>"]
    SCHED["scheduler.py<br/><i>650 lines · Core engine</i>"]
    RULES["rules.py<br/><i>96 lines · Scoring rules</i>"]
    SCENIO["scenario_io.py<br/><i>95 lines · JSON I/O</i>"]
    MODELS["models.py<br/><i>146 lines · Dataclasses</i>"]
    FORMAT["formatting.py<br/><i>37 lines · Display utils</i>"]
    TESTS["test_scheduler.py<br/><i>72 lines · Unit tests</i>"]

    APP --> SCENIO
    APP --> SCHED
    APP --> FORMAT
    INIT --> SCENIO
    INIT --> SCHED
    SCHED --> MODELS
    SCHED --> RULES
    SCHED --> FORMAT
    RULES --> MODELS
    SCENIO --> MODELS
    SCENIO --> FORMAT
    TESTS --> SCENIO
    TESTS --> SCHED

    style APP fill:#8B5CF6,color:#fff,stroke:#7C3AED
    style SCHED fill:#F59E0B,color:#fff,stroke:#D97706
    style RULES fill:#10B981,color:#fff,stroke:#059669
    style MODELS fill:#3B82F6,color:#fff,stroke:#2563EB
    style SCENIO fill:#06B6D4,color:#fff,stroke:#0891B2
    style FORMAT fill:#6366F1,color:#fff,stroke:#4F46E5
    style TESTS fill:#EF4444,color:#fff,stroke:#DC2626
    style INIT fill:#8B5CF6,color:#fff,stroke:#7C3AED
```

---

## Component Breakdown

### 1. Data Models — `models.py`

**Purpose:** Immutable dataclasses that represent every entity in the system. Frozen where possible to prevent accidental mutation during scheduling.

```mermaid
classDiagram
    direction TB

    class Scenario {
        +str id
        +str name
        +str description
        +tuple~Node~ nodes
        +tuple~Segment~ segments
        +VehicleConfig vehicle
        +dict weights
        +SchedulerConfig scheduler
        +tuple~Bus~ buses
        +dict raw
    }

    class Node {
        +str id
        +str name
        +str kind
        +int chargers
        +dict attributes
    }

    class Segment {
        +str from_id
        +str to_id
        +float distance_km
        +dict attributes
    }

    class VehicleConfig {
        +float battery_range_km
        +int charge_minutes
        +float speed_kmph
        +minutes_per_km() float
    }

    class SchedulerConfig {
        +int candidate_plan_limit
        +int local_search_passes
    }

    class Bus {
        +str id
        +str operator
        +str origin
        +str destination
        +str departure
        +int departure_min
        +dict attributes
    }

    class ScheduleResult {
        +str scenario_id
        +dict bus_schedules
        +dict station_schedules
        +float objective_score
        +list objective_contributions
        +list validations
        +dict candidate_plans
        +int search_iterations
    }

    class BusSchedule {
        +str bus_id
        +str operator
        +str direction
        +float departure_min
        +float arrival_min
        +tuple charge_plan
        +float total_wait_min
        +list timeline
        +list charge_events
        +list range_legs
    }

    class ChargeEvent {
        +int sequence
        +str station_id
        +float arrival_min
        +float start_min
        +float end_min
        +float wait_min
        +int charger_id
    }

    class StationSlot {
        +str station_id
        +int order
        +int charger_id
        +str bus_id
        +float arrival_min
        +float start_min
        +float end_min
        +float wait_min
    }

    class ValidationMessage {
        +str rule
        +bool ok
        +str message
    }

    class ObjectiveContribution {
        +str name
        +float weight
        +float raw_score
        +float weighted_score
    }

    Scenario "1" *-- "many" Node
    Scenario "1" *-- "many" Segment
    Scenario "1" *-- "1" VehicleConfig
    Scenario "1" *-- "1" SchedulerConfig
    Scenario "1" *-- "many" Bus
    ScheduleResult "1" *-- "many" BusSchedule
    ScheduleResult "1" *-- "many" StationSlot
    ScheduleResult "1" *-- "many" ValidationMessage
    ScheduleResult "1" *-- "many" ObjectiveContribution
    BusSchedule "1" *-- "many" ChargeEvent
```

**Key design choices:**
- `Scenario`, `Node`, `Segment`, `Bus`, `VehicleConfig` are **frozen** — immutable after creation
- Every model carries an `attributes` dict for **forward-compatible** extension (e.g. `priority`, tariff bands)
- `Scenario.raw` preserves the original JSON for rules that need raw data access

---

### 2. Scenario I/O — `scenario_io.py`

**Purpose:** Parse JSON files into typed `Scenario` objects. Handles clock parsing, unknown-field preservation, and weight overrides.

| Function | Description |
|---|---|
| `load_raw_scenario(path)` | Read JSON file → raw dict |
| `parse_scenario(raw)` | Raw dict → `Scenario` dataclass |
| `load_scenario(path)` | Combined load + parse |
| `load_scenarios(directory)` | Glob `*.json` in folder → list of `(Path, Scenario)` |
| `with_weight_overrides(scenario, weights)` | Return new `Scenario` with replaced weights (immutable) |
| `scenario_names(scenarios)` | Build `{name: path}` lookup |

**Field handling:** Known fields (`NODE_FIELDS`, `SEGMENT_FIELDS`, `BUS_FIELDS`) are mapped to typed dataclass fields. Everything else flows into `attributes` — this means you can add `"priority": "high"` to a bus and a custom rule can read it without touching the parser.

---

### 3. Formatting Utilities — `formatting.py`

**Purpose:** Human-readable time display for the dashboard.

| Function | Input | Output |
|---|---|---|
| `parse_clock("19:45")` | `"HH:MM"` string | `1185` (minutes since midnight) |
| `format_clock(1185)` | Minutes float | `"19:45"` |
| `format_clock(1855)` | Minutes > 24h | `"06:55 (+1d)"` |
| `format_duration(53)` | Minutes | `"53m"` |
| `format_duration(125)` | Minutes | `"2h 5m"` |

---

### 4. Objective Rules — `rules.py`

**Purpose:** Pluggable scoring rules that define the multi-objective optimization landscape.

```mermaid
graph TD
    subgraph PROTOCOL["ObjectiveRule Protocol"]
        P["name: str<br/>score(scenario, result) → float"]
    end

    subgraph RULES["Registered Rules"]
        IND["IndividualWaitRule<br/><i>name='individual'</i>"]
        OPR["OperatorSmoothnessRule<br/><i>name='operator'</i>"]
        NET["OverallNetworkRule<br/><i>name='overall'</i>"]
    end

    subgraph EVALUATOR["evaluate_objective()"]
        EVAL["For each rule:<br/>raw = rule.score()<br/>weighted = raw × weight<br/>total += weighted"]
    end

    P -.->|implements| IND
    P -.->|implements| OPR
    P -.->|implements| NET
    IND --> EVAL
    OPR --> EVAL
    NET --> EVAL
    EVAL --> RESULT["(total_score, contributions[])"]

    style PROTOCOL fill:#E0E7FF,stroke:#6366F1,color:#312E81
    style RULES fill:#D1FAE5,stroke:#10B981,color:#064E3B
    style EVALUATOR fill:#FEF3C7,stroke:#F59E0B,color:#78350F
```

#### Rule Formulas

**IndividualWaitRule** (`name = "individual"`)
```
score = max(all_waits) + 0.12 × Σ(wait²) / N
```
Penalizes the worst bus wait + applies convex pressure to avoid hiding one very delayed bus inside a good average.

**OperatorSmoothnessRule** (`name = "operator"`)
```
per_operator = avg_wait + 0.5 × max_wait + 0.5 × stdev
score = mean(per_operator) + mean(|avg_i - network_avg|)
```
Penalizes within-operator variance + between-operator imbalance.

**OverallNetworkRule** (`name = "overall"`)
```
score = total_wait + 0.8 × total_charge_time + 0.05 × arrival_span
```
Penalizes aggregate wait, total charging time, and how long until the last bus arrives.

---

### 5. Scheduling Engine — `scheduler.py`

**Purpose:** The core 650-line engine that generates plans, simulates shared charger contention, and optimizes assignments.

#### Class Structure

```mermaid
graph TD
    subgraph CLASSES["scheduler.py Classes"]
        WS["WeightedScheduler<br/><i>Main orchestrator</i>"]
        RM["RouteModel<br/><i>Distance & direction graph</i>"]
        CR["ChargeRequest<br/><i>Station queue entry</i>"]
    end

    WS --> |"builds"| RM
    WS --> |"creates"| CR

    subgraph WS_METHODS["WeightedScheduler Methods"]
        SCHEDULE["schedule()<br/><i>Main entry point</i>"]
        GENPLANS["generate_candidate_plans()<br/><i>DFS plan enumeration</i>"]
        INITASSIGN["_initial_assignment()<br/><i>Load-balanced heuristic</i>"]
        SIMULATE["_simulate()<br/><i>Event queue processing</i>"]
        DISPATCH["_dispatch_station()<br/><i>Charger allocation</i>"]
        SELECT["_select_request_index()<br/><i>Weighted priority pick</i>"]
        SCORE["_score_result()<br/><i>Objective evaluation</i>"]
        VALIDATE["validate()<br/><i>5 hard-rule checks</i>"]
    end

    SCHEDULE --> GENPLANS --> INITASSIGN --> SIMULATE --> SCORE
    SIMULATE --> DISPATCH --> SELECT
    SCHEDULE --> VALIDATE

    style CLASSES fill:#FEF3C7,stroke:#F59E0B,color:#78350F
    style WS_METHODS fill:#FED7AA,stroke:#EA580C,color:#7C2D12
```

#### RouteModel API

| Method | What It Does |
|---|---|
| `distance(from, to)` | Absolute distance between any two nodes using cumulative positions |
| `travel_minutes(from, to)` | Ceiling of `distance / speed` in minutes |
| `direction(origin, dest)` | `+1` (forward) or `-1` (reverse) |
| `charging_stations_between(origin, dest)` | Ordered list of chargeable stations between two nodes |
| `leg_distances(origin, plan, dest)` | Distance of each leg: origin → plan[0] → plan[1] → ... → dest |

---

### 6. Streamlit Dashboard — `app.py`

**Purpose:** 313-line interactive web application that renders scheduling results.

```mermaid
graph TD
    subgraph DASHBOARD["🖥️ Streamlit Dashboard"]
        HEADER["Title + Scenario Selector"]
        METRICS["5 Metric Cards<br/><i>Buses · Charges · Max Wait · Latest Arrival · Objective</i>"]
        STATUS["Hard-Rule Status Badge"]

        subgraph TABS["4 Tabs"]
            T1["📋 Scenario Input<br/><i>Departures · Route · Weights · Raw JSON</i>"]
            T2["🚌 Per-bus Timetable<br/><i>Summary · Timeline · Charge Details</i>"]
            T3["🔌 Per-station Order<br/><i>Station sub-tabs · Charger schedules</i>"]
            T4["🔍 Audit<br/><i>Validations · Objective Breakdown · Candidates</i>"]
        end

        SIDEBAR["🎚️ Sidebar<br/><i>Weight toggle + sliders</i>"]
    end

    HEADER --> METRICS --> STATUS --> TABS
    SIDEBAR -.-> HEADER

    style DASHBOARD fill:#F3E8FF,stroke:#8B5CF6,color:#4C1D95
    style TABS fill:#EDE9FE,stroke:#8B5CF6,color:#4C1D95
```

**Caching:** Both `scenario_index()` and `run_schedule()` use `@st.cache_data` — the schedule only re-runs when the scenario file or weight tuple changes.

**DataFrame helpers** (8 functions): Transform `Scenario` and `ScheduleResult` objects into clean `pd.DataFrame` tables for display.

---

## Scheduling Algorithm Deep-Dive

### Stage 1 — Candidate Plan Generation (DFS)

For each bus, enumerate all feasible subsets of charging stations along its route where every leg is within battery range:

```mermaid
graph TD
    START["Origin: Bengaluru<br/><i>Battery: 240 km</i>"]

    START -->|"100 km ✅"| A["Stop at A<br/><i>Recharge → 240 km</i>"]
    START -->|"220 km ✅"| B["Stop at B<br/><i>Recharge → 240 km</i>"]

    A -->|"120 km ✅"| AB["Stop at B<br/><i>Recharge → 240 km</i>"]
    A -->|"220 km ✅"| AC["Stop at C<br/><i>Recharge → 240 km</i>"]

    AB -->|"100 km ✅"| ABC["Stop at C"]
    AB -->|"220 km ✅"| ABD["Stop at D"]

    B -->|"100 km ✅"| BC["Stop at C"]
    B -->|"220 km ✅"| BD["Stop at D"]

    style START fill:#1E40AF,color:#fff
    style A fill:#EA580C,color:#fff
    style B fill:#EA580C,color:#fff
    style AB fill:#EA580C,color:#fff
    style AC fill:#EA580C,color:#fff
    style ABC fill:#EA580C,color:#fff
    style ABD fill:#EA580C,color:#fff
    style BC fill:#EA580C,color:#fff
    style BD fill:#EA580C,color:#fff
```

**Sort key priority:** `(# stops, short-leg penalty, -min_slack, station IDs)` — prefers fewer stops, balanced legs, and more battery margin.

**Cap:** Limited to `candidate_plan_limit` (default 24) plans per bus.

### Stage 2 — Initial Assignment

Greedy forward pass in departure order. For each bus, score every candidate plan by:

```
score = w_overall × charge_penalty
      + w_individual × (bucket_congestion + station_load)
      + w_operator × operator_bucket_congestion
```

Using 20-minute time buckets to spread demand temporally.

### Stage 3 — Event Simulation

Min-heap event queue with two event types:

| Event | Trigger | Action |
|---|---|---|
| `arrival` | Bus reaches a station or destination | If station: enqueue charge request. If destination: record arrival. |
| `charger_free` | A charger finishes a session | Re-dispatch the station queue. |

### Stage 4 — Weighted Dispatch

When a charger becomes free and buses are queued, `_select_request_index()` picks which bus charges next:

```
priority = w_overall × arrival_time
         - w_individual × (1.8 × current_wait + 0.6 × cumulative_wait)
         - w_operator × (operator_avg_wait + 4.0 × operator_queue_depth)
```

This creates a **weight-responsive dispatch**: high individual weight favors long-waiting buses; high operator weight favors operators with accumulated pressure; high overall weight favors FCFS throughput.

### Stage 5 — Local Search

```
For each pass in local_search_passes:
    Sort buses by total_wait (worst first)
    For each bus:
        Try every alternative candidate plan
        If any plan lowers the global objective → swap
    If no improvement in this pass → stop early
```

---

## Data Structure — Scenario JSON

```mermaid
graph TD
    ROOT["📄 Scenario JSON"]
    ROOT --> ID["id: string"]
    ROOT --> NAME["name: string"]
    ROOT --> DESC["description: string"]
    ROOT --> ROUTE["route"]
    ROOT --> VEHICLE["vehicle"]
    ROOT --> WEIGHTS["weights"]
    ROOT --> SCHED["scheduler"]
    ROOT --> BUSES["buses[]"]

    ROUTE --> NODES["nodes[]"]
    ROUTE --> SEGS["segments[]"]

    NODES --> NODE_F["id · name · kind · chargers<br/><i>+ any extra fields → attributes</i>"]
    SEGS --> SEG_F["from · to · distance_km<br/><i>+ any extra fields → attributes</i>"]
    VEHICLE --> VEH_F["battery_range_km<br/>charge_minutes<br/>speed_kmph"]
    WEIGHTS --> W_F["rule_name: weight_value<br/><i>e.g. individual: 1.0</i>"]
    SCHED --> S_F["candidate_plan_limit: 24<br/>local_search_passes: 3"]
    BUSES --> BUS_F["id · operator · origin<br/>destination · departure<br/><i>+ any extra fields → attributes</i>"]

    style ROOT fill:#3B82F6,color:#fff,stroke:#2563EB
    style ROUTE fill:#06B6D4,color:#fff,stroke:#0891B2
    style VEHICLE fill:#8B5CF6,color:#fff,stroke:#7C3AED
    style WEIGHTS fill:#10B981,color:#fff,stroke:#059669
    style SCHED fill:#F59E0B,color:#fff,stroke:#D97706
    style BUSES fill:#EF4444,color:#fff,stroke:#DC2626
```

---

## Data Model Relationships

How entities connect at runtime:

```mermaid
erDiagram
    SCENARIO ||--o{ NODE : "has nodes"
    SCENARIO ||--o{ SEGMENT : "has segments"
    SCENARIO ||--|| VEHICLE_CONFIG : "has vehicle"
    SCENARIO ||--|| SCHEDULER_CONFIG : "has config"
    SCENARIO ||--o{ BUS : "has buses"

    SCHEDULE_RESULT ||--o{ BUS_SCHEDULE : "contains"
    SCHEDULE_RESULT ||--o{ STATION_SLOT : "contains"
    SCHEDULE_RESULT ||--o{ VALIDATION_MSG : "contains"
    SCHEDULE_RESULT ||--o{ OBJ_CONTRIBUTION : "contains"

    BUS_SCHEDULE ||--o{ CHARGE_EVENT : "has charges"
    BUS_SCHEDULE ||--o{ RANGE_LEG : "has legs"

    NODE {
        string id PK
        string name
        string kind
        int chargers
    }

    SEGMENT {
        string from_id FK
        string to_id FK
        float distance_km
    }

    BUS {
        string id PK
        string operator
        string origin FK
        string destination FK
        int departure_min
    }

    BUS_SCHEDULE {
        string bus_id PK
        string operator
        float departure_min
        float arrival_min
        float total_wait_min
    }

    CHARGE_EVENT {
        int sequence
        string station_id FK
        float start_min
        float end_min
        int charger_id
    }

    STATION_SLOT {
        string station_id FK
        int charger_id
        string bus_id FK
        float start_min
        float end_min
    }
```

---

## Scoring System Internals

### Weight Flow Through the System

Weights from `scenario.weights` are used in **three** places — not just scoring:

```mermaid
graph LR
    W["scenario.weights<br/><i>individual · operator · overall</i>"]

    W --> IA["1️⃣ Initial Assignment<br/><i>Plan selection heuristic</i>"]
    W --> SD["2️⃣ Station Dispatch<br/><i>Queue priority ordering</i>"]
    W --> OE["3️⃣ Objective Evaluation<br/><i>Final score computation</i>"]

    style W fill:#7C3AED,color:#fff,stroke:#6D28D9
    style IA fill:#DBEAFE,stroke:#3B82F6,color:#1E3A5F
    style SD fill:#FEF3C7,stroke:#F59E0B,color:#78350F
    style OE fill:#D1FAE5,stroke:#10B981,color:#064E3B
```

This means **changing a weight changes behavior at every level** — not just the final score. The dispatch order adapts, the initial assignment adapts, and the objective reflects the new emphasis.

---

## Hard-Rule Validation

5 post-schedule checks, each producing a `PASS` or `FAIL` with a detailed message:

```mermaid
graph LR
    V["validate()"] --> R1["🔋 Battery Range<br/><i>No leg > battery_range_km</i>"]
    V --> R2["🔌 Charger Exclusivity<br/><i>No overlapping sessions</i>"]
    V --> R3["⏱️ Charge Duration<br/><i>Each charge = charge_minutes</i>"]
    V --> R4["🏁 Trip Completion<br/><i>Every bus reaches destination</i>"]
    V --> R5["📏 Route Order<br/><i>Stations visited in order</i>"]

    style V fill:#7C3AED,color:#fff,stroke:#6D28D9
    style R1 fill:#D1FAE5,stroke:#10B981,color:#064E3B
    style R2 fill:#D1FAE5,stroke:#10B981,color:#064E3B
    style R3 fill:#D1FAE5,stroke:#10B981,color:#064E3B
    style R4 fill:#D1FAE5,stroke:#10B981,color:#064E3B
    style R5 fill:#D1FAE5,stroke:#10B981,color:#064E3B
```

---

## Extensibility Design

### Adding a New Rule — Complete Flow

```mermaid
sequenceDiagram
    participant Dev as 👩‍💻 Developer
    participant Rules as rules.py
    participant JSON as scenario.json
    participant Engine as scheduler.py
    participant UI as app.py

    Dev->>Rules: 1. Create PriorityWaitRule class
    Note over Rules: name = "priority"<br/>score(scenario, result) → float
    Dev->>Rules: 2. Add to DEFAULT_OBJECTIVE_RULES
    Dev->>JSON: 3. Add "priority": 3.0 to weights

    Note over Engine: Engine auto-discovers rule<br/>via evaluate_objective()
    Note over UI: Score shows in Audit tab<br/>with weight & raw/weighted values
```

### Rule Protocol

Any object with `name: str` and `score(scenario, result) → float` qualifies:

```python
class ObjectiveRule(Protocol):
    name: str
    def score(self, scenario: Scenario, result: ScheduleResult) -> float: ...
```

---

## Data-Only Changes Matrix

Every change below requires **zero code modifications** — only scenario JSON edits:

| Change | Where to Edit | What the Engine Does |
|---|---|---|
| Add station E | `route.nodes` + `route.segments` | Candidate generation discovers it automatically |
| Remove station from scheduling | Set `"chargers": 0` | Station is skipped by candidate generator |
| Double chargers at B | `"chargers": 2` | Station calendar creates two charger lanes |
| Change segment distance | `route.segments` | Feasibility and travel time recompute |
| Change battery range | `vehicle.battery_range_km` | Candidate plans recompute with new range |
| Change charge duration | `vehicle.charge_minutes` | Simulation and validation use new duration |
| Change bus speed | `vehicle.speed_kmph` | Travel times recompute |
| Add / remove buses | `buses[]` | No fixed count in code |
| Add operator | New operator string | Operator scoring groups dynamically |
| Reverse / mix directions | Set origin & destination | Route order is inferred per bus |
| Add scenario weights | New weight key matching rule name | Missing weights default to 0.0 |
| Add extra bus metadata | Extra fields on bus object | Preserved in `bus.attributes` — accessible by custom rules |
| Add extra station metadata | Extra fields on node object | Preserved in `node.attributes` |

---

## Design Decisions & Tradeoffs

| Decision | Why |
|---|---|
| **DFS over ILP/LP** | Interpretable, fast for 20-bus scale, easy to debug. ILP would be overkill for this problem size. |
| **Frozen dataclasses** | Prevent accidental mutation during multi-pass scheduling. Immutability makes caching safe. |
| **Min-heap event sim** | Clean temporal ordering of arrivals and charger-free events. O(n log n) complexity. |
| **Weights in 3 places** | Assignment, dispatch, and scoring all respond to the same weights — one lever controls the full pipeline. |
| **Local search (not SA/GA)** | Deterministic, reproducible, and sufficient for the problem scale. No random restarts needed. |
| **JSON files (not DB)** | Self-contained scenarios. No server setup. Easy to version control and share. |
| **`attributes` dicts** | Forward-compatible: new fields in JSON survive parsing without schema changes. |
| **Protocol-based rules** | Structural typing via `Protocol` — no base class inheritance needed. Just match the interface. |

---

## Assumptions

| Assumption | Implication |
|---|---|
| All departures happen on the same service day | Arrivals may roll past midnight (shown as `+1d`) |
| Travel speed is constant per scenario | Configured in `vehicle.speed_kmph` |
| Charging always fills to full | Fixed duration, no partial charges |
| Endpoint slow chargers are outside scope | Terminals have `chargers: 0` |
| A bus only waits at stations where it charges | No pass-through delays |
| Route is modeled as a linear ordered path | Multiple independent routes sharing stations would be the next extension; the station calendar already keys resources by station ID |

---

<div align="center">

*For usage instructions, setup, and deployment — see [README.md](README.md)*

</div>
]]>
