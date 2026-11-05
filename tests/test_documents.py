import pytest
from pydantic import ValidationError

from schemas.models import Invoice
from services.documents import UnreadableDocument, load_text, parse_structured, repair_ocr
from services.text_fields import extract_fields

SAMPLES = "data/invoices/"


@pytest.mark.parametrize("name,number,vendor,total,item_count", [
    ("invoice_1004.json", "INV-1004", "Precision Parts Ltd.", 1890.00, 2),
    ("invoice_1006.csv", "INV-1006", "Acme Industrial Supplies", 2750.00, 2),
    ("invoice_1007.csv", "INV-1007", "MegaWidgets Corp", 15525.00, 3),
    ("invoice_1013.json", "INV-1013", "Atlas Industrial Supply", 22562.80, 8),
    ("invoice_1014.xml", "INV-1014", "TechParts International", 4125.00, 2),
])
def test_structured_files_become_invoices(name, number, vendor, total, item_count):
    invoice = Invoice.model_validate(parse_structured(SAMPLES + name))
    assert invoice.invoice_number == number
    assert invoice.vendor == vendor
    assert invoice.total == pytest.approx(total)
    assert len(invoice.items) == item_count


def test_bad_data_still_loads_so_validation_can_flag_it():
    invoice = Invoice.model_validate(parse_structured(SAMPLES + "invoice_1009.json"))
    assert invoice.vendor is None
    assert invoice.items[0].quantity == -5


def test_non_usd_currency_is_kept():
    invoice = Invoice.model_validate(parse_structured(SAMPLES + "invoice_1014.xml"))
    assert invoice.currency == "EUR"


def test_text_files_are_not_handled_here():
    assert parse_structured(SAMPLES + "invoice_1001.txt") is None

@pytest.mark.parametrize("name,vendor,total,item_count", [
    ("invoice_1001.txt", "Widgets Inc.", 5000.00, 2),
    ("invoice_1002.txt", "Gadgets Co.", 15000.00, 1),
    ("invoice_1003.txt", "Fraudster LLC", 100000.00, 1),
    ("invoice_1008.txt", "NoProd Industries", 9900.00, 2),
    ("invoice_1010.txt", "Consolidated Materials Group", 7185.00, 4),
    ("invoice_1011.pdf", "Summit Manufacturing Co.", 3000.00, 2),
    ("invoice_1012.txt", "QuickShip Distributers", 9975.00, 3),
    ("invoice_1013.pdf", "Atlas Industrial Supply", 22562.80, 8),
])
def test_free_text_extraction(name, vendor, total, item_count):
    text = repair_ocr(load_text(SAMPLES + name))
    invoice = Invoice.model_validate(extract_fields(text))
    assert invoice.vendor == vendor
    assert invoice.total == pytest.approx(total)
    assert len(invoice.items) == item_count


def test_ocr_errors_fail_until_repaired():
    text = load_text(SAMPLES + "invoice_1012.txt")
    with pytest.raises(ValidationError):
        Invoice.model_validate(extract_fields(text))
    repaired = Invoice.model_validate(extract_fields(repair_ocr(text)))
    assert repaired.invoice_number == "INV-1012"
    assert repaired.invoice_date == "26-Jan-2026"


def test_unreadable_files_give_a_clear_error(tmp_path):
    bad_pdf = tmp_path / "bad.pdf"
    bad_pdf.write_bytes(b"not a pdf")
    with pytest.raises(UnreadableDocument):
        load_text(bad_pdf)
    with pytest.raises(UnreadableDocument):
        load_text(tmp_path / "missing.txt")


@pytest.mark.parametrize("value,expected", [("$", "USD"), ("us$", "USD"), ("usd", "USD"), ("€", "EUR"), ("£", "GBP"), (None, "USD")])
def test_currency_symbols_become_codes(value, expected):
    assert Invoice(currency=value).currency == expected
