"""The invoice structure shared by every stage."""
import re
from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator


def parse_money(value: Any) -> Optional[float]:
    """Turn '$1,000.00' into 1000.0. Unreadable text such as '3,500.O0' raises an error."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return float(re.sub(r"[$€£,\s]", "", str(value)))


class LineItem(BaseModel):
    name: str
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    amount: Optional[float] = None
    note: Optional[str] = None

    @field_validator("quantity", "unit_price", "amount", mode="before")
    @classmethod
    def clean_number(cls, value):
        return parse_money(value)


class Invoice(BaseModel):
    invoice_number: Optional[str] = None
    revision: Optional[str] = None
    vendor: Optional[str] = None
    invoice_date: Optional[str] = None
    due_date: Optional[str] = None
    items: List[LineItem] = Field(default_factory=list)
    subtotal: Optional[float] = None
    tax: Optional[float] = None
    shipping: Optional[float] = None
    total: Optional[float] = None
    currency: str = "USD"
    payment_terms: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("subtotal", "tax", "shipping", "total", mode="before")
    @classmethod
    def clean_number(cls, value):
        return parse_money(value)

    @field_validator("invoice_number", mode="before")
    @classmethod
    def clean_invoice_number(cls, value):
        """'1002' and 'INV 1012' become 'INV-1002' and 'INV-1012'."""
        if value in (None, ""):
            return None
        match = re.search(r"\d{3,}", str(value))
        return f"INV-{match.group()}" if match else str(value).strip()

    @field_validator("vendor", mode="before")
    @classmethod
    def blank_vendor_is_missing(cls, value):
        return (str(value).strip() or None) if value is not None else None

    @field_validator("currency", mode="before")
    @classmethod
    def clean_currency(cls, value):
        return str(value).strip().upper() if value else "USD"
    
DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%b %d %Y", "%b %d, %Y", "%B %d %Y", "%B %d, %Y", "%d-%b-%Y")


def parse_date(value):
    """Return a date, or None when the text isn't one, such as 'yesterday'."""
    if not value:
        return None
    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(str(value).strip(), date_format).date()
        except ValueError:
            continue
    return None