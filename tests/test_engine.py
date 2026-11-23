"""Checks that the Python engine reproduces the browser tool exactly.

reference_js.json was produced by the browser tool's engine on a synthetic
profile (no client data), with the same seed and settings as below.
Run:  pytest -q
"""
import json
from pathlib import Path

import numpy as np
import pytest

from pvscaler import engine as E
from pvscaler import parsing as Pp
from pvscaler import context as X
from pvscaler import report as R

REF = json.loads((Path(__file__).parent / "reference_js.json").read_text())
MONTHLY = [9000, 10500, 14500, 17000, 19000, 19200, 18800, 17200, 15000, 13500, 10200, 8300]


@pytest.fixture(scope="module")
def base():
    return Pp.synth_8760(MONTHLY, 33.0, -84.0, -5, 100, True)


@pytest.fixture(scope="module")
def series(base):
    hours = np.arange(8760) % 24
    return np.where(base > 0, base, np.where((hours < 5) | (hours > 20), -0.3, 0.0))   # night transformer losses


def _sim(series, mode):
    cfg = E.Config(cod=2027, y0=1, y1=5, deg=0.5, sig_w=5.0, use_unc=True, sig_t=7.0, sig_m=8.0, win=7, runs=500, seed=7, cap=95, L=0.99, mode=mode)
    return E.simulate_range(series, cfg)


@pytest.fixture(scope="module")
def sim(series):
    return _sim(series, "energy")


def test_synthetic_profile_matches(base):
    assert base.sum() == pytest.approx(REF["series_sum"], rel=1e-12)
    assert np.allclose(base[4000:4012], REF["series_sample"], atol=1e-9)


@pytest.mark.parametrize("mode", ["energy", "resource"])
def test_matches_browser_engine(series, mode):
    sim = _sim(series, mode)
    ref = REF["modes"][mode]
    for y in range(1, 6):
        res = sim["by_year"][y]
        r = ref["years"][str(y)]
        assert res.kcal == pytest.approx(r["kcal"], rel=1e-9)
        assert res.det == pytest.approx(r["det"], rel=1e-9)
        for p in ("50", "75", "90", "95", "99"):
            assert res.P[int(p)] == pytest.approx(r["P"][p], rel=1e-9)
    assert np.allclose(E.representative(sim["by_year"][3], 90)[4000:4012], ref["years"]["3"]["rep90"], atol=1e-6)
    assert sim["agg"]["P"][50] == pytest.approx(ref["tot"]["p50"], rel=1e-9)
    assert sim["agg"]["P"][90] == pytest.approx(ref["tot"]["p90"], rel=1e-9)


def test_p50_anchored_to_source(series, sim):
    for res in sim["years"]:
        assert res.P[50] == pytest.approx(res.det, rel=1e-9)          # P50 = source x degradation (net)
        assert res.det < res.det_gen                                   # net includes night losses


def test_representative_is_net_consistent(sim):
    res = sim["by_year"][2]
    gen = E.representative(res, 90)
    assert gen.min() >= 0
    assert gen.sum() + res.pre.aux_tot == pytest.approx(res.P[90], rel=1e-6)


def test_multi_year_averaging(sim):
    assert sim["agg"]["P"][90] > sim["agg"]["sum_p90"]  # weather averages out over several years


def test_reports_and_exports(series, sim):
    meta = dict(series_file="synthetic.csv", column="E_Grid", units="MW")
    pdf, labels = R.build_report(X.report_context(sim, series, None, meta, "all", appendix=True), with_labels=True)
    assert pdf[:4] == b"%PDF" and len(labels) == 6 + 5
    pdf1 = R.build_report(X.report_context(sim, series, None, meta, "year", year=3))
    assert pdf1[:4] == b"%PDF"
    assert len(X.csv_8760_year(sim, series, 3)) == 8760
    assert X.csv_8760_multi(sim, series).shape == (8760, 3 + 2 * 5 + 1)
    assert X.excel_workbook(sim, series, None, meta, include_8760=False)[:2] == b"PK"


def test_report_text_parser():
    txt = ("PVsyst V8.1.6 Project: Demo Site Variant: 100MWp test Tracking System power: 100.0 MWp "
           "Produced Energy 180000 MWh/year Specific production 1800 kWh/kWp/year Simulation for year no 1 "
           "Year-to-year variability(Variance) TMY 5.0 % Variability (Quadratic sum) 7.0 % "
           "Variability P50 P90 P95 12.6 180.0 163.9 159.2 GWh")
    r = Pp.parse_report_text(txt)
    assert r.is_pvsyst and r.fields["pnom_mwp"] == 100.0 and r.fields["annual_mwh"] == 180000
    assert r.fields["weather_sigma_pct"] == 5.0 and r.fields["total_sigma_pct"] == 7.0
    assert r.fields["p90_mwh"] == pytest.approx(163900)
