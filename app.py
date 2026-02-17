import hashlib
import io
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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
FREE_EXIT_EMAIL = "hakanerdgnn@gmail.com"
APP_USER = str(st.secrets.get("APP_USER", os.getenv("APP_USER", "admin"))).strip()
APP_PASSWORD = str(st.secrets.get("APP_PASSWORD", os.getenv("APP_PASSWORD", "1234"))).strip()


def inject_styles():
    st.markdown(
        """
        <style>
        :root {
            --bg: #0f1218;
            --panel: #161b24;
            --panel-2: #1b2230;
            --text: #e8ebf2;
            --muted: #aeb8cb;
            --accent: #f0b429;
            --danger: #c7363e;
            --line: rgba(255,255,255,0.14);
        }
        .block-container {max-width: 1240px; padding-top: 0.8rem;}
        .stApp {
            background:
                radial-gradient(900px 320px at 4% -10%, rgba(240,180,41,0.18), transparent 55%),
                radial-gradient(880px 300px at 100% 0%, rgba(199,54,62,0.18), transparent 48%),
                var(--bg);
            color: var(--text);
        }
        .ef-shell {
            background: linear-gradient(165deg, #1a202c, #121722);
            border: 1px solid var(--line);
            border-radius: 14px;
            padding: 12px 14px;
            margin-bottom: 10px;
            box-shadow: 0 10px 24px rgba(0,0,0,0.25);
        }
        .ef-shell-title {
            font-size: 1.55rem;
            font-weight: 800;
            letter-spacing: 0.2px;
            color: #ffffff;
            margin: 0;
        }
        .ef-shell-sub {
            color: var(--muted);
            font-size: 0.92rem;
            margin: 2px 0 8px 0;
        }
        .ef-chip-row {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }
        .ef-chip {
            border: 1px solid var(--line);
            border-radius: 999px;
            padding: 4px 10px;
            font-size: 0.78rem;
            color: #f7f8fb;
            background: rgba(255,255,255,0.05);
        }
        .stMetric {
            border: 1px solid var(--line);
            border-radius: 12px;
            padding: 8px;
            background: linear-gradient(180deg, rgba(255,255,255,0.04), rgba(255,255,255,0.02));
        }
        [data-testid="stMetricLabel"] {color: #cfd6e6 !important; font-weight: 700;}
        [data-testid="stMetricValue"] {color: #ffffff !important; font-weight: 800;}
        [data-testid="stMetricDelta"] {color: #8fd19e !important;}
        div[data-testid="stDataFrame"] {
            border: 1px solid var(--line);
            border-radius: 10px;
            background: var(--panel);
        }
        .stButton > button {
            border-radius: 10px;
            font-weight: 700;
            border: 1px solid rgba(240,180,41,0.45);
        }
        div[role="radiogroup"] {
            border: 1px solid var(--line);
            border-radius: 12px;
            padding: 6px;
            background: rgba(255,255,255,0.03);
        }
        div[role="radiogroup"] label {
            border: 1px solid rgba(255,255,255,0.16);
            border-radius: 9px;
            padding: 4px 10px;
            margin-right: 6px;
            background: rgba(255,255,255,0.03);
        }
        .stTextInput > div > div, .stSelectbox > div > div, .stDateInput > div > div {
            background: var(--panel-2);
            border-radius: 10px;
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
        return q.replace("?", "%s") if self.driver == "postgres" else q

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


def get_conn() -> DBConn:
    if USE_POSTGRES:
        if psycopg2 is None:
            raise RuntimeError("Postgres icin psycopg2-binary gerekli.")
        kwargs = {
            "connect_timeout": 8,
            "application_name": "eternal-streamlit",
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 3,
        }
        if "sslmode=" in DATABASE_URL:
            raw = psycopg2.connect(DATABASE_URL, **kwargs)
        else:
            raw = psycopg2.connect(DATABASE_URL, sslmode="require", **kwargs)
        raw.autocommit = False
        cur = raw.cursor()
        cur.execute("SET statement_timeout TO 12000")
        cur.close()
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
            order_date TEXT NOT NULL,
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
        CREATE TABLE IF NOT EXISTS product_costs (
            sku TEXT PRIMARY KEY,
            unit_cost REAL NOT NULL DEFAULT 0,
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sales_week ON sales(week_label)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sales_sku ON sales(sku)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sales_free ON sales(is_free_exit)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sales_order_free ON sales(order_date, is_free_exit)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sales_monthly_ym ON sales_monthly_sku(ym)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_product_costs_sku ON product_costs(sku)")
    conn.commit()


def get_ready_conn() -> DBConn:
    if "_sales_conn" not in st.session_state:
        st.session_state["_sales_conn"] = get_conn()
    conn = st.session_state["_sales_conn"]
    schema_key = f"{conn.driver}:clean-v1"
    if st.session_state.get("_sales_schema_ready") != schema_key:
        init_db(conn)
        st.session_state["_sales_schema_ready"] = schema_key
    return conn


def df_query(conn: DBConn, q: str, params=()):
    cur = conn.execute(q, params)
    rows = cur.fetchall()
    cols = [c[0] for c in cur.description] if cur.description else []
    return pd.DataFrame(rows, columns=cols)


def tr_money(x: float) -> str:
    return f"₺{format(float(x), ',.0f').replace(',', '.')}"


def normalize_num(x) -> float:
    if pd.isna(x):
        return 0.0
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace("TL", "").replace("tl", "")
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


def is_valid_ym(ym: str) -> bool:
    return bool(re.fullmatch(r"\d{4}-\d{2}", ym.strip()))


def month_bounds(ym: str):
    start = f"{ym}-01"
    y, m = map(int, ym.split("-"))
    end = f"{y + 1:04d}-01-01" if m == 12 else f"{y:04d}-{m + 1:02d}-01"
    return start, end


def normalize_excel_date(value, fallback_iso: str) -> str:
    if value is None:
        return fallback_iso
    try:
        # openpyxl may return datetime/date objects directly.
        if hasattr(value, "strftime"):
            return value.strftime("%Y-%m-%d")
        dt = pd.to_datetime(str(value), errors="coerce", dayfirst=True)
        if pd.isna(dt):
            return fallback_iso
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return fallback_iso


def parse_uploaded_excel(file_bytes: bytes, week_label: str, order_date_iso: str) -> pd.DataFrame:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
    ws = wb.active
    rows = []
    for r in ws.iter_rows(min_row=2):
        customer_email = "" if r[4].value is None else str(r[4].value).strip().lower()  # E
        order_date = normalize_excel_date(r[7].value, order_date_iso)  # H
        is_free_exit = 1 if customer_email == FREE_EXIT_EMAIL else 0
        qty = normalize_num(r[17].value)  # R
        product_name = "" if r[18].value is None else str(r[18].value).strip()  # S
        unit_price = normalize_num(r[20].value)  # U
        sku = "" if r[24].value is None else str(r[24].value).strip()  # Y
        if sku and product_name and qty > 0:
            rows.append(
                {
                    "week_label": week_label,
                    "order_date": order_date,
                    "customer_email": customer_email,
                    "is_free_exit": is_free_exit,
                    "sku": sku,
                    "product_name": product_name,
                    "qty": float(qty),
                    "unit_price": float(unit_price),
                    "revenue": float(qty) * float(unit_price),
                }
            )
    return pd.DataFrame(rows)


def upsert_products_from_rows(conn: DBConn, rows_df: pd.DataFrame):
    if rows_df.empty:
        return
    uniq = rows_df[["sku", "product_name"]].dropna().drop_duplicates()
    now = datetime.now().isoformat(timespec="seconds")
    payload = [(str(r["sku"]), str(r["product_name"]), now) for _, r in uniq.iterrows()]
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


def upsert_product_costs(conn: DBConn, rows):
    now = datetime.now().isoformat(timespec="seconds")
    payload = [(r[0], float(r[1]), now) for r in rows]
    conn.executemany(
        """
        INSERT INTO product_costs(sku, unit_cost, updated_at)
        VALUES(?,?,?)
        ON CONFLICT(sku) DO UPDATE SET
            unit_cost=excluded.unit_cost,
            updated_at=excluded.updated_at
        """,
        payload,
    )
    conn.commit()


def refresh_monthly_summary_for_month(conn: DBConn, ym: str):
    start, end = month_bounds(ym)
    conn.execute("DELETE FROM sales_monthly_sku WHERE ym=?", (ym,))
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
        WHERE order_date >= ? AND order_date < ?
          AND COALESCE(is_free_exit, 0) = 0
        GROUP BY SUBSTR(order_date, 1, 7), sku
        """,
        (start, end),
    )
    conn.commit()


