import streamlit as st

_CSS = """
<style>
:root {
  --bg: #0e1621;
  --surface: #152233;
  --surface-2: #1b2b40;
  --border: #263a52;
  --text: #d5e0ee;
  --muted: #8fa3b8;
  --accent: #4fd1c5;
  --accent-2: #7aa2f7;
  --danger: #f07178;
  --ok: #7fd6a5;
}

html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stHeader"] {
  background: var(--bg) !important;
  color: var(--text) !important;
}
[data-testid="stSidebar"], [data-testid="stSidebar"] > div {
  background: var(--surface) !important;
  border-right: 1px solid var(--border) !important;
}
h1, h2, h3, h4, h5, h6, p, span, label, li, div, small {
  color: var(--text);
}
[data-testid="stCaptionContainer"], .stCaption, small { color: var(--muted) !important; }

/* glass panels / cards from the old design */
[class*="glass"], [class*="panel"], [class*="card"] {
  background: rgba(21, 34, 51, 0.72) !important;
  border: 1px solid var(--border) !important;
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.25) !important;
  backdrop-filter: blur(10px);
  color: var(--text) !important;
}

/* animated gradient title -> calm teal to blue */
[class*="title"], [class*="hero"] h1 {
  background: linear-gradient(90deg, #4fd1c5, #7aa2f7) !important;
  -webkit-background-clip: text !important;
  background-clip: text !important;
  -webkit-text-fill-color: transparent !important;
}
[class*="eyebrow"] { color: var(--accent) !important; }

/* inputs */
input, textarea, [data-baseweb="input"], [data-baseweb="textarea"], [data-baseweb="select"] > div {
  background: var(--surface-2) !important;
  color: var(--text) !important;
  border-color: var(--border) !important;
}
input:focus, textarea:focus { border-color: var(--accent) !important; box-shadow: 0 0 0 1px var(--accent) !important; }
[data-testid="stFileUploaderDropzone"] {
  background: var(--surface) !important;
  border: 1px dashed var(--border) !important;
}

/* buttons */
.stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] > button {
  background: var(--surface-2) !important;
  color: var(--text) !important;
  border: 1px solid var(--border) !important;
  border-radius: 10px !important;
  transition: all .2s ease;
}
.stButton > button:hover, .stDownloadButton > button:hover {
  border-color: var(--accent) !important;
  color: var(--accent) !important;
  box-shadow: 0 0 0 1px var(--accent) inset !important;
}
.stButton > button[kind="primary"], [data-testid="stFormSubmitButton"] > button[kind="primary"] {
  background: var(--accent) !important;
  color: #0b1a1f !important;
  border: none !important;
  font-weight: 600 !important;
}

/* tabs, expanders, metrics */
[data-baseweb="tab"] { color: var(--muted) !important; }
[aria-selected="true"][data-baseweb="tab"] { color: var(--accent) !important; }
[data-baseweb="tab-highlight"] { background: var(--accent) !important; }
[data-testid="stExpander"] {
  background: var(--surface) !important;
  border: 1px solid var(--border) !important;
  border-radius: 12px !important;
}
[data-testid="stMetric"] {
  background: var(--surface) !important;
  border: 1px solid var(--border) !important;
  border-radius: 12px !important;
  padding: 14px 16px !important;
}
[data-testid="stMetricValue"] { color: var(--accent) !important; }
[data-testid="stMetricLabel"] { color: var(--muted) !important; }

/* tables / editors */
[data-testid="stDataFrame"], [data-testid="stDataEditor"] {
  border: 1px solid var(--border) !important;
  border-radius: 10px !important;
}

/* alerts */
[data-testid="stAlert"] {
  background: var(--surface-2) !important;
  border: 1px solid var(--border) !important;
  color: var(--text) !important;
}

hr { border-color: var(--border) !important; }
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 8px; }
::-webkit-scrollbar-track { background: var(--bg); }
</style>
"""


def apply_theme():
    st.markdown(_CSS, unsafe_allow_html=True)
