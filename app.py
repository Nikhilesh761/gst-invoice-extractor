"""
GST Invoice Extractor & Analytics — Streamlit app
Zero-cost stack: Streamlit Community Cloud (hosting) + Google Gemini free tier (extraction)

SETUP:
1. Get a free Gemini API key: https://aistudio.google.com/app/apikey
2. Locally: create .streamlit/secrets.toml with:
       GEMINI_API_KEY = "your-key-here"
3. Run locally:  streamlit run app.py
4. Deploy free: push this repo to GitHub -> share.streamlit.io -> New app ->
   point at this file -> add GEMINI_API_KEY in the app's "Secrets" settings.
"""

import streamlit as st
import google.generativeai as genai
import json
import pandas as pd
from PIL import Image
from datetime import datetime
from decimal import Decimal, InvalidOperation
import io

st.set_page_config(page_title="GST Invoice Extractor", page_icon="🧾", layout="wide")

# ---------- CONFIG ----------
API_KEY = st.secrets.get("GEMINI_API_KEY", "")
if not API_KEY:
    st.error("No GEMINI_API_KEY found in secrets. Add it in .streamlit/secrets.toml (local) "
              "or your Streamlit Cloud app's Secrets settings.")
    st.stop()

genai.configure(api_key=API_KEY)
model = genai.GenerativeModel("gemini-3.6-flash")

EXTRACTION_PROMPT = """You are reading a handwritten or printed Indian GST tax invoice photo.
Extract every field into STRICT JSON only — no markdown fences, no commentary, no rounding.

Schema:
{
  "vendor_name": "", "vendor_gstin": "", "bill_no": "", "date": "",
  "customer_name": "", "customer_gstin": "",
  "line_items": [
    {"particulars": "", "hsn_code": "", "qty": "", "rate": "", "amount": ""}
  ],
  "subtotal": "",
  "cgst_pct": "", "cgst_amt": "",
  "sgst_pct": "", "sgst_amt": "",
  "igst_pct": "", "igst_amt": "",
  "grand_total": "",
  "uncertain_fields": ["list the exact field names/line-item indices you were not fully sure of"],
  "extraction_confidence": "high, medium, or low based on handwriting legibility"
}

HARD RULES — these are non-negotiable:
1. Copy every number EXACTLY as written, digit for digit, as a string (e.g. "1500.00", not 1500).
   Do not round. Do not convert. Do not "clean up" a number to make totals match.
2. NEVER guess a digit you cannot clearly see. If a number is illegible or ambiguous, put your
   best-guess value in the field AND add that field's name to "uncertain_fields" — do not silently
   invent a plausible-looking number.
3. Do NOT attempt to fix or reconcile math yourself. If the handwritten total doesn't match what
   qty*rate would produce, copy the number exactly as written anyway — a separate process will
   check the math. Your only job is faithful transcription, not correction.
4. If a field is genuinely not present on the invoice, use "" for text or "" for numbers (not 0 —
   0 implies you saw a zero, "" means absent).
5. Preserve leading/trailing characters exactly as written (e.g. if it looks like "1O0" due to
   handwriting ambiguity between O and 0, note it in uncertain_fields rather than silently choosing).
"""


def to_decimal(val):
    """Convert an extracted string field to Decimal without any float rounding.
    Returns (Decimal or None, was_parseable: bool)."""
    if val is None or val == "":
        return None, True  # legitimately absent, not an error
    s = str(val).strip().replace(",", "").replace("₹", "").replace("Rs.", "").replace("Rs", "")
    try:
        return Decimal(s), True
    except InvalidOperation:
        return None, False


