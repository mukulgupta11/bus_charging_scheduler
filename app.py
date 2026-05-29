from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from bus_scheduler.formatting import format_clock, format_duration
from bus_scheduler.scenario_io import (
    load_raw_scenario,
    parse_scenario,
    with_weight_overrides,
)
from bus_scheduler.scheduler import WeightedScheduler


ROOT = Path(__file__).parent
SCENARIO_DIR = ROOT / "data" / "scenarios"


st.set_page_config(
    page_title="Bus Charging Scheduler",
    layout="wide",
    initial_sidebar_state="collapsed",
)


st.markdown(
    """
    <style>
    .block-container { padding-top: 1.35rem; padding-bottom: 2rem; max-width: 1320px; }
    h1 { font-size: 2rem !important; margin-bottom: 0.25rem !important; }
    h2, h3 { letter-spacing: 0 !important; }
    div[data-testid="stMetric"] { background: rgba(127, 127, 127, 0.08); border: 1px solid rgba(127, 127, 127, 0.22); border-radius: 8px; padding: 0.75rem; }
    div[data-testid="stMetric"] label { color: inherit; opacity: 0.72; }
    div[data-testid="stMetricValue"] { color: inherit; }
    .status-ok { color: #116b39; font-weight: 650; }
    .status-bad { color: #b42318; font-weight: 650; }
    .small-muted { color: #667085; font-size: 0.92rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(show_spinner=False)
def scenario_index() -> list[dict[str, str]]:
    index = []
    for path in sorted(SCENARIO_DIR.glob("*.json")):
        raw = load_raw_scenario(path)
        index.append({"file": path.name, "name": raw["name"], "description": raw.get("description", "")})
    return index


@st.cache_data(show_spinner="Scheduling scenario...")
def run_schedule(file_name: str, weights: tuple[tuple[str, float], ...]):
    raw = load_raw_scenario(SCENARIO_DIR / file_name)
    scenario = parse_scenario(raw)
    scenario = with_weight_overrides(scenario, dict(weights))
    result = WeightedScheduler().schedule(scenario)
    return scenario, result


def node_names(scenario) -> dict[str, str]:
    return {node.id: node.name for node in scenario.nodes}


def route_dataframe(scenario) -> pd.DataFrame:
    names = node_names(scenario)
    return pd.DataFrame(
        {
            "From": names[segment.from_id],
            "To": names[segment.to_id],
            "Distance (km)": segment.distance_km,
        }
        for segment in scenario.segments
    )


def input_bus_dataframe(scenario) -> pd.DataFrame:
    names = node_names(scenario)
    return pd.DataFrame(
        {
            "Bus ID": bus.id,
            "Operator": bus.operator,
            "Direction": f"{names[bus.origin]} -> {names[bus.destination]}",
            "Departure": bus.departure,
        }
        for bus in scenario.buses
    )


def validation_dataframe(result) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Status": "PASS" if validation.ok else "FAIL",
            "Rule": validation.rule,
            "Message": validation.message,
        }
        for validation in result.validations
    )


def objective_dataframe(result) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Rule": contribution.name,
            "Weight": contribution.weight,
            "Raw score": round(contribution.raw_score, 2),
            "Weighted score": round(contribution.weighted_score, 2),
        }
        for contribution in result.objective_contributions
    )


def bus_summary_dataframe(scenario, result) -> pd.DataFrame:
    names = node_names(scenario)
    rows = []
    for bus in sorted(result.bus_schedules.values(), key=lambda item: (item.departure_min, item.bus_id)):
        plan = " -> ".join(names[station_id] for station_id in bus.charge_plan)
        rows.append(
            {
                "Bus ID": bus.bus_id,
                "Operator": bus.operator,
                "Direction": bus.direction,
                "Departure": format_clock(bus.departure_min),
                "Charging plan": plan,
                "Charges": len(bus.charge_events),
                "Total wait": format_duration(bus.total_wait_min),
                "Arrival": format_clock(bus.arrival_min),
            }
        )
    return pd.DataFrame(rows)


def timeline_dataframe(result) -> pd.DataFrame:
    rows = []
    for bus in sorted(result.bus_schedules.values(), key=lambda item: (item.departure_min, item.bus_id)):
        for step, event in enumerate(bus.timeline, start=1):
            rows.append(
                {
                    "Bus ID": bus.bus_id,
                    "Step": step,
                    "Event": event.get("event", ""),
                    "From": event.get("from", ""),
                    "To / Stop": event.get("to", event.get("stop", "")),
                    "Start": format_clock(event.get("start_min")),
                    "End": format_clock(event.get("end_min")),
                    "Duration": format_duration(event.get("duration_min", event.get("end_min", 0) - event.get("start_min", 0))),
                    "Details": event.get("details", ""),
                }
            )
    return pd.DataFrame(rows)


def station_dataframe(slots) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Order": slot.order,
            "Charger": slot.charger_id,
            "Bus ID": slot.bus_id,
            "Operator": slot.operator,
            "Direction": slot.direction,
            "Reached station": format_clock(slot.arrival_min),
            "Charge start": format_clock(slot.start_min),
            "Charge end": format_clock(slot.end_min),
            "Wait": format_duration(slot.wait_min),
        }
        for slot in slots
    )


def charge_detail_dataframe(result) -> pd.DataFrame:
    rows = []
    for bus in sorted(result.bus_schedules.values(), key=lambda item: (item.departure_min, item.bus_id)):
        for charge in bus.charge_events:
            rows.append(
                {
                    "Bus ID": bus.bus_id,
                    "Seq": charge.sequence,
                    "Station": charge.station_name,
                    "Reached": format_clock(charge.arrival_min),
                    "Start": format_clock(charge.start_min),
                    "End": format_clock(charge.end_min),
                    "Wait": format_duration(charge.wait_min),
                    "Charger": charge.charger_id,
                }
            )
    return pd.DataFrame(rows)


index = scenario_index()
if not index:
    st.error("No scenario JSON files were found.")
    st.stop()

st.title("Bus Charging Scheduler")

selected_name = st.selectbox(
    "Scenario",
    [item["name"] for item in index],
    index=0,
)
selected = next(item for item in index if item["name"] == selected_name)
raw_preview = load_raw_scenario(SCENARIO_DIR / selected["file"])
preview_scenario = parse_scenario(raw_preview)

with st.sidebar:
    st.subheader("Weights")
    use_file_weights = st.toggle("Use scenario weights", value=True)
    if use_file_weights:
        selected_weights = preview_scenario.weights
    else:
        selected_weights = {
            "individual": st.slider("Individual", 0.0, 5.0, float(preview_scenario.weights.get("individual", 1.0)), 0.25),
            "operator": st.slider("Operator", 0.0, 5.0, float(preview_scenario.weights.get("operator", 1.0)), 0.25),
            "overall": st.slider("Overall", 0.0, 5.0, float(preview_scenario.weights.get("overall", 1.0)), 0.25),
        }

weights_tuple = tuple(sorted((key, float(value)) for key, value in selected_weights.items()))
scenario, result = run_schedule(selected["file"], weights_tuple)
all_valid = all(validation.ok for validation in result.validations)
bus_count = len(scenario.buses)
charge_count = sum(len(bus.charge_events) for bus in result.bus_schedules.values())
max_wait = max((bus.total_wait_min for bus in result.bus_schedules.values()), default=0)
latest_arrival = max(
    (bus.arrival_min for bus in result.bus_schedules.values() if bus.arrival_min is not None),
    default=None,
)

st.markdown(f"<div class='small-muted'>{scenario.description}</div>", unsafe_allow_html=True)
st.write("")

metric_cols = st.columns(5)
metric_cols[0].metric("Buses", bus_count)
metric_cols[1].metric("Charging sessions", charge_count)
metric_cols[2].metric("Max wait", format_duration(max_wait))
metric_cols[3].metric("Latest arrival", format_clock(latest_arrival))
metric_cols[4].metric("Objective", f"{result.objective_score:,.1f}")

status_text = "All hard rules pass" if all_valid else "Hard-rule failure"
status_class = "status-ok" if all_valid else "status-bad"
st.markdown(f"<span class='{status_class}'>{status_text}</span>", unsafe_allow_html=True)

input_tab, bus_tab, station_tab, audit_tab = st.tabs(
    ["Scenario Input", "Per-bus Timetable", "Per-station Order", "Audit"]
)

with input_tab:
    left, right = st.columns([0.55, 0.45], gap="large")
    with left:
        st.subheader("Departures")
        st.dataframe(input_bus_dataframe(scenario), width="stretch", hide_index=True)
    with right:
        st.subheader("Route")
        st.dataframe(route_dataframe(scenario), width="stretch", hide_index=True)
        st.subheader("Scenario weights")
        st.dataframe(
            pd.DataFrame({"Rule": list(selected_weights.keys()), "Weight": list(selected_weights.values())}),
            width="stretch",
            hide_index=True,
        )
    with st.expander("Raw scenario JSON"):
        st.json(scenario.raw)

with bus_tab:
    st.subheader("Bus summary")
    st.dataframe(bus_summary_dataframe(scenario, result), width="stretch", hide_index=True)
    st.subheader("Full timeline")
    timeline_df = timeline_dataframe(result)
    operators = sorted(timeline_df["Bus ID"].unique())
    selected_buses = st.multiselect("Filter buses", operators, default=[])
    if selected_buses:
        timeline_df = timeline_df[timeline_df["Bus ID"].isin(selected_buses)]
    st.dataframe(timeline_df, width="stretch", hide_index=True)
    st.subheader("Charging details")
    st.dataframe(charge_detail_dataframe(result), width="stretch", hide_index=True)

with station_tab:
    station_nodes = [
        node for node in scenario.nodes if node.kind == "station" and node.chargers > 0
    ]
    station_tabs = st.tabs([node.name for node in station_nodes])
    for tab, node in zip(station_tabs, station_nodes):
        with tab:
            slots = result.station_schedules.get(node.id, [])
            if slots:
                st.dataframe(station_dataframe(slots), width="stretch", hide_index=True)
            else:
                st.info("No scheduled charges at this station.")

with audit_tab:
    left, right = st.columns(2, gap="large")
    with left:
        st.subheader("Validation")
        st.dataframe(validation_dataframe(result), width="stretch", hide_index=True)
    with right:
        st.subheader("Objective contributions")
        st.dataframe(objective_dataframe(result), width="stretch", hide_index=True)
    with st.expander("Candidate plans considered"):
        candidate_rows = []
        names = node_names(scenario)
        for bus_id, plans in result.candidate_plans.items():
            for rank, plan in enumerate(plans, start=1):
                candidate_rows.append(
                    {
                        "Bus ID": bus_id,
                        "Rank": rank,
                        "Plan": " -> ".join(names[station_id] for station_id in plan),
                    }
                )
        st.dataframe(pd.DataFrame(candidate_rows), width="stretch", hide_index=True)