def refresh_monthly_summary_all(conn: DBConn):
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
        WHERE COALESCE(is_free_exit, 0) = 0
        GROUP BY SUBSTR(order_date, 1, 7), sku
        """
    )
    conn.commit()


@st.cache_data(ttl=300, show_spinner=False)
def get_uploads(_conn: DBConn) -> pd.DataFrame:
    return df_query(
        _conn,
        """
        SELECT week_label, source_file, COUNT(*) AS row_count, MAX(id) AS max_id
        FROM sales
        GROUP BY week_label, source_file
        ORDER BY max_id DESC
        """,
    )


@st.cache_data(ttl=300, show_spinner=False)
def get_recent_sales(_conn: DBConn, limit_n: int = 300) -> pd.DataFrame:
    return df_query(
        _conn,
        """
        SELECT id, order_date, week_label, sku, product_name, qty, revenue
        FROM sales
        ORDER BY id DESC
        LIMIT ?
        """,
        (int(limit_n),),
    )


@st.cache_data(ttl=300, show_spinner=False)
def get_dashboard_metrics(_conn: DBConn, ym: str) -> pd.DataFrame:
    return df_query(
        _conn,
        """
        SELECT
            COALESCE(SUM(m.qty), 0) AS total_qty,
            COALESCE(SUM(m.revenue), 0) AS total_revenue,
            COALESCE(SUM(m.qty * COALESCE(c.unit_cost, p.unit_cost, 0)), 0) AS total_cost,
            COALESCE(SUM(m.revenue - (m.qty * COALESCE(c.unit_cost, p.unit_cost, 0))), 0) AS total_profit
        FROM sales_monthly_sku m
        LEFT JOIN products p ON p.sku = m.sku
        LEFT JOIN product_costs c ON c.sku = m.sku
        WHERE m.ym = ?
        """,
        (ym,),
    )


@st.cache_data(ttl=300, show_spinner=False)
def get_dashboard_top_products(_conn: DBConn, ym: str) -> pd.DataFrame:
    return df_query(
        _conn,
        """
        SELECT sku AS "Stok Kodu", product_name AS "Urun", qty AS "Adet"
        FROM sales_monthly_sku
        WHERE ym = ?
        ORDER BY qty DESC
        LIMIT 10
        """,
        (ym,),
    )


@st.cache_data(ttl=300, show_spinner=False)
def get_dashboard_categories(_conn: DBConn, ym: str) -> pd.DataFrame:
    return df_query(
        _conn,
        """
        SELECT
            COALESCE(p.category, 'Genel') AS "Kategori",
            COALESCE(SUM(m.revenue), 0) AS "Ciro",
            COALESCE(SUM(m.revenue - (m.qty * COALESCE(c.unit_cost, p.unit_cost, 0))), 0) AS "Kar"
        FROM sales_monthly_sku m
        LEFT JOIN products p ON p.sku = m.sku
        LEFT JOIN product_costs c ON c.sku = m.sku
        WHERE m.ym = ?
        GROUP BY COALESCE(p.category, 'Genel')
        ORDER BY "Ciro" DESC
        """,
        (ym,),
    )


@st.cache_data(ttl=300, show_spinner=False)
def get_month_products(_conn: DBConn, ym: str) -> pd.DataFrame:
    start, end = month_bounds(ym)
    return df_query(
        _conn,
        """
        SELECT
            m.sku AS "Stok Kodu",
            MAX(m.product_name) AS "Urun",
            COALESCE(SUM(m.qty), 0) AS "Adet",
            COALESCE(SUM(m.revenue), 0) AS "Ciro",
            COALESCE(SUM(m.qty * COALESCE(c.unit_cost, p.unit_cost, 0)), 0) AS "Maliyet",
            COALESCE(SUM(m.revenue - (m.qty * COALESCE(c.unit_cost, p.unit_cost, 0))), 0) AS "Kar",
            d.last_order_date AS "Son Satis Tarihi"
        FROM sales_monthly_sku m
        LEFT JOIN products p ON p.sku = m.sku
        LEFT JOIN product_costs c ON c.sku = m.sku
        LEFT JOIN (
            SELECT sku, MAX(order_date) AS last_order_date
            FROM sales
            WHERE order_date >= ? AND order_date < ?
              AND COALESCE(is_free_exit, 0) = 0
            GROUP BY sku
        ) d ON d.sku = m.sku
        WHERE m.ym = ?
        GROUP BY m.sku, d.last_order_date
        ORDER BY "Ciro" DESC
        """,
        (start, end, ym,),
    )


@st.cache_data(ttl=300, show_spinner=False)
def get_month_totals(_conn: DBConn, ym: str) -> pd.DataFrame:
    return df_query(
        _conn,
        """
        SELECT
            COALESCE(SUM("Adet"), 0) AS total_qty,
            COALESCE(SUM("Ciro"), 0) AS total_revenue,
            COALESCE(SUM("Maliyet"), 0) AS total_cost,
            COALESCE(SUM("Kar"), 0) AS total_profit
        FROM (
            SELECT
                COALESCE(SUM(m.qty), 0) AS "Adet",
                COALESCE(SUM(m.revenue), 0) AS "Ciro",
                COALESCE(SUM(m.qty * COALESCE(c.unit_cost, p.unit_cost, 0)), 0) AS "Maliyet",
                COALESCE(SUM(m.revenue - (m.qty * COALESCE(c.unit_cost, p.unit_cost, 0))), 0) AS "Kar"
            FROM sales_monthly_sku m
            LEFT JOIN products p ON p.sku = m.sku
            LEFT JOIN product_costs c ON c.sku = m.sku
            WHERE m.ym = ?
        ) x
        """,
        (ym,),
    )


@st.cache_data(ttl=300, show_spinner=False)
def get_month_sku_details(_conn: DBConn, ym: str, sku: str) -> pd.DataFrame:
    start, end = month_bounds(ym)
    return df_query(
        _conn,
        """
        SELECT
            week_label AS "Hafta",
            order_date AS "Tarih",
            sku AS "Stok Kodu",
            product_name AS "Urun",
            qty AS "Adet",
            unit_price AS "Birim Fiyat",
            revenue AS "Ciro"
        FROM sales
        WHERE order_date >= ? AND order_date < ?
          AND COALESCE(is_free_exit, 0) = 0
          AND sku = ?
        ORDER BY order_date, week_label
        """,
        (start, end, sku),
    )


@st.cache_data(ttl=300, show_spinner=False)
def get_month_free_exit_rows(_conn: DBConn, ym: str) -> pd.DataFrame:
    start, end = month_bounds(ym)
    return df_query(
        _conn,
        """
        SELECT
            week_label AS "Hafta",
            order_date AS "Tarih",
            customer_email AS "E-Posta",
            sku AS "Stok Kodu",
            product_name AS "Urun",
            qty AS "Adet",
            unit_price AS "Birim Fiyat",
            revenue AS "Bedelsiz Tutar"
        FROM sales
        WHERE order_date >= ? AND order_date < ?
          AND COALESCE(is_free_exit, 0) = 1
        ORDER BY order_date, week_label
        """,
        (start, end),
    )


@st.cache_data(ttl=300, show_spinner=False)
def get_products_master(_conn: DBConn) -> pd.DataFrame:
    return df_query(
        _conn,
        """
        SELECT
            p.sku,
            p.product_name,
            p.category,
            COALESCE(c.unit_cost, p.unit_cost, 0) AS unit_cost,
            p.active
        FROM products p
        LEFT JOIN product_costs c ON c.sku = p.sku
        ORDER BY product_name
        """,
    )


def render_header():
    now_txt = datetime.now(ZoneInfo("Europe/Istanbul")).strftime("%d.%m.%Y %H:%M")
    c1, c2 = st.columns([5, 1])
    with c1:
        if APP_LOGO_URL and not APP_LOGO_URL.lower().startswith(("http://", "https://")):
            st.image(APP_LOGO_URL, width=64)
        st.title("Eternal Fire")
        st.caption("Satis Operasyon ve Karlilik Kontrol Paneli")
    with c2:
        st.caption("Son Giris")
        st.code(now_txt)

    b1, b2, b3 = st.columns(3)
    b1.info("Canli Sistem")
    b2.info("Supabase / PostgreSQL")
    b3.info("Panel Aktif")


def render_login():
    st.subheader("Giris")
    user = st.text_input("Kullanici Adi", key="login_user")
    password = st.text_input("Parola", type="password", key="login_pass")
    if st.button("Giris Yap", type="primary"):
        if user.strip() == APP_USER and password == APP_PASSWORD:
            st.session_state["authenticated"] = True
            st.success("Giris basarili.")
            st.rerun()
        else:
            st.error("Kullanici adi veya parola hatali.")


inject_styles()
render_header()

if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False

if not st.session_state["authenticated"]:
    render_login()
    st.stop()

top_c1, top_c2 = st.columns([6, 1])
with top_c2:
    if st.button("Cikis"):
        st.session_state["authenticated"] = False
        st.rerun()

sections = ["Genel Dashboard", "Veri Ekle", "Aylik Rapor", "Urunler"]
section = st.radio("Bolum", sections, horizontal=True, label_visibility="collapsed")

if section == "Genel Dashboard":
    conn = get_ready_conn()
    ym = st.text_input("Dashboard Ay (YYYY-MM)", value=datetime.today().strftime("%Y-%m"), key="dash_ym")
    if not is_valid_ym(ym):
        st.warning("Ay formati gecersiz. Ornek: 2026-02")
        st.stop()

    if st.button("Aylik ozeti olustur/yenile", type="secondary"):
        with st.spinner("Ozet yenileniyor..."):
            refresh_monthly_summary_for_month(conn, ym.strip())
            st.cache_data.clear()
        st.success("Aylik ozet yenilendi.")
        st.rerun()

    metrics = get_dashboard_metrics(conn, ym.strip())
    if metrics.empty or float(metrics.iloc[0]["total_revenue"] or 0) <= 0:
        st.info("Bu ay icin veri yok.")
    else:
        q = float(metrics.iloc[0]["total_qty"] or 0)
        rev = float(metrics.iloc[0]["total_revenue"] or 0)
        cost = float(metrics.iloc[0]["total_cost"] or 0)
        profit = float(metrics.iloc[0]["total_profit"] or 0)
        margin = (profit / rev * 100.0) if rev > 0 else 0.0
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Toplam Adet", f"{q:,.0f}".replace(",", "."))
        c2.metric("Toplam Ciro", tr_money(rev))
        c3.metric("Toplam Maliyet", tr_money(cost))
        c4.metric("Net Kar", tr_money(profit))
        c5.metric("Kar Marji", f"%{margin:.1f}")

        st.markdown("#### En Cok Satan Urunler")
        top_df = get_dashboard_top_products(conn, ym.strip())
        if not top_df.empty:
            top_df["Adet"] = top_df["Adet"].map(lambda v: f"{float(v):,.0f}".replace(",", "."))
            st.dataframe(top_df, use_container_width=True, hide_index=True)

        st.markdown("#### Kategori Bazli Ciro / Kar")
        cat_df = get_dashboard_categories(conn, ym.strip())
        if not cat_df.empty:
            cat_df["Ciro"] = cat_df["Ciro"].map(tr_money)
            cat_df["Kar"] = cat_df["Kar"].map(tr_money)
            st.dataframe(cat_df, use_container_width=True, hide_index=True)

elif section == "Veri Ekle":
    conn = get_ready_conn()
    if st.button("Tum aylik ozetleri yeniden olustur", type="secondary"):
        with st.spinner("Tum ozetler hazirlaniyor..."):
            refresh_monthly_summary_all(conn)
            st.cache_data.clear()
        st.success("Tum aylik ozetler yenilendi.")
        st.rerun()

    st.markdown("#### Veri Yonetimi")
    m1, m2, m3 = st.columns(3)
    with m1:
        del_ym = st.text_input("Toplu silme ayi (YYYY-MM)", value=datetime.today().strftime("%Y-%m"), key="del_ym")
        if st.button("Secili Ayi Sil"):
            if not is_valid_ym(del_ym):
                st.warning("Ay formati gecersiz. Ornek: 2026-02")
            else:
                start, end = month_bounds(del_ym.strip())
                conn.execute("DELETE FROM sales WHERE order_date >= ? AND order_date < ?", (start, end))
                conn.commit()
                refresh_monthly_summary_for_month(conn, del_ym.strip())
                st.cache_data.clear()
                st.success(f"{del_ym.strip()} ayi verileri silindi.")
                st.rerun()
    with m2:
        recent_df = get_recent_sales(conn, 300)
        if recent_df.empty:
            st.caption("Tek satir silme icin veri yok.")
        else:
            recent_df["label"] = recent_df.apply(
                lambda r: f"#{int(r['id'])} | {r['order_date']} | {r['sku']} | {float(r['qty']):,.0f} adet".replace(",", "."),
                axis=1,
            )
            sel_label = st.selectbox("Tek satir sec", recent_df["label"].tolist(), key="single_delete_row")
            sel_row = recent_df[recent_df["label"] == sel_label].iloc[0]
            if st.button("Secili Satiri Sil"):
                row_id = int(sel_row["id"])
                ym = str(sel_row["order_date"])[:7]
                conn.execute("DELETE FROM sales WHERE id=?", (row_id,))
                conn.commit()
                if is_valid_ym(ym):
                    refresh_monthly_summary_for_month(conn, ym)
                st.cache_data.clear()
                st.success(f"Satir silindi: #{row_id}")
                st.rerun()
    with m3:
        wipe_ok = st.checkbox("Tum veriyi silmeyi onayliyorum", key="wipe_all_confirm")
        if st.button("Tum Verileri Sil", type="secondary", disabled=not wipe_ok):
            conn.execute("DELETE FROM sales")
            conn.execute("DELETE FROM sales_monthly_sku")
            conn.commit()
            st.cache_data.clear()
            st.success("Tum satis verileri silindi.")
            st.rerun()

    uploads = get_uploads(conn)
    if not uploads.empty:
        last_week = str(uploads.iloc[0]["week_label"])
        last_file = str(uploads.iloc[0]["source_file"])
        last_rows = int(uploads.iloc[0]["row_count"])
        st.caption(f"Son yukleme: {last_week} | {last_file} | {last_rows} satir")

        if st.button("Son yuklemeyi sil", type="secondary"):
            with st.spinner("Siliniyor..."):
                months_df = df_query(
                    conn,
                    "SELECT DISTINCT SUBSTR(order_date,1,7) AS ym FROM sales WHERE week_label=?",
                    (last_week,),
                )
                conn.execute("DELETE FROM sales WHERE week_label=?", (last_week,))
                conn.commit()
                for m in months_df["ym"].dropna().astype(str).tolist():
                    refresh_monthly_summary_for_month(conn, m)
                st.cache_data.clear()
            st.success(f"{last_week} haftasi silindi.")
            st.rerun()

        week_opts = sorted(uploads["week_label"].astype(str).unique().tolist(), reverse=True)
        sel_week = st.selectbox("Silmek icin hafta sec", week_opts)
        if st.button("Secili haftayi sil"):
            with st.spinner("Siliniyor..."):
                months_df = df_query(
                    conn,
                    "SELECT DISTINCT SUBSTR(order_date,1,7) AS ym FROM sales WHERE week_label=?",
                    (sel_week,),
                )
                conn.execute("DELETE FROM sales WHERE week_label=?", (sel_week,))
                conn.commit()
                for m in months_df["ym"].dropna().astype(str).tolist():
                    refresh_monthly_summary_for_month(conn, m)
                st.cache_data.clear()
            st.success(f"{sel_week} haftasi silindi.")
            st.rerun()

    week_label = st.text_input("Hafta etiketi (ornek: 2026-W07)")
    fallback_date = st.date_input("Bu dosya hangi aya yazilsin?", value=datetime.today())
    uploaded = st.file_uploader("Excel sec", type=["xlsx"])
    if st.button("Yukle ve Isle", type="primary", disabled=uploaded is None or week_label.strip() == ""):
        with st.spinner("Excel isleniyor..."):
            bytes_ = uploaded.getvalue()
            source_hash = hashlib.sha256(bytes_).hexdigest()
            source_file = uploaded.name
            parsed = parse_uploaded_excel(bytes_, week_label.strip(), fallback_date.isoformat())
            if parsed.empty:
                st.error("Islenecek satir bulunamadi.")
            else:
                parsed["source_file"] = source_file
                parsed["source_hash"] = source_hash
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
                upsert_products_from_rows(conn, parsed[["sku", "product_name"]])
                months = (
                    parsed["order_date"]
                    .astype(str)
                    .str.slice(0, 7)
                    .dropna()
                    .unique()
                    .tolist()
                )
                for ym in months:
                    if is_valid_ym(ym):
                        refresh_monthly_summary_for_month(conn, ym)
                st.cache_data.clear()
                free_rows = int(parsed["is_free_exit"].sum())
                st.success(f"Yukleme tamamlandi. {len(rows)} satir eklendi. Bedelsiz cikis: {free_rows}")

elif section == "Aylik Rapor":
    conn = get_ready_conn()
    ym = st.text_input("Ay (YYYY-MM)", value=datetime.today().strftime("%Y-%m"), key="month_ym")
    if not is_valid_ym(ym):
        st.warning("Ay formati gecersiz. Ornek: 2026-02")
        st.stop()

    prod_df = get_month_products(conn, ym.strip())
    totals_df = get_month_totals(conn, ym.strip())
    free_df = get_month_free_exit_rows(conn, ym.strip())

    if prod_df.empty:
        st.info("Bu ay icin veri yok.")
    else:
        t_qty = float(totals_df.iloc[0]["total_qty"] or 0)
        t_rev = float(totals_df.iloc[0]["total_revenue"] or 0)
        t_cost = float(totals_df.iloc[0]["total_cost"] or 0)
        t_profit = float(totals_df.iloc[0]["total_profit"] or 0)
        x1, x2, x3, x4 = st.columns(4)
        x1.metric("Toplam Adet", f"{t_qty:,.0f}".replace(",", "."))
        x2.metric("Toplam Ciro", tr_money(t_rev))
        x3.metric("Toplam Maliyet", tr_money(t_cost))
        x4.metric("Gercek Kar", tr_money(t_profit))

        disp = prod_df.copy()
        disp["Adet"] = disp["Adet"].map(lambda v: f"{float(v):,.0f}".replace(",", "."))
        disp["Ciro"] = disp["Ciro"].map(tr_money)
        disp["Maliyet"] = disp["Maliyet"].map(tr_money)
        disp["Kar"] = disp["Kar"].map(tr_money)
        st.dataframe(disp, use_container_width=True, hide_index=True)

        sku_opts = prod_df["Stok Kodu"].astype(str).tolist()
        sku_sel = st.selectbox("SKU detay sec", sku_opts)
        sku_df = get_month_sku_details(conn, ym.strip(), sku_sel)
        if not sku_df.empty:
            sku_df["Adet"] = sku_df["Adet"].map(lambda v: f"{float(v):,.0f}".replace(",", "."))
            sku_df["Birim Fiyat"] = sku_df["Birim Fiyat"].map(tr_money)
            sku_df["Ciro"] = sku_df["Ciro"].map(tr_money)
            st.dataframe(sku_df, use_container_width=True, hide_index=True)

    st.markdown("#### Bedelsiz Cikislar")
    if free_df.empty:
        st.info("Bu ay bedelsiz cikis yok.")
    else:
        fq = float(free_df["Adet"].sum())
        fr = float(free_df["Bedelsiz Tutar"].sum())
        f1, f2 = st.columns(2)
        f1.metric("Bedelsiz Toplam Adet", f"{fq:,.0f}".replace(",", "."))
        f2.metric("Bedelsiz Toplam Tutar", tr_money(fr))
        fd = free_df.copy()
        fd["Adet"] = fd["Adet"].map(lambda v: f"{float(v):,.0f}".replace(",", "."))
        fd["Birim Fiyat"] = fd["Birim Fiyat"].map(tr_money)
        fd["Bedelsiz Tutar"] = fd["Bedelsiz Tutar"].map(tr_money)
        st.dataframe(fd, use_container_width=True, hide_index=True)

else:
    conn = get_ready_conn()
    st.subheader("Urunler")
    products = get_products_master(conn)
    if products.empty:
        st.info("Once Excel yukleyin.")
    else:
        edit_df = products.copy()
        edit_df["category"] = edit_df["category"].fillna("Genel")
        edit_df["unit_cost"] = edit_df["unit_cost"].fillna(0.0)
        edit_df["active"] = edit_df["active"].fillna(1).astype(int).map(lambda x: x == 1)
        edited = st.data_editor(
            edit_df,
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
        if st.button("Urun Maliyet Kaydet", type="primary"):
            now = datetime.now().isoformat(timespec="seconds")
            rows = []
            cost_rows = []
            for _, r in edited.iterrows():
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
                cost_rows.append((r["sku"], float(r["unit_cost"])))
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
            conn.commit()
            upsert_product_costs(conn, cost_rows)
            st.cache_data.clear()
            st.success("Urun master kaydedildi.")
