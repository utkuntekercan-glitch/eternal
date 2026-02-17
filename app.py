import hashlib
import io
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from openpyxl import load_workbook

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
        pass
    try:
        conn.execute("ALTER TABLE sales ADD COLUMN is_free_exit INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sales_order_date ON sales(order_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sales_sku ON sales(sku)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sales_week ON sales(week_label)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_products_active ON products(active)")
    conn.commit()


def df_query(conn: DBConn, q: str, params=()):
    cur = conn.execute(q, params)
    rows = cur.fetchall()
    cols = [c[0] for c in cur.description] if cur.description else []
    return pd.DataFrame(rows, columns=cols)


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
    products = df_query(conn, "SELECT sku, category, unit_cost, active FROM products")
    merged = sales_calc.merge(products, on="sku", how="left")
    merged["category"] = merged["category"].fillna("Genel")
    merged["active"] = merged["active"].fillna(1)
    merged["unit_cost"] = merged["unit_cost"].fillna(0.0)
    merged["cost_total"] = merged["qty"] * merged["unit_cost"]
    merged["profit"] = merged["revenue"] - merged["cost_total"]
    merged["margin_pct"] = merged["profit"] / merged["revenue"].replace(0, pd.NA) * 100
    merged["margin_pct"] = merged["margin_pct"].fillna(0.0)
    return merged


st.markdown("<h1 style='text-align:center;'>Eternal Fire</h1>", unsafe_allow_html=True)

if "_sales_conn" not in st.session_state:
    st.session_state["_sales_conn"] = get_conn()
conn = st.session_state["_sales_conn"]
# Always ensure schema exists (safe/idempotent) to avoid missing-table errors after deploys.
init_db(conn)
if "_sales_bootstrap" not in st.session_state:
    sync_products_from_sales(conn)
    st.session_state["_sales_bootstrap"] = True

tab0, tab1, tab2, tab3 = st.tabs(["Genel Dashboard", "Excel Yukle", "Aylik Rapor", "Urun Master"])

with tab0:
    st.subheader("Genel Satis Ozeti")
    dash_ym = st.text_input("Dashboard Ay (YYYY-MM)", value=datetime.today().strftime("%Y-%m"), key="dash_ym")
    dash = load_month_data(conn, dash_ym.strip())
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

with tab1:
    st.subheader("Haftalik Excel Yukleme")
    uploads = df_query(
        conn,
        """
        SELECT week_label, source_file, COUNT(*) AS row_count, MAX(id) AS max_id
        FROM sales
        GROUP BY week_label, source_file
        ORDER BY max_id DESC
        """,
    )
    if not uploads.empty:
        last_week = str(uploads.iloc[0]["week_label"])
        last_file = str(uploads.iloc[0]["source_file"])
        last_rows = int(uploads.iloc[0]["row_count"])
        st.caption(f"Son yukleme: {last_week} | {last_file} | {last_rows} satir")
        if st.button("Son yuklemeyi sil", type="secondary"):
            conn.execute("DELETE FROM sales WHERE week_label=?", (last_week,))
            conn.commit()
            sync_products_from_sales(conn)
            st.success(f"{last_week} haftasi verileri silindi.")
            st.rerun()
        week_opts = sorted(uploads["week_label"].astype(str).unique().tolist(), reverse=True)
        sel_week = st.selectbox("Silmek icin hafta sec", week_opts, key="delete_week_select")
        if st.button("Secili haftayi sil"):
            conn.execute("DELETE FROM sales WHERE week_label=?", (sel_week,))
            conn.commit()
            sync_products_from_sales(conn)
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
        free_rows = int(parsed["is_free_exit"].sum()) if "is_free_exit" in parsed.columns else 0
        st.success(f"Yukleme tamamlandi. {len(rows)} satir eklendi. Bedelsiz cikis: {free_rows}")

with tab2:
    st.subheader("Aylik Satis / Ciro / Kar")
    ym = st.text_input("Ay (YYYY-MM)", value=datetime.today().strftime("%Y-%m"))
    report = load_month_data(conn, ym.strip())
    start = f"{ym.strip()}-01"
    y, m = map(int, ym.strip().split("-"))
    end = f"{y + 1:04d}-01-01" if m == 12 else f"{y:04d}-{m + 1:02d}-01"
    free_exit_rows = df_query(
        conn,
        """
        SELECT week_label, order_date, customer_email, sku, product_name, qty, unit_price, revenue
        FROM sales
        WHERE order_date >= ? AND order_date < ?
          AND COALESCE(is_free_exit, 0) = 1
        ORDER BY order_date, week_label
        """,
        (start, end),
    )
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

with tab3:
    st.subheader("Urun Master (Kategori + Maliyet + Aktif)")
    products = df_query(
        conn,
        """
        SELECT sku, product_name, category, unit_cost, active
        FROM products
        ORDER BY product_name
        """,
    )
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
            st.success("Urun master kaydedildi.")
