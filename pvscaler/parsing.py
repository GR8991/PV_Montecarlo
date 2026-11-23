"""Read PVsyst reports (PDF) and hourly PV files (CSV/TXT/Excel).

Ported from the browser tool so both versions read files the same way.
"""
from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass, field

import numpy as np

from .engine import MONTHS, MDAYS, MSTART, Mulberry32

LENS = [8760, 8784, 17520, 17568, 35040, 35136]
UNIT_RE = re.compile(r"^\s*\[?\(?\s*(W|kW|MW|GW|Wh|kWh|MWh|W/m²|W/m2|kWh/m²|°C|ratio|%)\s*\)?\]?\s*$", re.I)
_NUM_RE = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")
_COMMA_DEC = re.compile(r"^-?\d+,\d+$")


def to_num(v) -> float:
    if v is None or isinstance(v, bool):
        return math.nan
    if isinstance(v, (int, float, np.integer, np.floating)):
        v = float(v)
        return v if math.isfinite(v) else math.nan
    s = str(v).strip()
    if not s:
        return math.nan
    if _COMMA_DEC.match(s):
        s = s.replace(",", ".")
    if not _NUM_RE.match(s):
        return math.nan
    return float(s)


def _num_in(s) -> float:
    return float(str(s).replace(",", ""))


# --------------------------------------------------------------------------
# grids from files
# --------------------------------------------------------------------------
def parse_delimited(text: str):
    lines = text.replace("\r", "").split("\n")
    probe = "\n".join(lines[:60])
    best, best_n = ",", -1
    for d in [";", "\t", ","]:
        n = len(probe.split(d))
        if n > best_n:
            best_n, best = n, d
    out = []
    for line in lines:
        cells = []
        for c in line.split(best):
            c = c.strip()
            if len(c) >= 2 and c[0] == '"' and c[-1] == '"':
                c = c[1:-1]
            cells.append(c)
        out.append(cells)
    return out


