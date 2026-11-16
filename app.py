"""
International Cost of Capital Comparator
========================================
Compares a project's cost of equity, cost of debt, and WACC in USD and in the
local currency of a selected country.

Data sources (fetched live, with a manual-override fallback):
  1. Unlevered industry betas .............. Aswath Damodaran (NYU Stern), betas.xls
  2. BBB (Baa2) corporate default spread ... Aswath Damodaran (NYU Stern), ratings.html
  3. Statutory corporate tax rates ......... OECD Corporate Tax Statistics 2024
  4. 10-year government bond yields ........ Trading Economics

Formulas (differential / international Fisher approach -- replace with the class
formulas if they differ; the structure here makes that a one-function change):

  Lever the beta to the target capital structure:
      D/E       = w_d / (1 - w_d)
      beta_L    = beta_U * [1 + (1 - t) * D/E]

  USD cost of capital:
      k_e(USD)        = y_US + beta_L * ERP
      k_d,pre(USD)    = y_US + BBB_spread
      k_d,after(USD)  = k_d,pre(USD) * (1 - t)
      WACC(USD)       = w_e * k_e(USD) + w_d * k_d,after(USD)

  Convert a USD rate to the foreign currency with the risk-free (10y) differential:
      r(FX) = (1 + r(USD)) * (1 + y_foreign) / (1 + y_US) - 1

  Foreign cost of capital:
      k_e(FX)     = convert( k_e(USD) )
      k_d,pre(FX) = convert( k_d,pre(USD) )           # -> implied auto foreign spread = k_d,pre(FX) - y_foreign
                    OR  y_foreign + manual_foreign_spread   (when the override is on)
      WACC(FX)    = w_e * k_e(FX) + w_d * k_d,pre(FX) * (1 - t)

  The same project tax rate, debt share, and ERP are used on both sides; the tax
  rate can be taken from the U.S. or from the selected country.
"""

import io
import datetime as dt

import numpy as np
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="International Cost of Capital", page_icon="🌐", layout="wide")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "application/vnd.ms-excel,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

SOURCES = {
    "betas": "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls",
    "ratings": "https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html",
    "oecd": "https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/full-report/"
            "statutory-corporate-income-tax-rates_440f9286.html",
    "bonds": "https://tradingeconomics.com/bonds",
}

# --------------------------------------------------------------------------------------
# Fallback data (used only when a live fetch is blocked). Clearly dated and labelled in
# the UI. Replace by typing current values into the override fields, or hit Refresh on a
# host (e.g. Streamlit Cloud) that can reach the sources.
# --------------------------------------------------------------------------------------
FALLBACK_DATE = "approx. 2025 (fallback)"

FALLBACK_BETAS = {  # Damodaran US unlevered betas (illustrative; verify against live file)
    "Software (System & Application)": 1.15,
    "Software (Internet)": 1.33,
    "Semiconductor": 1.56,
    "Computers/Peripherals": 1.21,
    "Electronics (General)": 1.08,
    "Advertising": 1.09,
    "Aerospace/Defense": 0.86,
    "Air Transport": 1.03,
    "Apparel": 0.93,
    "Auto & Truck": 1.07,
    "Bank (Money Center)": 0.48,
    "Beverage (Soft)": 0.76,
    "Biotechnology": 1.12,
    "Chemical (Basic)": 0.90,
    "Construction Supplies": 1.05,
    "Drugs (Pharmaceutical)": 0.95,
    "Food Processing": 0.62,
    "Healthcare Products": 0.94,
    "Hotel/Gaming": 1.10,
    "Household Products": 0.78,
    "Machinery": 0.98,
    "Oil/Gas (Integrated)": 0.82,
    "Power": 0.42,
    "Retail (General)": 1.06,
    "Telecom (Wireless)": 0.80,
    "Tobacco": 0.63,
    "Transportation": 0.90,
    "Utility (General)": 0.38,
}

FALLBACK_TAX = {  # OECD combined statutory corporate income tax rate (central + sub-central)
    "United States": 25.8, "Germany": 29.9, "France": 25.8, "United Kingdom": 25.0,
    "Japan": 29.7, "Canada": 26.1, "Australia": 30.0, "China": 25.0, "India": 25.2,
    "Brazil": 34.0, "Mexico": 30.0, "Italy": 27.8, "Spain": 25.0, "Netherlands": 25.8,
    "Switzerland": 19.7, "Sweden": 20.6, "Ireland": 12.5, "South Korea": 26.5,
    "Poland": 19.0, "Turkey": 25.0, "Indonesia": 22.0, "Norway": 22.0, "Denmark": 22.0,
    "Belgium": 25.0, "Austria": 23.0, "Portugal": 31.5, "Greece": 22.0, "Argentina": 35.0,
    "South Africa": 27.0, "Singapore": 17.0,
}

