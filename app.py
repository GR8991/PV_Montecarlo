"""PV Monte Carlo Scaler — Streamlit app.

Load a PVsyst report and/or an hourly 8760 PV file, check what was read,
scale the profile over a range of project years (degradation + Monte Carlo
weather), review results, and download PVsyst-style reports and 8760 files.

Run locally:  streamlit run app.py
"""
from __future__ import annotations

import dataclasses
import hashlib
import io
from pathlib import Path

import streamlit as st

try:
    import numpy as np
    import pandas as pd
    import plotly.graph_objects as go
    from pvscaler import auth
    from pvscaler.ui import full
except ModuleNotFoundError as _e:  # dependencies missing on the server
    st.set_page_config(page_title="PV Monte Carlo Scaler")
    st.error(f"Package '{_e.name}' is not installed, so the app cannot start.")
    st.markdown("Check that **requirements.txt** is in the root of the GitHub repository (next to app.py), "
                "then open **Manage app → Reboot**. If it still fails, the install log under **Manage app** shows "
                "which package could not be installed.")
    st.stop()
from pvscaler import context as X
from pvscaler import engine as E
from pvscaler import parsing as Pp
from pvscaler import report as R

# all files in the repo must come from the same release; name the stale one if not
_STALE = [f for f, ok in (("pvscaler/parsing.py", hasattr(Pp, "check_units_against_report") and hasattr(Pp, "read_report")),
                          ("pvscaler/engine.py", getattr(E, "ENGINE_VERSION", "") == "2026.11.23-4"),
                          ("pvscaler/context.py", hasattr(X, "year_ctx") and hasattr(X, "excel_workbook")),
                          ("pvscaler/report.py", hasattr(R, "build_report") and hasattr(R, "render_page_png"))) if not ok]
if _STALE:
    st.set_page_config(page_title="PV Monte Carlo Scaler")
    st.error("These files in the GitHub repository are from an older version: " + ", ".join(_STALE)
             + ". Replace them with the files from the latest zip (keep app.py and the pvscaler folder from the same zip), then reboot the app.")
    st.stop()

APP_DIR = Path(__file__).parent
SAMPLE_DIR = APP_DIR / "sample_data"
MAX_YEARS = 50
LIMIT = 250_000  # runs x years
ACC, BLUE, INK = "#C27400", "#2C69A8", "#16202B"

st.set_page_config(page_title="PV Monte Carlo Scaler", layout="wide")
CURRENT_USER = auth.require_login()  # shows the sign-in page and stops until signed in


# ==========================================================================
# cached helpers
# ==========================================================================
@st.cache_data(show_spinner=False)
def parse_pdf(data: bytes, version: str = ""):
    return Pp.read_report(data)


@st.cache_data(show_spinner=False)
def parse_data_file(name: str, data: bytes, version: str = ""):
    return Pp.candidates_from_file(name, data)


@st.cache_data(show_spinner=False, max_entries=8)
def run_sim(series: np.ndarray, cfg_tuple: tuple, version: str = ""):
    cfg = E.Config(*cfg_tuple)
    return E.simulate_range(series, cfg)


def _key(*parts) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(p.tobytes() if isinstance(p, np.ndarray) else repr(p).encode())
    return h.hexdigest()


@st.cache_data(show_spinner=False, max_entries=16)
def build_pdf(key: str, _sim, _series, _report, meta: dict, scope: str, year: int, appendix: bool):
    ctx = X.report_context(_sim, _series, _report, meta, scope=scope, year=year, appendix=appendix)
    return R.build_report(ctx, with_labels=True)


@st.cache_data(show_spinner=False, max_entries=64)
def page_png(key: str, pdf: bytes, index: int):
    return R.render_page_png(pdf, index, scale=1.8)


@st.cache_data(show_spinner=False, max_entries=16)
def csv_year(key: str, _sim, _series, year: int) -> bytes:
    return X.csv_8760_year(_sim, _series, year).to_csv(index=False).encode()


@st.cache_data(show_spinner=False, max_entries=4)
def csv_multi(key: str, _sim, _series) -> bytes:
    return X.csv_8760_multi(_sim, _series).to_csv(index=False).encode()


@st.cache_data(show_spinner=False, max_entries=4)
def xlsx(key: str, _sim, _series, _report, meta: dict, include_8760: bool) -> bytes:
    return X.excel_workbook(_sim, _series, _report, meta, include_8760)


def fmt(v, dp=0):
    try:
        return f"{float(v):,.{dp}f}"
    except (TypeError, ValueError):
        return "–"


# ==========================================================================
# header
# ==========================================================================
st.title("PV Monte Carlo Scaler")
st.caption("Load a PVsyst report or an hourly 8760 PV profile. The app reads it, explains what it found, then scales the profile "
           "over a range of project years using module degradation and Monte Carlo weather variability. Technical analysis only, no financials.")

# ==========================================================================
# 1. load data
# ==========================================================================
st.header("1 · Load PV data")
uploads = st.file_uploader("PVsyst report (.pdf) for system data and P50/P90, plus the hourly 8760 (.csv, .txt, .xlsx). One or both.",
                           type=["pdf", "csv", "txt", "tsv", "xlsx", "xlsm"], accept_multiple_files=True)

