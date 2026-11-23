"""Build report contexts and exports from simulation results."""
from __future__ import annotations

import dataclasses
import datetime as dt
from types import SimpleNamespace
import io
import re

import numpy as np

from .engine import HOUR_MONTH, MDAYS, MONTHS, MSTART, DAY_MONTH, Params, det_series, prep, profile, representative
from . import plant as PL


def _monthly_sums(arr):
    return np.bincount(HOUR_MONTH, weights=np.asarray(arr, dtype=float), minlength=12)


def year_ctx(res, series, cfg, report):
    """Monthly 'Balances and main results' for one year (P50 table, P90 column)."""
    rf = report.fields if report else {}
    tb = report.table if report else None
    pnom = rf.get("pnom_mwp")
    det = det_series(series, res.D, cfg.cap, cfg.L)
    gd = _monthly_sums(det)
    gdT = gd.sum()
    g50 = gd * (res.P[50] - res.pre.aux_tot) / gdT   # P-levels are net; spread the generation part
    g90 = gd * (res.P[90] - res.pre.aux_tot) / gdT
    s = np.asarray(series, dtype=float)
    auxM = _monthly_sums(np.where(s < 0, s, 0.0))
    baseM = _monthly_sums(np.where(s > 0, s * cfg.L, 0.0))
    cols = tb["cols"] if tb else []
    col = lambda n: cols.index(n) if n in cols else -1
    has_irr = bool(tb and pnom and col("GlobInc") >= 0 and col("EArray") >= 0)
    months = []
    tot = dict(gen=0.0, aux=0.0, egrid=0.0, glob_hor=0.0, diff_hor=0.0, glob_inc=0.0, glob_eff=0.0, earray=0.0, base=0.0)
    t90 = dict(egrid=0.0, glob_inc=0.0)
    tdet = 0.0
    for k in range(12):
        w50 = g50[k] / gd[k] if gd[k] > 0 else 1.0
        w90 = g90[k] / gd[k] if gd[k] > 0 else 1.0
        row = tb["rows"][k] if tb else None
        src_e = row[col("E_Grid")] if (row and col("E_Grid") >= 0) else baseM[k] + auxM[k]
        o = dict(p50=g50[k], p90=g90[k], det=gd[k] + auxM[k], aux=auxM[k], egrid=g50[k] + auxM[k], p90net=g90[k] + auxM[k])
        o["vs_base"] = (o["egrid"] / src_e - 1) * 100 if src_e else float("nan")
        if has_irr:
            g = lambda n: row[col(n)] if col(n) >= 0 else float("nan")
            days = int(MDAYS[k])
            o.update(glob_hor=g("GlobHor") * w50, diff_hor=g("DiffHor"), tamb=g("T_Amb"), glob_inc=g("GlobInc") * w50, glob_eff=g("GlobEff") * w50, earray=g("EArray") * w50 * res.D)
            o["pr"] = o["egrid"] / (o["glob_inc"] * pnom)
            yr_, ya, yf = o["glob_inc"] / days, o["earray"] / pnom / days, o["egrid"] / pnom / days
            o.update(yf=yf, ls=ya - yf, lc=yr_ - ya)
            for key in ("glob_hor", "diff_hor", "glob_inc", "glob_eff", "earray"):
                tot[key] += o[key]
            t90["glob_inc"] += g("GlobInc") * w90
        tot["gen"] += g50[k]; tot["aux"] += auxM[k]; tot["egrid"] += o["egrid"]; tot["base"] += src_e
        t90["egrid"] += o["p90net"]; tdet += o["det"]
        months.append(o)
    p50 = dict(gen=tot["gen"], aux=tot["aux"], egrid=tot["egrid"], vs_base=(tot["egrid"] / tot["base"] - 1) * 100)
    if has_irr:
        p50.update(glob_hor=tot["glob_hor"], diff_hor=tot["diff_hor"], tamb=(tb["year"][col("T_Amb")] if tb.get("year") else float("nan")),
                   glob_inc=tot["glob_inc"], glob_eff=tot["glob_eff"], earray=tot["earray"], pr=tot["egrid"] / (tot["glob_inc"] * pnom))
        t90["pr"] = t90["egrid"] / (t90["glob_inc"] * pnom)
    return dict(year=res.year, cal=cfg.cal(res.year), D=res.D, P=res.P, sorted=res.sorted, sd=res.sd, mean=res.mean, mean_clip=res.mean_clip,
                monthly_q=res.monthly_q, det=res.det, main=dict(has_irr=has_irr, months=months, p50=p50, p90=t90, det=dict(egrid=tdet)))


