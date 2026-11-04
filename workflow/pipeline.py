"""Connects every stage: extract <-> verify -> validate -> VP <-> critic -> pay | hold | reject."""
import json
from pathlib import Path

from agents.approval import add_approval, policy_floor
from agents.extraction import add_extraction
from services.documents import load_text
from services.payment import hold, pay, reject
from services.validation import validate
from workflow.engine import RunState, Workflow


def validate_node(state):
    data = state.data
    data["findings"] = validate(data["invoice"], load_text(state.path), data["store"])
    data["policy"] = policy_floor(data["invoice"], data["findings"])
    state.log("validate", f"{len(data['findings'])} finding(s); hard rules allow {data['policy']['decision']}",
              codes=[finding.code for finding in data["findings"]])


def unreadable(state):
    data = state.data
    data["decision"] = "REJECT"
    data["reason"] = data.get("error") or "Could not extract a valid invoice: " + "; ".join(data["problems"])


def build_workflow():
    flow = Workflow(start="extract")
    add_extraction(flow, on_success="validate", on_failure="unreadable")
    flow.add_node("unreadable", unreadable, "reject")
    flow.add_node("validate", validate_node, "vp_review")
    add_approval(flow, on_approve="pay", on_hold="hold", on_reject="reject")
    flow.add_node("pay", pay)
    flow.add_node("hold", hold)
    flow.add_node("reject", reject)
    return flow


def process_invoice(path, store, llm, audit_log=None):
    """Run one invoice end to end and return a plain summary of what happened."""
    state = RunState(path=str(path), data={"llm": llm, "store": store})
    build_workflow().run(state)
    if audit_log:
        audit_log = Path(audit_log)
        audit_log.parent.mkdir(parents=True, exist_ok=True)
        with audit_log.open("a", encoding="utf-8") as handle:
            for entry in state.trace:
                handle.write(json.dumps(entry, default=str) + "\n")

    data = state.data
    invoice = data.get("invoice")
    return {
        "run_id": state.run_id,
        "file": Path(path).name,
        "outcome": data["outcome"],
        "decision": data["decision"],
        "reason": data["reason"],
        "extraction_method": data.get("method"),
        "attempts": data.get("attempts"),
        "invoice": invoice.model_dump() if invoice else None,
        "findings": [finding.model_dump() for finding in data.get("findings", [])],
        "drafts": [draft.model_dump() for draft in data.get("drafts", [])],
        "critiques": [critique.model_dump() for critique in data.get("critiques", [])],
        "trace": state.trace,
    }