files: list[tuple[str, bytes]] = [(u.name, u.getvalue()) for u in uploads] if uploads else []
sample_files = sorted(p for p in SAMPLE_DIR.glob("*") if p.suffix.lower() in (".pdf", ".csv", ".txt", ".xlsx", ".xlsm")) if SAMPLE_DIR.exists() else []
if not files and sample_files:
    if st.toggle("Use the sample files in sample_data/", value=True, key="use_sample"):
        files = [(p.name, p.read_bytes()) for p in sample_files]
        st.caption("Using sample: " + ", ".join(p.name for p in sample_files))

report, cands, series_file, problems = None, [], None, []
for name, data in files:
    ext = name.rsplit(".", 1)[-1].lower()
    try:
        if ext == "pdf":
            rep = parse_pdf(data, E.ENGINE_VERSION)   # version in the cache key: no stale objects after an update
            if not rep.is_pvsyst or rep.found < 3:
                problems.append(f"{name}: this PDF does not look like a PVsyst simulation report, so no system data was read.")
            else:
                report = rep
        else:
            cs = parse_data_file(name, data, E.ENGINE_VERSION)
            if cs:
                cands, series_file = cs, name
            else:
                problems.append(f"{name}: no column with a full year of hourly (8760), 30-minute or 15-minute values was found.")
    except Exception as e:  # noqa: BLE001
        problems.append(f"{name}: could not be read ({e}).")
for p in problems:
    st.warning(p)

if not report and not cands:
    st.info("Nothing loaded yet. Upload a PVsyst report, an hourly file, or both." + (" Or switch on the sample above." if sample_files else ""))
    st.stop()

# ==========================================================================
# 2. what's in your data
# ==========================================================================
st.header("2 · What's in your data")
F = report.fields if report else {}

# defaults from the report, applied once per loaded file set
sig = _key(tuple(n for n, _ in files), F.get("project"), len(cands))
if st.session_state.get("_loaded") != sig:
    st.session_state["_loaded"] = sig
    st.session_state["deg"] = float(F.get("degradation_pct") or 0.45)
    st.session_state["sig_w"] = float(F.get("weather_sigma_pct") or 5.0)
    st.session_state["sig_t"] = float(F.get("total_sigma_pct") or 7.4)
    st.session_state["col_idx"] = 0
    st.session_state["unit_sel"] = "Auto"
    st.session_state["add_back"] = True
    st.session_state["synthetic"] = False
    st.session_state.pop("cap", None)
    st.session_state.pop("pnom", None)

if report and getattr(report, "table_note", None):
    st.info(report.table_note)
if report:
    if F.get("pnom_note"):
        st.warning(F["pnom_note"])
    F["pnom_mwp"] = st.number_input("DC capacity (MWp)", 0.0, 100000.0, value=float(F.get("pnom_mwp") or 0.0), step=0.1, key="pnom",
                                    help="Read from the PVsyst report. Used for specific production and PR; correct it here if needed.") or None
series, unit_info, notes, column_label, lim, eg_series = None, None, [], "-", None, None
if cands:
    c1, c2, c3 = st.columns([2, 1, 2])
    labels = [c.label for c in cands]
    idx = c1.selectbox("Column to use", range(len(cands)), format_func=lambda i: labels[i], key="col_idx",
                       help=f"Found {len(cands)} full-year column(s); E_Grid is ranked first.")
    cand = cands[idx]
    unit_sel = c2.selectbox("Units in file", ["Auto", "W", "kW", "MW"], key="unit_sel", help="Values are treated as average power per hour.")
    pnom = F.get("pnom_mwp")
    fct, unit, guessed = Pp.unit_factor(cand.unit if unit_sel == "Auto" else unit_sel, cand.vmax, pnom)
    unit_note = None
    if unit_sel == "Auto":
        fct, unit, unit_note = Pp.check_units_against_report(cand.values, fct, unit, F.get("annual_mwh"))
        if unit_note:
            guessed = False
            st.warning(unit_note)
    if unit_sel != "Auto":
        guessed = False
    unit_info = (unit, guessed)
    series, notes = Pp.to_hourly(cand.values * fct)
    eg_series = series.copy()
    lim = next((x for x in cands if Pp.is_grid_lim(x) and x.sheet == cand.sheet and x.length == cand.length), None)
    if lim is not None:
        if c3.checkbox(f"Scale the unclipped power (E_Grid + {lim.name}) and re-apply the grid limit (recommended)", key="add_back",
                       help="E_Grid is already clipped at the grid limit. Scaling it directly understates hours at the limit in poor-weather runs."):
            lf, _, _ = Pp.unit_factor(lim.unit or unit, lim.vmax, pnom)
            series = series + np.maximum(0, Pp.to_hourly(lim.values * lf)[0])
    column_label = (cand.sheet + " > " if cand.sheet else "") + cand.name
elif report and report.monthly:
    st.info("To run the simulation I need the hourly 8760 export (PVsyst Output › Hourly results as CSV, or your post-processed Excel). "
            "If you don't have it, a synthetic 8760 can be built from the monthly table for screening.")
    if st.button("Build synthetic 8760 from monthly values") or st.session_state.get("synthetic"):
        st.session_state["synthetic"] = True
        series = Pp.synth_8760(report.monthly, F.get("lat"), F.get("lon"), F.get("tz"), F.get("grid_limit_mw"), True)
        notes, unit_info, column_label = ["Synthetic profile built from the monthly E_Grid table"], ("MW", False), "synthetic"
synthetic = bool(st.session_state.get("synthetic")) and not cands

if series is not None and eg_series is None:
    eg_series = series