def base_ctx(cfg, report, meta):
    rf = report.fields if report else {}
    return dict(cfg=cfg, sigma_used=cfg.sigma_single, sig_u=cfg.sig_u,
                src_p=dict(p50=rf["p50_mwh"], p90=rf.get("p90_mwh"), p95=rf.get("p95_mwh")) if rf.get("p50_mwh") else None,
                sys=dict(pnom=rf.get("pnom_mwp"), grid_limit=rf.get("grid_limit_mw"), lat=rf.get("lat"), lon=rf.get("lon"), tz=rf.get("tz"), sim_year=rf.get("sim_year")),
                meta=dict(project=rf.get("project") or meta.get("project", ""), variant=rf.get("variant") or "", version=rf.get("version"),
                          generated=dt.date.today().isoformat(), series_short=_short(meta.get("series_file", "-")), column=meta.get("column", "-"),
                          units=meta.get("units", "-"), add_back=meta.get("add_back", False), synthetic=meta.get("synthetic", False),
                          plant=meta.get("plant")))


def _short(s, n=24):
    s = s or "-"
    return s if len(s) <= n else s[: n - 2] + "..."


def report_context(sim, series, report, meta, scope="all", year=None, appendix=False):
    cfg = sim["cfg"]
    ctx = base_ctx(cfg, report, meta)
    if scope == "all":
        ctx.update(kind="combined", appendix=appendix, years=[year_ctx(r, series, cfg, report) for r in sim["years"]], agg=sim["agg"],
                   header_right=f"Project years {cfg.y0}-{cfg.y1} ({cfg.cal(cfg.y0)}-{cfg.cal(cfg.y1)})")
    else:
        res = sim["by_year"][year]
        ctx.update(kind="single", year=year_ctx(res, series, cfg, report),
                   life=[dict(y=r.year, cal=cfg.cal(r.year), D=r.D, det=r.det, p50=r.P[50], p90=r.P[90], p95=r.P[95]) for r in sim["years"] if r.year <= year],
                   header_right=f"Scaled to project year {year} ({cfg.cal(year)})")
    return ctx


def tag(report, meta):
    p = (report.fields.get("project") if report else None) or meta.get("project") or "PV"
    return re.sub(r"^_|_$", "", re.sub(r"[^A-Za-z0-9]+", "_", p)) or "PV"


# --------------------------------------------------------------------------
# exports
# --------------------------------------------------------------------------
def _mdh():
    d = np.arange(8760) // 24
    m = DAY_MONTH[d]
    return m + 1, d - MSTART[m] + 1, np.arange(8760) % 24


def csv_8760_year(sim, series, year):
    import pandas as pd
    cfg = sim["cfg"]
    res = sim["by_year"][year]
    s = np.asarray(series, dtype=float)
    mo, dy, hr = _mdh()
    df = pd.DataFrame({"Month": mo, "Day": dy, "Hour": hr,
                       "Base_Y1_MW": np.round(np.maximum(0, s) * cfg.L, 4),
                       f"Y{year}_DegradationOnly_MW": np.round(det_series(s, res.D, cfg.cap, cfg.L), 4)})
    for p in (50, 75, 90, 95):
        df[f"Y{year}_P{p}_MW"] = np.round(representative(res, p), 4)
    df["Night_Import_MW"] = np.round(np.where(s < 0, -s, 0.0), 4)
    return df


def csv_8760_multi(sim, series):
    import pandas as pd
    cfg = sim["cfg"]
    s = np.asarray(series, dtype=float)
    mo, dy, hr = _mdh()
    cols = {"Month": mo, "Day": dy, "Hour": hr}
    for r in sim["years"]:
        cols[f"Y{r.year}_{cfg.cal(r.year)}_P50_MW"] = np.round(representative(r, 50), 3)
        cols[f"Y{r.year}_{cfg.cal(r.year)}_P90_MW"] = np.round(representative(r, 90), 3)
    cols["Night_Import_MW"] = np.round(np.where(s < 0, -s, 0.0), 4)
    return pd.DataFrame(cols)


