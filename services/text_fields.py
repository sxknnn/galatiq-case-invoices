"""Pattern-based extraction from free text, used offline and when the LLM fails."""
import re

LABELS = {
    "vendor": r"vendor|vndr",
    "sender": r"from",
    "invoice_number": r"invoice number|invoice|inv no|inv #",
    "due_date": r"due date|due dt|due",
    "invoice_date": r"date|dt",
    "subtotal": r"subtotal",
    "tax": r"sales tax|tax(?: \([^)]*\))?",
    "shipping": r"shipping",
    "total": r"total amount|grand total|total|amt",
    "payment_terms": r"payment terms|pymnt terms|terms",
    "notes": r"notes",
}
LABEL_RE = re.compile(
    r"(?<![\w-])(?:" + "|".join(f"(?P<{k}>{v})" for k, v in LABELS.items()) + r")\s*:",
    flags=re.I,
)
ITEM_RE = re.compile(
    r"^[\s\-*]*(?P<name>[A-Za-z][\w ()\-]*?)\s+(?:qty:?\s*|x)?(?P<qty>-?\d+)\s+"
    r"(?:unit price:\s*|@\s*)?\$(?P<price>[\d,.O]+)(?:\s*(?:ea|each))?"
    r"(?:\s+\$(?P<amount>[\d,.O]+))?(?:\s+(?P<note>[A-Za-z][\w ]*))?\s*$",
    flags=re.I,
)
MONEY_FIELDS = ("subtotal", "tax", "shipping", "total")


def extract_fields(text):
    """Return raw field values. The Invoice schema decides whether they are valid."""
    fields = {}
    items = []
    in_notes = False

    for line in text.splitlines():
        if not line.strip():
            in_notes = False
            continue
        item = ITEM_RE.match(line)
        if item:
            items.append(_item(item))
            continue
        labelled = _labelled_values(line)
        if labelled:
            in_notes = "notes" in labelled
            for key, value in labelled.items():
                if key == "notes" and "notes" in fields:
                    fields["notes"] = f"{fields['notes']} {value}"
                else:
                    fields.setdefault(key, value)
        elif in_notes:
            fields["notes"] = f"{fields['notes']} {line.strip()}"
        elif re.match(r"\s*\(formerly", line, flags=re.I):
            fields["notes"] = f"{line.strip()} {fields.get('notes', '')}".strip()

    # An email's "From:" line is only the vendor when no "Vendor:" line exists.
    sender = fields.pop("sender", None)
    fields["vendor"] = fields.get("vendor") or sender
    if "invoice_number" not in fields:
        title = re.search(r"invoice\s*#\s*([\w-]+)", text, flags=re.I)
        fields["invoice_number"] = title.group(1) if title else None
    for key in MONEY_FIELDS:
        if key in fields:
            fields[key] = _money_token(fields[key])
    fields["items"] = items
    fields["currency"] = "USD" if "$" in text else None
    return fields


def _labelled_values(line):
    """Handles several 'Label: value' pairs on one line, as in the bulk-order PDF."""
    matches = list(LABEL_RE.finditer(line))
    values = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
        value = line[match.end():end].strip()
        if match.lastgroup and value:
            values[match.lastgroup] = value
    return values


def _item(match):
    name = match["name"].strip()
    bracket = re.search(r"\(([^)]*)\)", name)
    return {
        "name": name,
        "quantity": match["qty"],
        "unit_price": match["price"],
        "amount": match["amount"],
        "note": match["note"] or (bracket.group(1) if bracket else None),
    }


def _money_token(value):
    match = re.search(r"-?\$?\s*[\d,.O]*\d[\d,.O]*", value)
    return match.group(0) if match else value