def excel_sheets(data: bytes):
    """Yield (sheet_name, rows) for each worksheet."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    for ws in wb.worksheets:
        yield ws.title, [list(r) for r in ws.iter_rows(values_only=True)]


# --------------------------------------------------------------------------
# find full-year columns
# --------------------------------------------------------------------------
@dataclass
class Candidate:
    sheet: str
    col: int
    name: str
    unit: str
    length: int
    values: np.ndarray
    vmax: float
    vmin: float
    score: int
    file: str = ""

    @property
    def label(self) -> str:
        return (self.sheet + " › " if self.sheet else "") + self.name + (f" [{self.unit}]" if self.unit else "")


def _score(name: str, unit: str, sheet: str) -> int:
    n, s = name.lower(), 0
    if re.match(r"^e_?grid$", n):
        s += 100
    elif re.search(r"e_?grid|energy|power|output|net|poi|inject|ac\b|mw|gen", n):
        s += 30
    if re.search(r"grdlim|gridlim|unused|globhor|globinc|globeff|diff|t_?amb|temp|\bpr\b|ratio|irr|earray|eoutinv", n):
        s -= 60
    if re.match(r"^(k|m|g)?w$", unit, re.I):
        s += 20
    if re.search(r"w/m", unit, re.I):
        s -= 50
    if re.search(r"post", sheet or "", re.I):
        s += 15
    return s


def is_grid_lim(c: Candidate) -> bool:
    return bool(re.search(r"grdlim|gridlim|unused", c.name, re.I))


def find_candidates(rows, sheet: str = ""):
    out = []
    ncol = max((len(r) for r in rows if r), default=0)
    nrow = len(rows)
    for c in range(ncol):
        col = [to_num(r[c]) if (r and c < len(r)) else math.nan for r in rows]
        run_start, run_len, best_s, best_l = -1, 0, -1, 0
        for i in range(nrow + 1):
            v = col[i] if i < nrow else math.nan
            if not math.isnan(v):
                if run_start < 0:
                    run_start, run_len = i, 0
                run_len += 1
            else:
                if run_len > best_l:
                    best_l, best_s = run_len, run_start
                run_start, run_len = -1, 0
        L = 0
        for k in LENS:
            if best_l >= k and best_l - k <= 3:
                L = k
        if not L:
            continue
        vals = np.array(col[best_s:best_s + L], dtype=float)
        all_int = bool(np.all(vals == np.round(vals)))
        mx, mn = float(vals.max()), float(vals.min())
        if all_int and mn >= 0 and mx <= 31:
            continue  # month/day/hour index column
        if not np.any(vals != 0):
            continue
        name, unit = "", ""
        for u in range(best_s - 1, max(0, best_s - 8) - 1, -1):
            r = rows[u]
            cell = r[c] if (r and c < len(r)) else None
            if cell is None or str(cell).strip() == "":
                continue
            s = str(cell).strip()
            if not unit and UNIT_RE.match(s):
                unit = re.sub(r"[\[\]\(\)\s]", "", s)
                continue
            if not name and math.isnan(to_num(s)):
                name = s
                break
        if not name:
            name = f"Column {c + 1}"
        out.append(Candidate(sheet or "", c, name, unit, L, vals, mx, mn, _score(name, unit, sheet)))
    return out


def candidates_from_file(name: str, data: bytes):
    ext = name.rsplit(".", 1)[-1].lower()
    cands = []
    if ext in ("xlsx", "xlsm", "xls"):
        for sheet, rows in excel_sheets(data):
            cands += find_candidates(rows, sheet)
    else:
        text = data.decode("utf-8", errors="replace")
        cands = find_candidates(parse_delimited(text), "")
    for c in cands:
        c.file = name
    cands.sort(key=lambda c: -c.score)
    return cands


# --------------------------------------------------------------------------
# units and time step
# --------------------------------------------------------------------------
def unit_factor(unit: str, max_val: float, pnom_mw: float | None = None):
    u = (unit or "").lower()
    if u in ("w", "wh"):
        return 1e-6, "W", False
    if u in ("kw", "kwh"):
        return 1e-3, "kW", False
    if u in ("mw", "mwh"):
        return 1.0, "MW", False
    if u == "gw":
        return 1e3, "GW", False
    if pnom_mw and max_val > pnom_mw * 50:
        return (1e-6, "W", True) if max_val > pnom_mw * 50000 else (1e-3, "kW", True)
    if max_val > 5000:
        return 1e-3, "kW", True
    return 1.0, "MW", True


def to_hourly(vals):
    vals = np.asarray(vals, dtype=float)
    L = len(vals)
    notes = []
    per = 4 if L >= 35040 else (2 if L >= 17520 else 1)
    leap = L in (8784, 17568, 35136)
    h = vals.reshape(-1, per).mean(axis=1)
    if per > 1:
        notes.append(f"{60 // per}-minute data averaged to hourly")
    if leap:
        h = np.concatenate([h[:1416], h[1440:]])
        notes.append("Leap year: 29 February removed")
    return h, notes


# --------------------------------------------------------------------------
# PVsyst report
# --------------------------------------------------------------------------
def pdf_text(data: bytes) -> str:
    """Text of a PDF via pdfplumber (fallback: pypdf)."""
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n".join((page.extract_text() or "") for page in pdf.pages)
    except Exception:
        from pypdf import PdfReader
        return "\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(data)).pages)


def read_report(data: bytes) -> "Report":
    """Parse a PVsyst PDF, trying both text extractors and keeping the richer result."""
    best = parse_report_text(pdf_text(data))
    try:
        from pypdf import PdfReader
        alt = parse_report_text("\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(data)).pages))
        if alt.found > best.found or (alt.table and not best.table):
            best = alt
    except Exception:
        pass
    return best


@dataclass
class Report:
    fields: dict = field(default_factory=dict)
    monthly: list | None = None
    table: dict | None = None
    found: int = 0
    is_pvsyst: bool = False


def parse_report_text(raw: str) -> Report:
    t = re.sub(r"\s+", " ", raw)
    r = Report()
    F = r.fields

    def grab(pattern, fn=None):
        m = re.search(pattern, t)
        if not m:
            return None
        try:
            return fn(m) if fn else _num_in(m.group(1))
        except (ValueError, TypeError):
            return None

    F["project"] = grab(r"Project:\s*(.+?)\s+Variant:", lambda m: m.group(1).strip())
    F["variant"] = grab(r"Variant:\s*(.+?)\s+(?:Tracking|Sheds|Fixed|Unlimited|System power|Project summary|PVsyst)", lambda m: m.group(1).strip())
    F["version"] = grab(r"PVsyst V([\d.]+)", lambda m: m.group(1))
    p = grab(r"System power:\s*([\d.,]+)\s*(kWp|MWp)", lambda m: _num_in(m.group(1)) * (1e-3 if m.group(2) == "kWp" else 1))
    if p is None:
        p = grab(r"Pnom total\s+[\d,]+\s+([\d.,]+)\s*(?:units\s*)?(kWp|MWp)", lambda m: _num_in(m.group(1)) * (1e-3 if m.group(2) == "kWp" else 1))
    F["pnom_mwp"] = p
    F["annual_mwh"] = grab(r"Produced Energy\s+([\d.,]+)\s*(kWh|MWh|GWh)/year", lambda m: _num_in(m.group(1)) * {"kWh": 1e-3, "MWh": 1, "GWh": 1e3}[m.group(2)])
    F["specific_yield"] = grab(r"Specific production\s+([\d.,]+)\s*kWh/kWp")
    F["grid_limit_mw"] = grab(r"injection point\s+([\d.,]+)\s+[\d.]+\s*(kWac|MWac)", lambda m: _num_in(m.group(1)) * (1e-3 if m.group(2) == "kWac" else 1))
    if F["grid_limit_mw"] is None:
        F["grid_limit_mw"] = grab(r"Grid power limit[^%]{0,80}?\b\d+\s+[\d.]+\s+([\d.]+)\s+[\d.]+\s+units\s+kWac\s+MWac")
    F["hv_loss_pct"] = grab(r"-?([\d.]+)\s*%\s*High voltage transfo loss")
    sy = grab(r"Simulation for year no\s*(\d+)")
    F["sim_year"] = int(sy) if sy is not None else None
    F["degradation_pct"] = grab(r"([\d.]+)\s*%\s*/\s*year")
    F["lat"] = grab(r"Time zone\s+([-\d.]+)\s+[-\d.]+")
    F["lon"] = grab(r"Time zone\s+[-\d.]+\s+([-\d.]+)")
    F["tz"] = grab(r"(UTC[+-]\d+(?:[.:]\d+)?)", lambda m: float(m.group(1).replace("UTC", "").replace(":", ".")))
    F["weather_sigma_pct"] = grab(r"Year-to-year variability\s*\(Variance\)[\s\S]{0,220}?([\d.]+)\s*%")
    F["total_sigma_pct"] = grab(r"Quadratic sum\)\s*([\d.]+)\s*%")
    pv = re.search(r"P50\s+P90\s+P95\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*(GWh|MWh)", t)
    if pv:
        k = 1000 if pv.group(5) == "GWh" else 1
        F["p50_mwh"], F["p90_mwh"], F["p95_mwh"] = _num_in(pv.group(2)) * k, _num_in(pv.group(3)) * k, _num_in(pv.group(4)) * k
    else:
        F["p50_mwh"] = grab(r"P50\s*:?\s*([\d.]+)\s*GWh", lambda m: _num_in(m.group(1)) * 1000)
        F["p90_mwh"] = grab(r"P90\s*:?\s*([\d.]+)\s*GWh", lambda m: _num_in(m.group(1)) * 1000)
        F["p95_mwh"] = None

    hdr = re.search(r"GlobHor(?:\s+[A-Za-z_]+)+", t)
    if hdr:
        cols = []
        for tok in hdr.group(0).strip().split(" "):
            if re.match(r"^(kWh|MWh|GWh|W|ratio)$", tok, re.I):
                break
            cols.append(tok)
        gi = cols.index("E_Grid") if "E_Grid" in cols else -1

        def grab_row(label):
            for n in range(len(cols), gi, -1):
                m = re.search(label + r"\s+((?:-?[\d.]+\s+){" + str(n - 1) + r"}-?[\d.]+)(?![\d.])", t)
                if m:
                    nums = [float(x) for x in m.group(1).split()]
                    nums += [math.nan] * (len(cols) - len(nums))
                    return nums
            return None

        if gi >= 0:
            rows = [grab_row(mn) for mn in MONTHS]
            if all(rows):
                r.monthly = [row[gi] for row in rows]
                r.table = {"cols": cols, "rows": rows, "year": grab_row("Year")}
    r.found = sum(1 for v in F.values() if v is not None)
    # a PVsyst report, not a report produced by this app
    r.is_pvsyst = bool(re.search(r"PVsyst", t, re.I)) and not re.search(r"PV Monte Carlo Scaler", t) and (F.get("annual_mwh") is not None or F.get("pnom_mwp") is not None)
    return r


# --------------------------------------------------------------------------
# synthetic 8760 (screening only)
# --------------------------------------------------------------------------
def synth_8760(monthly_mwh, lat=None, lon=None, tz=None, cap_mw=None, tracking=True):
    lat = 30.0 if lat is None else lat
    lon = -90.0 if lon is None else lon
    tz = round(lon / 15) if tz is None else tz
    shape = np.zeros(8760)
    rng = Mulberry32([12345])
    phi = math.radians(lat)
    for d in range(365):
        B = 2 * math.pi * d / 365
        decl = (0.006918 - 0.399912 * math.cos(B) + 0.070257 * math.sin(B) - 0.006758 * math.cos(2 * B)
                + 0.000907 * math.sin(2 * B) - 0.002697 * math.cos(3 * B) + 0.00148 * math.sin(3 * B))
        eot = 229.18 * (0.000075 + 0.001868 * math.cos(B) - 0.032077 * math.sin(B) - 0.014615 * math.cos(2 * B) - 0.040849 * math.sin(2 * B))
        cloud = max(0.15, min(1.15, 0.8 + 0.35 * float(rng.normal()[0])))
        for h in range(24):
            tst = (h + 0.5) * 60 + eot + 4 * lon - 60 * tz
            ha = math.radians(tst / 4 - 180)
            se = math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.cos(ha)
            shape[d * 24 + h] = cloud * se ** (0.45 if tracking else 1.15) if se > 0 else 0.0
    cap = cap_mw if cap_mw else np.inf
    out = np.zeros(8760)
    for m in range(12):
        a, b = MSTART[m] * 24, (MSTART[m] + MDAYS[m]) * 24
        k = 1.0
        for _ in range(8):
            s = np.minimum(cap, shape[a:b] * k).sum()
            if s <= 0:
                break
            k *= monthly_mwh[m] / s
        out[a:b] = np.minimum(cap, shape[a:b] * k)
    return out
