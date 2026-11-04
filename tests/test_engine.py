import pytest

from workflow.engine import RunState, Workflow


def attempt(state):
    state.data["attempts"] = state.data.get("attempts", 0) + 1
    state.log("extract", f"Attempt {state.data['attempts']}")


def retry_until_third(state):
    return "finish" if state.data["attempts"] >= 3 else "extract"


def finish(state):
    state.log("finish", "Done")


def test_retry_loop_runs_until_router_stops_it():
    flow = Workflow(start="extract")
    flow.add_node("extract", attempt, retry_until_third)
    flow.add_node("finish", finish)
    state = flow.run(RunState(path="invoice.txt"))
    assert state.data["attempts"] == 3
    assert state.trace[-1]["message"] == "Done"


def test_endless_loop_is_stopped():
    flow = Workflow(start="extract")
    flow.add_node("extract", attempt, "extract")
    with pytest.raises(RuntimeError):
        flow.run(RunState(path="invoice.txt"))