prof = E.profile(eg_series) if series is not None else None          # the file as delivered (E_Grid)
unclipped = E.profile(series)["annual"] if (series is not None and st.session_state.get("add_back") and lim is not None) else None

# narrative
parts = []
if report:
    parts.append(f"The PVsyst report is for **{F.get('project') or 'an unnamed project'}**"
                 + (f", a **{fmt(F['pnom_mwp'], 1)} MWp** system" if F.get("pnom_mwp") else "")
                 + (f" with a **{fmt(F['grid_limit_mw'], 1)} MW** grid limit" if F.get("grid_limit_mw") else "")
                 + (f". It predicts **{fmt(F['annual_mwh'])} MWh** in simulation year {F.get('sim_year') or 1}" if F.get("annual_mwh") else "")
                 + (f", with P90 at {fmt(F['p90_mwh'] / 1000, 1)} GWh." if F.get("p90_mwh") else "."))
if prof:
    src = "The synthetic hourly profile" if synthetic else f"The hourly column **{column_label}**" + (" (with grid-limited energy added back, so the grid limit is re-applied after scaling)" if st.session_state.get("add_back") else "")
    parts.append(f"{src} gives **{fmt(prof['annual'])} MWh** of generation, peaking at **{fmt(prof['peak'], 1)} MW**"
                 + (f" ({fmt(prof['annual'] / F['pnom_mwp'])} kWh/kWp)" if F.get("pnom_mwp") else "")
                 + f" and producing in {fmt(prof['prod_hours'])} hours of the year.")
    if unclipped is not None:
        parts.append(f"Adding the grid-limited energy gives {fmt(unclipped)} MWh of unclipped power; the tool scales this and re-applies the grid limit.")
    if prof["hours_at_peak"] > 50:
        parts.append(f"Output sits at its {fmt(prof['peak'], 1)} MW ceiling for {fmt(prof['hours_at_peak'])} hours, which looks like a POI or inverter limit.")
    if prof["aux"] < -1:
        parts.append(f"Night values are negative ({fmt(-prof['aux'])} MWh/yr, about {fmt(-min(series), 2)} MW). These are transformer no-load (iron) losses supplied by the grid; "
                     "they are not scaled, and results are reported as net E_Grid like PVsyst (generation minus these losses).")
st.markdown(" ".join(parts))

kv = []
if report:
    kv += [("DC capacity", f"{fmt(F.get('pnom_mwp'), 1)} MWp"), ("Grid limit", f"{fmt(F.get('grid_limit_mw'), 1)} MWac"), ("PVsyst E_Grid", f"{fmt(F.get('annual_mwh'))} MWh"),
           ("Specific yield", f"{fmt(F.get('specific_yield'))} kWh/kWp"), ("PVsyst P50 / P90", f"{fmt((F.get('p50_mwh') or 0) / 1000, 1)} / {fmt((F.get('p90_mwh') or 0) / 1000, 1)} GWh"),
           ("Weather σ / total σ", f"{fmt(F.get('weather_sigma_pct'), 1)} / {fmt(F.get('total_sigma_pct'), 1)} %"), ("Degradation", f"{fmt(F.get('degradation_pct'), 2)} %/yr"), ("Location", f"{fmt(F.get('lat'), 3)}, {fmt(F.get('lon'), 3)}")]
if prof:
    kv += [("Hourly generation", f"{fmt(prof['annual'])} MWh"), ("Peak output", f"{fmt(prof['peak'], 1)} MW"),
           ("Capacity factor (AC)", f"{fmt(prof['annual'] / ((F.get('grid_limit_mw') or prof['peak']) * 8760) * 100, 1)} %"), ("Night import (transformer losses)", f"{fmt(-prof['aux'])} MWh/yr")]
for i in range(0, len(kv), 5):
    cols = st.columns(5)
    for col, (k, v) in zip(cols, kv[i:i + 5]):
        col.metric(k, v)

