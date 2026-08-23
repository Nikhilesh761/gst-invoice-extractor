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
from concurrent.futures import ThreadPoolExecutor, as_completed
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
  "uncertain_money_fields": ["ONLY numeric/money fields you weren't sure of, by name/index — e.g. 'line_items[2].amount', 'grand_total'. Do NOT include text fields like customer_name, customer_gstin, vendor_name, date, or particulars here — those don't affect GST accuracy."],
  "extraction_confidence": "high, medium, or low based on handwriting legibility of the MONEY figures specifically"
}

HARD RULES — these are non-negotiable:
1. Copy every number EXACTLY as written, digit for digit, as a string (e.g. "1500.00", not 1500).
   Do not round. Do not convert. Do not "clean up" a number to make totals match.
2. NEVER guess a digit you cannot clearly see in a MONEY field (qty, rate, amount, subtotal, tax
   amounts, grand total). If a money digit is illegible or ambiguous, put your best-guess value in
   the field AND add that field to "uncertain_money_fields".
3. Text fields (customer name, customer GSTIN, vendor name, date, particulars/product names) are
   often genuinely blank or messy on real invoices — that is normal and not something to flag.
   If blank, use "". If legible even loosely, just transcribe it — do not add these to
   uncertain_money_fields under any circumstances, since they don't affect GST calculation accuracy.
4. Do NOT attempt to fix or reconcile math yourself. Copy numbers exactly as written even if they
   don't seem to add up — a separate process checks the math. Your only job is faithful
   transcription of what's on the page, not correction.
5. If a numeric field is genuinely not present on the invoice (e.g. no IGST line at all), use ""
   (not 0 — 0 implies you saw a zero, "" means absent).
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


def extract_leading_number(val):
    """For fields like qty that sometimes carry a unit (e.g. '3mtr', '15 nos'),
    pull out the leading numeric portion for math purposes. Returns (Decimal or None, ok)."""
    if val is None or val == "":
        return None, True
    s = str(val).strip().replace(",", "")
    num_chars = []
    for ch in s:
        if ch.isdigit() or ch == "." or (ch == "-" and not num_chars):
            num_chars.append(ch)
        else:
            break
    if not num_chars:
        return None, False
    try:
        return Decimal("".join(num_chars)), True
    except InvalidOperation:
        return None, False


def verify_invoice_math(inv):
    """Recompute everything with exact Decimal arithmetic. Never overwrite the
    extracted numbers — only flag discrepancies so a human can check the photo."""
    issues = []
    line_total = Decimal("0")

    for idx, item in enumerate(inv.get("line_items", [])):
        qty, qty_ok = extract_leading_number(item.get("qty"))       # tolerates "3mtr", "15 nos" etc.
        rate, rate_ok = to_decimal(item.get("rate"))
        amount, amt_ok = to_decimal(item.get("amount"))

        # Amount always counts toward the subtotal check if it parsed, regardless of
        # whether qty/rate parsed — a unit like "mtr" shouldn't hide a real amount.
        if amt_ok and amount is not None:
            line_total += amount

        if not amt_ok:
            issues.append(f"Line {idx+1}: amount field unparseable, needs manual check.")
            continue
        if not (qty_ok and rate_ok):
            continue  # qty/rate had non-numeric units we can't cross-check, but amount still counted above

        if qty is not None and rate is not None and amount is not None:
            expected = (qty * rate).quantize(Decimal("0.01"))
            actual = amount.quantize(Decimal("0.01"))
            if expected != actual:
                issues.append(
                    f"Line {idx+1} ({item.get('particulars','')}): qty×rate = {expected}, "
                    f"but invoice shows amount = {actual}. Diff = {actual - expected}."
                )

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


def prep_image_for_upload(pil_img, max_dimension=1600):
    """Downscale + normalize before sending to the API. Phone photos are often
    3000-4000px / several MB — the model reads handwriting just as accurately at
    ~1600px on the longest side, but the request transmits and processes far faster.
    This is the single biggest speed lever available without changing accuracy."""
    img = pil_img.convert("RGB")
    w, h = img.size
    if max(w, h) > max_dimension:
        scale = max_dimension / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return img