def annual_summary(sim, series, report):
    import pandas as pd
    cfg = sim["cfg"]
    pn = report.fields.get("pnom_mwp") if report else None
    aux = -profile(series)["aux"]
    rows = []
    for r in sim["years"]:
        row = {"Project_Year": r.year, "Calendar_Year": cfg.cal(r.year), "Degradation_Factor": round(r.D, 5), "DegradationOnly_MWh": r.det,
               "P50_MWh": r.P[50], "P75_MWh": r.P[75], "P90_MWh": r.P[90], "P95_MWh": r.P[95], "P99_MWh": r.P[99], "Mean_MWh": r.mean,
               "StdDev_MWh": r.sd, "Mean_Clipped_MWh": r.mean_clip, "Night_Import_MWh": aux}
        if pn:
            row["P50_Yield_kWh_per_kWp"] = r.P[50] / pn
        rows.append(row)
    A = sim["agg"]
    rows.append({"Project_Year": "Range_total_multi_year", "Calendar_Year": f"{cfg.cal(cfg.y0)}-{cfg.cal(cfg.y1)}", "DegradationOnly_MWh": A["det_sum"],
                 "P50_MWh": A["P"][50], "P75_MWh": A["P"][75], "P90_MWh": A["P"][90], "P95_MWh": A["P"][95], "P99_MWh": A["P"][99], "Mean_MWh": A["mean"],
                 "StdDev_MWh": A["sd"], "Night_Import_MWh": aux * len(sim["years"])})
    rows.append({"Project_Year": "Sum_of_single_year_values", "DegradationOnly_MWh": A["det_sum"], "P50_MWh": A["sum_p50"], "P90_MWh": A["sum_p90"], "P95_MWh": A["sum_p95"]})
    rows.append({"Project_Year": "Note: energy values are net E_Grid (generation minus night transformer losses), as in PVsyst"})
    df = pd.DataFrame(rows)
    mwh = [c for c in df.columns if c.endswith("_MWh") or c.endswith("kWp")]
    df[mwh] = df[mwh].round(1)
    return df


def runs_table(sim, year):
    import pandas as pd
    r = sim["by_year"][year]
    df = pd.DataFrame({"Run": np.arange(1, len(r.annual) + 1), "Seed": r.seeds.astype(np.int64), "Annual_net_MWh": np.round(r.annual, 1)})
    for m in range(12):
        df[MONTHS[m][:3] + "_net_MWh"] = np.round(r.monthly[:, m], 1)
    df["Clipped_MWh"] = np.round(r.clip, 1)
    return df


def monthly_matrix(sim, series, report):
    import pandas as pd
    cfg = sim["cfg"]
    rows = []
    for r in sim["years"]:
        Y = year_ctx(r, series, cfg, report)
        row = {"Project_Year": r.year, "Calendar_Year": cfg.cal(r.year)}
        for m, o in zip(MONTHS, Y["main"]["months"]):
            row[m[:3] + "_EGrid_P50_MWh"] = o["egrid"]
        row["Year_EGrid_P50_MWh"] = Y["main"]["p50"]["egrid"]
        row["Year_EGrid_P90_MWh"] = Y["main"]["p90"]["egrid"]
        if Y["main"]["has_irr"]:
            row["PR_P50"] = Y["main"]["p50"]["pr"]
        rows.append(row)
    return pd.DataFrame(rows).round(3)


def excel_workbook(sim, series, report, meta, include_8760=True) -> bytes:
    import pandas as pd
    cfg = sim["cfg"]
    A = sim["agg"]
    inputs = pd.DataFrame([
        ("Project", (report.fields.get("project") if report else None) or meta.get("project", "")),
        ("Hourly file", meta.get("series_file", "")), ("Column", meta.get("column", "")),
        ("Years simulated", f"{cfg.y0}-{cfg.y1} ({cfg.cal(cfg.y0)}-{cfg.cal(cfg.y1)})"), ("Project year 1 (COD)", cfg.cod),
        ("Degradation %/yr", cfg.deg), ("Degradation model", cfg.deg_model), ("Weather sigma %", cfg.sig_w),
        ("Modelling uncertainty included", cfg.use_unc), ("Total sigma %", cfg.sig_t), ("Single-year sigma %", round(cfg.sigma_single * 100, 2)),
        ("Month-to-month sigma %", cfg.sig_m), ("Day window +/- days", cfg.win), ("Runs", cfg.runs), ("Seed", cfg.seed),
        ("Grid limit MW", cfg.cap), ("Measuring-point factor", round(cfg.L, 5)),
        ("Weather variation applied to", "energy (PVsyst method)" if cfg.mode == "energy" else "sunlight, then grid limit"),
        ("Energy basis", "net E_Grid (generation minus night transformer losses); P50 = source x degradation"),
        ("Range total P50 MWh", round(A["P"][50], 1)), ("Range total P90 MWh (multi-year)", round(A["P"][90], 1)),
        ("Sum of single-year P90 MWh", round(A["sum_p90"], 1)), ("Generated", dt.date.today().isoformat()),
    ], columns=["Item", "Value"])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        inputs.to_excel(xw, sheet_name="Summary", index=False)
        annual_summary(sim, series, report).to_excel(xw, sheet_name="Annual", index=False)
        monthly_matrix(sim, series, report).to_excel(xw, sheet_name="Monthly_P50", index=False)
        if include_8760:
            csv_8760_multi(sim, series).to_excel(xw, sheet_name="8760_P50_P90", index=False)
        for ws in xw.book.worksheets:
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = max(10, min(34, len(str(col[0].value or "")) + 2))
    return buf.getvalue()