if prof:
    g1, g2 = st.columns(2)
    fig = go.Figure(go.Bar(x=[m[:3] for m in E.MONTHS], y=prof["monthly"], name="Hourly file", marker_color=ACC))
    if report and report.monthly:
        fig.add_trace(go.Scatter(x=[m[:3] for m in E.MONTHS], y=report.monthly, mode="markers", name="Report E_Grid", marker=dict(color=BLUE, size=9)))
    fig.update_layout(title="Monthly generation, base year (MWh)", height=320, margin=dict(l=10, r=10, t=40, b=10), legend=dict(orientation="h", y=-0.15))
    g1.plotly_chart(fig, **full(st.plotly_chart))
    hm = go.Figure(go.Heatmap(z=np.asarray(series).reshape(365, 24).T, colorscale=[[0, "#eef1f5"], [1, ACC]], zmin=0, colorbar=dict(title="MW")))
    hm.update_layout(title="Hourly output map (day of year × hour)", height=320, margin=dict(l=10, r=10, t=40, b=10), xaxis_title="Day of year", yaxis_title="Hour")
    g2.plotly_chart(hm, **full(st.plotly_chart))

    checks = [("green", "8,760 hourly values" + (f" ({'; '.join(notes)})" if notes else ""))]
    if F.get("pnom_mwp"):
        sy = (prof["annual"] + prof["aux"]) / F["pnom_mwp"]
        if not 400 <= sy <= 3500:
            checks.append(("red", f"Specific production would be {fmt(sy)} kWh/kWp, which is not plausible. Check the units of the hourly file "
                                  f"(currently {unit_info[0] if unit_info else '?'}) and the DC capacity ({fmt(F['pnom_mwp'], 3)} MWp)."))
    if unit_info and unit_info[1]:
        checks.append(("orange", f"Units not labelled in the file; assumed {unit_info[0]} from the size of the numbers. Change it above if wrong."))
    if synthetic:
        checks.append(("orange", "Synthetic profile: monthly totals match the report, but hourly shapes and cloudy days are modelled, not measured. Use for screening only."))
    if F.get("annual_mwh"):
        net = prof["annual"] + prof["aux"]
        dpc = (net - F["annual_mwh"]) / F["annual_mwh"] * 100
        checks.append(("green" if abs(dpc) < 1 else "orange", f"Hourly net energy {fmt(net)} MWh vs report {fmt(F['annual_mwh'])} MWh ({dpc:+.2f}%)"
                       + (", the file matches the report." if abs(dpc) < 1 else ". The file may be post-processed or from a different variant.")))
    if unclipped is not None:
        checks.append(("blue", f"Scaling uses the unclipped power ({fmt(unclipped)} MWh/yr, peak {fmt(float(np.max(series)), 1)} MW) and re-applies the grid limit, "
                               "so year 1 without weather variation reproduces E_Grid exactly."))
    if F.get("sim_year") == 1:
        checks.append(("blue", "PVsyst simulated year 1, so the file already includes year-1 degradation. Year N applies N−1 further years."))
    elif F.get("sim_year"):
        checks.append(("orange", f"The report was simulated for year {F['sim_year']}, not year 1."))
    if not report:
        checks.append(("blue", "No PVsyst report loaded. Weather σ and degradation use typical defaults; add the report PDF to use its values."))
    st.markdown("**Checks**\n\n" + "\n".join(f"- :{c}[●] {t}" for c, t in checks))

if series is None:
    st.stop()

# ==========================================================================
# 3. scale to a range of years
# ==========================================================================
series_sig = _key(np.asarray(series, dtype=float))
if st.session_state.get("_cap_for") != series_sig:
    st.session_state["_cap_for"] = series_sig
    st.session_state.pop("cap", None)
st.header("3 · Scale to a range of years")
st.markdown("**Which years should this PV profile be scaled to?**")
a1, a2, a3, a4 = st.columns(4)
cod = a1.number_input("Project year 1 is (COD)", 1990, 2100, 2027, 1, key="cod")
y0 = a2.number_input("From project year", 1, MAX_YEARS, 1, 1, key="y0")
y1 = a3.number_input("To project year", 1, MAX_YEARS, 25, 1, key="y1")
if y1 < y0:
    st.error("'To' year must be the same as or after the 'From' year.")
    st.stop()
a4.markdown(f"<br>Calendar years **{cod + y0 - 1}–{cod + y1 - 1}**  \n{y1 - y0 + 1} year(s)", unsafe_allow_html=True)

b1, b2, b3, b4 = st.columns(4)
deg = b1.number_input("PV degradation (%/yr)", 0.0, 3.0, step=0.05, key="deg", help="From the PVsyst report when loaded")
deg_model = b2.selectbox("Degradation model", ["linear", "compound"], key="deg_model", help="Base file is treated as year 1")
sig_w = b3.number_input("Year-to-year weather σ (%)", 0.0, 30.0, step=0.1, key="sig_w", help="From the PVsyst report (weather data) when loaded")
runs = b4.selectbox("Monte Carlo runs", [500, 1000, 2000, 5000, 10000], index=1, key="runs")
u1, u2 = st.columns([3, 1])
use_unc = u1.checkbox("Include PVsyst modelling uncertainty (module, inverter, soiling, satellite data). It is applied once per run to every year.", value=True, key="use_unc")
sig_t = u2.number_input("Total σ (%)", 0.0, 40.0, step=0.1, key="sig_t", disabled=not use_unc)
with st.expander("Advanced: weather pattern and power limits"):
    d1, d2, d3, d4, d5 = st.columns(5)
    sig_m = d1.number_input("Month-to-month σ (%)", 0.0, 40.0, 8.0, 0.5, key="sig_m")
    win = d2.number_input("Day resampling window (± days)", 0, 15, 7, 1, key="win")
    if F.get("grid_limit_mw"):
        cap_default = float(F["grid_limit_mw"])
    elif st.session_state.get("add_back") and lim is not None:
        cap_default = float(round(float(np.max(cand.values * fct)), 3))   # E_Grid's own ceiling
    else:
        cap_default = float(round(float(np.max(series)), 3))
    cap = d3.number_input("Grid (POI) limit (MW)", 0.0, value=cap_default, step=0.1, key="cap",
                          help="Output limit applied after scaling. Default: grid limit from the PVsyst report. Raise it if the limit does not apply at your measuring point.")
    seed = d5.number_input("Random seed", 1, 2**31 - 1, 2026, 1, key="seed")
hv = F.get("hv_loss_pct")
ref_opts = ["Grid connection point (as PVsyst E_Grid)", "34.5 kV MV bus (before HV transformer)", "Custom factor"]
m1, m2, m3 = st.columns([2, 2, 1])
ref_point = m1.selectbox("Energy measured at", ref_opts, key="ref_point",
                         help="PVsyst E_Grid is at the grid connection point, after the HV transformer. The CFE model uses the MV collector bus.")
mode_lbl = m2.selectbox("Weather variation applied to", ["Energy (PVsyst P50/P90 method)", "Sunlight, then grid limit (physical)"], key="mode_lbl",
                        help="PVsyst applies its year-to-year variability to annual energy. The physical option scales sunlight and re-applies the grid limit, so poor years lose less where output was limited.")