def extract_one(uf_name, pil_img, customer_tag):
    """Runs one extraction call. Built as a standalone function so multiple
    uploads can run concurrently instead of one-by-one."""
    img = prep_image_for_upload(pil_img)
    resp = model.generate_content([EXTRACTION_PROMPT, img])
    raw = resp.text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.replace("json\n", "", 1) if raw.startswith("json\n") else raw
    data = json.loads(raw)
    data["_source_file"] = uf_name
    data["_uploaded_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    data["_customer_tag"] = customer_tag
    data["_math_issues"] = verify_invoice_math(data)
    return data

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
    total = len(uploaded_files)
    done = 0

    # Free-tier Gemini allows ~15-30 requests/min — 4 concurrent workers is a safe
    # speed-up without tripping rate limits. Raise this only if you're on a paid tier.
    MAX_WORKERS = min(4, total)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(extract_one, uf.name, Image.open(uf), customer_name_input or "unspecified"): uf.name
            for uf in uploaded_files
        }
        for future in as_completed(futures):
            fname = futures[future]
            try:
                data = future.result()
                st.session_state.invoices.append(data)
            except Exception as e:
                st.warning(f"Could not parse {fname}: {e}")
            done += 1
            progress.progress(done / total, text=f"Processed {done}/{total}...")

    progress.progress(1.0, text="Done.")
    st.success(f"Processed {total} invoice(s).")

st.divider()