def verify_invoice_math(inv):
    """Recompute everything with exact Decimal arithmetic. Never overwrite the
    extracted numbers — only flag discrepancies so a human can check the photo."""
    issues = []
    line_total = Decimal("0")

    for idx, item in enumerate(inv.get("line_items", [])):
        qty, qty_ok = to_decimal(item.get("qty"))
        rate, rate_ok = to_decimal(item.get("rate"))
        amount, amt_ok = to_decimal(item.get("amount"))

        if not (qty_ok and rate_ok and amt_ok):
            issues.append(f"Line {idx+1}: unparseable number, needs manual check.")
            continue

        if qty is not None and rate is not None and amount is not None:
            expected = (qty * rate).quantize(Decimal("0.01"))
            actual = amount.quantize(Decimal("0.01"))
            if expected != actual:
                issues.append(
                    f"Line {idx+1} ({item.get('particulars','')}): qty×rate = {expected}, "
                    f"but invoice shows amount = {actual}. Diff = {actual - expected}."
                )
            line_total += amount if amount is not None else Decimal("0")

    subtotal, sub_ok = to_decimal(inv.get("subtotal"))
    cgst, _ = to_decimal(inv.get("cgst_amt"))
    sgst, _ = to_decimal(inv.get("sgst_amt"))
    igst, _ = to_decimal(inv.get("igst_amt"))
    grand_total, gt_ok = to_decimal(inv.get("grand_total"))

    if sub_ok and subtotal is not None and line_total != Decimal("0"):
        if subtotal.quantize(Decimal("0.01")) != line_total.quantize(Decimal("0.01")):
            issues.append(
                f"Sum of line-item amounts = {line_total.quantize(Decimal('0.01'))}, "
                f"but invoice subtotal shows {subtotal.quantize(Decimal('0.01'))}."
            )

    if gt_ok and grand_total is not None and sub_ok and subtotal is not None:
        tax_sum = (cgst or Decimal("0")) + (sgst or Decimal("0")) + (igst or Decimal("0"))
        expected_gt = (subtotal + tax_sum).quantize(Decimal("0.01"))
        actual_gt = grand_total.quantize(Decimal("0.01"))
        if expected_gt != actual_gt:
            issues.append(
                f"Subtotal + tax = {expected_gt}, but invoice grand total shows {actual_gt}. "
                f"Diff = {actual_gt - expected_gt}."
            )

    return issues


def d(val, default="0"):
    """Safe Decimal accessor for display/aggregation — never silently rounds a real number,
    only substitutes 0 when the field was genuinely blank."""
    dec, ok = to_decimal(val)
    return dec if (ok and dec is not None) else Decimal(default)

# ---------- SESSION STATE ----------
if "invoices" not in st.session_state:
    st.session_state.invoices = []  # list of dicts, one per processed invoice

# ---------- SIDEBAR ----------
st.sidebar.title("🧾 GST Extractor")
st.sidebar.markdown("Upload invoice photos → get structured GST data + analytics, automatically.")
customer_name_input = st.sidebar.text_input("Customer / business name (optional tag)", "")

# ---------- MAIN: UPLOAD ----------
st.title("GST Invoice Extractor & Analytics")
st.caption("Upload one or more invoice photos below. Each is read, validated, and added to your running report.")

uploaded_files = st.file_uploader(
    "Upload invoice photo(s)", type=["jpg", "jpeg", "png"], accept_multiple_files=True
)

