"""Monte Carlo PV scaling engine (NumPy).

A direct port of the browser tool's engine. The random number generator
(mulberry32) and the order of draws are identical, so for the same inputs
and seed the results match the web version to rounding.

Units: hourly values are average MW over the hour, so a sum over 8760 hours
is MWh.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import numpy as np

ENGINE_VERSION = "2026.11.23-3"   # keep equal to PV.ENGINE_VERSION in the web tool

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
MDAYS = np.array([31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31])
MSTART = np.concatenate([[0], np.cumsum(MDAYS)[:-1]])
DAY_MONTH = np.repeat(np.arange(12), MDAYS)            # (365,)
HOUR_MONTH = np.repeat(DAY_MONTH, 24)                    # (8760,)

U32 = np.uint64(0xFFFFFFFF)


# --------------------------------------------------------------------------
# mulberry32, vectorised over many independent streams
# --------------------------------------------------------------------------
def _imul(a, b):
    return (a.astype(np.uint64) * b.astype(np.uint64)) & U32


class Mulberry32:
    """Vectorised mulberry32: one independent stream per element of `seeds`."""

    def __init__(self, seeds):
        self.a = np.asarray(seeds, dtype=np.uint64) & U32

    def next_u32(self):
        a = (self.a + np.uint64(0x6D2B79F5)) & U32
        self.a = a
        t = _imul(a ^ (a >> np.uint64(15)), a | np.uint64(1))
        t = (((t + _imul(t ^ (t >> np.uint64(7)), t | np.uint64(61))) & U32) ^ t)
        return (t ^ (t >> np.uint64(14))) & U32

    def random(self):
        return self.next_u32().astype(np.float64) / 4294967296.0

    def normal(self):
        u = self.random()
        v = self.random()
        u = np.where(u == 0, 1.0 / 4294967296.0, u)  # JS redraws on exactly 0 (p = 2^-32)
        return np.sqrt(-2.0 * np.log(u)) * np.cos(2.0 * np.pi * v)


def run_seeds(seed: int, runs: int, sig_u: float):
    """Run-level seeds and the per-run modelling-uncertainty bias (shared by all years)."""
    master = Mulberry32([seed])
    seeds = np.array([int(master.next_u32()[0]) for _ in range(runs)], dtype=np.uint64)
    if sig_u > 0:
        bias = sig_u * Mulberry32(seeds ^ np.uint64(0x5BD1E995)).normal()
    else:
        bias = np.zeros(runs)
    return seeds, bias


def year_seed(run_seeds_arr, year: int):
    s = np.asarray(run_seeds_arr, dtype=np.uint64)
    h = (s ^ np.uint64((((year + 1) * 0x9E3779B1) & 0xFFFFFFFF))) & U32
    h = _imul(h ^ (h >> np.uint64(16)), np.full_like(h, 0x85EBCA6B))
    return (h ^ (h >> np.uint64(13))) & U32


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def deg_factor(year: int, rate_pct: float, model: str = "linear", base_year: int = 1) -> float:
    n = year - base_year
    d = rate_pct / 100.0
    return (1 - d) ** n if model == "compound" else 1 - d * n


def quantile(sorted_vals, q):
    n = len(sorted_vals)
    if n == 0:
        return float("nan")
    pos = (n - 1) * q
    b = int(math.floor(pos))
    r = pos - b
    return float(sorted_vals[b] + r * (sorted_vals[b + 1] - sorted_vals[b])) if b + 1 < n else float(sorted_vals[b])


def pval(sorted_vals, p):
    """Exceedance level: P90 is exceeded in 90 % of runs (10th percentile)."""
    return quantile(sorted_vals, 1 - p / 100.0)


def profile(gen):
    gen = np.asarray(gen, dtype=float)
    pos = np.where(gen > 0, gen, 0.0)
    peak = float(pos.max()) if len(pos) else 0.0
    return {
        "annual": float(pos.sum()),
        "aux": float(np.where(gen < 0, gen, 0.0).sum()),
        "peak": peak,
        "monthly": np.bincount(HOUR_MONTH, weights=pos, minlength=12),
        "prod_hours": int((gen > 0).sum()),
        "hours_at_peak": int((gen >= peak * 0.999).sum()) if peak > 0 else 0,
    }


@dataclass
class Params:
    gen: np.ndarray          # 8760 MW, base year (year 1)
    cap: float = 0.0         # hourly output limit in base units (0 = none)
    sig_a: float = 0.051     # annual weather sigma (fraction)
    sig_m: float = 0.08      # month-to-month sigma (fraction)
    win: int = 7             # day resampling window (+/- days)
    D: float = 1.0           # degradation factor
    L: float = 1.0           # delivery factor (measuring point)
    mode: str = "energy"     # "energy": weather factor scales delivered energy (PVsyst P50/P90 method)
                             # "resource": weather factor scales sunlight, then the output limit clips


@dataclass
class Prep:
    pos: np.ndarray
    aux: np.ndarray
    Em: np.ndarray
    tot: float
    aux_m: np.ndarray = None     # monthly night import (negative, MWh)
    aux_tot: float = 0.0


def prep(P: Params) -> Prep:
    g = np.asarray(P.gen, dtype=float)
    pos = np.where(g > 0, g, 0.0)
    aux = np.where(g > 0, 0.0, g)
    Em = np.bincount(HOUR_MONTH, weights=pos, minlength=12)
    aux_m = np.bincount(HOUR_MONTH, weights=aux, minlength=12)
    return Prep(pos, aux, Em, float(Em.sum()), aux_m, float(aux_m.sum()))


def run_batch(P: Params, pre: Prep, seeds, bias, keep_hourly: bool = False, kcal: float = 1.0):
    """Simulate one year for a batch of runs.

    Returns generation (positive hours only, after the output limit, x delivery factor,
    x calibration factor `kcal`): annual, monthly, clip, and the A=1 "shape" annual used for calibration.
    """
    seeds = np.asarray(seeds, dtype=np.uint64)
    R = len(seeds)
    rng = Mulberry32(seeds)
    A = np.maximum(0.4, 1 + P.sig_a * rng.normal() + np.asarray(bias, dtype=float))
    em = np.empty((R, 12))
    for m in range(12):
        em[:, m] = np.maximum(0.3, 1 + P.sig_m * rng.normal())
    w = em @ pre.Em
    norm = np.where(w > 0, pre.tot / np.where(w > 0, w, 1), 1.0)
    em *= norm[:, None]

    days = np.arange(365)
    if P.win > 0:
        src = np.empty((R, 365), dtype=np.int64)
        lo_all = MSTART[DAY_MONTH]
        nd_all = MDAYS[DAY_MONTH]
        hi_all = lo_all + nd_all - 1
        for d in range(365):
            s = d + np.floor(rng.random() * (2 * P.win + 1)).astype(np.int64) - P.win
            lo, hi, nd = lo_all[d], hi_all[d], nd_all[d]
            s = np.where(s < lo, lo + (lo - s) % nd, s)
            s = np.where(s > hi, hi - (s - hi) % nd, s)
            src[:, d] = s
    else:
        src = np.broadcast_to(days, (R, 365))

    pos_days = pre.pos.reshape(365, 24)
    tmp = pos_days[src]                                  # (R, 365, 24)
    day_sum = tmp.sum(axis=2)                            # (R, 365)
    Sm = np.add.reduceat(day_sum, MSTART, axis=1)        # (R, 12)
    resource = P.mode == "resource"
    scale = (A[:, None] if resource else 1.0) * P.D
    k = np.where(Sm > 0, pre.Em[None, :] * em / np.where(Sm > 0, Sm, 1) * scale, 0.0)
    y = tmp * k[:, DAY_MONTH][:, :, None]
    cap = P.cap if P.cap and P.cap > 0 else np.inf
    over = np.maximum(y - cap, 0.0)
    clip = over.sum(axis=(1, 2)) * P.L
    y = np.minimum(y, cap) * P.L
    shape = y.sum(axis=(1, 2))
    if not resource:
        y = y * A[:, None, None]
    y = y * kcal
    mon = np.add.reduceat(y.sum(axis=2), MSTART, axis=1)
    ann = mon.sum(axis=1)
    out = {"annual": ann, "monthly": mon, "clip": clip, "A": A, "shape": shape}
    if keep_hourly:
        out["hourly"] = y.reshape(R, 8760)
    return out


@dataclass
class YearResult:
    year: int
    D: float
    params: Params
    pre: Prep
    annual: np.ndarray
    monthly: np.ndarray
    clip: np.ndarray
    seeds: np.ndarray
    bias: np.ndarray
    det: float = 0.0             # degradation-only net E_Grid
    det_gen: float = 0.0         # degradation-only generation
    kcal: float = 1.0            # calibration factor (see simulate_year)
    sorted: np.ndarray = field(default=None)
    P: dict = field(default_factory=dict)
    mean: float = 0.0
    sd: float = 0.0
    mean_clip: float = 0.0
    monthly_q: list = field(default_factory=list)

    def summarize(self):
        self.sorted = np.sort(self.annual)
        R = len(self.annual)
        self.mean = float(self.annual.mean())
        self.sd = float(self.annual.std(ddof=1)) if R > 1 else 0.0
        self.mean_clip = float(self.clip.mean())
        self.P = {p: pval(self.sorted, p) for p in (10, 50, 75, 90, 95, 99)}
        self.monthly_q = []
        for m in range(12):
            col = np.sort(self.monthly[:, m])
            self.monthly_q.append({"p10": pval(col, 10), "p50": pval(col, 50), "p90": pval(col, 90)})
        return self


def simulate_year(P: Params, pre: Prep, rseeds, bias, year: int, batch: int = 250) -> YearResult:
    """One project year. Results are net E_Grid (generation + night import), like PVsyst.

    P50 anchoring: spreading energy between months and days pushes more hours above the
    output limit, and a finite number of runs adds sampling noise, so the raw median drifts
    from the source value. All runs are scaled by kcal = degradation-only generation /
    median generation, so P50 equals the source simulation x degradation for that year
    (PVsyst's definition of P50); the spread of the runs sets P75/P90/P95.
    """
    R = len(rseeds)
    ys = year_seed(rseeds, year)
    ann = np.empty(R); mon = np.empty((R, 12)); clip = np.empty(R); shape = np.empty(R)
    for a in range(0, R, batch):
        b = min(R, a + batch)
        o = run_batch(P, pre, ys[a:b], bias[a:b])
        ann[a:b] = o["annual"]; mon[a:b] = o["monthly"]; clip[a:b] = o["clip"]; shape[a:b] = o["shape"]
    det_gen = float(det_series(pre.pos, P.D, P.cap, P.L).sum())
    med = pval(np.sort(ann), 50)
    kcal = det_gen / med if med > 0 else 1.0
    ann = ann * kcal + pre.aux_tot
    mon = mon * kcal + pre.aux_m[None, :]
    res = YearResult(year, P.D, P, pre, ann, mon, clip, ys, np.asarray(bias), det=det_gen + pre.aux_tot, det_gen=det_gen, kcal=kcal)
    return res.summarize()


def representative(res: YearResult, p: int):
    """Hourly generation for an exceedance level: the run nearest the quantile, trimmed so that
    generation + night import equals the quantile exactly. Night import is not included in the result."""
    target = res.P[p] - res.pre.aux_tot
    best = int(np.argmin(np.abs(res.annual - res.P[p])))
    out = run_batch(res.params, res.pre, res.seeds[best:best + 1], res.bias[best:best + 1], keep_hourly=True, kcal=res.kcal)["hourly"][0].copy()
    capL = (res.params.cap if res.params.cap and res.params.cap > 0 else np.inf) * res.params.L
    for _ in range(6):
        s = out.sum()
        free_mask = out < capL - 1e-9
        free = out[free_mask].sum()
        gap = target - s
        if abs(gap) < 1e-6 * target or free <= 0:
            break
        f = 1 + gap / free
        out[free_mask] = np.minimum(capL, out[free_mask] * f)
    return out


def det_series(gen, D, cap, L):
    g = np.asarray(gen, dtype=float)
    c = cap if cap and cap > 0 else np.inf
    return np.where(g > 0, np.minimum(c, g * D) * L, 0.0)


@dataclass
class Config:
    cod: int = 2027
    y0: int = 1
    y1: int = 25
    deg: float = 0.45
    deg_model: str = "linear"
    sig_w: float = 5.1          # %
    use_unc: bool = True
    sig_t: float = 7.4          # %
    sig_m: float = 8.0          # %
    win: int = 7
    runs: int = 1000
    seed: int = 2026
    cap: float = 0.0
    L: float = 1.0
    mode: str = "energy"

    @property
    def sig_u(self) -> float:
        return math.sqrt(max(0.0, self.sig_t ** 2 - self.sig_w ** 2)) / 100 if self.use_unc else 0.0

    @property
    def sigma_single(self) -> float:
        return math.sqrt((self.sig_w / 100) ** 2 + self.sig_u ** 2)

    def cal(self, y: int) -> int:
        return self.cod + y - 1


def simulate_range(gen, cfg: Config, progress=None):
    """Simulate every project year y0..y1. Returns dict with per-year results and range aggregates."""
    R = cfg.runs
    rseeds, bias = run_seeds(cfg.seed, R, cfg.sig_u)
    tot = np.zeros(R)
    years, cum = [], []
    sum50 = sum90 = sum95 = det_sum = 0.0
    n = cfg.y1 - cfg.y0 + 1
    for i, y in enumerate(range(cfg.y0, cfg.y1 + 1)):
        D = deg_factor(y, cfg.deg, cfg.deg_model, 1)
        P = Params(gen=np.asarray(gen, dtype=float), cap=cfg.cap, sig_a=cfg.sig_w / 100, sig_m=cfg.sig_m / 100, win=cfg.win, D=D, L=cfg.L, mode=cfg.mode)
        pre = prep(P)
        res = simulate_year(P, pre, rseeds, bias, y)
        years.append(res)
        tot += res.annual
        sum50 += res.P[50]; sum90 += res.P[90]; sum95 += res.P[95]; det_sum += res.det
        srt = np.sort(tot)
        cum.append({"year": y, "p50": pval(srt, 50), "p90multi": pval(srt, 90), "p90sum": sum90, "det": det_sum})
        if progress:
            progress((i + 1) / n, y)
    srt = np.sort(tot)
    agg = {"total": tot, "sorted": srt, "mean": float(tot.mean()), "sd": float(tot.std(ddof=1)) if R > 1 else 0.0,
           "det_sum": det_sum, "sum_p50": sum50, "sum_p90": sum90, "sum_p95": sum95, "cum": cum,
           "P": {p: pval(srt, p) for p in (50, 75, 90, 95, 99)}}
    return {"years": years, "by_year": {r.year: r for r in years}, "agg": agg, "cfg": cfg}
