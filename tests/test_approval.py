from types import SimpleNamespace

import pytest

from agents.approval import add_approval, policy_floor
from agents.extraction import add_extraction
from database.store import Store
from schemas.models import Finding, Invoice
from services.documents import load_text
from services.validation import validate
from workflow.engine import RunState, Workflow

SAMPLES = "data/invoices/"

EXPECTED = {
    "invoice_1001.txt": "APPROVE",
    "invoice_1002.txt": "REJECT",
    "invoice_1003.txt": "REJECT",
    "invoice_1004.json": "APPROVE",
    "invoice_1007.csv": "REJECT",
    "invoice_1010.txt": "APPROVE",
    "invoice_1012.txt": "HOLD",
    "invoice_1014.xml": "HOLD",
    "invoice_1015.csv": "APPROVE",
    "invoice_1016.json": "REJECT",
}


class FakeLLM:
    """Scripted replies, so these tests need no key and cost nothing."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.tools = None

    def bind_tools(self, tools):
        self.tools = tools
        return self

    def invoke(self, messages):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, str):
            return SimpleNamespace(content=reply, tool_calls=[])
        return reply


APPROVE = '{"decision": "APPROVE", "reasoning": "Looks fine."}'
HOLD = '{"decision": "HOLD", "reasoning": "A person should verify."}'
AGREE = '{"verdict": "agree", "critique": "Sound."}'
OBJECT = '{"verdict": "object", "critique": "Too risky.", "recommended_decision": "HOLD"}'


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.db")


def decide(name, store, llm=None):
    extraction = Workflow(start="extract")
    add_extraction(extraction)
    invoice = extraction.run(RunState(path=SAMPLES + name, data={"llm": None})).data["invoice"]
    findings = validate(invoice, load_text(SAMPLES + name), store)
    flow = Workflow(start="vp_review")
    add_approval(flow)
    data = {"llm": llm, "store": store, "invoice": invoice, "findings": findings,
            "policy": policy_floor(invoice, findings)}
    return flow.run(RunState(path=name, data=data))


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_offline_decisions(name, store):
    assert decide(name, store).data["decision"] == EXPECTED[name]


def test_critic_changes_the_vp_decision_on_1012(store):
    data = decide("invoice_1012.txt", store).data
    assert data["drafts"][0].decision == "APPROVE"
    assert data["critiques"][0].verdict == "object"
    assert data["drafts"][-1].decision == "HOLD"
    assert len(data["critiques"]) == 1


def test_clean_confident_small_invoice_skips_the_critic(store):
    llm = FakeLLM(APPROVE)
    data = decide("invoice_1001.txt", store, llm).data
    assert data["critiques"] == []
    assert data["decision"] == "APPROVE"


def test_flagged_invoice_goes_to_the_critic(store):
    assert len(decide("invoice_1010.txt", store).data["critiques"]) == 1


def test_rejection_goes_to_the_critic(store):
    assert len(decide("invoice_1002.txt", store).data["critiques"]) == 1


def test_high_value_goes_to_the_critic(store, monkeypatch):
    monkeypatch.setattr("config.REVIEW_LIMIT_USD", 1000)
    data = decide("invoice_1001.txt", store, FakeLLM(APPROVE, AGREE)).data
    assert len(data["critiques"]) == 1


def test_vp_has_the_final_say_after_an_objection(store):
    data = decide("invoice_1010.txt", store, FakeLLM(APPROVE, OBJECT, APPROVE)).data
    assert len(data["drafts"]) == 2
    assert data["decision"] == "APPROVE"


def test_vp_can_change_its_decision_after_an_objection(store):
    assert decide("invoice_1010.txt", store, FakeLLM(APPROVE, OBJECT, HOLD)).data["decision"] == "HOLD"


def test_vp_cannot_approve_what_the_rules_reject(store):
    data = decide("invoice_1003.txt", store, FakeLLM(APPROVE, OBJECT, APPROVE)).data
    assert data["decision"] == "REJECT"
    assert "Guardrail" in data["reason"]


def test_llm_can_be_stricter_than_the_rules(store):
    assert decide("invoice_1001.txt", store, FakeLLM(HOLD, AGREE)).data["decision"] == "HOLD"


def test_vp_tool_call_reaches_the_database(store):
    call = {"name": "lookup_inventory", "args": {"item": "WidgetA"}, "id": "1"}
    llm = FakeLLM(SimpleNamespace(content="", tool_calls=[call]), APPROVE)
    state = decide("invoice_1001.txt", store, llm)
    assert llm.tools
    assert any("lookup_inventory" in entry["message"] and entry["result"]["stock"] == 15
               for entry in state.trace)


def test_malformed_output_is_corrected(store):
    assert decide("invoice_1001.txt", store, FakeLLM("not json", APPROVE)).data["decision"] == "APPROVE"


def test_vp_failure_rejects_the_invoice(store):
    data = decide("invoice_1001.txt", store, FakeLLM(RuntimeError("connection refused"), AGREE)).data
    assert data["decision"] == "REJECT"
    assert "failed" in data["reason"]


def test_critic_failure_rejects_the_invoice(store):
    data = decide("invoice_1010.txt", store, FakeLLM(APPROVE, RuntimeError("timeout"))).data
    assert data["decision"] == "REJECT"
    assert "failed" in data["reason"]


def test_over_threshold_with_a_warning_is_held():
    invoice = Invoice(total=12500)
    warning = Finding(code="PRICE_VARIANCE", severity="warning", message="High price")
    assert policy_floor(invoice, [warning])["decision"] == "HOLD"
    assert policy_floor(invoice, [])["decision"] == "APPROVE"
    assert policy_floor(invoice, [])["enhanced_scrutiny"] is True