if uploaded_files and st.button("Extract data from uploaded photos", type="primary"):
    progress = st.progress(0, text="Starting extraction...")
    for i, uf in enumerate(uploaded_files):
        progress.progress((i) / len(uploaded_files), text=f"Reading {uf.name}...")
        img = Image.open(uf)
        try:
            resp = model.generate_content([EXTRACTION_PROMPT, img])
            raw = resp.text.strip()
            # strip accidental markdown fences
            if raw.startswith("```"):
                raw = raw.strip("`")
                raw = raw.replace("json\n", "", 1) if raw.startswith("json\n") else raw
            data = json.loads(raw)
            data["_source_file"] = uf.name
            data["_uploaded_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            data["_customer_tag"] = customer_name_input or "unspecified"
            data["_math_issues"] = verify_invoice_math(data)  # independent Decimal recheck
            st.session_state.invoices.append(data)
        except Exception as e:
            st.warning(f"Could not parse {uf.name}: {e}")
    progress.progress(1.0, text="Done.")
    st.success(f"Processed {len(uploaded_files)} invoice(s).")

st.divider()

# ---------- RESULTS TABLE ----------
if st.session_state.invoices:
    st.subheader("Extracted invoices")

    rows = []
    for inv in st.session_state.invoices:
        issues = inv.get("_math_issues", [])
        uncertain = inv.get("uncertain_fields", [])
        rows.append({
            "File": inv.get("_source_file", ""),
            "Customer tag": inv.get("_customer_tag", ""),
            "Vendor": inv.get("vendor_name", ""),
            "Bill No.": inv.get("bill_no", ""),
            "Date": inv.get("date", ""),
            "Subtotal": float(d(inv.get("subtotal"))),
            "CGST": float(d(inv.get("cgst_amt"))),
            "SGST": float(d(inv.get("sgst_amt"))),
            "IGST": float(d(inv.get("igst_amt"))),
            "Grand Total": float(d(inv.get("grand_total"))),
            "Confidence": inv.get("extraction_confidence", ""),
            "Math OK?": "⚠️ Check" if issues else "✅ Verified",
            "Uncertain fields": ", ".join(uncertain) if uncertain else "—",
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True)

    # ---------- FLAGGED INVOICES — shown prominently, never silently corrected ----------
    any_issues = any(inv.get("_math_issues") for inv in st.session_state.invoices)
    any_uncertain = any(inv.get("uncertain_fields") for inv in st.session_state.invoices)
    if any_issues or any_uncertain:
        st.error("Some invoices need a manual look before you trust the numbers below. "
                  "Nothing is auto-corrected — the app only flags, it never guesses on your behalf.")
        for inv in st.session_state.invoices:
            issues = inv.get("_math_issues", [])
            uncertain = inv.get("uncertain_fields", [])
            if issues or uncertain:
                with st.expander(f"⚠️ {inv.get('_source_file','')} — {inv.get('vendor_name','')} "
                                  f"(Bill {inv.get('bill_no','')})", expanded=False):
                    if issues:
                        st.markdown("**Math discrepancies (recomputed independently, exact decimal math):**")
                        for iss in issues:
                            st.markdown(f"- {iss}")
                    if uncertain:
                        st.markdown("**Fields the model wasn't fully confident reading:**")
                        st.markdown(f"- {', '.join(uncertain)}")
                    st.caption("Open the original photo and correct these manually before relying on this invoice's totals.")

    # ---------- LINE ITEM DETAIL ----------
    with st.expander("View line-item detail per invoice"):
        for inv in st.session_state.invoices:
            st.markdown(f"**{inv.get('vendor_name','')} — Bill {inv.get('bill_no','')} ({inv.get('date','')})**")
            items = inv.get("line_items", [])
            if items:
                st.table(pd.DataFrame(items))
            else:
                st.caption("No line items extracted.")

    # ---------- ANALYTICS ----------
    st.subheader("Analytics")

    # exact Decimal totals for headline metrics — summed before any float conversion
    total_spend = sum((d(inv.get("grand_total")) for inv in st.session_state.invoices), Decimal("0"))
    total_cgst = sum((d(inv.get("cgst_amt")) for inv in st.session_state.invoices), Decimal("0"))
    total_sgst = sum((d(inv.get("sgst_amt")) for inv in st.session_state.invoices), Decimal("0"))
    total_igst = sum((d(inv.get("igst_amt")) for inv in st.session_state.invoices), Decimal("0"))
    total_gst = total_cgst + total_sgst + total_igst

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total invoices", len(df))
    col2.metric("Total spend (₹)", f"{total_spend:,.2f}")
    col3.metric("Total GST paid (₹)", f"{total_gst:,.2f}")
    col4.metric("Effective GST rate", f"{(total_gst / total_spend * 100):.2f}%" if total_spend else "—")

    tab1, tab2, tab3, tab4, tab5 = st.tabs(
        ["By Vendor", "By HSN Code", "By Item", "Monthly Trend", "Tax Breakdown"]
    )

    with tab1:
        vendor_summary = df.groupby("Vendor")["Grand Total"].sum().sort_values(ascending=False)
        st.bar_chart(vendor_summary)
        st.dataframe(vendor_summary.reset_index().rename(columns={"Grand Total": "Total Spend (₹)"}),
                     use_container_width=True)

    with tab2:
        hsn_rows = []
        for inv in st.session_state.invoices:
            for item in inv.get("line_items", []):
                hsn_rows.append({
                    "HSN Code": item.get("hsn_code", "") or "unspecified",
                    "Amount": float(d(item.get("amount"))),
                })
        if hsn_rows:
            hsn_df = pd.DataFrame(hsn_rows)
            hsn_summary = hsn_df.groupby("HSN Code")["Amount"].sum().sort_values(ascending=False)
            st.bar_chart(hsn_summary)
            st.dataframe(hsn_summary.reset_index().rename(columns={"Amount": "Total Amount (₹)"}),
                         use_container_width=True)
        else:
            st.caption("No line items to break down yet.")

    with tab3:
        item_rows = []
        for inv in st.session_state.invoices:
            for item in inv.get("line_items", []):
                qty = d(item.get("qty"))
                rate = d(item.get("rate"))
                item_rows.append({
                    "Particulars": item.get("particulars", ""),
                    "Vendor": inv.get("vendor_name", ""),
                    "Qty": float(qty),
                    "Rate": float(rate),
                    "Amount": float(d(item.get("amount"))),
                })
        if item_rows:
            item_df = pd.DataFrame(item_rows)
            st.caption("Same item, different rate across vendors/dates — useful for spotting overcharging.")
            st.dataframe(
                item_df.groupby(["Particulars", "Vendor"]).agg(
                    Times_Billed=("Amount", "count"),
                    Avg_Rate=("Rate", "mean"),
                    Total_Amount=("Amount", "sum"),
                ).reset_index().sort_values("Total_Amount", ascending=False),
                use_container_width=True,
            )
        else:
            st.caption("No line items to break down yet.")

    with tab4:
        trend_rows = []
        for inv in st.session_state.invoices:
            trend_rows.append({"Date": inv.get("date", "") or "unspecified",
                                "Grand Total": float(d(inv.get("grand_total")))})
        trend_df = pd.DataFrame(trend_rows)
        monthly = trend_df.groupby("Date")["Grand Total"].sum()
        st.line_chart(monthly)
        st.caption("Grouped by the date exactly as it appears on each invoice — normalize date formats "
                    "manually if vendors write dates inconsistently.")

    with tab5:
        tax_df = pd.DataFrame({
            "Tax type": ["CGST", "SGST", "IGST"],
            "Amount (₹)": [float(total_cgst), float(total_sgst), float(total_igst)],
        })
        st.bar_chart(tax_df.set_index("Tax type"))
        st.dataframe(tax_df, use_container_width=True)

    low_conf = df[df["Confidence"] == "low"]
    if not low_conf.empty:
        st.warning(f"{len(low_conf)} invoice(s) had low extraction confidence — worth a manual double-check "
                    "against the original photo.")

    # ---------- EXPORT ----------
    st.subheader("Export")
    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Summary", index=False)

        all_items = []
        for inv in st.session_state.invoices:
            for item in inv.get("line_items", []):
                all_items.append({
                    "Vendor": inv.get("vendor_name", ""),
                    "Bill No.": inv.get("bill_no", ""),
                    "Particulars": item.get("particulars", ""),
                    "HSN Code": item.get("hsn_code", ""),
                    "Qty": str(item.get("qty", "")),      # kept as exact string, not re-rounded
                    "Rate": str(item.get("rate", "")),
                    "Amount": str(item.get("amount", "")),
                })
        if all_items:
            pd.DataFrame(all_items).to_excel(writer, sheet_name="Line Items", index=False)

        flags = []
        for inv in st.session_state.invoices:
            for iss in inv.get("_math_issues", []):
                flags.append({"File": inv.get("_source_file", ""), "Issue": iss})
            for u in inv.get("uncertain_fields", []):
                flags.append({"File": inv.get("_source_file", ""), "Issue": f"Uncertain field: {u}"})
        if flags:
            pd.DataFrame(flags).to_excel(writer, sheet_name="Flags - Needs Review", index=False)

    st.download_button(
        "Download Excel report",
        data=excel_buffer.getvalue(),
        file_name=f"gst_report_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    if st.button("Clear all data"):
        st.session_state.invoices = []
        st.rerun()
else:
    st.info("No invoices processed yet. Upload photos above to get started.")
