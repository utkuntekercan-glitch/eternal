import hashlib
import io
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

try:
    import psycopg2
except Exception:
    psycopg2 = None

st.set_page_config(page_title="Eternal Fire", layout="wide")

DB_PATH = Path("sales_reports.db")
DATABASE_URL = str(st.secrets.get("DATABASE_URL", os.getenv("DATABASE_URL", ""))).strip()
USE_POSTGRES = bool(DATABASE_URL)
APP_LOGO_URL = str(st.secrets.get("APP_LOGO_URL", os.getenv("APP_LOGO_URL", ""))).strip()


def inject_styles():
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700;800&display=swap');

        html, body, [class*="css"]  {
            font-family: 'Manrope', sans-serif;
        }
        .stApp {
            background:
                radial-gradient(1200px 420px at 8% -8%, rgba(212,175,55,0.20), transparent 55%),
                radial-gradient(950px 380px at 100% 0%, rgba(191,34,40,0.18), transparent 45%),
                #0b0d11;
            color: #e8eaee;
        }
        .block-container {
            padding-top: 1.2rem;
            max-width: 1280px;
        }
        .ef-hero {
            border: 1px solid rgba(255,255,255,0.16);
            border-radius: 14px;
            background: linear-gradient(160deg, rgba(191,34,40,0.28), rgba(10,12,16,0.88));
            padding: 14px 16px;
            margin-bottom: 10px;
            box-shadow: 0 12px 30px rgba(0,0,0,0.35);
        }
        .ef-hero-row {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 12px;
        }
        .ef-hero-logo {
            width: 48px;
            height: 48px;
            object-fit: contain;
            border-radius: 10px;
            background: rgba(255,255,255,0.08);
            padding: 4px;
        }
        .ef-title {
            text-align: center;
            font-size: 2.1rem;
            font-weight: 800;
            letter-spacing: 0.6px;
            margin: 0.2rem 0 0.1rem 0;
            color: #f3f5f7;
        }
        .ef-subtitle {
            text-align: center;
            font-size: 0.95rem;
            color: #b7bec9;
            margin-bottom: 0;
        }
        .ef-statbar {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 10px;
            margin: 0.2rem 0 1rem 0;
        }
        .ef-chip {
            background: linear-gradient(160deg, rgba(255,255,255,0.06), rgba(255,255,255,0.02));
            border: 1px solid rgba(255,255,255,0.14);
            border-radius: 12px;
            padding: 10px 12px;
        }
        .ef-chip-k {
            font-size: 0.75rem;
            color: #b7bec9;
        }
        .ef-chip-v {
            font-size: 1.05rem;
            font-weight: 800;
            color: #f8d26a;
        }
        div[role="radiogroup"] {
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.10);
            border-radius: 12px;
            padding: 6px 8px;
            margin-bottom: 8px;
        }
        div[role="radiogroup"] label {
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.10);
            border-radius: 10px;
            padding: 6px 10px;
            margin-right: 6px;
        }
        div[role="radiogroup"] label:has(input:checked) {
            border: 1px solid rgba(248,210,106,0.55);
            background: linear-gradient(180deg, rgba(248,210,106,0.20), rgba(248,210,106,0.10));
        }
        .stMetric {
            background: linear-gradient(180deg, rgba(255,255,255,0.06), rgba(255,255,255,0.03));
            border: 1px solid rgba(255,255,255,0.12);
            border-radius: 12px;
            padding: 8px 10px;
        }
        div.stDataFrame, div[data-testid="stDataEditor"] {
            border: 1px solid rgba(255,255,255,0.14);
            border-radius: 12px;
            overflow: hidden;
        }
        .stButton > button {
            border-radius: 10px;
            border: 1px solid rgba(248,210,106,0.45);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


class DBConn:
    def __init__(self, driver: str, raw_conn):
        self.driver = driver
        self._conn = raw_conn

    def _sql(self, q: str) -> str:
        if self.driver == "postgres":
            return q.replace("?", "%s")
        return q

    def execute(self, q: str, params=()):
        cur = self._conn.cursor()
        cur.execute(self._sql(q), tuple(params))
        return cur

    def executemany(self, q: str, seq):
        cur = self._conn.cursor()
        cur.executemany(self._sql(q), [tuple(x) for x in seq])
        return cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def get_conn() -> DBConn:
    if USE_POSTGRES:
        if psycopg2 is None:
            raise RuntimeError("Postgres icin psycopg2-binary gerekli.")
        if "sslmode=" in DATABASE_URL:
            raw = psycopg2.connect(DATABASE_URL, connect_timeout=8, application_name="eternal-streamlit")
        else:
            raw = psycopg2.connect(
                DATABASE_URL,
                sslmode="require",
                connect_timeout=8,
                application_name="eternal-streamlit",
            )
        raw.autocommit = False
        return DBConn("postgres", raw)

    raw = sqlite3.connect(DB_PATH, check_same_thread=False)
    raw.execute("PRAGMA foreign_keys = ON;")
    return DBConn("sqlite", raw)


def init_db(conn: DBConn):
    id_col = "BIGSERIAL PRIMARY KEY" if conn.driver == "postgres" else "INTEGER PRIMARY KEY AUTOINCREMENT"
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS sales (
            id {id_col},
            week_label TEXT NOT NULL,
            order_date TEXT,
            customer_email TEXT,
            is_free_exit INTEGER NOT NULL DEFAULT 0,
            sku TEXT NOT NULL,
            product_name TEXT NOT NULL,
            qty REAL NOT NULL,
            unit_price REAL NOT NULL,
            revenue REAL NOT NULL,
            source_file TEXT NOT NULL,
            source_hash TEXT NOT NULL
        )
        """
    )
    # Backward-compatible column migration for existing databases.
    try:
        conn.execute("ALTER TABLE sales ADD COLUMN customer_email TEXT")
    except Exception:
        conn.rollback()
    try:
        conn.execute("ALTER TABLE sales ADD COLUMN is_free_exit INTEGER NOT NULL DEFAULT 0")
    except Exception:
        conn.rollback()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS costs (
            sku TEXT PRIMARY KEY,
            product_name TEXT,
            unit_cost REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
            sku TEXT PRIMARY KEY,
            product_name TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'Genel',
            unit_cost REAL NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sales_monthly_sku (
            ym TEXT NOT NULL,
            sku TEXT NOT NULL,
            product_name TEXT NOT NULL,
            qty REAL NOT NULL,
            revenue REAL NOT NULL,
            PRIMARY KEY (ym, sku)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sales_order_date ON sales(order_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sales_sku ON sales(sku)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sales_week ON sales(week_label)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sales_monthly_ym ON sales_monthly_sku(ym)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_products_active ON products(active)")
    conn.commit()


def df_query(conn: DBConn, q: str, params=()):
    cur = conn.execute(q, params)
    rows = cur.fetchall()
    cols = [c[0] for c in cur.description] if cur.description else []
    return pd.DataFrame(rows, columns=cols)


def refresh_monthly_summary(conn: DBConn):
    conn.execute("DELETE FROM sales_monthly_sku")
    conn.execute(
        """
        INSERT INTO sales_monthly_sku(ym, sku, product_name, qty, revenue)
        SELECT
            SUBSTR(order_date, 1, 7) AS ym,
            sku,
            MAX(product_name) AS product_name,
            SUM(qty) AS qty,
            SUM(revenue) AS revenue
        FROM sales
        WHERE order_date IS NOT NULL
          AND COALESCE(is_free_exit, 0) = 0
        GROUP BY SUBSTR(order_date, 1, 7), sku
        """
    )
    conn.commit()


@st.cache_data(ttl=60, show_spinner=False)
def get_uploads_cached(_conn: DBConn) -> pd.DataFrame:
    return df_query(
        _conn,
        """
        SELECT week_label, source_file, COUNT(*) AS row_count, MAX(id) AS max_id
        FROM sales
        GROUP BY week_label, source_file
        ORDER BY max_id DESC
        """,
    )


@st.cache_data(ttl=60, show_spinner=False)
def get_products_cached(_conn: DBConn) -> pd.DataFrame:
    return df_query(
        _conn,
        """
        SELECT sku, product_name, category, unit_cost, active
        FROM products
        ORDER BY product_name
        """,
    )


def tr_money(x: float) -> str:
    s = format(float(x), ",.0f").replace(",", ".")
    return f"₺{s}"


def normalize_num(x) -> float:
    if pd.isna(x):
        return 0.0
    # If Excel already parsed as numeric, keep it as-is.
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace("TL", "").replace("tl", "")
    # Handle both TR and EN formatted strings safely.
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        parts = s.split(".")
        if len(parts) > 1 and all(len(p) == 3 for p in parts[1:]):
            s = "".join(parts)
    try:
        return float(s)
    except Exception:
        return 0.0


def parse_uploaded_excel(file_bytes: bytes, week_label: str) -> pd.DataFrame:
    from openpyxl import load_workbook

    # Read with fixed Excel letters to avoid index-shift issues across exports.
    wb = load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
    ws = wb.active

    rows = []
    for r in ws.iter_rows(min_row=2):
        customer_email = "" if r[4].value is None else str(r[4].value).strip().lower()  # E
        is_free_exit = 1 if customer_email == "hakanerdgnn@gmail.com" else 0
        qty = normalize_num(r[17].value)   # R
        name = "" if r[18].value is None else str(r[18].value).strip()  # S
        price = normalize_num(r[20].value)  # U
        sku = "" if r[24].value is None else str(r[24].value).strip()   # Y
        rows.append(
            {
                "customer_email": customer_email,
                "is_free_exit": is_free_exit,
                "product_name": name,
                "sku": sku,
                "qty": qty,
                "unit_price": price,
            }
        )
    out = pd.DataFrame(rows)
    out["week_label"] = week_label
    out["order_date"] = None
    out["revenue"] = out["qty"] * out["unit_price"]
    out = out[(out["sku"] != "") & (out["product_name"] != "")]
    out = out[out["qty"] > 0]
    return out


def sync_products_from_sales(conn: DBConn):
    now = datetime.now().isoformat(timespec="seconds")
    rows = df_query(
        conn,
        """
        SELECT sku, MAX(product_name) AS product_name
        FROM sales
        GROUP BY sku
        """,
    )
    if rows.empty:
        return
    payload = [(r["sku"], r["product_name"], now) for _, r in rows.iterrows()]
    conn.executemany(
        """
        INSERT INTO products(sku, product_name, updated_at)
        VALUES(?,?,?)
        ON CONFLICT(sku) DO UPDATE SET
            product_name=excluded.product_name,
            updated_at=excluded.updated_at
        """,
        payload,
    )
    conn.commit()


def upsert_product_master(conn: DBConn, df: pd.DataFrame):
    now = datetime.now().isoformat(timespec="seconds")
    rows = []
    for _, r in df.iterrows():
        rows.append(
            (
                r["sku"],
                r["product_name"],
                str(r.get("category", "Genel") or "Genel"),
                float(r["unit_cost"]),
                1 if bool(r.get("active", True)) else 0,
                now,
            )
        )
    conn.executemany(
        """
        INSERT INTO products(sku, product_name, category, unit_cost, active, updated_at)
        VALUES(?,?,?,?,?,?)
        ON CONFLICT(sku) DO UPDATE SET
            product_name=excluded.product_name,
            category=excluded.category,
            unit_cost=excluded.unit_cost,
            active=excluded.active,
            updated_at=excluded.updated_at
        """,
        rows,
    )
    # Keep legacy costs table aligned.
    conn.executemany(
        """
        INSERT INTO costs(sku, product_name, unit_cost, updated_at)
        VALUES(?,?,?,?)
        ON CONFLICT(sku) DO UPDATE SET
            product_name=excluded.product_name,
            unit_cost=excluded.unit_cost,
            updated_at=excluded.updated_at
        """,
        [(r[0], r[1], r[3], r[5]) for r in rows],
    )
    conn.commit()


@st.cache_data(ttl=60, show_spinner=False)
def load_month_data(_conn: DBConn, ym: str) -> pd.DataFrame:
    start = f"{ym}-01"
    end_y, end_m = map(int, ym.split("-"))
    if end_m == 12:
        end = f"{end_y + 1:04d}-01-01"
    else:
        end = f"{end_y:04d}-{end_m + 1:02d}-01"

    sales = df_query(
        _conn,
        """
        SELECT week_label, order_date, customer_email, is_free_exit, sku, product_name, qty, unit_price, revenue
        FROM sales
        WHERE order_date >= ? AND order_date < ?
        """,
        (start, end),
    )
    if sales.empty:
        return sales

    # Keep free-exit rows for separate listing, but exclude from financial calculations.
    sales_calc = sales[sales["is_free_exit"].fillna(0).astype(int) == 0].copy()
    products = df_query(_conn, "SELECT sku, category, unit_cost, active FROM products")
    merged = sales_calc.merge(products, on="sku", how="left")
    merged["category"] = merged["category"].fillna("Genel")
    merged["active"] = merged["active"].fillna(1)
    merged["unit_cost"] = merged["unit_cost"].fillna(0.0)
    merged["cost_total"] = merged["qty"] * merged["unit_cost"]
    merged["profit"] = merged["revenue"] - merged["cost_total"]
    merged["margin_pct"] = merged["profit"] / merged["revenue"].replace(0, pd.NA) * 100
    merged["margin_pct"] = merged["margin_pct"].fillna(0.0)
    return merged


@st.cache_data(ttl=60, show_spinner=False)
def load_month_summary(_conn: DBConn, ym: str) -> pd.DataFrame:
    base = df_query(
        _conn,
        """
        SELECT ym, sku, product_name, qty, revenue
        FROM sales_monthly_sku
        WHERE ym = ?
        """,
        (ym,),
    )
    if base.empty:
        return base
    products = df_query(_conn, "SELECT sku, category, unit_cost, active FROM products")
    merged = base.merge(products, on="sku", how="left")
    merged["category"] = merged["category"].fillna("Genel")
    merged["active"] = merged["active"].fillna(1)
    merged["unit_cost"] = merged["unit_cost"].fillna(0.0)
    merged["cost_total"] = merged["qty"] * merged["unit_cost"]
    merged["profit"] = merged["revenue"] - merged["cost_total"]
    merged["margin_pct"] = merged["profit"] / merged["revenue"].replace(0, pd.NA) * 100
    merged["margin_pct"] = merged["margin_pct"].fillna(0.0)
    return merged


@st.cache_data(ttl=60, show_spinner=False)
def load_free_exit_rows(_conn: DBConn, ym: str) -> pd.DataFrame:
    start = f"{ym}-01"
    y, m = map(int, ym.split("-"))
    end = f"{y + 1:04d}-01-01" if m == 12 else f"{y:04d}-{m + 1:02d}-01"
    return df_query(
        _conn,
        """
        SELECT week_label, order_date, customer_email, sku, product_name, qty, unit_price, revenue
        FROM sales
        WHERE order_date >= ? AND order_date < ?
          AND COALESCE(is_free_exit, 0) = 1
        ORDER BY order_date, week_label
        """,
        (start, end),
    )


def render_brand_header():
    left, center, right = st.columns([1, 4, 1])
    with center:
        # Remote image URLs can block first paint on slow networks.
        if APP_LOGO_URL and not APP_LOGO_URL.lower().startswith(("http://", "https://")):
            st.image(APP_LOGO_URL, width=72)
        st.title("Eternal Fire")
        st.caption("Satis Operasyon ve Karlilik Kontrol Paneli")


inject_styles()
render_brand_header()

if "_sales_conn" not in st.session_state:
    st.session_state["_sales_conn"] = get_conn()
conn = st.session_state["_sales_conn"]
# Ensure schema once per session to avoid repeating init work on every rerun.
schema_key = f"{conn.driver}:v3"
if st.session_state.get("_sales_schema_ready") != schema_key:
    init_db(conn)
    st.session_state["_sales_schema_ready"] = schema_key
if "_sales_bootstrap" not in st.session_state:
    pcount_df = df_query(conn, "SELECT COUNT(*) AS n FROM products")
    pcount = int(pcount_df.iloc[0]["n"]) if not pcount_df.empty else 0
    if pcount == 0:
        sync_products_from_sales(conn)
    summary_count_df = df_query(conn, "SELECT COUNT(*) AS n FROM sales_monthly_sku")
    summary_count = int(summary_count_df.iloc[0]["n"]) if not summary_count_df.empty else 0
    if summary_count == 0:
        refresh_monthly_summary(conn)
        st.cache_data.clear()
    st.session_state["_sales_bootstrap"] = True

sections = ["Genel Dashboard", "Excel Yukle", "Aylik Rapor", "Urun Master"]
section = st.radio("Bolum", sections, horizontal=True, label_visibility="collapsed")

if section == "Genel Dashboard":
    st.subheader("Genel Satis Ozeti")
    dash_ym = st.text_input("Dashboard Ay (YYYY-MM)", value=datetime.today().strftime("%Y-%m"), key="dash_ym")
    if "_dash_loaded" not in st.session_state:
        st.session_state["_dash_loaded"] = False
    if "_dash_last_ym" not in st.session_state:
        st.session_state["_dash_last_ym"] = ""

    if st.session_state["_dash_last_ym"] != dash_ym.strip():
        st.session_state["_dash_loaded"] = False
        st.session_state["_dash_last_ym"] = dash_ym.strip()

    if not st.session_state["_dash_loaded"]:
        if st.button("Dashboard verisini getir", type="primary"):
            st.session_state["_dash_loaded"] = True
            st.rerun()
        else:
            st.info("Hizli acilis icin dashboard verisi butonla yuklenir.")
            st.stop()

    dash = load_month_summary(conn, dash_ym.strip())
    if dash.empty:
        st.info("Bu ay icin veri yok.")
    else:
        d_qty = float(dash["qty"].sum())
        d_rev = float(dash["revenue"].sum())
        d_cost = float(dash["cost_total"].sum())
        d_profit = float(dash["profit"].sum())
        d_margin = (d_profit / d_rev * 100.0) if d_rev > 0 else 0.0

        a, b, c, d, e = st.columns(5)
        a.metric("Toplam Adet", f"{d_qty:,.0f}".replace(",", "."))
        b.metric("Toplam Ciro", tr_money(d_rev))
        c.metric("Toplam Maliyet", tr_money(d_cost))
        d.metric("Net Kar", tr_money(d_profit))
        e.metric("Kar Marji", f"%{d_margin:.1f}")

        st.markdown("#### En Cok Satan Urunler (Adet)")
        by_qty = (
            dash.groupby(["sku", "product_name"], as_index=False)["qty"]
            .sum()
            .sort_values("qty", ascending=False)
            .head(10)
        )
        by_qty.rename(columns={"sku": "Stok Kodu", "product_name": "Urun", "qty": "Adet"}, inplace=True)
        by_qty["Adet"] = by_qty["Adet"].map(lambda v: f"{float(v):,.0f}".replace(",", "."))
        st.dataframe(by_qty, use_container_width=True, hide_index=True)

        st.markdown("#### Kategori Bazli Ciro / Kar")
        by_cat = (
            dash.groupby("category", as_index=False)[["revenue", "profit"]]
            .sum()
            .sort_values("revenue", ascending=False)
        )
        by_cat.rename(columns={"category": "Kategori", "revenue": "Ciro", "profit": "Kar"}, inplace=True)
        by_cat["Ciro"] = by_cat["Ciro"].map(tr_money)
        by_cat["Kar"] = by_cat["Kar"].map(tr_money)
        st.dataframe(by_cat, use_container_width=True, hide_index=True)

elif section == "Excel Yukle":
    st.subheader("Haftalik Excel Yukleme")
    uploads = get_uploads_cached(conn)
    if not uploads.empty:
        last_week = str(uploads.iloc[0]["week_label"])
        last_file = str(uploads.iloc[0]["source_file"])
        last_rows = int(uploads.iloc[0]["row_count"])
        st.caption(f"Son yukleme: {last_week} | {last_file} | {last_rows} satir")
        if st.button("Son yuklemeyi sil", type="secondary"):
            with st.spinner("Siliniyor..."):
                conn.execute("DELETE FROM sales WHERE week_label=?", (last_week,))
                conn.commit()
                refresh_monthly_summary(conn)
                st.cache_data.clear()
            st.success(f"{last_week} haftasi verileri silindi.")
            st.rerun()
        week_opts = sorted(uploads["week_label"].astype(str).unique().tolist(), reverse=True)
        sel_week = st.selectbox("Silmek icin hafta sec", week_opts, key="delete_week_select")
        if st.button("Secili haftayi sil"):
            with st.spinner("Siliniyor..."):
                conn.execute("DELETE FROM sales WHERE week_label=?", (sel_week,))
                conn.commit()
                refresh_monthly_summary(conn)
                st.cache_data.clear()
            st.success(f"{sel_week} haftasi verileri silindi.")
            st.rerun()

    week_label = st.text_input("Hafta etiketi (ornek: 2026-W07)")
    fallback_date = st.date_input("Bu dosya hangi aya yazilsin? (ornek: 2026-02 icin 2026-02-01 sec)", value=datetime.today())
    uploaded = st.file_uploader("Excel sec", type=["xlsx"])
    if st.button("Yukle ve Isle", type="primary", disabled=uploaded is None or week_label.strip() == ""):
        file_bytes = uploaded.getvalue()
        source_hash = hashlib.sha256(file_bytes).hexdigest()
        source_file = uploaded.name

        parsed = parse_uploaded_excel(file_bytes, week_label.strip())
        parsed["source_file"] = source_file
        parsed["source_hash"] = source_hash
        parsed["order_date"] = fallback_date.isoformat()

        rows = [
            (
                r["week_label"],
                r["order_date"],
                r["customer_email"],
                int(r["is_free_exit"]),
                r["sku"],
                r["product_name"],
                float(r["qty"]),
                float(r["unit_price"]),
                float(r["revenue"]),
                r["source_file"],
                r["source_hash"],
            )
            for _, r in parsed.iterrows()
        ]
        conn.executemany(
            """
            INSERT INTO sales(week_label, order_date, customer_email, is_free_exit, sku, product_name, qty, unit_price, revenue, source_file, source_hash)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )
        conn.commit()
        sync_products_from_sales(conn)
        refresh_monthly_summary(conn)
        st.cache_data.clear()
        free_rows = int(parsed["is_free_exit"].sum()) if "is_free_exit" in parsed.columns else 0
        st.success(f"Yukleme tamamlandi. {len(rows)} satir eklendi. Bedelsiz cikis: {free_rows}")

