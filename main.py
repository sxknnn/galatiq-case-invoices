"""Command-line entry point.

    python main.py --invoice_path=data/invoices/invoice_1001.txt
    python main.py --batch=data/invoices --reset
"""
import argparse
import json
import sys
from pathlib import Path

import config
from agents.llm import get_llm
from database.store import Store
from workflow.pipeline import process_invoice

SUPPORTED = {".txt", ".pdf", ".json", ".csv", ".xml"}


def print_result(result):
    invoice = result["invoice"] or {}
    print(f"\n{result['file']}: {result['outcome']}")
    print(f"  Invoice: {invoice.get('invoice_number')} | Vendor: {invoice.get('vendor')} | "
          f"Total: {invoice.get('total')} {invoice.get('currency', '')} | Due: {invoice.get('due_date')}")
    for finding in result["findings"]:
        print(f"  [{finding['severity'].upper()}] {finding['code']}: {finding['message']}")
    print(f"  Reason: {result['reason']}")


def main():
    parser = argparse.ArgumentParser(description="Process invoices with a multi-agent workflow.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--invoice_path", help="One invoice file (TXT, PDF, JSON, CSV or XML)")
    source.add_argument("--batch", help="A folder of invoices, processed in filename order")
    parser.add_argument("--offline", action="store_true", help="Use the simulation even if a key is set")
    parser.add_argument("--reset", action="store_true", help="Clear the payment ledger first")
    parser.add_argument("--json", action="store_true", help="Print full results as JSON")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles may not default to UTF-8.

    if args.invoice_path:
        paths = [Path(args.invoice_path)]
    else:
        folder = Path(args.batch)
        if not folder.is_dir():
            parser.error(f"Not a folder: {folder}")
        paths = sorted(path for path in folder.iterdir() if path.suffix.lower() in SUPPORTED)

    store = Store()
    if args.reset:
        store.reset_ledger()
    llm = None if args.offline else get_llm()
    print(f"Model: {'offline simulation (no LLM)' if llm is None else config.MODEL}")

    results = []
    for number, path in enumerate(paths, start=1):
        print(f"[{number}/{len(paths)}] Processing {path.name}", flush=True)
        results.append(process_invoice(path, store, llm, config.AUDIT_LOG))

    if args.json:
        print(json.dumps(results, indent=2, default=str))
        return
    for result in results:
        print_result(result)
    if len(results) > 1:
        counts = {name: sum(r["outcome"] == name for r in results) for name in ("PAID", "HELD", "REJECTED")}
        print(f"\nSummary: {counts['PAID']} paid, {counts['HELD']} held, {counts['REJECTED']} rejected")
    print(f"\nAudit log: {config.AUDIT_LOG}")


if __name__ == "__main__":
    main()
