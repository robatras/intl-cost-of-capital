# International Cost of Capital

A Streamlit app that compares a project's **cost of equity, cost of debt, and WACC**
side by side in **USD** and in the **local currency of a selected country**.

## What it does

- Pulls live data from four sources (with a dated fallback and manual overrides for each):
  1. **Unlevered industry betas** — Aswath Damodaran, NYU Stern (`betas.xls`)
  2. **BBB (Baa2) corporate default spread** — Aswath Damodaran, NYU Stern (`ratings.html`)
  3. **Statutory corporate tax rates** — OECD Corporate Tax Statistics 2024
  4. **10-year government bond yields** — Trading Economics
- Lets you choose an **industry** and **country**, set the **equity risk premium** and
  **target debt share**, pick the **project tax rate** (U.S. or selected country), and
  **override** any fetched value (unlevered beta, tax rates, U.S. BBB spread, both
  countries' 10y yields, and the foreign BBB spread).
- Shows **USD vs local-currency** results side by side, a full **all-industry comparison**,
  an **assumptions** panel, and **CSV downloads**.
- A **Refresh** button clears the cache and re-fetches.

Default view: Software (System & Application), United States, U.S. tax rate, 6.5% ERP, 30% debt.

## Formulas

Lever the beta to the target structure, then build each cost of capital:

```
D/E            = w_d / (1 - w_d)
beta_L         = beta_U * [1 + (1 - t) * D/E]

k_e(USD)       = y_US + beta_L * ERP
k_d,pre(USD)   = y_US + BBB_spread
k_d,after(USD) = k_d,pre(USD) * (1 - t)
WACC(USD)      = w_e * k_e(USD) + w_d * k_d,after(USD)
```

Translate a USD rate into the foreign currency with the 10-year risk-free differential
(international Fisher):

```
r(FX) = (1 + r_USD) * (1 + y_foreign) / (1 + y_US) - 1

k_e(FX)     = convert( k_e(USD) )
k_d,pre(FX) = convert( k_d,pre(USD) )          # implied auto foreign spread = k_d,pre(FX) - y_foreign
              OR  y_foreign + manual_spread    # when the foreign-spread override is on
WACC(FX)    = w_e * k_e(FX) + w_d * k_d,pre(FX) * (1 - t)
```

The same project tax rate, debt share, and ERP are used on both sides. Beta is a risk
measure, so the same levered beta applies in both currencies. **If your class formulas
differ, edit the `usd_costs` / `foreign_costs` / `convert_rate` functions in `app.py`.**

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Community Cloud

1. Create a public GitHub repo and add `app.py`, `requirements.txt`, `README.md`.
2. Go to https://share.streamlit.io, "New app", pick the repo/branch, main file `app.py`.
3. Deploy. Streamlit Cloud has open internet, so the live fetch works there even if it is
   blocked on your local network.

## Verification (built in by design)

- **Equal 10y yields -> equal results** when shared parameters are fixed and the foreign
  spread override is off (the currency-conversion factor becomes 1).
- **Zero debt -> WACC equals the cost of equity.**
- **Overriding the foreign BBB spread leaves the USD results unchanged** (USD never uses it).
- An independent spreadsheet/calculator check of one example is in the submission note.

## Note on fallbacks

If a source host is unreachable, the app shows a dated **fallback** value (🟡) instead of a
**live** one (🟢) and keeps working. Hit **Refresh** on an open network, or type the current
number into the matching override field.