elif section == "Aylik Rapor":
    st.subheader("Aylik Satis / Ciro / Kar")
    ym = st.text_input("Ay (YYYY-MM)", value=datetime.today().strftime("%Y-%m"))
    report = load_month_data(conn, ym.strip())
    free_exit_rows = load_free_exit_rows(conn, ym.strip())
    if report.empty:
        st.info("Bu ay icin veri yok.")
    else:
        total_qty = float(report["qty"].sum())
        total_rev = float(report["revenue"].sum())
        total_cost = float(report["cost_total"].sum())
        total_profit = float(report["profit"].sum())

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Toplam Adet", f"{total_qty:,.0f}".replace(",", "."))
        c2.metric("Toplam Ciro", tr_money(total_rev))
        c3.metric("Toplam Maliyet", tr_money(total_cost))
        c4.metric("Gercek Kar", tr_money(total_profit))

        by_product = (
            report.groupby(["sku", "product_name"], as_index=False)[["qty", "revenue", "cost_total", "profit"]]
            .sum()
            .sort_values("revenue", ascending=False)
        )
        by_product.rename(
            columns={
                "sku": "Stok Kodu",
                "product_name": "Urun",
                "qty": "Adet",
                "revenue": "Ciro",
                "cost_total": "Maliyet",
                "profit": "Kar",
            },
            inplace=True,
        )
        by_product_display = by_product.copy()
        by_product_display["Adet"] = by_product_display["Adet"].map(lambda v: f"{float(v):,.0f}".replace(",", "."))
        by_product_display["Ciro"] = by_product_display["Ciro"].map(tr_money)
        by_product_display["Maliyet"] = by_product_display["Maliyet"].map(tr_money)
        by_product_display["Kar"] = by_product_display["Kar"].map(tr_money)
        st.dataframe(by_product_display, use_container_width=True, hide_index=True)

        st.markdown("#### SKU Detay Kontrol")
        sku_options = sorted(report["sku"].astype(str).unique().tolist())
        sku_sel = st.selectbox("Detay SKU sec", sku_options, key="sku_detail_select")
        sku_rows = report[report["sku"].astype(str) == sku_sel].copy()
        if not sku_rows.empty:
            sku_rows = sku_rows[["week_label", "order_date", "sku", "product_name", "qty", "unit_price", "revenue"]]
            sku_rows.rename(
                columns={
                    "week_label": "Hafta",
                    "order_date": "Tarih",
                    "sku": "Stok Kodu",
                    "product_name": "Urun",
                    "qty": "Adet",
                    "unit_price": "Birim Fiyat",
                    "revenue": "Ciro",
                },
                inplace=True,
            )
            sku_rows["Adet"] = sku_rows["Adet"].map(lambda v: f"{float(v):,.0f}".replace(",", "."))
            sku_rows["Birim Fiyat"] = sku_rows["Birim Fiyat"].map(tr_money)
            sku_rows["Ciro"] = sku_rows["Ciro"].map(tr_money)
            st.dataframe(sku_rows, use_container_width=True, hide_index=True)

    st.markdown("#### Bedelsiz Cikislar (hakanerdgnn@gmail.com)")
    if free_exit_rows.empty:
        st.info("Bu ay bedelsiz cikis yok.")
    else:
        free_disp = free_exit_rows.rename(
            columns={
                "week_label": "Hafta",
                "order_date": "Tarih",
                "customer_email": "E-Posta",
                "sku": "Stok Kodu",
                "product_name": "Urun",
                "qty": "Adet",
                "unit_price": "Birim Fiyat",
                "revenue": "Bedelsiz Tutar",
            }
        )
        free_disp["Adet"] = free_disp["Adet"].map(lambda v: f"{float(v):,.0f}".replace(",", "."))
        free_disp["Birim Fiyat"] = free_disp["Birim Fiyat"].map(tr_money)
        free_disp["Bedelsiz Tutar"] = free_disp["Bedelsiz Tutar"].map(tr_money)
        st.dataframe(free_disp, use_container_width=True, hide_index=True)

else:
    st.subheader("Urun Master (Kategori + Maliyet + Aktif)")
    products = get_products_cached(conn)
    if products.empty:
        st.info("Once Excel yukleyin.")
    else:
        editor_df = products.copy()
        editor_df["unit_cost"] = editor_df["unit_cost"].fillna(0.0)
        editor_df["category"] = editor_df["category"].fillna("Genel")
        editor_df["active"] = editor_df["active"].fillna(1).astype(int).map(lambda x: True if x == 1 else False)
        edited = st.data_editor(
            editor_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "sku": st.column_config.TextColumn("Stok Kodu", disabled=True),
                "product_name": st.column_config.TextColumn("Urun", disabled=True),
                "category": st.column_config.TextColumn("Kategori"),
                "unit_cost": st.column_config.NumberColumn("Birim Maliyet", min_value=0.0, step=0.01),
                "active": st.column_config.CheckboxColumn("Aktif"),
            },
            num_rows="fixed",
        )
        if st.button("Urun Master Kaydet", type="primary"):
            upsert_product_master(conn, edited[["sku", "product_name", "category", "unit_cost", "active"]])
            st.cache_data.clear()
            st.success("Urun master kaydedildi.")