FALLBACK_YIELD = {  # 10-year government bond yields, % (illustrative recent levels)
    "United States": 4.30, "Germany": 2.65, "France": 3.25, "United Kingdom": 4.45,
    "Japan": 1.45, "Canada": 3.35, "Australia": 4.45, "China": 1.90, "India": 6.70,
    "Brazil": 13.80, "Mexico": 9.70, "Italy": 3.55, "Spain": 3.10, "Netherlands": 2.85,
    "Switzerland": 0.45, "Sweden": 2.25, "Ireland": 2.90, "South Korea": 3.05,
    "Poland": 5.70, "Turkey": 29.50, "Indonesia": 6.80, "Norway": 3.90, "Denmark": 2.70,
    "Belgium": 3.10, "Austria": 3.05, "Portugal": 3.00, "Greece": 3.45, "Argentina": 30.00,
    "South Africa": 9.90, "Singapore": 2.55,
}

FALLBACK_BBB = 1.50  # Damodaran Baa2/BBB default spread, %

CURRENCY = {
    "United States": "USD", "Germany": "EUR", "France": "EUR", "United Kingdom": "GBP",
    "Japan": "JPY", "Canada": "CAD", "Australia": "AUD", "China": "CNY", "India": "INR",
    "Brazil": "BRL", "Mexico": "MXN", "Italy": "EUR", "Spain": "EUR", "Netherlands": "EUR",
    "Switzerland": "CHF", "Sweden": "SEK", "Ireland": "EUR", "South Korea": "KRW",
    "Poland": "PLN", "Turkey": "TRY", "Indonesia": "IDR", "Norway": "NOK", "Denmark": "DKK",
    "Belgium": "EUR", "Austria": "EUR", "Portugal": "EUR", "Greece": "EUR",
    "Argentina": "ARS", "South Africa": "ZAR", "Singapore": "SGD",
}


# --------------------------------------------------------------------------------------
# Live fetchers. Each returns (data, meta) where meta = {"live": bool, "date": str,
# "note": str}. Any failure falls back silently to the bundled values.
# --------------------------------------------------------------------------------------
def _parse_beta_matrix(raw):
    """Pull {industry: unlevered beta} from a header-less matrix (Excel or HTML table).
    Positional lookups avoid duplicate-label Series issues."""
    hdr = None
    for i in range(min(25, len(raw))):
        cells = [str(x).strip().lower() for x in raw.iloc[i].tolist()]
        if any("industry name" in c for c in cells):
            hdr = i
            break
    if hdr is None:
        raise ValueError("header row not found")
    header_cells = [str(x).strip().lower() for x in raw.iloc[hdr].tolist()]

    def find_col(*keys):
        for key in keys:
            for j, c in enumerate(header_cells):
                if key in c:
                    return j
        return None

    name_j = find_col("industry name")
    unlev_j = find_col("unlevered beta", "unlever")
    if name_j is None or unlev_j is None:
        raise ValueError("name/unlevered column not found")

    out = {}
    for i in range(hdr + 1, len(raw)):
        name = str(raw.iat[i, name_j]).strip()
        try:
            b = float(raw.iat[i, unlev_j])
        except (TypeError, ValueError):
            continue
        if name and name.lower() != "nan" and np.isfinite(b):
            out[name] = round(b, 4)
    if len(out) < 10:
        raise ValueError("too few industries parsed")
    return out


