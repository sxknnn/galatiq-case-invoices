from types import SimpleNamespace

from agents.extraction import add_extraction
from workflow.engine import RunState, Workflow

SAMPLES = "data/invoices/"


def run(name, llm=None):
    flow = Workflow(start="extract")
    add_extraction(flow)
    return flow.run(RunState(path=str(name), data={"llm": llm})).data


class FakeLLM:
    """Returns scripted replies, so these tests need no key and cost nothing."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(content=reply)


GOOD_REPLY = """```json
{"invoice_number": "INV-1001", "vendor": "Widgets Inc.", "invoice_date": "2026-01-15",
 "due_date": "2026-02-01", "total": 5000,
 "items": [{"name": "WidgetA", "quantity": 10, "unit_price": 250}]}
```"""
MADE_UP_TOTAL = GOOD_REPLY.replace('"total": 5000', '"total": 1')


def test_structured_file_never_calls_the_llm():
    llm = FakeLLM()
    data = run(SAMPLES + "invoice_1004.json", llm)
    assert llm.calls == 0
    assert data["invoice"].invoice_number == "INV-1004"


def test_offline_retry_repairs_ocr_errors():
    data = run(SAMPLES + "invoice_1012.txt")
    assert data["attempts"] == 2
    assert data["method"] == "offline simulation"
    assert data["invoice"].invoice_date == "26-Jan-2026"


def test_bad_source_data_stops_retrying_and_goes_to_validation():
    data = run(SAMPLES + "invoice_1003.txt")
    assert data["invoice"].due_date == "yesterday"
    assert data["attempts"] == 2


def test_made_up_total_is_sent_back_and_corrected():
    llm = FakeLLM(MADE_UP_TOTAL, GOOD_REPLY)
    data = run(SAMPLES + "invoice_1001.txt", llm)
    assert llm.calls == 2
    assert data["invoice"].total == 5000
    assert any("does not appear" in message[1] for message in data["messages"])


def test_llm_failure_uses_the_pattern_fallback():
    data = run(SAMPLES + "invoice_1001.txt", FakeLLM(RuntimeError("connection refused")))
    assert data["method"] == "pattern fallback"
    assert data["invoice"].total == 5000


def test_unreadable_file_reports_an_error(tmp_path):
    data = run(tmp_path / "missing.txt")
    assert "not found" in data["error"]
    assert "invoice" not in data
