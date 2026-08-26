# GST Invoice Extractor — Zero-Cost Deploy Guide

## What this does
Upload photos of GST invoices (handwritten or printed) → Gemini reads them into
structured data → app validates the math → you get a live dashboard + downloadable
Excel report.

## Cost: ₹0/month
- Streamlit Community Cloud hosting: free
- Google Gemini 2.5 Flash API: free tier (generous daily limit, plenty for early usage)

## Deploy in 4 steps

1. **Get a free Gemini API key**
   Go to https://aistudio.google.com/app/apikey → "Create API key". Copy it.

2. **Push this folder to GitHub**
   Create a new repo (e.g. `gst-invoice-extractor`), push `app.py` and
   `requirements.txt` to it.

3. **Deploy on Streamlit Community Cloud**
   - Go to https://share.streamlit.io → "New app"
   - Point it at your GitHub repo, branch `main`, file `app.py`
   - Before/after deploying, open the app's **Settings → Secrets** and add:
     ```
     GEMINI_API_KEY = "your-key-here"
     ADMIN_PASSWORD = "choose-a-strong-password"
     ```
   - Click Deploy. You'll get a public URL like `yourapp.streamlit.app`.

4. **Share the URL with customers**
   They open it on their phone, upload the invoice photo, done. You can view
   the same URL to see all extracted data, or add a login later if you want
   per-customer separation.

## Two interfaces, one app
- **Customers** enter their business name in the sidebar and see/manage only their own invoices.
- **You (admin)** open the "🔑 Admin login" panel in the sidebar with `ADMIN_PASSWORD`, and get a
  dashboard showing every customer's data, filterable by customer, with the same analytics/export.

## ⚠️ Important limitation to know about
Data is stored in a SQLite file (`gst_invoices.db`) that lives on the app's disk. This works well
and is shared correctly across all users while the app is running — but on Streamlit Community
Cloud, a **fresh code deploy wipes this file**. It survives normal usage and the app sleeping/waking
from inactivity, just not a `git push` redeploy. For real production durability (data that survives
every update), migrate this to Supabase Postgres (still free) when you're ready to scale past testing.

## Next upgrades (once this validates)
- Move storage from SQLite to Supabase Postgres — free tier, and survives redeploys
- Add real customer accounts (passwords) instead of just a typed business name, so customer data
  can't be viewed by someone who simply guesses another customer's name
- Add per-customer login sessions so customers don't need to retype their name every visit
