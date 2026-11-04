import pytest

from schemas.models import Invoice
from services.documents import parse_structured

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