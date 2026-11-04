import json
from pathlib import Path

import pytest

from database.store import Store
from workflow.pipeline import process_invoice

SAMPLES = Path("data/invoices")


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.db")


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


def test_same_invoice_is_never_paid_twice(store):
    assert process_invoice(SAMPLES / "invoice_1011.pdf", store, None)["outcome"] == "PAID"
    second = process_invoice(SAMPLES / "invoice_1011.txt", store, None)
    assert second["outcome"] == "REJECTED"
    assert "DUPLICATE_PAYMENT" in {finding["code"] for finding in second["findings"]}


def test_database_blocks_a_payment_if_every_earlier_check_misses_it(store, monkeypatch):
    monkeypatch.setattr("workflow.pipeline.validate", lambda *args: [])
    assert process_invoice(SAMPLES / "invoice_1011.pdf", store, None)["outcome"] == "PAID"
    second = process_invoice(SAMPLES / "invoice_1011.txt", store, None)
    assert second["outcome"] == "REJECTED"
    assert "Blocked by the ledger" in second["reason"]


def test_revision_of_a_paid_invoice_is_held(store):
    process_invoice(SAMPLES / "invoice_1004.json", store, None)
    assert process_invoice(SAMPLES / "invoice_1004_revised.json", store, None)["outcome"] == "HELD"


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