# --------------------------------------------------------------------------
# plant size report
# --------------------------------------------------------------------------
def _pseudo_year1(series, cfg, report):
    """Year-1 result without a Monte Carlo run: P50 = the scaled profile, P-levels from the report ratio or sigma."""
    pre = prep(Params(gen=np.asarray(series, dtype=float)))
    net = float(det_series(series, 1.0, cfg.cap, cfg.L).sum() + pre.aux_tot)
    rf = report.fields if report else {}
    s = cfg.sigma_single
    q90 = rf["p90_mwh"] / rf["p50_mwh"] if rf.get("p50_mwh") and rf.get("p90_mwh") else 1 - 1.2816 * s
    q95 = rf["p95_mwh"] / rf["p50_mwh"] if rf.get("p50_mwh") and rf.get("p95_mwh") else 1 - 1.645 * s
    P = {10: net * (2 - q90), 50: net, 75: net * (1 - 0.674 * s), 90: net * q90, 95: net * q95, 99: net * (1 - 2.326 * s)}
    return SimpleNamespace(year=1, D=1.0, pre=pre, P=P, sorted=np.array([net]), sd=0.0, mean=net, mean_clip=0.0, monthly_q=[], det=net)


def plant_context(base, out, B, pl, ch, report, report_scaled, cfg, meta, sim_year1=None):
    """Context for the plant size report (original vs resized design, year 1).
    sim_year1: the Monte Carlo year-1 result when the years were run on the resized plant."""
    base_poi = report.fields.get("grid_limit_mw") or 0
    cfg1 = dataclasses.replace(cfg, cap=ch.poi if ch.poi > 0 else float(np.max(out)))
    ctx = base_ctx(cfg1, report_scaled, meta)
    t0, t1 = PL.series_totals(base, base_poi), PL.series_totals(out, ch.poi)
    tb0, rf0 = report.table, report.fields
    pri = tb0["cols"].index("PR") if tb0 and "PR" in tb0["cols"] else -1
    res = sim_year1 if sim_year1 is not None else _pseudo_year1(out, cfg1, report_scaled)
    active = PL.is_active(pl, ch, base_poi)
    lbl, txt = PL.status_text(pl, ch, active)
    ctx.update(kind="plant", year=year_ctx(res, out, cfg1, report_scaled),
               header_right=f"Plant size: {ch.mwp:,.1f} MWp / {ch.ac:,.1f} MWac",
               plant=dict(B=B, t0=t0, t1=t1, validated=PL.is_validated(pl, ch), status_label=lbl, status_text=txt,
                          month_orig=PL.monthly_net(base, base_poi).tolist(), month_new=PL.monthly_net(out, ch.poi).tolist(),
                          orig=dict(dc=pl["dc_mwp"], ac=pl["ac_mw"], n_inv=pl.get("n_inv"), unit=pl.get("inv_unit_mw"), dcac=pl["dcac"], poi=base_poi, e=t0["net"],
                                    pr=(tb0["year"][pri] if pri >= 0 and tb0.get("year") else None), p50=rf0.get("p50_mwh"), p90=rf0.get("p90_mwh")),
                          scaled=dict(dc=ch.mwp, ac=ch.ac, n_inv=round(ch.ac / pl["inv_unit_mw"]) if pl.get("inv_unit_mw") else None, dcac=ch.dcac,
                                      poi=ch.poi, cable=ch.cable, aux=ch.aux, e=t1["net"])))
    return ctx