@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
def fetch_betas():
    last = None
    try:
        r = requests.get(SOURCES["betas"], headers=HEADERS, timeout=30)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        return dict(FALLBACK_BETAS), {"live": False, "date": FALLBACK_DATE,
                                      "note": f"fallback ({type(e).__name__})"}

    # 1) try real Excel (binary .xls or .xlsx)
    for engine in ("xlrd", "openpyxl"):
        try:
            raw = pd.read_excel(io.BytesIO(r.content), sheet_name=0, header=None, engine=engine)
            return _parse_beta_matrix(raw), {"live": True, "date": dt.date.today().isoformat(),
                                             "note": "NYU Stern betas.xls (Excel)"}
        except Exception as e:  # noqa: BLE001
            last = e

    # 2) some Damodaran ".xls" files are actually HTML tables -> read as HTML
    try:
        tables = pd.read_html(io.StringIO(r.content.decode("latin-1", errors="ignore")))
        for t in tables:
            raw = pd.DataFrame([list(t.columns)] + t.values.tolist())
            try:
                return _parse_beta_matrix(raw), {"live": True, "date": dt.date.today().isoformat(),
                                                 "note": "NYU Stern betas.xls (HTML)"}
            except Exception as e:  # noqa: BLE001
                last = e
    except Exception as e:  # noqa: BLE001
        last = e

    return dict(FALLBACK_BETAS), {"live": False, "date": FALLBACK_DATE,
                                  "note": f"fallback ({type(last).__name__ if last else 'error'})"}


@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
def fetch_bbb():
    try:
        r = requests.get(SOURCES["ratings"], headers=HEADERS, timeout=30)
        r.raise_for_status()
        tables = pd.read_html(io.StringIO(r.text))
        for t in tables:
            flat = t.astype(str)
            mask = flat.apply(lambda col: col.str.contains("Baa2", case=False, na=False)).any(axis=1)
            if mask.any():
                rowi = flat[mask].index[0]
                # spread is the right-most numeric-looking cell on that row
                for val in reversed(t.loc[rowi].tolist()):
                    s = str(val).replace("%", "").strip()
                    try:
                        num = float(s)
                    except ValueError:
                        continue
                    spread = num * 100 if num < 1 else num  # 0.0147 -> 1.47
                    if 0 < spread < 15:
                        return round(spread, 4), {"live": True, "date": dt.date.today().isoformat(),
                                                  "note": "NYU Stern ratings.html (Baa2/BBB)"}
        raise ValueError("Baa2 row not found")
    except Exception as e:  # noqa: BLE001
        return FALLBACK_BBB, {"live": False, "date": FALLBACK_DATE, "note": f"fallback ({type(e).__name__})"}


@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
def fetch_yields():
    # Trading Economics is JS-rendered and bot-screened, so a plain request often returns
    # no usable table. We scan every table for rows whose first cell names a country we
    # track and whose row has a plausible yield number. Falls back otherwise; the override
    # fields let you enter current yields by hand.
    known = {k.lower(): k for k in FALLBACK_YIELD}
    try:
        r = requests.get(SOURCES["bonds"], headers=HEADERS, timeout=30)
        r.raise_for_status()
        tables = pd.read_html(io.StringIO(r.text))
        out = {}
        for t in tables:
            for _, row in t.iterrows():
                vals = [str(v).strip() for v in row.tolist()]
                name = known.get(vals[0].lower()) if vals else None
                if not name:
                    continue
                for v in vals[1:]:
                    try:
                        y = float(v.replace("%", "").strip())
                    except ValueError:
                        continue
                    if -5 < y < 100:
                        out[name] = round(y, 4)
                        break
        if len(out) < 5:
            raise ValueError("too few yields parsed")
        merged = dict(FALLBACK_YIELD)
        merged.update(out)
        return merged, {"live": True, "date": dt.date.today().isoformat(), "note": "Trading Economics /bonds"}
    except Exception as e:  # noqa: BLE001
        return dict(FALLBACK_YIELD), {"live": False, "date": FALLBACK_DATE, "note": f"fallback ({type(e).__name__})"}


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def fetch_tax():
    # OECD's statutory-rate page is JavaScript-rendered; a plain fetch rarely yields the
    # table, so the bundled OECD 2024 figures are the practical default. A manual override
    # lets you enter the exact current rate.
    try:
        r = requests.get(SOURCES["oecd"], headers=HEADERS, timeout=30)
        r.raise_for_status()
        tables = pd.read_html(io.StringIO(r.text))
        out = {}
        for t in tables:
            if t.shape[1] < 2:
                continue
            for _, row in t.iterrows():
                name = str(row.iloc[0]).strip()
                for val in row.iloc[1:]:
                    try:
                        rate = float(str(val).replace("%", "").strip())
                    except ValueError:
                        continue
                    if name in FALLBACK_TAX and 0 < rate < 60:
                        out[name] = round(rate, 2)
                        break
        if len(out) < 5:
            raise ValueError("OECD table not parseable")
        merged = dict(FALLBACK_TAX)
        merged.update(out)
        return merged, {"live": True, "date": dt.date.today().isoformat(), "note": "OECD 2024 (parsed)"}
    except Exception as e:  # noqa: BLE001
        return dict(FALLBACK_TAX), {"live": False, "date": "OECD Corporate Tax Statistics 2024",
                                    "note": f"bundled OECD 2024 ({type(e).__name__})"}


