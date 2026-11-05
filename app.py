"""AutoVoice: a client-facing invoice dashboard. Run with: streamlit run app.py"""
import tempfile
import time
from pathlib import Path

import pandas as pd
import streamlit as st

import config
from agents.llm import get_llm
from database.store import Store
from workflow.pipeline import process_invoice

APP_NAME = "AutoVoice"
SAMPLES = Path("data/invoices")
SUPPORTED = ["txt", "pdf", "json", "csv", "xml"]

# The diagram's stages, and which workflow steps belong to each one.
STAGES = ["Read invoice", "Run checks", "VP and critic review", "Make payment"]
STAGE_OF_STEP = {
    "extract": 0, "verify": 0, "validate": 1, "vp_review": 2, "critique": 2,
    "finalize": 2, "unreadable": 3, "pay": 3, "hold": 3, "reject": 3,
}
OUTCOMES = {
    "PAID": {"title": "Paid", "banner": st.success, "color": "#15803D", "ink": "#FFFFFF"},
    "HELD": {"title": "Needs human review", "banner": st.warning, "color": "#F2B62B", "ink": "#2B1D00"},
    "REJECTED": {"title": "Rejected, no payment made", "banner": st.error, "color": "#C02B2B", "ink": "#FFFFFF"},
}
SEVERITY_LABELS = {"reject": "Problem", "hold": "Needs a person", "warning": "Worth noting"}
OUTCOME_SUMMARIES = {
    "PAID": "Every check passed, so this invoice was paid.",
    "HELD": "A person needs to look at this invoice before it can be paid.",
    "REJECTED": "A problem stopped this invoice from being paid.",
}
# Each check shown to the user: its name, what it looks at, and the findings it can produce.
CHECKS = [
    ("Required details", "Looks for the invoice number, vendor, due date and line items.",
     {"MISSING_INVOICE_NUMBER", "MISSING_VENDOR", "MISSING_DUE_DATE", "INVALID_DUE_DATE",
      "INVALID_INVOICE_DATE", "NO_LINE_ITEMS"}),
    ("Inventory and stock", "Confirms each item is in inventory, and that the quantities are valid and in stock.",
     {"INVALID_QUANTITY", "UNKNOWN_ITEM", "OUT_OF_STOCK", "STOCK_EXCEEDED"}),
    ("Prices", "Compares each unit price with the catalog price.", {"PRICE_VARIANCE"}),
    ("Math", "Adds up the line amounts, subtotal, tax and shipping, then compares them with the total.",
     {"LINE_TOTAL_MISMATCH", "MISSING_TOTAL", "INVALID_TOTAL", "SUBTOTAL_MISMATCH", "TOTAL_MISMATCH"}),
    ("Past payments", "Checks whether this invoice or an earlier version has already been paid.",
     {"DUPLICATE_PAYMENT", "REVISION_OF_PAID_INVOICE"}),
    ("Currency", "Confirms the invoice is in US dollars.", {"NON_USD_CURRENCY"}),
    ("Dates and terms", "Compares the due date with the invoice date and payment terms.",
     {"DUE_BEFORE_ISSUE", "TERMS_MISMATCH"}),
    ("Warning signs", "Looks for pressure to pay, a vendor name change, a total just under the review limit, "
     "and text trying to influence the approval.",
     {"PROMPT_INJECTION", "PRESSURE_LANGUAGE", "VENDOR_RENAMED", "NEAR_APPROVAL_THRESHOLD"}),
]
DECISION_LABELS = {"APPROVE": "Approved", "HOLD": "Sent to a person for review", "REJECT": "Not approved"}
STATUS_LABELS = {"PAID": "Paid", "HELD": "Needs review", "REJECTED": "Rejected"}


# Green that stays readable in each mode, paired with the text color that sits on top of it.
ACCENTS = {"light": ("#15803D", "#FFFFFF"), "dark": ("#22C55E", "#052E16")}

# Icon artwork from Lucide, the set behind lucide-react (ISC license: https://lucide.dev/license).
STEP_ICONS = {
    "Read": (
        '<path d="M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z"/>'
        '<path d="M14 2v5a1 1 0 0 0 1 1h5"/><path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>'
    ),
    "Check": '<path d="m8 11 2 2 4-4"/><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "Approve": (
        '<path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"/>'
        '<path d="M7 10v12"/>'
    ),
    "Pay": '<circle cx="12" cy="12" r="10"/><path d="m16 9-5.5 5.5L8 12"/>',
}


CHECK_ICONS = {
    "passed": '<circle cx="12" cy="12" r="10"/><path d="m16 9-5.5 5.5L8 12"/>',
    "blocked": '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/>',
    "flagged": (
        '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/>'
        '<path d="M12 9v4"/><path d="M12 17h.01"/>'
    ),
}
CHECK_COLORS = {
    "blocked": {"light": "#C02B2B", "dark": "#F87171"},
    "flagged": {"light": "#B25E00", "dark": "#F2B62B"},
}