mode = "energy" if mode_lbl.startswith("Energy") else "resource"
if ref_point.startswith("Grid"):
    L = 1.0
elif ref_point.startswith("34.5"):
    L = 1 / (1 - (hv if hv else 0.8) / 100)
    m3.metric("Factor", f"{L:.4f}", help=f"1 / (1 − {fmt(hv if hv else 0.8, 1)}% HV transformer loss{' from the report' if hv else ', typical value'})")
else:
    L = m3.number_input("Factor", 0.5, 1.2, 1.0, 0.0001, format="%.4f", key="L_custom", help="Multiplier applied to generation, e.g. 0.973832 / 0.992 = 0.9817")

n_years = int(y1 - y0 + 1)
if n_years * runs > LIMIT:
    st.error(f"{n_years} years × {runs:,} runs is above the {LIMIT:,} limit. Reduce the runs or the range.")
    st.stop()
if E.deg_factor(int(y1), deg, deg_model) <= 0:
    st.error(f"Degradation reaches 100% before year {y1}. Lower the rate or the range.")
    st.stop()

cfg = E.Config(cod=int(cod), y0=int(y0), y1=int(y1), deg=float(deg), deg_model=deg_model, sig_w=float(sig_w), use_unc=bool(use_unc), sig_t=float(sig_t),
               sig_m=float(sig_m), win=int(win), runs=int(runs), seed=int(seed), cap=float(cap), L=float(L), mode=mode)
series = np.asarray(series, dtype=float)
with st.spinner(f"Simulating {n_years} year(s) × {runs:,} runs…"):
    sim = run_sim(series, dataclasses.astuple(cfg), E.ENGINE_VERSION)
sim_key = _key(series, dataclasses.astuple(cfg))
A = sim["agg"]
years = sim["years"]
meta = dict(project=F.get("project") or "", series_file=series_file or ("synthetic from monthly" if synthetic else "-"), column=column_label,
            units=unit_info[0] if unit_info else "-", add_back=bool(st.session_state.get("add_back")), synthetic=synthetic)

# ==========================================================================
# 4. results
# ==========================================================================
st.header(f"4 · Results for years {y0}–{y1} ({cfg.cal(y0)}–{cfg.cal(y1)})" if n_years > 1 else f"4 · Results for year {y0} ({cfg.cal(y0)})")
st.caption(f"Engine {E.ENGINE_VERSION} · years {y0}–{y1} · runs {runs} · seed {seed} · σ weather {sig_w:g}% / total {(f'{sig_t:g}%' if use_unc else 'off')} · "
           f"monthly {sig_m:g}% · window ±{win} d · limit {cap:g} MW · factor {L:.4f} · method {mode} · degradation {deg:g}%/yr {deg_model} · "
           f"input {fmt(np.maximum(series, 0).sum())} MWh")
if synthetic:
    st.warning("Based on a synthetic profile. Treat as screening results.")

st.subheader(f"All {n_years} years combined" if n_years > 1 else "Selected year")
tiles = ([("Total P50", A["P"][50], f"years {y0}–{y1}"), ("Total P90", A["P"][90], f"multi-year · {A['P'][90] / A['P'][50] * 100:.1f}% of P50"),
          ("Average year P50", A["P"][50] / n_years, "per year"), ("Average year P90", A["P"][90] / n_years, "multi-year, per year"),
          ("Sum of single-year P90s", A["sum_p90"], "for comparison")] if n_years > 1 else
         [("P50", A["P"][50], f"year {y0}"), ("P90", A["P"][90], f"{A['P'][90] / A['P'][50] * 100:.1f}% of P50")])
for col, (k, v, sub) in zip(st.columns(len(tiles)), tiles):
    col.metric(k, f"{fmt(v / 1000, 0 if v > 1e6 else 1)} GWh", help=f"{fmt(v)} MWh · {sub}")
    col.caption(f"{fmt(v)} MWh · {sub}")

yrs = [r.year for r in years]
fan = go.Figure()
fan.add_trace(go.Bar(x=yrs, y=[(r.P[10] - r.P[90]) / 1000 for r in years], base=[r.P[90] / 1000 for r in years], name="P90 to P10",
                     marker=dict(color="rgba(194,116,0,0.22)", line=dict(color=ACC, width=1)), hovertemplate="Year %{x}<br>P90–P10 range<extra></extra>"))
fan.add_trace(go.Scatter(x=yrs, y=[r.P[50] / 1000 for r in years], name="P50", mode="lines+markers", line=dict(color=ACC, width=3)))
fan.add_trace(go.Scatter(x=yrs, y=[r.P[90] / 1000 for r in years], name="P90", mode="lines", line=dict(color=INK, width=1.5, dash="dot")))
fan.add_trace(go.Scatter(x=yrs, y=[r.det / 1000 for r in years], name="Degradation only", mode="lines", line=dict(color=BLUE, width=1.5, dash="dash")))
fan.update_layout(title="Annual net E_Grid by year (GWh)", height=380, margin=dict(l=10, r=10, t=40, b=10), xaxis_title="Project year",
                  yaxis_title="GWh", legend=dict(orientation="h", y=-0.2), bargap=0.45)
st.plotly_chart(fan, **full(st.plotly_chart))

