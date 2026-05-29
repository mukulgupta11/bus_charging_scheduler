# Bus Charging Scheduler

Python + Streamlit take-home for scheduling electric buses across shared charging stations.

## Run Locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

Run tests:

```powershell
python -m unittest discover -s tests
```

## Project Layout

- `app.py` - Streamlit UI.
- `bus_scheduler/` - scheduling engine, data parsing, formatting, and scoring rules.
- `data/scenarios/` - all five scenario JSON files.
- `tests/` - hard-rule and data-only change checks.
- `ARCHITECTURE.md` - design notes, tradeoffs, and extension examples.

## Change a Weight

Weights live in one obvious place in each scenario file:

```json
"weights": {
  "individual": 1.0,
  "operator": 2.0,
  "overall": 1.0
}
```

The Streamlit sidebar also has temporary weight overrides so reviewers can see how a scenario reacts without editing JSON.

## Add a Scenario

Copy any file in `data/scenarios/`, change `id`, `name`, route data, weights, and `buses`. The app discovers every `*.json` file in that folder automatically.

## Add a Rule

Add a class in `bus_scheduler/rules.py` with a unique `name` and `score(...)`, then include it in `DEFAULT_OBJECTIVE_RULES`. Put its weight in scenario JSON using the same name. The scheduler engine does not need to change.

```python
class PriorityBusRule:
    name = "priority"

    def score(self, scenario, result):
        bus_meta = {bus["id"]: bus for bus in scenario.raw["buses"]}
        return sum(
            bus.total_wait_min
            for bus in result.bus_schedules.values()
            if bus_meta[bus.bus_id].get("priority") == "high"
        )
```

See `ARCHITECTURE.md` for the complete extension pattern.

## Streamlit Cloud

Push this repo to GitHub, create a Streamlit Community Cloud app, and set the entrypoint to `app.py`. Streamlit installs dependencies from `requirements.txt`.