# --------------------------------------------------------------------------------------
# Pure calculation engine (unit-tested separately). All rates are decimals.
# --------------------------------------------------------------------------------------
def levered_beta(beta_u, tax, debt_share):
    we = 1.0 - debt_share
    if we <= 0:
        return float("nan")
    de = debt_share / we
    return beta_u * (1.0 + (1.0 - tax) * de)


def convert_rate(rate_usd, y_foreign, y_us):
    """Translate a USD rate into the foreign currency using the 10y risk-free differential."""
    return (1.0 + rate_usd) * (1.0 + y_foreign) / (1.0 + y_us) - 1.0


def usd_costs(y_us, beta_u, erp, tax, debt_share, bbb_spread):
    bl = levered_beta(beta_u, tax, debt_share)
    ke = y_us + bl * erp
    kd_pre = y_us + bbb_spread
    kd_at = kd_pre * (1.0 - tax)
    we = 1.0 - debt_share
    wacc = we * ke + debt_share * kd_at
    return {"beta_l": bl, "ke": ke, "kd_pre": kd_pre, "kd_at": kd_at, "wacc": wacc}


def foreign_costs(usd, y_us, y_foreign, tax, debt_share, foreign_spread_override=None):
    ke = convert_rate(usd["ke"], y_foreign, y_us)
    if foreign_spread_override is None:
        kd_pre = convert_rate(usd["kd_pre"], y_foreign, y_us)
        auto_spread = kd_pre - y_foreign
    else:
        auto_spread = foreign_spread_override
        kd_pre = y_foreign + foreign_spread_override
    kd_at = kd_pre * (1.0 - tax)
    we = 1.0 - debt_share
    wacc = we * ke + debt_share * kd_at
    return {"beta_l": usd["beta_l"], "ke": ke, "kd_pre": kd_pre, "kd_at": kd_at,
            "wacc": wacc, "auto_spread": auto_spread}


def pct(x):
    return "-" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x * 100:.2f}%"


# --------------------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------------------
st.title("🌐 International Cost of Capital")
st.caption("Cost of equity, cost of debt, and WACC for a project, side by side in USD and a "
           "selected country's currency. Live data from Damodaran, OECD, and Trading Economics, "
           "with manual overrides.")

with st.sidebar:
    st.header("Data")
    if st.button("🔄 Refresh fetched data", use_container_width=True):
        fetch_betas.clear(); fetch_bbb.clear(); fetch_yields.clear(); fetch_tax.clear()
        st.rerun()

betas, m_betas = fetch_betas()
bbb_live, m_bbb = fetch_bbb()
yields, m_yields = fetch_yields()
taxes, m_tax = fetch_tax()

# country lists
countries = sorted(set(FALLBACK_TAX) | set(taxes) | set(yields))
industries = sorted(betas)

def _src_badge(meta):
    tag = "🟢 live" if meta["live"] else "🟡 fallback"
    return f"{tag} · {meta['note']} · {meta['date']}"

