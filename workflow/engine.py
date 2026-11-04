"""Runs an invoice through the workflow stages and records an audit trail."""
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Union

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


Node = Callable[[RunState], None]
Router = Callable[[RunState], Optional[str]]


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

    def run(self, state: RunState) -> RunState:
        current: Optional[str] = self.start
        steps = 0
        while current:
            steps += 1
            if steps > MAX_STEPS:
                raise RuntimeError(f"Workflow exceeded {MAX_STEPS} steps")
            self.nodes[current](state)
            edge = self.edges.get(current)
            current = edge(state) if callable(edge) else edge
        return state