"""Runs an invoice through the workflow on LangGraph and records an audit trail."""
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, TypedDict, Union

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph

MAX_STEPS = 30  # Stops a faulty retry loop from running forever.


@dataclass
class RunState:
    """Everything known about one invoice as it moves through the workflow."""

    path: str
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    data: Dict[str, Any] = field(default_factory=dict)
    trace: List[Dict[str, Any]] = field(default_factory=list)

    def log(self, stage: str, message: str, **details: Any) -> None:
        self.trace.append({
            "time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "run_id": self.run_id,
            "stage": stage,
            "message": message,
            **details,
        })


class GraphState(TypedDict):
    run: RunState


Node = Callable[[RunState], None]
Router = Callable[[RunState], Optional[str]]
OnStep = Callable[[str, RunState], None]


def _as_graph_node(name: str, node: Node, on_step: Optional[OnStep]):
    def graph_node(state: GraphState) -> GraphState:
        if on_step:
            on_step(name, state["run"])
        node(state["run"])
        return {"run": state["run"]}

    return graph_node


def _as_graph_router(router: Router):
    def graph_router(state: GraphState) -> str:
        return router(state["run"]) or END

    return graph_router


class Workflow:
    """Nodes do the work. Edges choose what runs next, which allows retry loops."""

    def __init__(self, start: str):
        self.start = start
        self.nodes: Dict[str, Node] = {}
        self.edges: Dict[str, Union[str, Router]] = {}

    def add_node(self, name: str, node: Node, next_step: Union[str, Router, None] = None) -> None:
        self.nodes[name] = node
        if next_step is not None:
            self.edges[name] = next_step

    def compile(self, on_step: Optional[OnStep] = None):
        """Build the LangGraph graph."""
        builder = StateGraph(GraphState)
        for name, node in self.nodes.items():
            builder.add_node(name, _as_graph_node(name, node, on_step))
        builder.add_edge(START, self.start)
        for name in self.nodes:
            edge = self.edges.get(name)
            if edge is None:
                builder.add_edge(name, END)
            elif callable(edge):
                builder.add_conditional_edges(name, _as_graph_router(edge))
            else:
                builder.add_edge(name, edge)
        return builder.compile()

    def run(self, state: RunState, on_step: Optional[OnStep] = None) -> RunState:
        """Run the workflow. on_step is told each step as it starts, so a UI can show live progress."""
        try:
            self.compile(on_step).invoke({"run": state}, {"recursion_limit": MAX_STEPS})
        except GraphRecursionError as exc:
            raise RuntimeError(f"Workflow exceeded {MAX_STEPS} steps") from exc
        return state