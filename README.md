# PV Monte Carlo Scaler

A Streamlit app that takes a PVsyst report and/or an hourly 8760 PV profile, explains what it found, and scales
the profile over a range of project years using module degradation and Monte Carlo weather variability.
It produces PVsyst-style PDF reports (single year or all years combined) and 8760 files ready for a
CFE / BESS dispatch model. Technical analysis only; no financial calculations.

## What it does

1. **Load PV data.** PVsyst report (`.pdf`) for capacity, grid limit, monthly table, P50/P90 and variability;
   hourly 8760 (`.csv`, `.txt`, `.xlsx`). 15- and 30-minute data are averaged to hourly; leap years drop 29 February.
2. **What's in your data.** Picks the full-year column (E_Grid first), detects units, compares the file with the
   report, and shows monthly energy and an hourly heatmap. Negative night values are treated as auxiliary import.
3. **Scale to a range of years.** Project year 1 (COD), from/to years, degradation, weather σ, modelling
   uncertainty, runs, and advanced options (monthly σ, day resampling window, output limit, MV-bus factor, seed).
4. **Results.** All-years totals (multi-year P50/P90), a P90–P10 chart by year, an exact year-by-year table, and a
   detailed view of any single year.
5. **Report.** Live preview and PDF download: single-year report (4 pages) or lifetime report (6 pages, plus an
   optional appendix with each year's Main results page).

Downloads: report PDF, 8760 for one year (base, degradation-only, P50/P75/P90/P95, aux import), multi-year 8760
(P50 + P90 per year), annual summary CSV, runs CSV, and an Excel workbook.

## Method

- **Degradation:** year N = base × (1 − d·(N−1)) (linear) or (1 − d)^(N−1) (compound). The base file is year 1.
- **Weather:** each simulated year draws its own annual factor (σ from the PVsyst report), independent between years.
- **Modelling uncertainty:** one factor per run, applied to every year of that run (σ_u = √(σ_total² − σ_weather²)),
  so a single year keeps PVsyst's total σ while multi-year totals average weather out correctly.
- **Monthly pattern and daily shapes:** monthly σ redistributes energy between months; whole days are resampled
  within the same month, so hourly shapes always come from the source data.
- **Limits:** hours above the output limit are clipped; night auxiliary load is never scaled.
- **P-levels:** P90 = exceeded in 90 % of runs. Multi-year P-levels come from each run's total over the range.

The engine is a port of the browser version of the tool and uses the same random number generator, so the same
inputs and seed give the same numbers. `tests/test_engine.py` checks this against reference output from the
browser engine.

## Run locally (PyCharm or terminal)

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

In PyCharm: open the folder, set the interpreter to the `.venv` above, then create a Run configuration with
**Module name** `streamlit` and **Parameters** `run app.py`.

Run the tests with `pip install -r requirements-dev.txt` and `pytest -q`.

## Sign-in (Streamlit secrets)

The app opens on a sign-in page. Users are read from Streamlit secrets, never from the repository.
If no users are set up, the app stays locked and shows the setup steps.

```toml
[auth]
session_timeout_minutes = 480   # optional: sign out after 8 h without activity
max_attempts = 5                # optional: failed sign-ins before a 60 s pause
lockout_seconds = 60

[passwords]
gangaraju = "change-me"                       # plain password, or
engineer1 = "pbkdf2_sha256$260000$...$..."    # hash from: python tools/hash_password.py

[names]
gangaraju = "Gangaraju P"
```

- **Locally:** copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and edit it. That file is git-ignored.
- **Streamlit Cloud:** paste the same block into the app's **Settings → Secrets**. Changes apply within a minute,
  with no redeploy.
- To add or remove a user, edit the `[passwords]` section. Usernames are not case-sensitive.
- `enabled = false` under `[auth]` turns sign-in off (for local testing only).

## Deploy on Streamlit Community Cloud

1. Push this folder to a GitHub repository (private is recommended if you add client data).
2. Go to share.streamlit.io, choose **Create app → Deploy a public app from GitHub**, pick the repository and
   branch, and set **Main file path** to `app.py`.
3. Under **Advanced settings**, choose **Python 3.12** and paste your secrets block (see Sign-in above).
4. Click **Deploy**. Later pushes to the branch redeploy automatically.

## Project layout

```
app.py                  Streamlit user interface
pvscaler/engine.py      Monte Carlo engine (NumPy)
pvscaler/parsing.py     PVsyst PDF and 8760 CSV/Excel reading
pvscaler/context.py     report data, CSV and Excel exports
pvscaler/report.py      PDF reports (reportlab) and page previews (pypdfium2)
pvscaler/auth.py        sign-in page backed by Streamlit secrets
pvscaler/passwords.py   password hashing (PBKDF2-SHA256, standard library)
tools/hash_password.py  makes a password hash to paste into secrets
tests/                  parity and smoke tests
sample_data/            optional sample files (ignored by Git by default)
```

## Notes

- Reports state that they are derived from PVsyst outputs and are not PVsyst simulations.
- Uploaded files are processed in memory for the session and are not stored by the app.
