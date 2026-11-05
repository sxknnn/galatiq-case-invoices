"""Human review of held invoices. A person can pay or reject, within the same payment safeguards."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from services.payment import mock_payment


class ReviewError(Exception):
    """The review can't be applied, with a message that says why."""


def amount_due(row, store):
    """What approving would pay. A revision of a paid invoice owes only the difference."""
    paid = sum(record["amount"] or 0 for record in store.paid_records(row["invoice_number"]))
    return round((row["amount"] or 0) - paid, 2)


def approval_blocker(row, store):
    """Explain why this invoice can't be paid, or return None if it can."""
    if row["currency"] != "USD":
        currency = row["currency"] or "an unknown currency"
        return f"This invoice is in {currency}, and the payment system only pays USD. It can only be rejected."
    if not row["vendor"]:
        return "This invoice has no vendor to pay. It can only be rejected."
    for record in store.paid_records(row["invoice_number"]):
        if record["revision"] == row["revision"]:
            return f"{row['invoice_number']} has already been paid. It can only be rejected."
    if amount_due(row, store) <= 0:
        return "Nothing more is owed on this invoice. It can only be rejected."
    return None


def _held_row(store, row_id, note):
    if not note or not note.strip():
        raise ReviewError("Add a note explaining your decision.")
    row = store.get_ledger_row(row_id)
    if row is None or row["status"] != "HELD":
        raise ReviewError("This invoice is no longer waiting for review.")
    return row, note.strip()


def _log(audit_log, row, status, amount, note):
    if not audit_log:
        return
    path = Path(audit_log)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "stage": "human_review", "message": f"A person decided: {status}", "ledger_id": row["id"],
        "invoice_number": row["invoice_number"], "amount": amount, "note": note,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def approve_held(store, row_id, note, audit_log=None):
    """Pay a held invoice, and return the amount paid."""
    row, note = _held_row(store, row_id, note)
    blocker = approval_blocker(row, store)
    if blocker:
        raise ReviewError(blocker)
    amount = amount_due(row, store)
    try:
        # Record first: the unique index blocks a second payment even if the check above missed it.
        store.resolve_held(row_id, "PAID", amount, f"Approved by a person: {note}")
    except sqlite3.IntegrityError as exc:
        raise ReviewError(f"{row['invoice_number']} has already been paid.") from exc
    mock_payment(row["vendor"], amount)
    _log(audit_log, row, "PAID", amount, note)
    return amount


def reject_held(store, row_id, note, audit_log=None):
    row, note = _held_row(store, row_id, note)
    store.resolve_held(row_id, "REJECTED", row["amount"], f"Rejected by a person: {note}")
    _log(audit_log, row, "REJECTED", row["amount"], note)