with st.sidebar:
    st.caption(f"Betas: {_src_badge(m_betas)}")
    st.caption(f"BBB spread: {_src_badge(m_bbb)}")
    st.caption(f"Yields: {_src_badge(m_yields)}")
    st.caption(f"Tax rates: {_src_badge(m_tax)}")
    st.divider()

    st.header("Inputs")
    default_ind = "Software (System & Application)" if "Software (System & Application)" in industries else industries[0]
    industry = st.selectbox("Industry", industries, index=industries.index(default_ind))

    default_ctry = "United States"
    country = st.selectbox("Selected country", countries,
                           index=countries.index(default_ctry) if default_ctry in countries else 0)

    erp = st.number_input("Equity risk premium (%)", min_value=0.0, max_value=25.0,
                          value=6.5, step=0.1) / 100.0
    debt_share = st.slider("Target debt share w_d (%)", min_value=0, max_value=90,
                           value=30, step=1) / 100.0

    tax_source = st.radio("Project tax rate from", ["United States", "Selected country"],
                          index=0, horizontal=True)

    st.divider()
    st.subheader("Overrides")
    st.caption("Switch any of these on to replace the fetched value with your own.")

    # beta
    ov_beta = st.checkbox("Override unlevered beta")
    beta_u = st.number_input("Unlevered beta", value=float(betas[industry]), step=0.01,
                             disabled=not ov_beta) if ov_beta else float(betas[industry])

    # US tax
    us_tax_fetched = float(taxes.get("United States", FALLBACK_TAX["United States"]))
    ov_ustax = st.checkbox("Override U.S. tax rate")
    us_tax = st.number_input("U.S. corporate tax (%)", value=us_tax_fetched, step=0.1,
                             disabled=not ov_ustax) if ov_ustax else us_tax_fetched

    # foreign tax
    f_tax_fetched = float(taxes.get(country, FALLBACK_TAX.get(country, 25.0)))
    ov_ftax = st.checkbox("Override selected-country tax rate")
    f_tax = st.number_input(f"{country} corporate tax (%)", value=f_tax_fetched, step=0.1,
                            disabled=not ov_ftax) if ov_ftax else f_tax_fetched

    # US BBB spread
    ov_bbb = st.checkbox("Override U.S. BBB spread")
    bbb = st.number_input("U.S. BBB spread (%)", value=float(bbb_live), step=0.01,
                          disabled=not ov_bbb) if ov_bbb else float(bbb_live)

    # yields
    us_y_fetched = float(yields.get("United States", FALLBACK_YIELD["United States"]))
    ov_usy = st.checkbox("Override U.S. 10y yield")
    us_yield = st.number_input("U.S. 10y gov yield (%)", value=us_y_fetched, step=0.01,
                               disabled=not ov_usy) if ov_usy else us_y_fetched

    f_y_fetched = float(yields.get(country, FALLBACK_YIELD.get(country, us_y_fetched)))
    ov_fy = st.checkbox("Override selected-country 10y yield")
    f_yield = st.number_input(f"{country} 10y gov yield (%)", value=f_y_fetched, step=0.01,
                              disabled=not ov_fy) if ov_fy else f_y_fetched

    # foreign spread
    ov_fspread = st.checkbox("Override selected-country BBB spread")
    f_spread_in = st.number_input("Selected-country BBB spread (%)", value=float(bbb_live),
                                  step=0.01, disabled=not ov_fspread) if ov_fspread else None

# resolve the single project tax rate
tax = (us_tax if tax_source == "United States" else f_tax) / 100.0
y_us = us_yield / 100.0
y_f = f_yield / 100.0
bbb_d = bbb / 100.0
f_spread_override = (f_spread_in / 100.0) if (ov_fspread and f_spread_in is not None) else None

# calculations
usd = usd_costs(y_us, beta_u, erp, tax, debt_share, bbb_d)
fx = foreign_costs(usd, y_us, y_f, tax, debt_share, f_spread_override)
cur = CURRENCY.get(country, country)
we = 1.0 - debt_share

# ---- results side by side -----------------------------------------------------------
st.subheader("Results")
c1, c2 = st.columns(2)
with c1:
    st.markdown("### 🇺🇸 USD")
    st.metric("WACC (USD)", pct(usd["wacc"]))
    st.write(f"**Levered beta:** {usd['beta_l']:.3f}")
    st.write(f"**Cost of equity:** {pct(usd['ke'])}")
    st.write(f"**Cost of debt (pre-tax):** {pct(usd['kd_pre'])}")
    st.write(f"**Cost of debt (after-tax):** {pct(usd['kd_at'])}")
with c2:
    st.markdown(f"### {country} ({cur})")
    st.metric(f"WACC ({cur})", pct(fx["wacc"]))
    st.write(f"**Levered beta:** {fx['beta_l']:.3f}")
    st.write(f"**Cost of equity:** {pct(fx['ke'])}")
    st.write(f"**Cost of debt (pre-tax):** {pct(fx['kd_pre'])}")
    st.write(f"**Cost of debt (after-tax):** {pct(fx['kd_at'])}")
    tag = "overridden" if f_spread_override is not None else "auto (implied)"
    st.caption(f"{cur} BBB spread, {tag}: {pct(fx['auto_spread'])}")