rows_tbl = [{"Project year": str(r.year), "Calendar": str(cfg.cal(r.year)), "Degradation factor": round(r.D, 4), "Degradation only (MWh)": r.det,
             "P50 (MWh)": r.P[50], "P75 (MWh)": r.P[75], "P90 (MWh)": r.P[90], "P95 (MWh)": r.P[95], "P90/P50 (%)": r.P[90] / r.P[50] * 100} for r in years]
if n_years > 1:
    rows_tbl.append({"Project year": "Range total", "Calendar": f"{cfg.cal(y0)}–{cfg.cal(y1)}", "Degradation factor": np.nan, "Degradation only (MWh)": A["det_sum"],
                     "P50 (MWh)": A["P"][50], "P75 (MWh)": A["P"][75], "P90 (MWh)": A["P"][90], "P95 (MWh)": A["P"][95], "P90/P50 (%)": A["P"][90] / A["P"][50] * 100})
table = pd.DataFrame(rows_tbl)
st.dataframe(table.style.format({c: "{:,.0f}" for c in table.columns if "MWh" in c} | {"P90/P50 (%)": "{:.1f}", "Degradation factor": "{:.4f}"}, na_rep=""),
             **full(st.dataframe), hide_index=True, height=min(420, 38 + 35 * len(table)))
st.caption("Each year is simulated separately. The total row gives P-levels of the range total across runs, not the sum of single-year values. "
           "All values are net E_Grid (generation minus night transformer losses), as in PVsyst. P50 equals the source simulation × degradation.")

st.subheader("Year in detail")
view = st.selectbox("Show year", yrs, index=min(len(yrs) - 1, yrs.index(10)) if 10 in yrs else len(yrs) - 1,
                    format_func=lambda y: f"Year {y} ({cfg.cal(y)})", key="view_year")
res = sim["by_year"][view]
aux_m = res.pre.aux_m
base = float(E.det_series(series, 1.0, cfg.cap, cfg.L).sum() + res.pre.aux_tot)   # year-1 net E_Grid
det = E.det_series(series, res.D, cfg.cap, cfg.L)
detp = E.profile(det)
detp["monthly"] = detp["monthly"] + aux_m
for col, (k, v) in zip(st.columns(5), [("Degradation only", res.det), ("P50", res.P[50]), ("P75", res.P[75]), ("P90", res.P[90]), ("P95", res.P[95])]):
    col.metric(k, f"{fmt(v / 1000, 1)} GWh", f"{(v / base - 1) * 100:+.1f}% vs year 1", delta_color="off")

h1, h2 = st.columns(2)
hist = go.Figure(go.Histogram(x=res.annual / 1000, nbinsx=40, marker_color=ACC, name="Runs"))
for p, colr in ((50, INK), (90, BLUE), (95, "#845BA6")):
    hist.add_vline(x=res.P[p] / 1000, line=dict(color=colr, dash="dash"), annotation_text=f"P{p}", annotation_position="top")
hist.update_layout(title=f"Annual net E_Grid across all runs, year {view} (GWh)", height=340, margin=dict(l=10, r=10, t=50, b=10), showlegend=False, xaxis_title="GWh", yaxis_title="Runs")
h1.plotly_chart(hist, **full(st.plotly_chart))
mq = res.monthly_q
band = go.Figure()
band.add_trace(go.Bar(x=[m[:3] for m in E.MONTHS], y=[q["p10"] - q["p90"] for q in mq], base=[q["p90"] for q in mq], name="P90 to P10",
                      marker=dict(color="rgba(194,116,0,0.22)", line=dict(color=ACC, width=1))))
band.add_trace(go.Scatter(x=[m[:3] for m in E.MONTHS], y=[q["p50"] for q in mq], mode="markers", name="P50", marker=dict(color=ACC, symbol="line-ew-open", size=22, line=dict(width=3))))
band.add_trace(go.Scatter(x=[m[:3] for m in E.MONTHS], y=detp["monthly"], mode="markers", name="Degradation only", marker=dict(color=BLUE, size=8)))
band.update_layout(title="Monthly net E_Grid range across runs (MWh)", height=340, margin=dict(l=10, r=10, t=50, b=10), legend=dict(orientation="h", y=-0.2))
h2.plotly_chart(band, **full(st.plotly_chart))

spread = (1 - res.P[90] / res.P[50]) * 100
msg = (f"Run spread in year {view}: P90 is {spread:.1f}% below P50 (single-year σ {cfg.sigma_single * 100:.1f}%, "
       f"{res.sd / res.mean * 100:.1f}% after clipping). Mean clipping in this year: {fmt(res.mean_clip)} MWh.")
if F.get("p90_mwh") and F.get("p50_mwh"):
    rs = (1 - F["p90_mwh"] / F["p50_mwh"]) * 100
    msg += f"  \n{'Consistent' if abs(rs - spread) < 1.2 else 'Differs'}: PVsyst P90 is {rs:.1f}% below its P50" + ("" if use_unc else " (PVsyst includes modelling uncertainty; tick the box above to compare like for like)") + "."
st.info(msg)

# ---- Main results (PVsyst layout) for the selected year ----
Ymr = X.year_ctx(res, series, cfg, report)
Tm = Ymr["main"]
pn = F.get("pnom_mwp")
st.subheader(f"Main results - project year {view} ({cfg.cal(view)})")
b1, b2 = st.columns(2)
for col, lbl, o in ((b1, "P50", Tm["p50"]), (b2, "P90", Tm["p90"])):
    with col.container(border=True):
        st.markdown(f"**{lbl}, project year {view}**")
        rows_mr = [("Produced energy", f"{fmt(o['egrid'])} MWh/year")]
        if Tm["has_irr"]:
            rows_mr += [("Specific production", f"{fmt(o['egrid'] / pn)} kWh/kWp/year"), ("Performance ratio PR", f"{o['pr'] * 100:.2f} %")]
        st.markdown("\n".join(f"- {k}: **{v}**" for k, v in rows_mr))
