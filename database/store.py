"""SQLite storage for the mock inventory and the payment ledger."""
import sqlite3
import config

# Starter data from the brief, plus prices so we can flag overcharges later.
SEED_INVENTORY = [
    ("WidgetA", 15, 250.0),
    ("WidgetB", 10, 500.0),
    ("GadgetX", 5, 750.0),
    ("FakeItem", 0, None),
]


class Store:
    def __init__(self, path=config.DB_PATH):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row  # Lets us read columns by name.
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS inventory (
                item TEXT PRIMARY KEY,
                stock INTEGER NOT NULL,
                unit_price REAL
            );
            CREATE TABLE IF NOT EXISTS ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_number TEXT,
                revision TEXT NOT NULL DEFAULT '',
                vendor TEXT,
                amount REAL,
                status TEXT NOT NULL,
                reason TEXT,
                recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            -- The database refuses to pay the same invoice revision twice.
            CREATE UNIQUE INDEX IF NOT EXISTS one_payment_per_invoice
                ON ledger (invoice_number, revision) WHERE status = 'PAID';
            """
        )
        self.conn.executemany(
            "INSERT OR IGNORE INTO inventory VALUES (?, ?, ?)", SEED_INVENTORY
        )
        self.conn.commit()

    def lookup_item(self, name):
        row = self.conn.execute(
            "SELECT * FROM inventory WHERE lower(item) = lower(?)", (name,)
        ).fetchone()
        return dict(row) if row else None

    
    def paid_records(self, invoice_number):
        rows = self.conn.execute(
            "SELECT * FROM ledger WHERE invoice_number = ? AND status = 'PAID'",
            (invoice_number,),
        ).fetchall()
        return [dict(row) for row in rows]

    def record(self, invoice_number, revision, vendor, amount, status, reason):
        self.conn.execute(
            "INSERT INTO ledger (invoice_number, revision, vendor, amount, status, reason) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (invoice_number, revision or "", vendor, amount, status, reason),
        )
        self.conn.commit()