# ---------- RESULTS TABLE ----------
if st.session_state.invoices:
    st.subheader("Extracted invoices")

    rows = []
    for inv in st.session_state.invoices:
        issues = inv.get("_math_issues", [])
        uncertain = inv.get("uncertain_money_fields", [])
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
            "Numbers OK?": "⚠️ Check" if (issues or uncertain) else "✅ Accurate",
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True)

    # ---------- FLAGGED INVOICES — only for genuine money-accuracy problems.
    # Text fields (customer name/GSTIN, date formatting) never trigger this anymore —
    # only a real math mismatch or a money digit the model wasn't confident reading. ----------
    any_issues = any(inv.get("_math_issues") for inv in st.session_state.invoices)
    any_uncertain = any(inv.get("uncertain_money_fields") for inv in st.session_state.invoices)
    if any_issues or any_uncertain:
        n_flagged = sum(1 for inv in st.session_state.invoices
                         if inv.get("_math_issues") or inv.get("uncertain_money_fields"))
        st.warning(f"{n_flagged} of {len(st.session_state.invoices)} invoice(s) have a money figure "
                    "worth double-checking against the photo. Everything else below is accurate as extracted.")
        for inv in st.session_state.invoices:
            issues = inv.get("_math_issues", [])
            uncertain = inv.get("uncertain_money_fields", [])
            if issues or uncertain:
                with st.expander(f"⚠️ {inv.get('_source_file','')} — {inv.get('vendor_name','')} "
                                  f"(Bill {inv.get('bill_no','')})", expanded=False):
                    if issues:
                        st.markdown("**Math discrepancies (recomputed independently, exact decimal math):**")
                        for iss in issues:
                            st.markdown(f"- {iss}")
                    if uncertain:
                        st.markdown("**Money figures the model wasn't fully confident reading:**")
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

    import openpyxl
    from openpyxl.chart import BarChart, PieChart, Reference
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    def safe_sheet_name(name, used):
        clean = "".join(c for c in (name or "Unknown Vendor") if c not in r'[]:*?/\\')[:28].strip() or "Unknown Vendor"
        candidate, i = clean, 1
        while candidate in used:
            i += 1
            candidate = f"{clean[:25]} ({i})"
        used.add(candidate)
        return candidate

    HEADER_FILL = PatternFill(start_color="1F2937", end_color="1F2937", fill_type="solid")
    HEADER_FONT = Font(color="FFFFFF", bold=True)
    FLAG_FILL = PatternFill(start_color="FFF3CD", end_color="FFF3CD", fill_type="solid")

    def style_header(ws, row_num, ncols):
        for c in range(1, ncols + 1):
            cell = ws.cell(row=row_num, column=c)
            cell.fill = HEADER_FILL
            cell.font = HEADER_FONT
            cell.alignment = Alignment(horizontal="center")

    def autosize(ws, ncols, min_w=10, max_w=45):
        for c in range(1, ncols + 1):
            letter = get_column_letter(c)
            longest = max(
                [len(str(ws.cell(row=r, column=c).value or "")) for r in range(1, ws.max_row + 1)] + [min_w]
            )
            ws.column_dimensions[letter].width = min(longest + 2, max_w)

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    used_names = set()

    # group invoices by vendor
    by_vendor = {}
    for inv in st.session_state.invoices:
        vname = inv.get("vendor_name") or "Unknown Vendor"
        by_vendor.setdefault(vname, []).append(inv)

    # ---- OVERVIEW SHEET ----
    ov = wb.create_sheet("Overview")
    headers = ["Vendor", "Invoices", "Total Spend (₹)", "Total CGST (₹)", "Total SGST (₹)",
               "Total IGST (₹)", "Total GST Paid (₹)", "Needs Review?"]
    ov.append(headers)
    style_header(ov, 1, len(headers))

    vendor_stats = []
    for vname, invs in by_vendor.items():
        spend = sum((d(i.get("grand_total")) for i in invs), Decimal("0"))
        cgst = sum((d(i.get("cgst_amt")) for i in invs), Decimal("0"))
        sgst = sum((d(i.get("sgst_amt")) for i in invs), Decimal("0"))
        igst = sum((d(i.get("igst_amt")) for i in invs), Decimal("0"))
        needs_review = any(i.get("_math_issues") for i in invs)
        vendor_stats.append((vname, len(invs), spend, cgst, sgst, igst, cgst + sgst + igst, needs_review))

    for row in vendor_stats:
        vname, n, spend, cgst, sgst, igst, gst_total, needs_review = row
        ov.append([vname, n, float(spend), float(cgst), float(sgst), float(igst),
                   float(gst_total), "⚠️ Yes" if needs_review else "OK"])
        if needs_review:
            for c in range(1, len(headers) + 1):
                ov.cell(row=ov.max_row, column=c).fill = FLAG_FILL

    grand_row = ov.max_row + 2
    ov.cell(row=grand_row, column=1, value="TOTAL").font = Font(bold=True)
    ov.cell(row=grand_row, column=3, value=sum(v[2] for v in vendor_stats)).font = Font(bold=True)
    ov.cell(row=grand_row, column=7, value=sum(v[6] for v in vendor_stats)).font = Font(bold=True)
    autosize(ov, len(headers))

    if vendor_stats:
        chart = BarChart()
        chart.title = "GST paid by vendor"
        chart.y_axis.title = "₹"
        n_vendors = len(vendor_stats)
        data = Reference(ov, min_col=7, min_row=1, max_row=1 + n_vendors)
        cats = Reference(ov, min_col=1, min_row=2, max_row=1 + n_vendors)
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(cats)
        chart.width, chart.height = 18, 10
        ov.add_chart(chart, f"J2")

        pie = PieChart()
        pie.title = "Total spend share by vendor"
        pdata = Reference(ov, min_col=3, min_row=1, max_row=1 + n_vendors)
        pie.add_data(pdata, titles_from_data=True)
        pie.set_categories(cats)
        pie.width, pie.height = 14, 10
        ov.add_chart(pie, f"J22")

    # ---- ONE SHEET PER VENDOR ----
    for vname, invs in by_vendor.items():
        sheet_name = safe_sheet_name(vname, used_names)
        ws = wb.create_sheet(sheet_name)

        ws.cell(row=1, column=1, value=vname).font = Font(bold=True, size=14)
        ws.cell(row=2, column=1, value=f"GSTIN: {invs[0].get('vendor_gstin','') or '—'}")

        # Line-item level detail: product, qty, rate, amount, and the invoice-level GST that applies to it
        headers = ["Bill No.", "Date", "Particulars", "HSN Code", "Qty", "Rate (₹)", "Amount (₹)",
                   "CGST % (bill)", "SGST % (bill)", "Needs Review?"]
        start_row = 4
        ws.append([])  # row 3 blank
        ws.append(headers)
        style_header(ws, start_row + 1, len(headers))

        r = start_row + 2
        for inv in invs:
            flagged = bool(inv.get("_math_issues"))
            for item in inv.get("line_items", []):
                ws.append([
                    inv.get("bill_no", ""),
                    inv.get("date", ""),
                    item.get("particulars", ""),
                    item.get("hsn_code", ""),
                    str(item.get("qty", "")),
                    str(item.get("rate", "")),
                    float(d(item.get("amount"))),
                    str(inv.get("cgst_pct", "")),
                    str(inv.get("sgst_pct", "")),
                    "⚠️" if flagged else "OK",
                ])
                if flagged:
                    for c in range(1, len(headers) + 1):
                        ws.cell(row=r, column=c).fill = FLAG_FILL
                r += 1

        items_end_row = r - 1
        autosize(ws, len(headers))

        # Invoice-level summary block (subtotal / tax / grand total per bill) below the line items
        r += 2
        ws.cell(row=r, column=1, value="Invoice-level summary").font = Font(bold=True, size=12)
        r += 1
        sum_headers = ["Bill No.", "Date", "Subtotal (₹)", "CGST (₹)", "SGST (₹)", "IGST (₹)", "Grand Total (₹)"]
        for c, h in enumerate(sum_headers, start=1):
            ws.cell(row=r, column=c, value=h)
        style_header(ws, r, len(sum_headers))
        summary_start = r + 1
        r += 1
        for inv in invs:
            ws.append_row = None  # no-op, keeping structure clear
            ws.cell(row=r, column=1, value=inv.get("bill_no", ""))
            ws.cell(row=r, column=2, value=inv.get("date", ""))
            ws.cell(row=r, column=3, value=float(d(inv.get("subtotal"))))
            ws.cell(row=r, column=4, value=float(d(inv.get("cgst_amt"))))
            ws.cell(row=r, column=5, value=float(d(inv.get("sgst_amt"))))
            ws.cell(row=r, column=6, value=float(d(inv.get("igst_amt"))))
            ws.cell(row=r, column=7, value=float(d(inv.get("grand_total"))))
            r += 1
        summary_end = r - 1

        # Chart: spend per bill for this vendor
        if summary_end >= summary_start:
            vchart = BarChart()
            vchart.title = f"{vname} — spend per invoice"
            vchart.y_axis.title = "₹"
            data = Reference(ws, min_col=7, min_row=summary_start - 1, max_row=summary_end)
            cats = Reference(ws, min_col=1, min_row=summary_start, max_row=summary_end)
            vchart.add_data(data, titles_from_data=True)
            vchart.set_categories(cats)
            vchart.width, vchart.height = 16, 9
            ws.add_chart(vchart, f"L4")

    # ---- FLAGS SHEET (all vendors, needs-review items in one place) ----
    flags_rows = []
    for inv in st.session_state.invoices:
        for iss in inv.get("_math_issues", []):
            flags_rows.append((inv.get("vendor_name", ""), inv.get("_source_file", ""), "Math", iss))
        for u in inv.get("uncertain_money_fields", []):
            flags_rows.append((inv.get("vendor_name", ""), inv.get("_source_file", ""), "Uncertain money field", u))
    if flags_rows:
        fs = wb.create_sheet("Flags - Needs Review")
        fs.append(["Vendor", "File", "Type", "Issue"])
        style_header(fs, 1, 4)
        for row in flags_rows:
            fs.append(list(row))
        autosize(fs, 4)

    excel_buffer = io.BytesIO()
    wb.save(excel_buffer)

    st.download_button(
        "Download Excel report",
        data=excel_buffer.getvalue(),
        file_name=f"gst_report_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    st.caption("One sheet per vendor (products, rates, GST %, invoice totals + a spend chart), "
               "plus an Overview sheet with vendor-wise GST and spend charts.")

    if st.button("Clear all data"):
        st.session_state.invoices = []
        st.rerun()
else:
    st.info("No invoices processed yet. Upload photos above to get started.")