pr_ok = Tm["has_irr"] and 0.3 <= Tm["p50"]["pr"] <= 1.2 and all(0 <= m["pr"] <= 1.5 for m in Tm["months"])
mon3 = [m[:3] for m in E.MONTHS]
if Tm["has_irr"] and not pr_ok:
    st.warning(f"Charts not drawn: performance ratio {Tm['p50']['pr'] * 100:,.1f} % is not plausible. "
               f"Check the DC capacity ({fmt(pn, 3)} MWp) and the units of the hourly file (MW vs kW) in step 2.")
elif Tm["has_irr"]:
    c1m, c2m = st.columns(2)
    nf = go.Figure()
    for key, name, colr in (("yf", "Yf: produced useful energy", ACC), ("ls", "Ls: system loss", "#3F945C"), ("lc", "Lc: collection loss (PV array)", "#855CA6")):
        nf.add_trace(go.Bar(x=mon3, y=[max(0.0, m[key]) for m in Tm["months"]], name=name, marker_color=colr))
    nf.update_layout(barmode="stack", title="Normalized productions (kWh/kWp/day)", height=340, margin=dict(l=10, r=10, t=40, b=10), legend=dict(orientation="h", y=-0.2))
    c1m.plotly_chart(nf, **full(st.plotly_chart))
    pf = go.Figure(go.Bar(x=mon3, y=[m["pr"] for m in Tm["months"]], marker_color=BLUE, name="Monthly PR"))
    pf.add_hline(y=Tm["p50"]["pr"], line=dict(color=ACC, dash="dash"), annotation_text=f"Year {Tm['p50']['pr']:.3f}")
    pf.update_layout(title=f"Performance ratio PR (year {Tm['p50']['pr']:.3f})", height=340, margin=dict(l=10, r=10, t=40, b=10), yaxis=dict(range=[0, 1]))
    c2m.plotly_chart(pf, **full(st.plotly_chart))
if Tm["has_irr"]:
    mr = pd.DataFrame([{"Month": E.MONTHS[i], "GlobHor kWh/m²": m["glob_hor"], "DiffHor kWh/m²": m["diff_hor"], "T_Amb °C": m["tamb"], "GlobInc kWh/m²": m["glob_inc"],
                        "GlobEff kWh/m²": m["glob_eff"], "EArray MWh": m["earray"], "E_Grid MWh": m["egrid"], "PR": m["pr"], "E_Grid P90 MWh": m["p90net"], "vs yr 1 %": m["vs_base"]}
                       for i, m in enumerate(Tm["months"])])
    p5 = Tm["p50"]
    mr.loc[len(mr)] = ["Year", p5["glob_hor"], p5["diff_hor"], p5["tamb"], p5["glob_inc"], p5["glob_eff"], p5["earray"], p5["egrid"], p5["pr"], Tm["p90"]["egrid"], p5["vs_base"]]
    fmts = {"GlobHor kWh/m²": "{:,.1f}", "DiffHor kWh/m²": "{:,.2f}", "T_Amb °C": "{:.2f}", "GlobInc kWh/m²": "{:,.1f}", "GlobEff kWh/m²": "{:,.1f}",
            "EArray MWh": "{:,.0f}", "E_Grid MWh": "{:,.0f}", "PR": "{:.3f}", "E_Grid P90 MWh": "{:,.0f}", "vs yr 1 %": "{:+.1f}"}
else:
    mr = pd.DataFrame([{"Month": E.MONTHS[i], "Generation P50 MWh": m["p50"], "Night import MWh": -m["aux"], "E_Grid net P50 MWh": m["egrid"],
                        "E_Grid net P90 MWh": m["p90net"], "Degradation only MWh": m["det"], "vs yr 1 %": m["vs_base"]} for i, m in enumerate(Tm["months"])])
    fmts = {c: "{:,.0f}" for c in mr.columns if "MWh" in c} | {"vs yr 1 %": "{:+.1f}"}
st.markdown(f"**Balances and main results (P50, project year {view})**")
st.dataframe(mr.style.format(fmts), **full(st.dataframe), hide_index=True, height=38 + 35 * len(mr))
st.caption("Monthly values spread the annual P50 (and P90) in the degradation-only proportions. GlobHor, GlobInc, GlobEff: source TMY × P50 weather factor; "
           "DiffHor and T_Amb: source TMY; EArray: source × weather × degradation; E_Grid: net of night transformer losses; PR = E_Grid / (GlobInc × Pnom). Same as page 2 of the report.")

# ==========================================================================
# 5. report
# ==========================================================================
st.header("5 · Report")
r1, r2, r3 = st.columns([1.3, 1, 1.6])
scope = r1.radio("Report scope", ["All years combined", "Single year"], horizontal=True, key="scope")
scope_key = "all" if scope.startswith("All") else "year"
rep_year = r2.selectbox("Year", yrs, index=yrs.index(view), format_func=lambda y: f"Year {y} ({cfg.cal(y)})", key="rep_year", disabled=scope_key == "all")
appendix = r3.checkbox("Include each year's Main results page (appendix)", value=False, key="appendix", disabled=scope_key != "all")

