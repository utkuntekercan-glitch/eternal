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

st.set_page_config(page_title="Oldschool Espor Satis Rapor", layout="wide")

DB_PATH = Path("sales_reports.db")
DATABASE_URL = str(st.secrets.get("DATABASE_URL", os.getenv("DATABASE_URL", ""))).strip()
USE_POSTGRES = bool(DATABASE_URL)


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

    def close(self):
        self._conn.close()


def get_conn() -> DBConn:
    if USE_POSTGRES:
        if psycopg2 is None:
            raise RuntimeError("Postgres icin psycopg2-binary gerekli.")
        if "sslmode=" in DATABASE_URL:
            raw = psycopg2.connect(DATABASE_URL)
        else:
            raw = psycopg2.connect(DATABASE_URL, sslmode="require")
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
        CREATE TABLE IF NOT EXISTS costs (
            sku TEXT PRIMARY KEY,
            product_name TEXT,
            unit_cost REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sales_order_date ON sales(order_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sales_sku ON sales(sku)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sales_week ON sales(week_label)")
    conn.commit()


def df_query(conn: DBConn, q: str, params=()):
    cur = conn.execute(q, params)
    rows = cur.fetchall()
    cols = [c[0] for c in cur.description] if cur.description else []
    return pd.DataFrame(rows, columns=cols)


def tr_money(x: float) -> str:
    s = format(float(x), ",.2f").replace(",", "X").replace(".", ",").replace("X", ".")
    return f"TL {s}"


def normalize_num(x) -> float:
    if pd.isna(x):
        return 0.0
    s = str(x).strip().replace("TL", "").replace("tl", "")
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return 0.0


def parse_uploaded_excel(file_bytes: bytes, week_label: str) -> pd.DataFrame:
    # User mapping:
    # S -> product_name (index 18)
    # Y -> sku (index 24)
    # R -> qty (index 17)
    # U -> unit_price (index 20)
    df = pd.read_excel(io.BytesIO(file_bytes), engine="openpyxl")
    if len(df.columns) < 25:
        raise ValueError("Excel beklenen kadar sutun icermiyor. En az Y sutununa kadar olmali.")

    out = pd.DataFrame(
        {
            "product_name": df.iloc[:, 18].astype(str).str.strip(),
            "sku": df.iloc[:, 24].astype(str).str.strip(),
            "qty": df.iloc[:, 17].apply(normalize_num),
            "unit_price": df.iloc[:, 20].apply(normalize_num),
        }
    )
    out["week_label"] = week_label
    out["order_date"] = None
    out["revenue"] = out["qty"] * out["unit_price"]
    out = out[(out["sku"] != "") & (out["product_name"] != "")]
    out = out[out["qty"] > 0]
    return out


def upsert_costs(conn: DBConn, df: pd.DataFrame):
    now = datetime.now().isoformat(timespec="seconds")
    rows = []
    for _, r in df.iterrows():
        rows.append((r["sku"], r["product_name"], float(r["unit_cost"]), now))
    conn.executemany(
        """
        INSERT INTO costs(sku, product_name, unit_cost, updated_at)
        VALUES(?,?,?,?)
        ON CONFLICT(sku) DO UPDATE SET
            product_name=excluded.product_name,
            unit_cost=excluded.unit_cost,
            updated_at=excluded.updated_at
        """,
        rows,
    )
    conn.commit()


def load_month_data(conn: DBConn, ym: str) -> pd.DataFrame:
    start = f"{ym}-01"
    end_y, end_m = map(int, ym.split("-"))
    if end_m == 12:
        end = f"{end_y + 1:04d}-01-01"
    else:
        end = f"{end_y:04d}-{end_m + 1:02d}-01"

    sales = df_query(
        conn,
        """
        SELECT week_label, order_date, sku, product_name, qty, unit_price, revenue
        FROM sales
        WHERE order_date >= ? AND order_date < ?
        """,
        (start, end),
    )
    if sales.empty:
        return sales

    costs = df_query(conn, "SELECT sku, unit_cost FROM costs")
    merged = sales.merge(costs, on="sku", how="left")
    merged["unit_cost"] = merged["unit_cost"].fillna(0.0)
    merged["cost_total"] = merged["qty"] * merged["unit_cost"]
    merged["profit"] = merged["revenue"] - merged["cost_total"]
    return merged


st.markdown("<h1 style='text-align:center;'>Eternal Fire</h1>", unsafe_allow_html=True)

if "_sales_conn" not in st.session_state:
    st.session_state["_sales_conn"] = get_conn()
conn = st.session_state["_sales_conn"]
if "_sales_bootstrap" not in st.session_state:
    init_db(conn)
    st.session_state["_sales_bootstrap"] = True

tab1, tab2, tab3 = st.tabs(["Excel Yukle", "Aylik Rapor", "Maliyet Girisi"])

with tab1:
    st.subheader("Haftalik Excel Yukleme")
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
            INSERT INTO sales(week_label, order_date, sku, product_name, qty, unit_price, revenue, source_file, source_hash)
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )
        conn.commit()
        st.success(f"Yukleme tamamlandi. {len(rows)} satir eklendi.")

with tab2:
    st.subheader("Aylik Satis / Ciro / Kar")
    ym = st.text_input("Ay (YYYY-MM)", value=datetime.today().strftime("%Y-%m"))
    report = load_month_data(conn, ym.strip())
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

with tab3:
    st.subheader("Urun Maliyet Girisi")
    products = df_query(
        conn,
        """
        SELECT sku, MAX(product_name) AS product_name
        FROM sales
        GROUP BY sku
        ORDER BY product_name
        """,
    )
    if products.empty:
        st.info("Once Excel yukleyin.")
    else:
        current_costs = df_query(conn, "SELECT sku, unit_cost FROM costs")
        editor_df = products.merge(current_costs, on="sku", how="left")
        editor_df["unit_cost"] = editor_df["unit_cost"].fillna(0.0)
        edited = st.data_editor(
            editor_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "sku": st.column_config.TextColumn("Stok Kodu", disabled=True),
                "product_name": st.column_config.TextColumn("Urun", disabled=True),
                "unit_cost": st.column_config.NumberColumn("Birim Maliyet", min_value=0.0, step=0.01),
            },
            num_rows="fixed",
        )
        if st.button("Maliyetleri Kaydet", type="primary"):
            upsert_costs(conn, edited[["sku", "product_name", "unit_cost"]])
            st.success("Maliyetler kaydedildi.")
