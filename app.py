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
import io

st.set_page_config(page_title="GST Invoice Extractor", page_icon="🧾", layout="wide")

# ---------- CONFIG ----------
API_KEY = st.secrets.get("GEMINI_API_KEY", "")
if not API_KEY:
    st.error("No GEMINI_API_KEY found in secrets. Add it in .streamlit/secrets.toml (local) "
              "or your Streamlit Cloud app's Secrets settings.")
    st.stop()

genai.configure(api_key=API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")

EXTRACTION_PROMPT = """You are reading a handwritten or printed Indian GST tax invoice photo.
Extract every field you can see into STRICT JSON only, no markdown fences, no commentary.

Schema:
{
  "vendor_name": "", "vendor_gstin": "", "bill_no": "", "date": "",
  "customer_name": "", "customer_gstin": "",
  "line_items": [
    {"particulars": "", "hsn_code": "", "qty": 0, "rate": 0, "amount": 0}
  ],
  "subtotal": 0,
  "cgst_pct": 0, "cgst_amt": 0,
  "sgst_pct": 0, "sgst_amt": 0,
  "igst_pct": 0, "igst_amt": 0,
  "grand_total": 0,
  "math_check_note": "state briefly if line item math (qty*rate=amount) or totals don't reconcile, else 'OK'",
  "extraction_confidence": "high, medium, or low based on handwriting legibility"
}

Rules:
- If a field is not visible/present, use "" for strings or 0 for numbers.
- Numbers must be plain numbers, no currency symbols or commas.
- Do your best on cursive/messy handwriting; note uncertainty in math_check_note rather than guessing wildly.
"""

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
        rows.append({
            "File": inv.get("_source_file", ""),
            "Customer tag": inv.get("_customer_tag", ""),
            "Vendor": inv.get("vendor_name", ""),
            "Bill No.": inv.get("bill_no", ""),
            "Date": inv.get("date", ""),
            "Subtotal": inv.get("subtotal", 0),
            "CGST": inv.get("cgst_amt", 0),
            "SGST": inv.get("sgst_amt", 0),
            "IGST": inv.get("igst_amt", 0),
            "Grand Total": inv.get("grand_total", 0),
            "Confidence": inv.get("extraction_confidence", ""),
            "Math check": inv.get("math_check_note", ""),
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True)

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
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total invoices", len(df))
    col2.metric("Total spend (₹)", f"{df['Grand Total'].sum():,.2f}")
    col3.metric("Total CGST paid (₹)", f"{df['CGST'].sum():,.2f}")
    col4.metric("Total SGST paid (₹)", f"{df['SGST'].sum():,.2f}")

    vendor_summary = df.groupby("Vendor")["Grand Total"].sum().sort_values(ascending=False)
    st.bar_chart(vendor_summary)

    low_conf = df[df["Confidence"] == "low"]
    if not low_conf.empty:
        st.warning(f"{len(low_conf)} invoice(s) had low extraction confidence — worth a manual double-check.")

    flagged = df[df["Math check"].str.lower() != "ok"]
    if not flagged.empty:
        st.warning(f"{len(flagged)} invoice(s) have a math discrepancy flagged — see table above.")

    # ---------- EXPORT ----------
    st.subheader("Export")
    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Summary", index=False)
        all_items = []
        for inv in st.session_state.invoices:
            for item in inv.get("line_items", []):
                item_row = {"Vendor": inv.get("vendor_name", ""), "Bill No.": inv.get("bill_no", ""), **item}
                all_items.append(item_row)
        if all_items:
            pd.DataFrame(all_items).to_excel(writer, sheet_name="Line Items", index=False)
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
