"""Reads invoice files. Labelled formats are mapped by code, with no LLM call."""
import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pdfplumber


def parse_structured(path):
    """Return invoice fields for JSON, CSV or XML. Return None for other or malformed files."""
    path = Path(path)
    parsers = {".json": _from_json, ".csv": _from_csv, ".xml": _from_xml}
    parser = parsers.get(path.suffix.lower())
    if parser is None:
        return None
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    try:
        return parser(text)
    except (ValueError, KeyError, IndexError, ET.ParseError):
        return None  # Malformed files can still be read as plain text later.


def _from_json(text):
    raw = json.loads(text)
    vendor = raw.get("vendor")
    if isinstance(vendor, dict):
        vendor = vendor.get("name")
    return {
        "invoice_number": raw.get("invoice_number"),
        "revision": raw.get("revision"),
        "vendor": vendor,
        "invoice_date": raw.get("date"),
        "due_date": raw.get("due_date"),
        "items": [
            {
                "name": line.get("item") or "",
                "quantity": line.get("quantity"),
                "unit_price": line.get("unit_price"),
                "amount": line.get("amount"),
                "note": line.get("note"),
            }
            for line in raw.get("line_items", [])
        ],
        "subtotal": raw.get("subtotal"),
        "tax": raw.get("tax_amount"),
        "total": raw.get("total"),
        "currency": raw.get("currency"),
        "payment_terms": raw.get("payment_terms"),
        "notes": raw.get("notes"),
    }


def _from_xml(text):
    root = ET.fromstring(text)
    return {
        "invoice_number": root.findtext("header/invoice_number"),
        "vendor": root.findtext("header/vendor"),
        "invoice_date": root.findtext("header/date"),
        "due_date": root.findtext("header/due_date"),
        "currency": root.findtext("header/currency"),
        "items": [
            {
                "name": item.findtext("name") or "",
                "quantity": item.findtext("quantity"),
                "unit_price": item.findtext("unit_price"),
            }
            for item in root.findall("line_items/item")
        ],
        "subtotal": root.findtext("totals/subtotal"),
        "tax": root.findtext("totals/tax_amount"),
        "total": root.findtext("totals/total"),
        "payment_terms": root.findtext("payment_terms"),
    }


def _from_csv(text):
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise ValueError("Empty CSV")
    header = [cell.strip().lower() for cell in rows[0]]
    if header[:2] == ["field", "value"]:
        return _csv_key_value(rows[1:])
    return _csv_table(header, rows[1:])


def _csv_key_value(rows):
    """Layout used by INV-1006: one 'field,value' pair per row."""
    result = {"items": []}
    for row in rows:
        if len(row) < 2:
            continue
        key, value = row[0].strip().lower(), row[1].strip()
        if key == "item":
            result["items"].append({"name": value})
        elif key in ("quantity", "unit_price") and result["items"]:
            result["items"][-1][key] = value
        elif key == "date":
            result["invoice_date"] = value
        else:
            result[key] = value
    return result


def _csv_table(header, rows):
    """Layout used by INV-1007 and INV-1015: one row per item, then summary rows."""
    col = {name: index for index, name in enumerate(header)}
    result = {"items": []}
    for row in rows:
        if row[col["item"]].strip():
            result.update(
                invoice_number=row[col["invoice number"]],
                vendor=row[col["vendor"]],
                invoice_date=row[col["date"]],
                due_date=row[col["due date"]],
            )
            result["items"].append({
                "name": row[col["item"]],
                "quantity": row[col["qty"]],
                "unit_price": row[col["unit price"]],
                "amount": row[col["line total"]],
            })
        else:  # Summary rows keep their label in the unit price column.
            label = row[col["unit price"]].strip().lower()
            value = row[col["line total"]]
            if label.startswith("subtotal"):
                result["subtotal"] = value
            elif label.startswith("tax"):
                result["tax"] = value
            elif label.startswith("total"):
                result["total"] = value
    return result

class UnreadableDocument(Exception):
    pass


def load_text(path):
    """Return a file's text, or raise UnreadableDocument with a clear reason."""
    path = Path(path)
    if not path.is_file():
        raise UnreadableDocument(f"File not found: {path}")
    if path.suffix.lower() != ".pdf":
        return path.read_text(encoding="utf-8-sig", errors="replace")
    try:
        with pdfplumber.open(path) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception as exc:
        raise UnreadableDocument(f"Could not open PDF: {exc}") from exc
    if not text.strip():
        raise UnreadableDocument("PDF has no text layer (it may be a scan)")
    return text


def repair_ocr(text):
    """Fix the letter O used for zero, but only next to digits or money punctuation."""
    text = re.sub(r"(?<=\d)O(?=\d)", "0", text)
    text = re.sub(r"(?<=[\d.,])O(?=[\d\s]|$)", "0", text, flags=re.M)
    return re.sub(r"(?<=\d)O(?=\b)", "0", text)