with st.spinner("Building report…"):
    pdf, page_labels = build_pdf(_key(sim_key, scope_key, rep_year, appendix, F.get("project")), sim, series, report, meta, scope_key, int(rep_year), bool(appendix and scope_key == "all"))
fname = (f"{X.tag(report, meta)}_Lifetime_Yield_Report_Y{y0}-{y1}_{cfg.cal(y0)}-{cfg.cal(y1)}{'_with_appendix' if appendix else ''}.pdf" if scope_key == "all"
         else f"{X.tag(report, meta)}_Scaled_Yield_Report_Y{rep_year}_{cfg.cal(rep_year)}.pdf")

p1, p2 = st.columns([3, 1])
page = p1.selectbox("Page", range(len(page_labels)), format_func=lambda i: f"{i + 1} · {page_labels[i]}", key=f"page_{scope_key}_{len(page_labels)}")
p2.download_button("Download report (PDF)", pdf, file_name=fname, mime="application/pdf", type="primary", **full(st.download_button))
_, mid, _ = st.columns([1, 4, 1])
mid.image(page_png(_key(sim_key, scope_key, rep_year, appendix, page), pdf, int(page)), **full(st.image))

# ==========================================================================
# exports
# ==========================================================================
st.header("Downloads")
e1, e2, e3 = st.columns(3)
e1.download_button(f"8760 for year {view} (CSV)", csv_year(_key(sim_key, view), sim, series, int(view)), file_name=f"{X.tag(report, meta)}_PV_8760_Y{view}_{cfg.cal(view)}.csv", mime="text/csv", **full(st.download_button))
e2.download_button("Annual summary (CSV)", X.annual_summary(sim, series, report).to_csv(index=False).encode(), file_name=f"{X.tag(report, meta)}_PV_Annual_Summary_Y{y0}-{y1}.csv", mime="text/csv", **full(st.download_button))
e3.download_button(f"Runs for year {view} (CSV)", X.runs_table(sim, int(view)).to_csv(index=False).encode(), file_name=f"{X.tag(report, meta)}_PV_MonteCarlo_Y{view}_runs.csv", mime="text/csv", **full(st.download_button))

f1, f2 = st.columns(2)
if f1.button("Prepare multi-year 8760, P50 + P90 (CSV)", **full(st.button)) or st.session_state.get("_multi_ready") == sim_key:
    st.session_state["_multi_ready"] = sim_key
    with st.spinner("Building multi-year 8760…"):
        data = csv_multi(sim_key, sim, series)
    f1.download_button("Download multi-year 8760 (CSV)", data, file_name=f"{X.tag(report, meta)}_PV_8760_Y{y0}-{y1}_P50_P90.csv", mime="text/csv", **full(st.download_button))
inc = f2.checkbox("Include the multi-year 8760 sheet in Excel", value=False, key="xl8760")
if f2.button("Prepare Excel workbook", **full(st.button)) or st.session_state.get("_xl_ready") == (sim_key, inc):
    st.session_state["_xl_ready"] = (sim_key, inc)
    with st.spinner("Building Excel workbook…"):
        data = xlsx(_key(sim_key, inc), sim, series, report, meta, inc)
    f2.download_button("Download Excel workbook", data, file_name=f"{X.tag(report, meta)}_PV_Scaling_Y{y0}-{y1}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", **full(st.download_button))
st.caption("The single-year 8760 holds hourly MW at the MV bus for the base year, the degradation-only case and representative P50, P75, P90 and P95 profiles, "
           "plus night import from the grid (transformer losses). The multi-year 8760 holds P50 and P90 profiles for every year in the range, ready for the CFE/BESS dispatch model.")

with st.expander("How the scaling works"):
    st.markdown("""
1. **Degradation.** The base file is project year 1. Year N output is multiplied by 1 − d·(N−1) (linear) or (1 − d)^(N−1) (compound). Every year in the range is simulated separately.
2. **Weather, per year.** Each simulated year draws its own annual factor with the year-to-year σ from the PVsyst report. Years are independent, so good and bad years partly cancel over a longer period.
3. **Modelling uncertainty, per run.** If ticked, one extra factor is drawn per run and applied to every year of that run, because a systematic model error does not average out. A single year still gets PVsyst's total σ.
4. **Monthly pattern.** Monthly energy is perturbed by the month-to-month σ, then rescaled so the year total matches the annual draw.
5. **Daily shapes.** Whole days are resampled from nearby days in the same month, so each run has a realistic sequence of clear and cloudy days. Hourly shapes always come from your data.
6. **Limits.** When the grid-limited energy column (EGrdLim) is available, the unclipped power is scaled and the grid limit is re-applied, so hours at the limit are handled correctly. Night-time negative values are transformer no-load losses, supplied by the grid, and are never scaled.
7. **P50 anchoring.** Each year's runs are scaled so that P50 equals the source simulation × degradation for that year (PVsyst's definition of P50). The spread of the runs sets P75, P90 and P95. In the PVsyst method the weather factor scales delivered energy; in the physical method it scales sunlight before the grid limit.
8. **Basis.** All energy results are net E_Grid (generation minus night transformer losses), at the selected measuring point.
9. **P-levels.** P90 is the energy exceeded in 90% of runs. Multi-year P-levels come from each run's total over the range, not from adding single-year values. Each exported P-level profile is the run closest to that energy, trimmed to match it exactly.
""")
