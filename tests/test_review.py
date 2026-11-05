import json
import sqlite3
from pathlib import Path

import pytest

from database.store import Store
from services.review import ReviewError, amount_due, approval_blocker, approve_held, reject_held
from workflow.pipeline import process_invoice

SAMPLES = Path("data/invoices")


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.db")


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