# downloadable side-by-side
results_df = pd.DataFrame({
    "Component": ["Unlevered beta", "Levered beta", "Cost of equity", "Cost of debt (pre-tax)",
                  "Cost of debt (after-tax)", "WACC", "10y gov yield", "BBB spread",
                  "Tax rate used", "ERP", "Debt share"],
    "USD": [beta_u, usd["beta_l"], usd["ke"], usd["kd_pre"], usd["kd_at"], usd["wacc"],
            y_us, bbb_d, tax, erp, debt_share],
    f"{country} ({cur})": [beta_u, fx["beta_l"], fx["ke"], fx["kd_pre"], fx["kd_at"], fx["wacc"],
                           y_f, fx["auto_spread"], tax, erp, debt_share],
})
st.download_button("⬇️ Download these results (CSV)",
                   results_df.to_csv(index=False).encode(),
                   file_name=f"cost_of_capital_{industry}_{country}.csv".replace(" ", "_"),
                   mime="text/csv")

# ---- assumptions --------------------------------------------------------------------
with st.expander("Main assumptions and formulas", expanded=False):
    st.markdown(f"""
- **Industry (unlevered beta):** {industry} = {beta_u:.3f}
- **Capital structure:** debt share w_d = {debt_share:.0%}, equity share w_e = {we:.0%},
  so D/E = {debt_share/we:.3f} when w_d < 100%.
- **Project tax rate:** {tax:.2%} (from **{tax_source}**), used in the tax shield on both sides.
- **Equity risk premium:** {erp:.2%}, applied in both currencies.
- **Risk-free rate:** each currency's 10-year government bond yield
  (U.S. {y_us:.2%}, {country} {y_f:.2%}).
- **Beta levering:** beta_L = beta_U x [1 + (1 - t) x D/E]. Beta is a risk measure, so the
  same levered beta is used in both currencies.
- **Currency translation:** a USD rate is converted with the risk-free differential,
  r(FX) = (1 + r_USD) x (1 + y_foreign) / (1 + y_US) - 1. Equal yields => identical results.
- **Cost of debt:** USD = y_US + BBB spread. The {cur} spread is, by default, the spread
  *implied* by translating the USD cost of debt; you can override it, which leaves the USD
  side unchanged.
""")

# ---- all-industry comparison --------------------------------------------------------
st.subheader("All-industry comparison")
st.caption("Same shared inputs (country, ERP, debt share, tax, yields, spread) applied to every "
           "industry's unlevered beta.")
rows = []
for ind, bu in sorted(betas.items()):
    u = usd_costs(y_us, bu, erp, tax, debt_share, bbb_d)
    f = foreign_costs(u, y_us, y_f, tax, debt_share, f_spread_override)
    rows.append({
        "Industry": ind,
        "Unlevered beta": round(bu, 3),
        "Levered beta": round(u["beta_l"], 3),
        "Cost of equity (USD)": u["ke"],
        "WACC (USD)": u["wacc"],
        f"Cost of equity ({cur})": f["ke"],
        f"WACC ({cur})": f["wacc"],
    })
all_df = pd.DataFrame(rows).sort_values("WACC (USD)", ascending=False).reset_index(drop=True)
show_df = all_df.copy()
for col in show_df.columns:
    if "equity" in col or "WACC" in col:
        show_df[col] = show_df[col].map(pct)
st.dataframe(show_df, use_container_width=True, hide_index=True)
st.download_button("⬇️ Download all-industry table (CSV)",
                   all_df.to_csv(index=False).encode(),
                   file_name=f"all_industry_cost_of_capital_{country}.csv".replace(" ", "_"),
                   mime="text/csv")

# ---- sources ------------------------------------------------------------------------
with st.expander("Sources", expanded=False):
    st.markdown(f"""
1. **Unlevered industry betas** — Aswath Damodaran (NYU Stern):
   [betas.xls]({SOURCES['betas']}) · {_src_badge(m_betas)}
2. **BBB (Baa2) default spread** — Aswath Damodaran (NYU Stern):
   [ratings.html]({SOURCES['ratings']}) · {_src_badge(m_bbb)}
3. **Statutory corporate tax rates** — OECD Corporate Tax Statistics 2024:
   [source]({SOURCES['oecd']}) · {_src_badge(m_tax)}
4. **10-year government bond yields** — Trading Economics:
   [tradingeconomics.com/bonds]({SOURCES['bonds']}) · {_src_badge(m_yields)}

🟡 *fallback* means the live fetch was blocked (common on networks that block these hosts);
the bundled, dated value is shown instead. Use **Refresh** on an open network, or type the
current figure into the matching override.
""")
