"""Plant size scaling: round trip at the original size, and sensible behaviour when resized (synthetic data only)."""
import numpy as np

from pvscaler import parsing as Pp
from pvscaler import plant as PL

MONTHLY = [9000, 10500, 14500, 17000, 19000, 19200, 18800, 17200, 15000, 13500, 10200, 8300]
TEXT = ("System power: 100.0 MWp Nominal (STC) 100.0 MWp Total power 80000 kWac Unit Nom. Power 4000 kWac Number of inverters 20 units "
        "Produced Energy 170000 MWh/year Specific production 1700 kWh/kWp/year "
        "180000 MWh Array virtual energy at MPP -1.5% Inverter Loss during operation (efficiency) 0.0% Inverter Loss over nominal inv. power "
        "177300 MWh Available Energy at Inverter Output -0.5% Auxiliaries (fans, other) -0.4% AC ohmic loss -1.0% Medium voltage transfo loss "
        "-0.3% MV line ohmic loss -0.6% High voltage transfo loss Night aux. cons. 500 500 W W PVsyst")


def _setup():
    rep = Pp.parse_report_text(TEXT)
    base = Pp.synth_8760(MONTHLY, 33.0, -84.0, -5, None, True)
    hours = np.arange(8760) % 24
    base = np.where(base > 0, base, np.where((hours < 5) | (hours > 20), -0.05, 0.0))
    return rep, base


def test_parse_plant():
    rep, _ = _setup()
    pl = rep.plant
    assert pl["ok"] and pl["ac_mw"] == 80 and pl["n_inv"] == 20 and abs(pl["dcac"] - 1.25) < 1e-9
    assert abs(pl["loss"]["mv_t"] - 0.01) < 1e-12 and abs(pl["aux_night_mw"] - 0.0005) < 1e-12


def test_round_trip_original_size():
    rep, base = _setup()
    pl = rep.plant
    out, _ = PL.scale_plant(base, pl, PL.PlantChoice(pl["dc_mwp"], pl["ac_mw"], 0))
    assert np.abs(out - base).max() < 1e-9


def test_same_ratio_scales_close_to_linear_and_extra_dc_clips():
    rep, base = _setup()
    pl = rep.plant
    t0 = PL.series_totals(base, 0)["gen"]
    out, B = PL.scale_plant(base, pl, PL.PlantChoice(150, 120, 0))
    assert abs(PL.series_totals(out, 0)["gen"] / t0 - 1.5) < 0.02 and B["clip1"] < 1
    out2, B2 = PL.scale_plant(base, pl, PL.PlantChoice(150, 80, 0))
    assert B2["clip1"] > 0 and PL.series_totals(out2, 0)["gen"] < PL.series_totals(out, 0)["gen"]