def theme_mode():
    return "dark" if st.context.theme.type == "dark" else "light"


def accent():
    return ACCENTS[theme_mode()][0]


def lucide_icon(paths, size=42):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
        f'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{paths}</svg>'
    )


def safe(text):
    """Show invoice text literally. Streamlit would otherwise read $...$ as math and * as formatting."""
    text = str(text)
    for character in "\\$*_`#[]~":
        text = text.replace(character, f"\\{character}")
    return text


@st.cache_resource
def load_llm():
    return get_llm()


def sample_paths():
    return sorted(path for path in SAMPLES.iterdir() if path.suffix.lstrip(".").lower() in SUPPORTED)


def diagram_html(current, outcome=None):
    """Draw the pipeline. Labels are fixed text, so no invoice content reaches this HTML."""
    boxes = []
    for index, label in enumerate(STAGES):
        if outcome and index == len(STAGES) - 1:
            color, status, text = OUTCOMES[outcome]["color"], "filled", OUTCOMES[outcome]["title"]
        elif index < current or outcome:
            color, status, text = accent(), "done", label
        elif index == current:
            color, status, text = accent(), "filled", label
        else:
            color, status, text = "rgba(128,128,128,.45)", "todo", label
        background = color if status == "filled" else "transparent"
        if status != "filled":
            ink = "inherit"
        elif outcome and index == len(STAGES) - 1:
            ink = OUTCOMES[outcome]["ink"]
        else:
            ink = ACCENTS[theme_mode()][1]
        mark = "&#10003; " if status == "done" else ""
        boxes.append(
            f'<div style="flex:1 1 120px;padding:12px 8px;text-align:center;font-weight:600;font-size:14px;'
            f'border:2px solid {color};border-radius:10px;background:{background};color:{ink};">{mark}{text}</div>'
        )
    arrow = '<div style="align-self:center;opacity:.6;">&#8594;</div>'
    return f'<div style="display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 16px;">{arrow.join(boxes)}</div>'


def live_tracker(placeholder, pause):
    """Update the diagram as each workflow step starts."""

    def on_step(step, state):
        placeholder.markdown(diagram_html(STAGE_OF_STEP[step]), unsafe_allow_html=True)
        time.sleep(pause)

    return on_step


def show_read_step(result):
    invoice = result["invoice"]
    if invoice is None:
        st.write("We couldn't read this file.")
        st.write(safe(result["reason"]))
        return
    st.write("Here's what we found on the invoice.")
    total = invoice["total"]
    facts = {
        "Invoice": invoice["invoice_number"] or "Missing",
        "Vendor": invoice["vendor"] or "Missing",
        "Total": "Missing" if total is None else f"{total:,.2f} {invoice['currency']}",
        "Due": invoice["due_date"] or "Missing",
    }
    for column, (label, value) in zip(st.columns(4), facts.items()):
        column.caption(label)
        column.markdown(f"**{safe(value)}**")
    items = pd.DataFrame(invoice["items"], columns=["name", "quantity", "unit_price"])
    items.columns = ["Item", "Quantity", "Unit price"]
    st.dataframe(items, hide_index=True)


def show_check_line(status, title, explanation):
    """One line of the checks list. The title and explanation are fixed text, so the HTML is safe."""
    color = accent() if status == "passed" else CHECK_COLORS[status][theme_mode()]
    icon = lucide_icon(CHECK_ICONS[status], size=20)
    st.markdown(
        f'<div style="display:flex;gap:10px;align-items:flex-start;margin-bottom:8px;">'
        f'<span style="color:{color};flex:none;line-height:0;margin-top:3px;">{icon}</span>'
        f"<span><strong>{title}:</strong> {explanation}</span></div>",
        unsafe_allow_html=True,
    )


def show_check_step(result):
    if result["invoice"] is None:
        st.write("The checks didn't run, because the file couldn't be read.")
        return
    remaining = list(result["findings"])
    for title, explanation, codes in CHECKS:
        found = [finding for finding in remaining if finding["code"] in codes]
        remaining = [finding for finding in remaining if finding["code"] not in codes]
        if not found:
            show_check_line("passed", title, explanation)
            continue
        blocked = any(finding["severity"] == "reject" for finding in found)
        show_check_line("blocked" if blocked else "flagged", title, explanation)
        for finding in found:
            st.markdown(f"- **{SEVERITY_LABELS[finding['severity']]}:** {safe(finding['message'])}")
    for finding in remaining:  # A new check that hasn't been added to CHECKS still gets shown.
        st.markdown(f"- **{SEVERITY_LABELS[finding['severity']]}:** {safe(finding['message'])}")


