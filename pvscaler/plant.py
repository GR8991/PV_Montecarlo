"""Plant size / DC-AC scaling from a PVsyst report, without rerunning PVsyst.

Same model as the web tool (core.js scalePlant):
  hourly inverter output  = E / f_AC + daytime auxiliaries
  DC energy at MPP        = inverter output / inverter efficiency (from the loss diagram)
  new DC                  = DC x k (k = new MWp / original MWp)
  new inverter output     = min(new DC x efficiency, new MWac x observed overload ratio)
  new E                   = (inverter output - auxiliaries) x f_AC(new)
Night values: fixed night auxiliaries plus transformer no-load losses that grow with the AC size.
At the original size the original profile is returned exactly.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

import numpy as np

from .engine import HOUR_MONTH

VALIDATION_NOTE = "The method reproduced a 515.6 MWp PVsyst simulation from a 195 MWp report within 0.04 %."


@dataclass
class PlantChoice:
    mwp: float            # new DC size
    ac: float             # new inverter AC capacity (MWac)
    poi: float            # grid (POI) limit, 0 = none
    cable: str = "same"   # "same": same cables, line loss grows with power; "resized": loss % unchanged
    aux: str = "fixed"    # "fixed": same MWh; "scaled": scale with AC size

    @property
    def dcac(self) -> float:
        return self.mwp / self.ac


def ac_factor(L: dict, s: float) -> float:
    return (1 - L["ac_ohm"] * s) * (1 - L["mv_t"]) * (1 - L["mv_l"] * s) * (1 - L["hv_t"]) * (1 - L["hv_l"] * s)


def scale_plant(base, pl: dict, ch: PlantChoice):
    """Rescale an hourly profile (MW at the grid connection, original design). Returns (series, breakdown)."""
    base = np.asarray(base, dtype=float)
    L = pl["loss"]
    eta = pl["inv_mwh"] / pl["mpp_mwh"]
    k = ch.mwp / pl["dc_mwp"]
    kac = ch.ac / pl["ac_mw"]
    f0, f1 = ac_factor(L, 1.0), ac_factor(L, kac if ch.cable == "same" else 1.0)
    pos = base > 0
    prod = int(pos.sum())
    neg = float(-base[~pos].sum())
    aux_abs = pl["inv_mwh"] * L["aux"]
    aux_day = (max(0.0, aux_abs - neg) if neg > 0.5 else aux_abs) / max(1, prod)   # night values already carry the night part
    aux_day1 = aux_day if ch.aux == "fixed" else aux_day * kac
    night_aux = pl.get("aux_night_mw") or 0.0

    inv = np.where(pos, base / f0 + aux_day, 0.0)
    ovl = max(1.0, float(inv.max()) / pl["ac_mw"]) if prod else 1.0   # inverters deliver above nominal at low temperature
    ac_lim = ch.ac * ovl
    dc = np.where(pos, inv / eta, 0.0)
    dc1 = dc * k
    raw = dc1 * eta
    inv1 = np.minimum(raw, ac_lim)
    g = np.maximum(0.0, inv1 - aux_day1) * f1
    if night_aux:
        n1 = -(night_aux if ch.aux == "fixed" else night_aux * kac) + (base + night_aux) * kac
    else:
        n1 = base * kac
    n1 = np.where(base == 0, 0.0, n1)
    out = np.where(pos, g, n1)

    B = dict(dc0=float(dc.sum()), inv0=float(inv.sum()), aux0=aux_day * prod, ac0=float(((inv - aux_day) * (1 - f0))[pos].sum()), e0=float(base[pos].sum()),
             dc1=float(dc1.sum()), inv1=float(inv1[pos].sum()), clip1=float((raw - inv1)[pos].sum()), aux1=float(np.minimum(aux_day1, inv1)[pos].sum()),
             ac1=float((np.maximum(0.0, inv1 - aux_day1) * (1 - f1))[pos].sum()), e1=float(g[pos].sum()),
             night0=float(base[~pos].sum()), night1=float(n1[~pos].sum()), eta=eta, ovl=ovl, aux_day0=aux_day, aux_day1=aux_day1, kac=kac)
    return out, B


def series_totals(arr, poi: float) -> dict:
    a = np.asarray(arr, dtype=float)
    pos = np.where(a > 0, a, 0.0)
    clipped = np.minimum(pos, poi) if poi and poi > 0 else pos
    gen, neg = float(clipped.sum()), float(np.where(a > 0, 0.0, a).sum())
    return dict(gen=gen, neg=neg, lim=float((pos - clipped).sum()), net=gen + neg)


def monthly_net(arr, poi: float) -> np.ndarray:
    a = np.asarray(arr, dtype=float)
    v = np.where(a > 0, np.minimum(a, poi), a) if poi and poi > 0 else a
    return np.bincount(HOUR_MONTH, weights=v, minlength=12)


def is_active(pl: dict, ch: PlantChoice, base_poi: float) -> bool:
    return abs(ch.mwp - pl["dc_mwp"]) > 1e-3 or abs(ch.ac - pl["ac_mw"]) > 1e-3 or abs((ch.poi or 0) - (base_poi or 0)) > 1e-6


def is_validated(pl: dict, ch: PlantChoice) -> bool:
    k = ch.mwp / pl["dc_mwp"]
    return abs(ch.dcac - pl["dcac"]) < 0.03 and 0.5 <= k <= 3


def status_text(pl: dict, ch: PlantChoice, active: bool) -> tuple[str, str]:
    """(label, text) for the status banner."""
    if not active:
        return "Original design", "Results use the plant exactly as simulated in PVsyst."
    if is_validated(pl, ch):
        return "Validated method", "Same DC/AC ratio as the PVsyst design. " + VALIDATION_NOTE
    k = ch.mwp / pl["dc_mwp"]
    return "Estimate", (f"DC/AC changed from {pl['dcac']:.2f} to {ch.dcac:.2f}" + (" and the size is far from the original" if k < 0.5 or k > 3 else "")
                        + ". Inverter clipping is estimated from the hourly profile and has not been checked against a PVsyst run; confirm the final design in PVsyst.")


def scaled_report(report, base, out, pl: dict, ch: PlantChoice):
    """Copy of the report as seen by results and PDFs for the resized plant."""
    R = copy.deepcopy(report)
    F = R.fields
    base_poi = report.fields.get("grid_limit_mw") or 0
    t0, t1 = series_totals(base, base_poi), series_totals(out, ch.poi)
    ratio = t1["net"] / t0["net"] if t0["net"] > 0 else 1.0
    F["pnom_mwp"] = ch.mwp
    F["grid_limit_mw"] = ch.poi if ch.poi > 0 else None
    F["annual_mwh"] = (F.get("annual_mwh") or t0["net"]) * ratio
    F["specific_yield"] = F["annual_mwh"] / ch.mwp
    for key in ("p50_mwh", "p90_mwh", "p95_mwh"):
        if F.get(key):
            F[key] *= ratio
    F["variant"] = (F.get("variant") or "") + f" | scaled to {ch.mwp:,.1f} MWp / {ch.ac:,.1f} MWac (DC/AC {ch.dcac:.2f})"
    F["pnom_note"] = None
    if R.table:
        cols = R.table["cols"]
        ci = lambda n: cols.index(n) if n in cols else -1
        gi, ea, pr, gc = ci("E_Grid"), ci("EArray"), ci("PR"), ci("GlobInc")
        k = ch.mwp / pl["dc_mwp"]
        mon0, mon1 = monthly_net(base, base_poi), monthly_net(out, ch.poi)
        rows = R.table["rows"] + ([R.table["year"]] if R.table.get("year") else [])
        for i, row in enumerate(rows):
            r = (mon1.sum() / max(1e-9, mon0.sum())) if i == 12 else (mon1[i] / mon0[i] if mon0[i] else ratio)
            if gi >= 0:
                row[gi] *= r
            if ea >= 0:
                row[ea] *= k
            if pr >= 0 and gc >= 0 and gi >= 0:
                row[pr] = row[gi] / (row[gc] * ch.mwp)
        if gi >= 0:
            R.monthly = [row[gi] for row in R.table["rows"]]
    return R
