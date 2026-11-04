"""Deterministic checks. Stock, arithmetic and fraud rules must not depend on an LLM."""
import re
from collections import defaultdict

import config
from schemas.models import Finding, parse_date

MONEY_TOLERANCE = 0.01
PRICE_VARIANCE_LIMIT = 0.10
TERMS_TOLERANCE_DAYS = 3
NEAR_THRESHOLD_BAND = 0.05

PRESSURE_RE = re.compile(r"\burgent\b|pay immediately|avoid penalties|wire transfer", re.I)
INJECTION_RE = re.compile(
    r"ignore (?:all |any )?(?:previous|prior|above)|disregard .{0,30}instructions|"
    r"you are now|\bsystem\s*:|approve this invoice|mark (?:this|it) as approved",
    re.I,
)
RENAME_RE = re.compile(r"\bformerly\b|\bf/k/a\b", re.I)


def canonical_item(name):
    """'Widget A' and 'WidgetA (rush order)' both mean inventory item WidgetA."""
    return re.sub(r"\s+", "", re.sub(r"\([^)]*\)", "", name))


def validate(invoice, raw_text, store):
    """Return every finding. raw_text is the original file, since an LLM may drop details."""
    findings = []

    def add(code, severity, message):
        findings.append(Finding(code=code, severity=severity, message=message))

    _check_required(invoice, add)
    _check_items_and_stock(invoice, store, add)
    _check_arithmetic(invoice, add)
    _check_duplicates(invoice, store, add)
    _check_risk_signals(invoice, raw_text, add)
    return findings


def _check_required(invoice, add):
    if not invoice.invoice_number:
        add("MISSING_INVOICE_NUMBER", "reject", "Invoice number is missing.")
    if not invoice.vendor:
        add("MISSING_VENDOR", "reject", "Vendor name is missing, so there is no one to pay.")
    if not invoice.due_date:
        add("MISSING_DUE_DATE", "reject", "Due date is missing.")
    elif parse_date(invoice.due_date) is None:
        add("INVALID_DUE_DATE", "reject", f"Due date '{invoice.due_date}' is not a real date.")
    if not invoice.items:
        add("NO_LINE_ITEMS", "reject", "No line items were found.")


def _check_items_and_stock(invoice, store, add):
    # Quantities are summed first: INV-1013 splits items across lines that look fine alone.
    requested = defaultdict(float)
    for line in invoice.items:
        name = canonical_item(line.name)
        if line.quantity is None or line.quantity <= 0:
            add("INVALID_QUANTITY", "reject", f"{line.name}: quantity {line.quantity} is not positive.")
            continue
        requested[name] += line.quantity
        record = store.lookup_item(name)
        if record and record["unit_price"] and line.unit_price and invoice.currency == "USD":
            variance = (line.unit_price - record["unit_price"]) / record["unit_price"]
            if variance > PRICE_VARIANCE_LIMIT:
                add("PRICE_VARIANCE", "warning",
                    f"{line.name}: ${line.unit_price:,.2f} is {variance:.0%} above the "
                    f"catalog price of ${record['unit_price']:,.2f}.")

    for name, quantity in requested.items():
        record = store.lookup_item(name)
        if record is None:
            add("UNKNOWN_ITEM", "reject", f"{name} is not in the inventory database.")
        elif record["stock"] == 0:
            add("OUT_OF_STOCK", "reject", f"{name} has zero stock; {quantity:g} requested.")
        elif quantity > record["stock"]:
            add("STOCK_EXCEEDED", "reject",
                f"{name}: {quantity:g} requested across the invoice, only {record['stock']} in stock.")


def _check_arithmetic(invoice, add):
    for line in invoice.items:
        if None not in (line.quantity, line.unit_price, line.amount):
            if abs(line.quantity * line.unit_price - line.amount) > MONEY_TOLERANCE:
                add("LINE_TOTAL_MISMATCH", "reject",
                    f"{line.name}: {line.quantity:g} x {line.unit_price:,.2f} does not equal {line.amount:,.2f}.")

    if invoice.total is None:
        add("MISSING_TOTAL", "reject", "Invoice total is missing.")
        return
    if invoice.total <= 0:
        add("INVALID_TOTAL", "reject", f"Total {invoice.total:,.2f} is not a payable amount.")

    if not all(line.quantity is not None and line.unit_price is not None for line in invoice.items):
        return
    computed = sum(line.quantity * line.unit_price for line in invoice.items)
    if invoice.subtotal is not None and abs(computed - invoice.subtotal) > MONEY_TOLERANCE:
        add("SUBTOTAL_MISMATCH", "reject",
            f"Line items add up to {computed:,.2f}, but the stated subtotal is {invoice.subtotal:,.2f}.")
    expected = computed + (invoice.tax or 0) + (invoice.shipping or 0)
    if invoice.items and abs(expected - invoice.total) > MONEY_TOLERANCE:
        add("TOTAL_MISMATCH", "reject",
            f"Lines, tax and shipping add up to {expected:,.2f}, but the stated total is "
            f"{invoice.total:,.2f} (off by {abs(expected - invoice.total):,.2f}).")


def _check_duplicates(invoice, store, add):
    for paid in store.paid_records(invoice.invoice_number):
        if paid["revision"] == (invoice.revision or ""):
            add("DUPLICATE_PAYMENT", "reject",
                f"{invoice.invoice_number} was already paid ({paid['amount']:,.2f} on {paid['recorded_at']}).")
        else:
            difference = (invoice.total or 0) - paid["amount"]
            add("REVISION_OF_PAID_INVOICE", "hold",
                f"{invoice.invoice_number} revision {invoice.revision} replaces an invoice already paid "
                f"at {paid['amount']:,.2f}. Paying it in full would pay that amount twice; only the "
                f"{difference:,.2f} difference may be due.")


def _check_risk_signals(invoice, raw_text, add):
    threshold = config.APPROVAL_THRESHOLD
    if INJECTION_RE.search(raw_text):
        add("PROMPT_INJECTION", "reject", "The document contains text trying to instruct the approval system.")
    if PRESSURE_RE.search(raw_text):
        add("PRESSURE_LANGUAGE", "hold", "The document uses urgency or wire-transfer language common in payment fraud.")
    if invoice.currency != "USD":
        add("NON_USD_CURRENCY", "hold",
            f"Invoice is in {invoice.currency}. The payment API has no currency field and the "
            f"${threshold:,} rule is in USD, so a person must confirm the amount.")
    if RENAME_RE.search(raw_text):
        add("VENDOR_RENAMED", "warning", "The vendor says it changed its name, a common cover for changed bank details.")

    total = invoice.total or 0
    if invoice.currency == "USD" and threshold * (1 - NEAR_THRESHOLD_BAND) <= total < threshold:
        add("NEAR_APPROVAL_THRESHOLD", "warning",
            f"Total of ${total:,.2f} sits just under the ${threshold:,} scrutiny threshold.")

    issued, due = parse_date(invoice.invoice_date), parse_date(invoice.due_date)
    if issued and due:
        days = (due - issued).days
        terms = re.search(r"net\s*(\d+)", invoice.payment_terms or "", flags=re.I)
        if days < 0:
            add("DUE_BEFORE_ISSUE", "warning", "Due date is earlier than the invoice date.")
        elif terms and abs(days - int(terms.group(1))) > TERMS_TOLERANCE_DAYS:
            add("TERMS_MISMATCH", "warning",
                f"Terms say {invoice.payment_terms}, but the due date is {days} days after the invoice date.")