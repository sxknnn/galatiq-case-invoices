import json
import sqlite3
from pathlib import Path

import pytest

from agents.llm import get_llm
from database.store import Store
from services.review import ReviewError, amount_due, approval_blocker, approve_held, reject_held
from workflow.engine import RunState, Workflow
from workflow.pipeline import process_invoice

SAMPLES = Path("data/invoices")


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.db")


# Model setup

def test_runs_offline_without_a_key(monkeypatch, capsys):
    monkeypatch.setattr("config.MODEL", "xai:any-model")
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    assert get_llm() is None
    assert "offline simulation" in capsys.readouterr().out


def test_key_loads_the_configured_model(monkeypatch):
    monkeypatch.setattr("config.MODEL", "xai:any-model")
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    assert get_llm() is not None


# Workflow engine

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


def test_on_step_reports_each_step_in_order():
    flow = Workflow(start="extract")
    flow.add_node("extract", attempt, retry_until_third)
    flow.add_node("finish", finish)
    seen = []
    flow.run(RunState(path="invoice.txt"), on_step=lambda name, state: seen.append(name))
    assert seen == ["extract", "extract", "extract", "finish"]


# Full runs

def run_all(store):
    return [process_invoice(path, store, None) for path in sorted(SAMPLES.iterdir())]


def test_batch_pays_only_the_clean_invoices(store):
    results = run_all(store)
    counts = {name: sum(r["outcome"] == name for r in results) for name in ("PAID", "HELD", "REJECTED")}
    assert counts == {"PAID": 6, "HELD": 4, "REJECTED": 10}
    paid = [r["invoice"] for r in results if r["outcome"] == "PAID"]
    assert {invoice["invoice_number"] for invoice in paid} == {
        "INV-1001", "INV-1004", "INV-1006", "INV-1010", "INV-1011", "INV-1015"}
    assert sum(invoice["total"] for invoice in paid) == pytest.approx(26325)


def test_database_blocks_a_payment_if_every_earlier_check_misses_it(store, monkeypatch):
    monkeypatch.setattr("workflow.pipeline.validate", lambda *args: [])
    assert process_invoice(SAMPLES / "invoice_1011.pdf", store, None)["outcome"] == "PAID"
    second = process_invoice(SAMPLES / "invoice_1011.txt", store, None)
    assert second["outcome"] == "REJECTED"
    assert "Blocked by the ledger" in second["reason"]


def test_unreadable_file_is_rejected_without_crashing(store, tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    for path in (bad, tmp_path / "missing.txt"):
        result = process_invoice(path, store, None)
        assert result["outcome"] == "REJECTED"
        assert result["invoice"] is None


def test_every_outcome_reaches_the_ledger_and_audit_log(store, tmp_path):
    log = tmp_path / "logs" / "audit.jsonl"
    process_invoice(SAMPLES / "invoice_1014.xml", store, None, log)
    rows = store.conn.execute("SELECT status FROM ledger").fetchall()
    assert [row["status"] for row in rows] == ["HELD"]
    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert {"extract", "validate", "approve", "payment"} <= {entry["stage"] for entry in entries}


# Human review

def hold(store, *names):
    for name in names:
        process_invoice(SAMPLES / name, store, None)
    return store.held_invoices()


def test_person_can_approve_a_held_invoice(store):
    row = hold(store, "invoice_1012.txt")[0]
    assert approve_held(store, row["id"], "Vendor confirmed by phone.") == 9975
    updated = store.get_ledger_row(row["id"])
    assert updated["status"] == "PAID"
    assert "Vendor confirmed by phone." in updated["reason"]
    assert store.held_invoices() == []


def test_person_can_reject_a_held_invoice(store):
    row = hold(store, "invoice_1012.txt")[0]
    reject_held(store, row["id"], "Vendor could not be verified.")
    assert store.get_ledger_row(row["id"])["status"] == "REJECTED"


@pytest.mark.parametrize("note", ["", "   ", None])
def test_a_note_is_required(store, note):
    row = hold(store, "invoice_1012.txt")[0]
    with pytest.raises(ReviewError):
        approve_held(store, row["id"], note)
    with pytest.raises(ReviewError):
        reject_held(store, row["id"], note)
    assert store.get_ledger_row(row["id"])["status"] == "HELD"


def test_revision_pays_only_the_difference(store):
    row = hold(store, "invoice_1004.json", "invoice_1004_revised.json")[0]
    assert amount_due(row, store) == 4050
    assert approve_held(store, row["id"], "Amendment confirmed.") == 4050
    paid = store.paid_records("INV-1004")
    assert sum(record["amount"] for record in paid) == pytest.approx(5940)


def test_foreign_currency_cannot_be_approved_but_can_be_rejected(store):
    row = hold(store, "invoice_1014.xml")[0]
    assert "EUR" in approval_blocker(row, store)
    with pytest.raises(ReviewError):
        approve_held(store, row["id"], "Looks fine.")
    assert store.get_ledger_row(row["id"])["status"] == "HELD"
    reject_held(store, row["id"], "No way to pay in euros.")


def test_same_invoice_held_twice_is_only_paid_once(store):
    first, second = hold(store, "invoice_1012.pdf", "invoice_1012.txt")
    approve_held(store, first["id"], "Confirmed.")
    with pytest.raises(ReviewError):
        approve_held(store, second["id"], "Confirmed again.")
    assert len(store.paid_records("INV-1012")) == 1


def test_a_decided_invoice_cannot_be_decided_again(store):
    row = hold(store, "invoice_1012.txt")[0]
    reject_held(store, row["id"], "No.")
    with pytest.raises(ReviewError):
        approve_held(store, row["id"], "Changed my mind.")


def test_database_blocks_a_second_payment_if_the_checks_miss_it(store, monkeypatch):
    first, second = hold(store, "invoice_1012.pdf", "invoice_1012.txt")
    approve_held(store, first["id"], "Confirmed.")
    monkeypatch.setattr("services.review.approval_blocker", lambda row, store: None)
    monkeypatch.setattr("services.review.amount_due", lambda row, store: 9975)
    with pytest.raises(ReviewError):
        approve_held(store, second["id"], "Confirmed again.")
    assert len(store.paid_records("INV-1012")) == 1


def test_human_decisions_reach_the_audit_log(store, tmp_path):
    log = tmp_path / "logs" / "audit.jsonl"
    row = hold(store, "invoice_1012.txt")[0]
    approve_held(store, row["id"], "Confirmed.", log)
    entry = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["stage"] == "human_review"
    assert entry["note"] == "Confirmed."


def test_older_databases_are_upgraded_and_their_rows_cannot_be_paid(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_number TEXT, revision TEXT NOT NULL "
        "DEFAULT '', vendor TEXT, amount REAL, status TEXT NOT NULL, reason TEXT, "
        "recorded_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    old.execute("INSERT INTO ledger (invoice_number, vendor, amount, status) VALUES ('INV-1', 'V', 10, 'HELD')")
    old.commit()
    old.close()
    store = Store(path)
    row = store.held_invoices()[0]
    assert "unknown currency" in approval_blocker(row, store)