def round_label(reviewer, number, total):
    """Mention rounds only when the critic's objection caused more than one."""
    return f"{reviewer}, round {number}" if total > 1 else reviewer


def show_vp_step(result):
    if not result["drafts"]:
        st.write("The VP didn't review this, because the file couldn't be read.")
    for number, draft in enumerate(result["drafts"], start=1):
        with st.container(border=True):
            st.caption(round_label("VP of Finance", number, len(result["drafts"])))
            st.write(safe(draft["reasoning"]))


def show_critic_step(result):
    if not result["critiques"]:
        st.write("The critic didn't review this, because the file couldn't be read.")
    for number, critique in enumerate(result["critiques"], start=1):
        with st.container(border=True):
            st.caption(round_label("Critic", number, len(result["critiques"])))
            st.write(safe(critique["critique"]))


def show_final_decision(result):
    invoice = result["invoice"] or {}
    st.write(f"**{DECISION_LABELS[result['decision']]}.**")
    if result["outcome"] == "PAID":
        st.write(f"Paid {invoice['total']:,.2f} {invoice['currency']} to {safe(invoice['vendor'].rstrip('.'))}.")
    elif result["outcome"] == "HELD":
        st.write("No payment yet. This is waiting for a person to review it.")
    else:
        st.write("No payment was made.")


def show_outcome(result):
    outcome = OUTCOMES[result["outcome"]]
    outcome["banner"](f"**{outcome['title']}**\n\n{OUTCOME_SUMMARIES[result['outcome']]}")


def show_result(result):
    """The finished diagram, the outcome, then every step in plain language."""
    st.markdown(diagram_html(len(STAGES) - 1, result["outcome"]), unsafe_allow_html=True)
    show_outcome(result)

    st.header("What happened")
    st.subheader("1. Read invoice")
    show_read_step(result)
    st.subheader("2. Run checks")
    show_check_step(result)
    st.subheader("3. VP review")
    show_vp_step(result)
    st.subheader("4. Critic review")
    show_critic_step(result)
    st.subheader("5. Final decision")
    show_final_decision(result)
    with st.expander(f"Full audit trail ({len(result['trace'])} events)"):
        for entry in result["trace"]:
            st.text(f"{entry['time'][11:23]}  {entry['stage']:<9} {entry['message']}")


def process_single(path, store, llm):
    placeholder = st.empty()
    pause = 0.25 if llm is None else 0  # The offline run is otherwise too fast to see.
    on_step = live_tracker(placeholder, pause)
    with st.spinner("Checking your invoice"):
        result = process_invoice(path, store, llm, config.AUDIT_LOG, on_step)
    placeholder.empty()
    return result


def process_all(store, llm):
    store.reset_ledger()
    paths = sample_paths()
    progress = st.progress(0.0)
    placeholder = st.empty()
    on_step = live_tracker(placeholder, 0)
    results = []
    for number, path in enumerate(paths, start=1):
        progress.progress((number - 1) / len(paths), text=f"Checking {path.name} ({number} of {len(paths)})")
        results.append(process_invoice(path, store, llm, config.AUDIT_LOG, on_step))
    progress.empty()
    placeholder.empty()
    return results


def show_dashboard(store):
    rows = [dict(row) for row in store.conn.execute("SELECT * FROM ledger ORDER BY id DESC").fetchall()]
    if not rows:
        st.info("No invoices have been processed yet. Go to Process invoices to check your first one.")
        return

    paid = [row for row in rows if row["status"] == "PAID"]
    columns = st.columns(4)
    columns[0].metric("Invoices checked", len(rows))
    columns[1].metric("Paid", len(paid))
    columns[2].metric("Needs review", sum(row["status"] == "HELD" for row in rows))
    columns[3].metric("Rejected", sum(row["status"] == "REJECTED" for row in rows))

    total_paid = sum(row["amount"] for row in paid)
    st.write("**Total paid:** " + safe(f"${total_paid:,.2f}"))
    choices = st.multiselect("Show", list(STATUS_LABELS.values()), default=list(STATUS_LABELS.values()))
    table = pd.DataFrame(rows)
    table["status"] = table["status"].map(STATUS_LABELS)
    table = table[table["status"].isin(choices)]
    table = table[["invoice_number", "vendor", "amount", "status", "reason"]]
    table.columns = ["Invoice", "Vendor", "Amount", "Status", "Reason"]
    table["Status"] = table["Status"].map(lambda label: [label])  # This column type shows a list as colored pills.
    labels = list(STATUS_LABELS.values())
    st.dataframe(
        table,
        hide_index=True,
        column_config={
            "Amount": st.column_config.NumberColumn(format="%.2f"),
            "Status": st.column_config.MultiselectColumn(
                options=labels,
                color=[OUTCOMES[status]["color"] for status in STATUS_LABELS],
                width=125,
            ),
        },
    )
    st.caption("Amounts are in the invoice's own currency. Use the table's menu to search or download it as a CSV.")


