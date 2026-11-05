"""Final outcomes: pay, reject or hold. Every outcome is written to the ledger."""
import sqlite3


def mock_payment(vendor, amount):
    """The payment API from the brief."""
    print(f"Paid {amount} to {vendor}")
    return {"status": "success"}


def short_reason(state):
    """One line for the ledger. The full reports stay with the run and in the audit log."""
    findings = state.data.get("findings", [])
    blocking = [finding for finding in findings if finding.severity != "warning"]
    main = blocking or findings
    if state.data.get("outcome") == "PAID" and not findings:
        return "All checks passed."
    if main:
        extra = len(main) - 1
        return main[0].message + (f" (+{extra} more)" if extra else "")
    return state.data.get("reason", "").split(". ")[0].rstrip(".") + "."


def _record(state, status):
    invoice = state.data.get("invoice")
    state.data["store"].record(
        invoice.invoice_number if invoice else None,
        invoice.revision if invoice else None,
        invoice.vendor if invoice else None,
        invoice.total if invoice else None,
        status,
        short_reason(state),
        invoice.currency if invoice else None,
    )


def pay(state):
    data = state.data
    invoice = data["invoice"]
    data["outcome"] = "PAID"
    try:
        # Record first: the unique index blocks a second payment even if earlier checks missed it.
        _record(state, "PAID")
    except sqlite3.IntegrityError:
        data["decision"] = "REJECT"
        data["reason"] = "Blocked by the ledger: this invoice has already been paid."
        reject(state)
        return
    data["payment"] = mock_payment(invoice.vendor, invoice.total)
    data["outcome"] = "PAID"
    state.log("payment", f"Paid {invoice.total:,.2f} to {invoice.vendor}")


def reject(state):
    state.data["outcome"] = "REJECTED"
    _record(state, "REJECTED")
    state.log("payment", f"Rejected, no payment made: {state.data.get('reason', '')}")


def hold(state):
    state.data["outcome"] = "HELD"
    _record(state, "HELD")
    state.log("payment", f"Held for a person to review, no payment made: {state.data.get('reason', '')}")