def show_start_page():
    """The welcome screen. All text here is fixed, so the HTML is safe."""
    color = accent()
    circles = "".join(
        '<div style="flex:1 1 120px;text-align:center;">'
        f'<div style="width:96px;height:96px;margin:0 auto 12px;border:2px solid {color};border-radius:50%;'
        f'display:flex;align-items:center;justify-content:center;color:{color};">{lucide_icon(icon)}</div>'
        f'<div style="font-size:18px;font-weight:600;">{label}</div></div>'
        for label, icon in STEP_ICONS.items()
    )
    st.markdown(
        f"""<div style="position:fixed;top:0;left:24px;height:60px;display:flex;align-items:center;z-index:999991;
font-size:24px;font-weight:800;color:{color};">{APP_NAME}</div>
<div style="text-align:center;padding-top:8vh;max-width:820px;margin:0 auto;">
<div style="font-size:44px;font-weight:700;">How It Works</div>
<p style="font-size:20px;margin:8px auto 40px;">Upload an invoice, and {APP_NAME} checks it and pays it once it's approved.</p>
<div style="display:flex;flex-wrap:wrap;gap:24px;margin-bottom:44px;">{circles}</div>
</div>""",
        unsafe_allow_html=True,
    )
    _, middle, _ = st.columns([1, 1, 1])
    if middle.button("Check an invoice", type="primary", width="stretch"):
        st.session_state["page"] = "app"
        st.rerun()
    # The first model setup imports heavy libraries. Do it now, after this page is on screen,
    # so that Get started opens the app right away.
    load_llm()


st.set_page_config(page_title=APP_NAME, layout="wide", initial_sidebar_state="collapsed")
# White text on the darker green stays readable in both light and dark mode.
st.markdown(
    '<style>button[data-testid="stBaseButton-primary"] {background: #15803D !important; '
    'border-color: #15803D !important; color: #FFFFFF !important;} '
    'button[data-testid="stBaseButton-primary"]:hover {background: #166534 !important; '
    'border-color: #166534 !important;} '
    'button[data-testid="stBaseButton-primary"] p {color: #FFFFFF !important;}</style>',
    unsafe_allow_html=True,
)
store = Store()  # A new connection per run, because Streamlit may use a different thread each time.

if st.session_state.get("page", "home") == "home":
    show_start_page()
    st.stop()

st.markdown(f'<h1 style="color:{accent()};">{APP_NAME}</h1>', unsafe_allow_html=True)

with st.sidebar:
    st.header("Settings")
    use_offline = st.toggle("Demo mode (no AI model, free and instant)", value=False)
    llm = None if use_offline else load_llm()
    if llm is None:
        st.info("Demo mode: a built-in simulation stands in for the AI model.")
    else:
        st.success(f"AI model: {config.MODEL}")
    if st.button("Clear all results"):
        store.reset_ledger()
        for name in ("single", "batch"):
            st.session_state.pop(name, None)
        st.rerun()
    if st.button("Back to start page"):
        st.session_state["page"] = "home"
        st.rerun()

process_tab, batch_tab, dashboard_tab = st.tabs(["Process an invoice", "Process all samples", "Dashboard"])

with process_tab:
    chosen = st.selectbox("Choose a sample invoice", [path.name for path in sample_paths()])
    uploaded = st.file_uploader("Or upload your own", type=SUPPORTED)
    # Clear the last result when a different invoice is chosen, so it can't be mistaken for this one.
    selected_invoice = uploaded.name if uploaded else chosen
    if st.session_state.get("single_source") != selected_invoice:
        st.session_state.pop("single", None)
    st.session_state["single_source"] = selected_invoice
    if st.button("Process invoice", type="primary"):
        if uploaded:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / Path(uploaded.name).name
                path.write_bytes(uploaded.getvalue())
                st.session_state["single"] = process_single(path, store, llm)
        else:
            st.session_state["single"] = process_single(SAMPLES / chosen, store, llm)
    if "single" in st.session_state:
        result = st.session_state["single"]
        show_result(result)

with batch_tab:
    st.write("Clears earlier results, then checks every sample in order. "
             "With a live AI model, it can take several minutes.")
    if st.button("Process all samples", type="primary"):
        st.session_state["batch"] = process_all(store, llm)
    if "batch" in st.session_state:
        results = st.session_state["batch"]
        st.success(f"Finished checking {len(results)} files. Open Dashboard for the complete table.")
        selected = st.selectbox("See how one was decided", [result["file"] for result in results])
        result = next(item for item in results if item["file"] == selected)
        show_result(result)

with dashboard_tab:
    show_dashboard(store)
