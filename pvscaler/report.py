"""PDF reports in a PVsyst-style layout (reportlab).

build_report(ctx) -> PDF bytes
  ctx["kind"] == "single"   : one project year, 4 pages
  ctx["kind"] == "combined" : all years in the range, 6 pages (+ optional appendix:
                              one Main results page per year)
render_page_png(pdf_bytes, index) -> PNG bytes for the in-app preview.

Layout and wording match the browser tool's reports.
"""
from __future__ import annotations

import io
import math

from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as rl_canvas

MON = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
MS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
W, H, M = 595.28, 841.89, 40
CW = W - 2 * M
HALF = (CW - 8) / 2

C = {
    "ink": (0.09, 0.13, 0.17), "mute": (0.38, 0.43, 0.49), "line": (0.80, 0.83, 0.87), "band": (0.90, 0.93, 0.96),
    "head": (0.17, 0.30, 0.45), "acc": (0.80, 0.49, 0.05), "accS": (0.98, 0.91, 0.78), "blue": (0.17, 0.41, 0.66),
    "green": (0.25, 0.58, 0.36), "purple": (0.52, 0.36, 0.65), "zebra": (0.965, 0.972, 0.98),
}
_CP1252 = "€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ"


def clean(s) -> str:
    s = "" if s is None else str(s)
    for a, b in (("σ", "sigma"), ("−", "-"), ("‒", "-"), ("≥", ">="), ("≤", "<="), ("≈", "~"), ("→", "->"), (" ", " ")):
        s = s.replace(a, b)
    return "".join(ch if (32 <= ord(ch) <= 126 or 160 <= ord(ch) <= 255 or ch in _CP1252) else "?" for ch in s)


def f(n, dp=0) -> str:
    if n is None:
        return "-"
    try:
        x = float(n)
    except (TypeError, ValueError):
        return "-"
    if not math.isfinite(x):
        return "-"
    if abs(x) < 0.5 * 10 ** -dp:
        x = 0.0
    return f"{x:,.{dp}f}"


def gwh(v) -> str:
    return f(v / 1000, 1) + " GWh"


class _Doc:
    """Records drawing operations per page so footers can show 'Page n/N'."""

    def __init__(self):
        self.pages = []      # list of op lists
        self.labels = []
        self.ops = None
        self.y = M

    # --- primitives (top-down y) ---
    def txt(self, s, x, yy, size=9, bold=False, color="ink", align="left"):
        t = clean(s)
        font = "Helvetica-Bold" if bold else "Helvetica"
        w = stringWidth(t, font, size)
        if align == "right":
            x -= w
        elif align == "center":
            x -= w / 2
        self.ops.append(("text", t, x, H - yy, font, size, C[color] if isinstance(color, str) else color))
        return w

    @staticmethod
    def tw(s, size=9, bold=False):
        return stringWidth(clean(s), "Helvetica-Bold" if bold else "Helvetica", size)

    def rect(self, x, yy, w, h, fill=None, stroke=None, bw=0.6):
        self.ops.append(("rect", x, H - yy - h, w, h, C.get(fill) if isinstance(fill, str) else fill, C.get(stroke) if isinstance(stroke, str) else stroke, bw))

    def line(self, x1, y1, x2, y2, color="line", w=0.6, dash=None):
        self.ops.append(("line", x1, H - y1, x2, H - y2, C[color] if isinstance(color, str) else color, w, dash))

    def wrap(self, s, size, width, bold=False):
        out, cur = [], ""
        for word in clean(s).split(" "):
            t = (cur + " " + word) if cur else word
            if self.tw(t, size, bold) > width and cur:
                out.append(cur)
                cur = word
            else:
                cur = t
        if cur:
            out.append(cur)
        return out

    def para(self, s, x, width, size=9, color="ink", bold=False):
        for ln in self.wrap(s, size, width, bold):
            self.txt(ln, x, self.y + size, size=size, bold=bold, color=color)
            self.y += size * 1.4

    # --- page furniture ---
    def new_page(self, ctx, title=None, label=None):
        self.ops = []
        self.pages.append(self.ops)
        self.labels.append(label or title or "Page")
        self.y = M
        y = self.y
        self.txt("PV Monte Carlo Scaler", M, y + 8, size=9, bold=True, color="head")
        self.txt(ctx["header_right"], W - M, y + 8, size=8, color="mute", align="right")
        y += 14
        self.txt("Project: " + (ctx["meta"]["project"] or "-"), M, y + 8, size=8)
        v = "Variant: " + (ctx["meta"]["variant"] or "-")
        if self.tw(v, 8) > CW * 0.62:
            while self.tw(v + "...", 8) > CW * 0.62:
                v = v[:-1]
            v += "..."
        self.txt(v, W - M, y + 8, size=8, color="mute", align="right")
        y += 8
        self.line(M, y + 4, W - M, y + 4, color="head", w=1)
        y += 18
        if title:
            self.rect(M, y, CW, 20, fill="band")
            self.txt(title, M + 8, y + 14, size=12, bold=True, color="head")
            y += 32
        self.y = y

    def section(self, title):
        self.txt(title, M, self.y + 10, size=10.5, bold=True, color="head")
        self.line(M, self.y + 15, W - M, self.y + 15)
        self.y += 24

    def kv_box(self, x, w, rows, title=None, lw=None):
        y0 = self.y
        lw = lw or w * 0.5
        if title:
            self.txt(title, x + 6, self.y + 11, size=9, bold=True, color="head")
            self.y += 16
        for r in rows:
            self.txt(r[0], x + 6, self.y + 10, size=8.5, color="mute")
            self.txt(r[1], x + lw, self.y + 10, size=8.5, bold=len(r) > 2 and bool(r[2]))
            self.y += 13.5
        self.y += 4
        h = self.y - y0
        self.rect(x, y0, w, h, stroke="line")
        self.y = y0
        return h

    def boxes2(self, a, b):
        top = self.y
        h1 = self.kv_box(M, HALF, a["rows"], a.get("title"), a.get("lw"))
        h2 = self.kv_box(M + HALF + 8, HALF, b["rows"], b.get("title"), b.get("lw"))
        self.y = top + max(h1, h2) + 16

    def table(self, cols, rows, rh=14, size=8, head_h=26, head_size=7.8):
        x = M
        widths = [c[1] for c in cols]
        tot = sum(widths)
        self.rect(x, self.y, tot, head_h, fill="band")
        cx = x
        for i, (h, w) in enumerate(cols):
            parts = str(h).split("\n")
            al = "left" if i == 0 else "right"
            px = cx + 4 if i == 0 else cx + w - 4
            for k, s in enumerate(parts):
                self.txt(s, px, self.y + 10 + k * 9, size=head_size if k == 0 else 6.8, bold=k == 0, color="ink" if k == 0 else "mute", align=al)
            cx += w
        self.y += head_h
        for ri, r in enumerate(rows):
            vals, flag = (r["cells"], r.get("flag")) if isinstance(r, dict) else (r, None)
            if flag == "hl":
                self.rect(x, self.y, tot, rh, fill="accS")
            elif ri % 2 == 1:
                self.rect(x, self.y, tot, rh, fill="zebra")
            if flag == "total":
                self.line(x, self.y, x + tot, self.y, color="ink", w=0.8)
            cx = x
            for i, v in enumerate(vals):
                self.txt(v, cx + 4 if i == 0 else cx + widths[i] - 4, self.y + rh - 3.8, size=size, bold=flag in ("hl", "total"), align="left" if i == 0 else "right")
                cx += widths[i]
            self.y += rh
        self.line(x, self.y, x + tot, self.y)
        self.y += 8

    def axes(self, x, yy, w, h, top, step, fmt_y, label=None, bottom=0.0):
        v = bottom
        while v <= top + 1e-9:
            py = yy + h - h * (v - bottom) / (top - bottom)
            self.line(x, py, x + w, py, w=0.4)
            self.txt(fmt_y(v), x - 4, py + 3, size=7, color="mute", align="right")
            v += step
        self.line(x, yy + h, x + w, yy + h, color="mute", w=0.7)
        if label:
            self.txt(label, x - 4, yy - 6, size=7, color="mute")

    def legend(self, items, x, yy):
        cx = x
        for it in items:
            name, col = it[0], it[1]
            if len(it) > 2 and it[2] == "line":
                self.line(cx, yy - 3, cx + 12, yy - 3, color=col, w=1.6, dash=it[3] if len(it) > 3 else None)
            else:
                self.rect(cx, yy - 7, 12, 7, fill=col)
            self.txt(name, cx + 15, yy, size=7.5, color="mute")
            cx += 15 + self.tw(name, 7.5) + 14

    def bullets(self, items):
        for b in items:
            lines = self.wrap(b, 8.3, CW - 14)
            self.txt("•", M + 2, self.y + 8.3, size=8.3)
            for ln in lines:
                self.txt(ln, M + 12, self.y + 8.3, size=8.3)
                self.y += 11.8
            self.y += 3

    def toc(self, items):
        self.section("Table of contents")
        for name, pg in items:
            self.txt(name, M + 4, self.y + 9, size=9)
            self.txt(str(pg), W - M - 4, self.y + 9, size=9, align="right")
            self.line(M + 8 + self.tw(name, 9), self.y + 7, W - M - 14, self.y + 7, dash=[1, 2])
            self.y += 14

    # --- output ---
    def render(self, meta_generated, title):
        buf = io.BytesIO()
        cv = rl_canvas.Canvas(buf, pagesize=(W, H))
        cv.setTitle(clean(title))
        cv.setAuthor("PV Monte Carlo Scaler")
        cv.setCreator("PV Monte Carlo Scaler")
        n = len(self.pages)
        for k, ops in enumerate(self.pages):
            self.ops = ops
            self.line(M, H - 30, W - M, H - 30)
            self.txt(f"Generated by PV Monte Carlo Scaler, {meta_generated}. Derived from PVsyst outputs; not a PVsyst simulation.", M, H - 18, size=7, color="mute")
            self.txt(f"Page {k + 1}/{n}", W - M, H - 18, size=7, color="mute", align="right")
            for op in ops:
                kind = op[0]
                if kind == "text":
                    _, t, x, y, font, size, col = op
                    cv.setFillColorRGB(*col)
                    cv.setFont(font, size)
                    cv.drawString(x, y, t)
                elif kind == "rect":
                    _, x, y, w, h, fill, stroke, bw = op
                    if fill:
                        cv.setFillColorRGB(*fill)
                    if stroke:
                        cv.setStrokeColorRGB(*stroke)
                        cv.setLineWidth(bw)
                    cv.setDash([])
                    cv.rect(x, y, w, h, stroke=1 if stroke else 0, fill=1 if fill else 0)
                elif kind == "line":
                    _, x1, y1, x2, y2, col, w, dash = op
                    cv.setStrokeColorRGB(*col)
                    cv.setLineWidth(w)
                    cv.setDash(dash or [])
                    cv.line(x1, y1, x2, y2)
            cv.showPage()
        cv.save()
        return buf.getvalue()


def _nice_top(mx, n):
    raw = mx / n
    mag = 10 ** math.floor(math.log10(raw if raw > 0 else 1))
    st = next(k * mag for k in (1, 2, 2.5, 5, 10) if k * mag >= raw)
    return st, math.ceil(mx / st) * st


def build_report(ctx, with_labels: bool = False):
    d = _Doc()
    c, sys_ = ctx["cfg"], ctx["sys"]

    def weather_rows():
        return [["Year-to-year weather sigma", f(c.sig_w, 1) + " %"],
                ["Modelling uncertainty", (f(ctx["sig_u"] * 100, 1) + " % (total " + f(c.sig_t, 1) + " %)") if c.use_unc else "not included"],
                ["Single-year sigma", f(ctx["sigma_used"] * 100, 1) + " %", True],
                ["Month-to-month sigma", f(c.sig_m, 1) + " %"], ["Day resampling window", "± " + str(c.win) + " days"],
                ["Runs / seed", f(c.runs) + " / " + str(c.seed)]]

    def title_block(title, sub):
        d.y += 6
        d.txt(title, M, d.y + 22, size=22, bold=True); d.y += 34
        d.txt(sub, M, d.y + 10, size=10.5, color="mute"); d.y += 26
        d.txt("Project: " + (ctx["meta"]["project"] or "-"), M, d.y + 10, size=10, bold=True); d.y += 15
        for ln in d.wrap("Variant: " + (ctx["meta"]["variant"] or "-"), 9, CW):
            d.txt(ln, M, d.y + 9, size=9, color="mute"); d.y += 13
        ver = ctx["meta"].get("version")
        d.txt("Generated " + ctx["meta"]["generated"] + (f"   |   Source: PVsyst V{ver} simulation report" if ver else ""), M, d.y + 9, size=8.5, color="mute")
        d.y += 26

    def project_summary():
        d.section("Project summary")
        colw = (CW - 16) / 3
        top = d.y
        h1 = d.kv_box(M, colw, [["Latitude", (f(sys_["lat"], 4) + " °") if sys_.get("lat") is not None else "-"],
                                ["Longitude", (f(sys_["lon"], 4) + " °") if sys_.get("lon") is not None else "-"],
                                ["Time zone", ("UTC" + ("+" if sys_["tz"] >= 0 else "") + f(sys_["tz"], 0)) if sys_.get("tz") is not None else "-"]], "Geographical site", colw * 0.48)
        h2 = d.kv_box(M + colw + 8, colw, [["PV array", (f(sys_["pnom"], 1) + " MWp") if sys_.get("pnom") else "-"],
                                           ["Grid limit", (f(sys_["grid_limit"], 1) + " MWac") if sys_.get("grid_limit") else "-"],
                                           ["Output limit used", (f(c.cap, 1) + " MW") if c.cap else "none"],
                                           ["Source sim. year", str(sys_.get("sim_year") or 1)]], "System", colw * 0.52)
        m = ctx["meta"]
        h3 = d.kv_box(M + 2 * (colw + 8), colw, [["Hourly file", m["series_short"]], ["Column", m["column"]], ["Units", m["units"]],
                                                 ["Grid-lim. add-back", "yes" if m["add_back"] else "no"]], "Source data", colw * 0.46)
        d.y = top + max(h1, h2, h3) + 16

    def synth_banner():
        if ctx["meta"].get("synthetic"):
            d.rect(M, d.y, CW, 20, fill="accS")
            d.txt("Hourly data is a synthetic profile built from monthly totals. Use these results for screening only.", M + 8, d.y + 13, size=8.5, bold=True)
            d.y += 28

    def method_bullets():
        return [
            'Base profile: the hourly file is treated as project year 1, matching the PVsyst "simulation for year no 1" setting, which already includes year-1 module degradation.',
            "Degradation: year N output = base x " + ("(1 - d)^(N-1)" if c.deg_model == "compound" else "(1 - d(N-1))") + ", d = " + f(c.deg, 2) + " %/year. Every year in the range is simulated separately.",
            "Weather: each simulated year draws its own annual factor with sigma " + f(c.sig_w, 1) + " %, independent from year to year, so good and bad years average out over a longer period.",
            ("Modelling uncertainty: one factor with sigma " + f(ctx["sig_u"] * 100, 1) + " % is drawn per run and applied to every year of that run, because a systematic model error does not average out. A single year therefore has sigma " + f(ctx["sigma_used"] * 100, 1) + " % (quadratic sum, as in PVsyst).") if c.use_unc else "Modelling uncertainty is not included; only weather variability is simulated.",
            "Monthly pattern: monthly energy is perturbed with sigma " + f(c.sig_m, 1) + " %, then rescaled so the year total equals the annual draw.",
            "Daily shapes: whole days are resampled within the same month (± " + str(c.win) + " days), so hourly shapes always come from the source data.",
            "Limits: when the grid-limited energy (EGrdLim) is available, the unclipped power is scaled and the grid limit of " + ((f(c.cap, 1) + " MW") if c.cap else "-") + " is re-applied. Night-time negative values are transformer no-load losses supplied by the grid and are not scaled.",
            ("Weather method: as in PVsyst, the annual weather factor scales delivered energy." if c.mode == "energy" else "Weather method: the annual weather factor scales sunlight before the grid limit, so poor years lose less where output was limited."),
            "P50 anchoring: each year's runs are scaled so that P50 equals the source simulation x degradation for that year, as PVsyst defines P50. All energy values are net E_Grid (generation minus night transformer losses)" + ("." if c.L == 1 else ", multiplied by the measuring-point factor " + f(c.L, 4) + "."),
            "P-levels: P90 is the energy exceeded in 90 % of runs. Multi-year P-levels are taken from the distribution of each run's total over the period, not by adding single-year values. Exported 8760 P-level profiles are the run closest to each quantile, trimmed to match it exactly.",
            "This report is derived from PVsyst outputs by statistical scaling. It is not a PVsyst simulation and contains no financial analysis.",
        ]

    def main_page(Y, label):
        T, yr = Y["main"], Y["year"]
        d.new_page(ctx, f"Main results - project year {yr} ({Y['cal']})", label)
        d.section("System production")
        sp = [["Produced energy", f(T["p50"]["egrid"]) + " MWh/year", True]]
        p9 = [["Produced energy", f(T["p90"]["egrid"]) + " MWh/year", True]]
        if T["has_irr"]:
            sp += [["Specific production", f(T["p50"]["egrid"] / sys_["pnom"]) + " kWh/kWp/year"], ["Performance ratio PR", f(T["p50"]["pr"] * 100, 2) + " %"]]
            p9 += [["Specific production", f(T["p90"]["egrid"] / sys_["pnom"]) + " kWh/kWp/year"], ["Performance ratio PR", f(T["p90"]["pr"] * 100, 2) + " %"]]
        d.boxes2({"rows": sp, "title": f"P50, project year {yr}", "lw": HALF * 0.45}, {"rows": p9, "title": f"P90, project year {yr}", "lw": HALF * 0.45})
        chH, chW, cy = 150, (CW - 40) / 2, d.y + 24
        months = T["months"]
        if T["has_irr"]:
            d.txt("Normalized productions (per installed kWp)", M, d.y + 8, size=8.5, bold=True)
            d.txt("Performance ratio PR (year: " + f(T["p50"]["pr"], 3) + ")", M + chW + 40, d.y + 8, size=8.5, bold=True)
            stack = [(m["yf"], m["ls"], m["lc"]) for m in months]
            mx = max(s[0] + max(0, s[1]) + max(0, s[2]) for s in stack)
            st, top = _nice_top(mx * 1.05, 4)
            ax = M + 26
            d.axes(ax, cy + 6, chW - 26, chH, top, st, lambda v: f(v, 1), "kWh/kWp/day")
            bw = (chW - 26) / 12
            for i, s in enumerate(stack):
                bx, base, sc, acc = ax + i * bw + bw * 0.15, cy + 6 + chH, chH / top, 0
                for val, col in ((s[0], "acc"), (max(0, s[1]), "green"), (max(0, s[2]), "purple")):
                    hh = val * sc
                    d.rect(bx, base - acc - hh, bw * 0.7, hh, fill=col)
                    acc += hh
                d.txt(MS[i][0], ax + i * bw + bw / 2, base + 10, size=7, color="mute", align="center")
            px = M + chW + 40 + 26
            d.axes(px, cy + 6, chW - 26, chH, 1, 0.2, lambda v: f(v, 1), "PR")
            for i, m in enumerate(months):
                hh = chH * max(0, m["pr"])
                d.rect(px + i * bw + bw * 0.15, cy + 6 + chH - hh, bw * 0.7, hh, fill="blue")
                d.txt(MS[i][0], px + i * bw + bw / 2, cy + 6 + chH + 10, size=7, color="mute", align="center")
            py = cy + 6 + chH - chH * T["p50"]["pr"]
            d.line(px, py, px + chW - 26, py, color="acc", w=1, dash=[3, 2])
            d.y = cy + chH + 24
            d.legend([["Yf: produced useful energy (inverter output)", "acc"], ["Ls: system loss", "green"], ["Lc: collection loss (PV-array)", "purple"]], M, d.y + 4)
            d.y += 18
        else:
            d.txt("Monthly produced energy, P50 and P90 (MWh)", M, d.y + 8, size=8.5, bold=True)
            mx = max(m["p50"] for m in months)
            st, top = _nice_top(mx * 1.05, 4)
            ax, wE = M + 34, CW - 34
            bwE = wE / 12
            d.axes(ax, cy + 6, wE, chH, top, st, lambda v: f(v), "MWh")
            for i, m in enumerate(months):
                sc, bx = chH / top, ax + i * bwE + bwE * 0.12
                d.rect(bx, cy + 6 + chH - m["p50"] * sc, bwE * 0.36, m["p50"] * sc, fill="acc")
                d.rect(bx + bwE * 0.38, cy + 6 + chH - m["p90"] * sc, bwE * 0.36, m["p90"] * sc, fill="blue")
                d.txt(MS[i], ax + i * bwE + bwE / 2, cy + 6 + chH + 10, size=7, color="mute", align="center")
            d.y = cy + chH + 24
            d.legend([["P50", "acc"], ["P90", "blue"]], M, d.y + 4)
            d.y += 18
        d.section(f"Balances and main results (P50, project year {yr})")
        p50, p90 = T["p50"], T["p90"]
        if T["has_irr"]:
            cols = [("", 58), ("GlobHor\nkWh/m2", 46), ("DiffHor\nkWh/m2", 46), ("T_Amb\n°C", 36), ("GlobInc\nkWh/m2", 48), ("GlobEff\nkWh/m2", 48),
                    ("EArray\nMWh", 50), ("E_Grid\nMWh", 52), ("PR\nratio", 36), ("E_Grid P90\nMWh", 54), ("vs yr 1\n%", CW - 474)]
            rows = [[MON[i], f(m["glob_hor"], 1), f(m["diff_hor"], 2), f(m["tamb"], 2), f(m["glob_inc"], 1), f(m["glob_eff"], 1), f(m["earray"]), f(m["egrid"]),
                     f(m["pr"], 3), f(m["p90net"]), f(m["vs_base"], 1)] for i, m in enumerate(months)]
            rows.append({"cells": ["Year", f(p50["glob_hor"], 1), f(p50["diff_hor"], 2), f(p50["tamb"], 2), f(p50["glob_inc"], 1), f(p50["glob_eff"], 1), f(p50["earray"]),
                                   f(p50["egrid"]), f(p50["pr"], 3), f(p90["egrid"]), f(p50["vs_base"], 1)], "flag": "total"})
        else:
            cols = [("", 80), ("Generation P50\nMWh", 80), ("Night import\nMWh", 70), ("E_Grid net P50\nMWh", 80), ("E_Grid net P90\nMWh", 80), ("Degradation only\nMWh", 80), ("vs yr 1\n%", CW - 470)]
            rows = [[MON[i], f(m["p50"]), f(-m["aux"]), f(m["egrid"]), f(m["p90net"]), f(m["det"]), f(m["vs_base"], 1)] for i, m in enumerate(months)]
            rows.append({"cells": ["Year", f(p50["gen"]), f(-p50["aux"]), f(p50["egrid"]), f(p90["egrid"]), f(T["det"]["egrid"]), f(p50["vs_base"], 1)], "flag": "total"})
        d.table(cols, rows, rh=13.5, size=7.8)
        d.para(("Monthly values spread the annual P50 (and P90) over the months in the same proportions as the degradation-only case. GlobHor, GlobInc, GlobEff: source TMY values x the P50 weather factor. DiffHor and T_Amb: source TMY values, unchanged. EArray: source value x weather factor x degradation. E_Grid: net of night transformer losses" + (", x measuring-point factor" if c.L != 1 else "") + '. PR = E_Grid / (GlobInc x Pnom). "vs yr 1" compares E_Grid with the source year-1 value.')
               if T["has_irr"] else "Energy columns from the simulated profiles. No PVsyst report was loaded, so irradiance, PR and specific production are not shown.", M, CW, size=7.3, color="mute")

    def histogram(sorted_vals, Pq, title):
        d.txt(title, M, d.y + 8, size=8.5, bold=True)
        d.y += 16
        s = sorted_vals
        lo, hi, nb = float(s[0]), float(s[-1]), 40
        bwv = (hi - lo) / nb or 1
        bins = [0] * nb
        for v in s:
            bins[min(nb - 1, int((v - lo) // bwv))] += 1
        mb = max(bins)
        dx, dw, dh, dy = M + 30, CW - 30, 160, d.y + 6
        topc = math.ceil(mb / 10) * 10 or 10
        d.axes(dx, dy, dw, dh, topc, max(5, math.ceil(mb / 40) * 10), lambda v: f(v), "runs")
        xs = lambda v: dx + dw * (v - lo) / ((hi - lo) or 1)
        for k, n in enumerate(bins):
            hh = dh * n / topc
            d.rect(xs(lo + k * bwv) + 0.4, dy + dh - hh, dw / nb - 0.8, hh, fill="acc")
        for p, col in ((50, "ink"), (90, "blue"), (95, "purple")):
            vx = xs(Pq[p])
            d.line(vx, dy, vx, dy + dh, color=col, w=1, dash=[3, 2])
            d.txt(f"P{p} " + f(Pq[p] / 1000, 1), vx + 3, dy + 8 + (10 if p == 95 else 0), size=7, color=col)
        for j in range(6):
            v = lo + (hi - lo) * j / 5
            d.txt(f(v / 1000), xs(v), dy + dh + 10, size=7, color="mute", align="left" if j == 0 else "right" if j == 5 else "center")
        d.txt("GWh", dx + dw / 2, dy + dh + 21, size=7, color="mute", align="center")
        d.y = dy + dh + 34

    src = ctx.get("src_p")

    # ============================== SINGLE YEAR ==============================
    if ctx["kind"] == "single":
        Y = ctx["year"]
        T, yr, cal = Y["main"], Y["year"], Y["cal"]
        d.new_page(ctx, None, "Summary")
        title_block("Scaled yield report", f"Project year {yr} ({cal}) - Monte Carlo weather variability and module degradation")
        project_summary()
        d.section("Scaling parameters")
        d.boxes2({"title": "Degradation", "lw": HALF * 0.5, "rows": [["Project year", f"{yr} ({cal})", True], ["Project year 1 (COD)", str(c.cod)],
                                                                    ["Module degradation", f(c.deg, 2) + " %/year, " + c.deg_model], [f"Degradation factor yr {yr}", f(Y["D"], 4)],
                                                                    ["Measuring-point factor", f(c.L, 4)]]},
                 {"title": "Monte Carlo weather", "lw": HALF * 0.5, "rows": weather_rows()})
        d.section(f"Results summary - project year {yr}")
        sr = [["Produced energy (P50)", f(T["p50"]["egrid"]) + " MWh/year", True]]
        if T["has_irr"]:
            sr += [["Specific production (P50)", f(T["p50"]["egrid"] / sys_["pnom"]) + " kWh/kWp/year"], ["Performance ratio PR (P50)", f(T["p50"]["pr"] * 100, 2) + " %"]]
        sr += [["Produced energy (P90)", f(T["p90"]["egrid"]) + " MWh/year"], ["Degradation only (no weather)", f(T["det"]["egrid"]) + " MWh/year"]]
        if src:
            sr.append([f"Source report P50 / P90 (yr {sys_.get('sim_year') or 1})", gwh(src["p50"]) + " / " + gwh(src["p90"])])
        P = Y["P"]
        d.boxes2({"title": "System production", "lw": HALF * 0.52, "rows": sr},
                 {"title": "Annual production probability (net E_Grid)", "lw": HALF * 0.5,
                  "rows": [["P50", gwh(P[50]), True], ["P75", gwh(P[75])], ["P90", gwh(P[90]), True], ["P95", gwh(P[95])], ["P99", gwh(P[99])], ["Night import (transformer losses)", f(-T["p50"]["aux"]) + " MWh/year"]]})
        synth_banner()
        d.toc([["Project and results summary", 1], ["Main results", 2], ["P50 - P90 evaluation", 3], ["Year-by-year production and method", 4]])

        main_page(Y, "Main results")

        d.new_page(ctx, "P50 - P90 evaluation", "P50–P90")
        d.boxes2({"title": "Variability", "lw": HALF * 0.52, "rows": weather_rows()[:3] + [["Spread after clipping", f(Y["sd"] / Y["mean"] * 100, 1) + " %"], ["Mean clipped energy", f(Y["mean_clip"]) + " MWh/year"]]},
                 {"title": f"Annual production probability, year {yr}", "lw": HALF * 0.5,
                  "rows": [["P50", gwh(P[50]), True], ["P75", gwh(P[75])], ["P90", gwh(P[90]), True], ["P95", gwh(P[95])], ["P99", gwh(P[99])], ["P90 / P50", f(P[90] / P[50] * 100, 1) + " %"]]})
        histogram(Y["sorted"], P, f"Probability distribution of annual net E_Grid ({f(c.runs)} runs)")
        if src:
            d.section("Comparison with source report")
            rows = [[f"Source report (year {sys_.get('sim_year') or 1})", gwh(src["p50"]), gwh(src["p90"]), gwh(src["p95"]) if src.get("p95") else "-", f(src["p90"] / src["p50"] * 100, 1) + " %"],
                    [f"Source x degradation (year {yr})", gwh(src["p50"] * Y["D"]), gwh(src["p90"] * Y["D"]), gwh(src["p95"] * Y["D"]) if src.get("p95") else "-", f(src["p90"] / src["p50"] * 100, 1) + " %"],
                    {"cells": [f"This Monte Carlo (year {yr}, net E_Grid)", gwh(P[50]), gwh(P[90]), gwh(P[95]), f(P[90] / P[50] * 100, 1) + " %"], "flag": "hl"}]
            d.table([("", 200), ("P50", 75), ("P90", 75), ("P95", 75), ("P90 / P50", CW - 425)], rows, rh=15, size=8.2, head_h=18)
            d.para("Differences come from hourly clipping at the output limit (good-weather years gain less than bad years lose), the month and day resampling, and sampling noise. Source report values are net energy at the injection point.", M, CW, size=7.5, color="mute")
            d.y += 6
        d.section("Monthly range across runs (MWh, net E_Grid)")
        d.table([("", 110), ("P90\nexceeded 90 %", 100), ("P50\nmedian", 100), ("P10\nexceeded 10 %", 100), ("Range\n% of P50", CW - 410)],
                [[MON[i], f(q["p90"]), f(q["p50"]), f(q["p10"]), f((q["p10"] - q["p90"]) / q["p50"] * 100, 1)] for i, q in enumerate(Y["monthly_q"])], rh=12.5, size=7.8)

        d.new_page(ctx, "Year-by-year production and method", "Year by year")
        life = ctx["life"]
        lr = []
        for r in life:
            cells = [str(r["y"]), str(r["cal"]), f(r["D"], 4), f(r["det"]), f(r["p50"]), f(r["p90"]), f(r["p95"]), f(r["p50"] / sys_["pnom"]) if sys_.get("pnom") else "-"]
            lr.append({"cells": cells, "flag": "hl" if r["y"] == yr else None})
        if len(lr) > 30:
            lr = lr[-30:]
        d.table([("Project year", 62), ("Calendar", 52), ("Degradation\nfactor", 62), ("Degradation only\nMWh", 76), ("P50\nMWh", 70), ("P90\nMWh", 70), ("P95\nMWh", 70), ("P50 yield\nkWh/kWp", CW - 462)], lr, rh=12.2, size=7.8)
        d.para("Net E_Grid (generation minus night transformer losses), as in PVsyst. " + (f"Year {yr} only, as selected." if len(life) == 1 else f"Simulated years {life[0]['y']} to {yr} of the selected range; each year is simulated separately."), M, CW, size=7.3, color="mute")
        d.y += 6
        d.section("Method and assumptions")
        d.bullets(method_bullets())
        title = f"Scaled yield report - {ctx['meta']['project'] or 'PV'} - Year {yr}"

    # ============================ ALL YEARS COMBINED ============================
    else:
        Ys, A = ctx["years"], ctx["agg"]
        n = len(Ys)
        y0, y1 = Ys[0]["year"], Ys[-1]["year"]
        has_irr = Ys[0]["main"]["has_irr"]
        range_txt = f"Project years {y0}-{y1} ({Ys[0]['cal']}-{Ys[-1]['cal']}), {n} year" + ("s" if n > 1 else "")
        toc = [["Project and lifetime summary", 1], ["Lifetime production", 2], ["Year-by-year production", 3], ["Monthly production by year", 4], ["Multi-year P50 - P90 evaluation", 5], ["Method and assumptions", 6]]
        if ctx.get("appendix"):
            toc.append(["Appendix: Main results for each year", f"7 - {6 + n}"])
        AP = A["P"]

        d.new_page(ctx, None, "Summary")
        title_block("Lifetime yield report", range_txt + " - Monte Carlo weather variability and module degradation")
        project_summary()
        d.section("Scaling parameters")
        d.boxes2({"title": "Range and degradation", "lw": HALF * 0.5, "rows": [["Years simulated", f"{y0} to {y1} ({n})", True], ["Calendar years", f"{Ys[0]['cal']} to {Ys[-1]['cal']}"],
                                                                              ["Project year 1 (COD)", str(c.cod)], ["Module degradation", f(c.deg, 2) + " %/year, " + c.deg_model],
                                                                              [f"Degradation factor yr {y1}", f(Ys[-1]["D"], 4)], ["Measuring-point factor", f(c.L, 4)]]},
                 {"title": "Monte Carlo weather", "lw": HALF * 0.5, "rows": weather_rows()})
        d.section("Results summary - " + range_txt)
        fy, ly = Ys[0], Ys[-1]
        r1 = [["Total energy P50", gwh(AP[50]), True], ["Total energy P90", gwh(AP[90]), True], ["Total energy P95", gwh(AP[95])], ["Average annual P50", gwh(AP[50] / n)],
              ["Average annual P90", gwh(AP[90] / n)], ["Night import, total", f(-fy["main"]["p50"]["aux"] * n) + " MWh"]]
        r2 = [[f"Year {fy['year']} P50 / P90", gwh(fy["P"][50]) + " / " + gwh(fy["P"][90])], [f"Year {ly['year']} P50 / P90", gwh(ly["P"][50]) + " / " + gwh(ly["P"][90])]]
        if has_irr:
            r2 += [[f"PR P50, year {fy['year']}", f(fy["main"]["p50"]["pr"] * 100, 2) + " %"], [f"PR P50, year {ly['year']}", f(ly["main"]["p50"]["pr"] * 100, 2) + " %"],
                   ["Specific yield P50, avg", f(AP[50] / n / sys_["pnom"]) + " kWh/kWp/yr"]]
        if src:
            r2.append([f"Source report P50 / P90 (yr {sys_.get('sim_year') or 1})", gwh(src["p50"]) + " / " + gwh(src["p90"])])
        d.boxes2({"title": "Range totals (net E_Grid)", "lw": HALF * 0.5, "rows": r1}, {"title": "First and last year", "lw": HALF * 0.5, "rows": r2})
        synth_banner()
        d.toc(toc)

        # lifetime charts
        d.new_page(ctx, "Lifetime production", "Lifetime")
        d.section("Annual net E_Grid by year")
        chX, chW2, chH2, cy2 = M + 34, CW - 34, 175, d.y + 12
        max_v = max(max(Yy["P"][10], Yy["det"]) for Yy in Ys)
        min_v = min(Yy["P"][95] for Yy in Ys)
        st, top = _nice_top(max_v / 1000 * 1.03, 5)
        bot = max(0, math.floor(min_v / 1000 * 0.95 / st) * st)
        d.axes(chX, cy2, chW2, chH2, top, st, lambda v: f(v), "GWh", bot)
        xsY = (lambda i: chX + chW2 / 2) if n == 1 else (lambda i: chX + 10 + (chW2 - 20) * i / (n - 1))
        ysY = lambda v: cy2 + chH2 - chH2 * (v / 1000 - bot) / (top - bot)
        bwY = min(14, (chW2 - 20) / max(1, n) * 0.6)
        for i, Yy in enumerate(Ys):
            x = xsY(i)
            d.rect(x - bwY / 2, ysY(Yy["P"][10]), bwY, max(0.5, ysY(Yy["P"][90]) - ysY(Yy["P"][10])), fill="accS")

        def poly(key, col, dash, w=1.4):
            for i in range(1, n):
                d.line(xsY(i - 1), ysY(key(Ys[i - 1])), xsY(i), ysY(key(Ys[i])), color=col, w=w, dash=dash)
            if n == 1:
                d.line(xsY(0) - 6, ysY(key(Ys[0])), xsY(0) + 6, ysY(key(Ys[0])), color=col, w=w)
        poly(lambda Yy: Yy["det"], "blue", [3, 2], 1)
        poly(lambda Yy: Yy["P"][50], "acc", None, 1.8)
        poly(lambda Yy: Yy["P"][90], "ink", [1.5, 1.5], 1)
        stepL = max(1, math.ceil(n / 13))
        for i, Yy in enumerate(Ys):
            if i % stepL == 0 or i == n - 1:
                d.txt(str(Yy["year"]), xsY(i), cy2 + chH2 + 10, size=7, color="mute", align="center")
        d.txt("Project year", chX + chW2 / 2, cy2 + chH2 + 21, size=7, color="mute", align="center")
        d.y = cy2 + chH2 + 34
        d.legend([["P90 to P10 range", "accS"], ["P50", "acc", "line"], ["P90", "ink", "line", [1.5, 1.5]], ["Degradation only", "blue", "line", [3, 2]]], M, d.y)
        d.y += 20
        d.section("Cumulative net E_Grid")
        cum = A["cum"]
        cy3, chH3 = d.y + 12, 150
        cum_max = max(cum[-1]["det"], cum[-1]["p50"])
        stC, topC = _nice_top(cum_max / 1000 * 1.05, 4)
        d.axes(chX, cy3, chW2, chH3, topC, stC, lambda v: f(v), "GWh")
        ysC = lambda v: cy3 + chH3 - chH3 * (v / 1000) / topC

        def cum_line(key, col, dash, w=1.4):
            for i in range(n):
                x0 = (xsY(0) - ((xsY(1) - xsY(0)) if n > 1 else 10)) if i == 0 else xsY(i - 1)
                v0 = 0 if i == 0 else key(cum[i - 1])
                d.line(x0, ysC(v0), xsY(i), ysC(key(cum[i])), color=col, w=w, dash=dash)
        cum_line(lambda q: q["p50"], "acc", None, 1.8)
        cum_line(lambda q: q["p90multi"], "ink", [1.5, 1.5], 1.2)
        cum_line(lambda q: q["p90sum"], "purple", [4, 2], 1.2)
        for i, Yy in enumerate(Ys):
            if i % stepL == 0 or i == n - 1:
                d.txt(str(Yy["year"]), xsY(i), cy3 + chH3 + 10, size=7, color="mute", align="center")
        d.y = cy3 + chH3 + 24
        d.legend([["Cumulative P50", "acc", "line"], ["Multi-year P90 of the cumulative total", "ink", "line", [1.5, 1.5]], ["Sum of single-year P90s", "purple", "line", [4, 2]]], M, d.y)
        d.y += 14
        d.para("The multi-year P90 line sits above the sum of single-year P90s because independent weather years partly cancel out over time. Only the modelling uncertainty, which applies to every year of a run, does not average out.", M, CW, size=7.5, color="mute")

        # year table
        d.new_page(ctx, "Year-by-year production", "Year by year")
        cols = [("Year", 34), ("Calendar", 44), ("Degr.\nfactor", 42), ("Degr. only\nMWh", 58), ("P50\nMWh", 56), ("P75\nMWh", 56), ("P90\nMWh", 56), ("P95\nMWh", 56), ("P90/P50\n%", 40), ("PR\nP50", 34), ("Yield\nkWh/kWp", CW - 476)]
        rh3 = 11.2 if n > 40 else 12.4
        rows = [[str(Yy["year"]), str(Yy["cal"]), f(Yy["D"], 4), f(Yy["det"]), f(Yy["P"][50]), f(Yy["P"][75]), f(Yy["P"][90]), f(Yy["P"][95]), f(Yy["P"][90] / Yy["P"][50] * 100, 1),
                 f(Yy["main"]["p50"]["pr"], 3) if has_irr else "-", f(Yy["P"][50] / sys_["pnom"]) if sys_.get("pnom") else "-"] for Yy in Ys]
        rows.append({"cells": ["Range total", "", "", f(A["det_sum"]), f(AP[50]), f(AP[75]), f(AP[90]), f(AP[95]), f(AP[90] / AP[50] * 100, 1), "", ""], "flag": "total"})
        d.table(cols, rows, rh=rh3, size=7.1 if n > 40 else 7.6)
        d.para("Net E_Grid (generation minus night transformer losses), as in PVsyst. Each row is a separate Monte Carlo simulation of that year. The total row gives the P-levels of the range total across runs (multi-year), not the sum of the single-year values; the sum of single-year P90s is " + f(A["sum_p90"]) + " MWh.", M, CW, size=7.3, color="mute")

        # monthly matrix
        d.new_page(ctx, "Monthly production by year", "Monthly")
        d.section("E_Grid P50 by month (MWh, net of night transformer losses)")
        mcols = [("Year", 30)] + [(m, 33.5) for m in MS] + [("Total", 45), ("PR", CW - 30 - 402 - 45)]
        mrows = [[str(Yy["year"])] + [f(m["egrid"]) for m in Yy["main"]["months"]] + [f(Yy["main"]["p50"]["egrid"]), f(Yy["main"]["p50"]["pr"], 3) if has_irr else "-"] for Yy in Ys]
        d.table(mcols, mrows, rh=11 if n > 40 else 12.2, size=6.3 if n > 40 else 6.7, head_h=16, head_size=7.2)
        d.para("Monthly values spread each year's P50 over the months in the degradation-only proportions. PR = E_Grid / (GlobInc x Pnom), with irradiance scaled by the weather factor.", M, CW, size=7.3, color="mute")

        # multi-year P50-P90
        d.new_page(ctx, "Multi-year P50 - P90 evaluation", "Multi-year P50–P90")
        d.boxes2({"title": "Variability", "lw": HALF * 0.55, "rows": [["Weather sigma, per year", f(c.sig_w, 1) + " % (independent)"],
                                                                      ["Modelling sigma, per run", (f(ctx["sig_u"] * 100, 1) + " % (all years)") if c.use_unc else "not included"],
                                                                      ["Single-year sigma", f(ctx["sigma_used"] * 100, 1) + " %"], ["Range total spread", f(A["sd"] / A["mean"] * 100, 1) + " %", True],
                                                                      ["Runs / seed", f(c.runs) + " / " + str(c.seed)]]},
                 {"title": f"Range total probability ({n} years)", "lw": HALF * 0.5, "rows": [["P50", gwh(AP[50]), True], ["P75", gwh(AP[75])], ["P90", gwh(AP[90]), True], ["P95", gwh(AP[95])],
                                                                                        ["P99", gwh(AP[99])], ["P90 / P50", f(AP[90] / AP[50] * 100, 1) + " %"]]})
        histogram(A["sorted"], AP, f"Probability distribution of total net E_Grid, years {y0}-{y1} ({f(c.runs)} runs)")
        d.section("Single-year vs multi-year P-levels")
        mid = Ys[(n - 1) // 2]
        rows = [{"cells": ["Range total, multi-year", gwh(AP[50]), gwh(AP[90]), gwh(AP[95]), f(AP[90] / AP[50] * 100, 1) + " %"], "flag": "hl"},
                ["Range total, sum of single-year values", gwh(A["sum_p50"]), gwh(A["sum_p90"]), gwh(A["sum_p95"]), f(A["sum_p90"] / A["sum_p50"] * 100, 1) + " %"],
                ["Average year, multi-year", gwh(AP[50] / n), gwh(AP[90] / n), gwh(AP[95] / n), f(AP[90] / AP[50] * 100, 1) + " %"],
                [f"Typical single year (year {mid['year']})", gwh(mid["P"][50]), gwh(mid["P"][90]), gwh(mid["P"][95]), f(mid["P"][90] / mid["P"][50] * 100, 1) + " %"]]
        d.table([("", 215), ("P50", 75), ("P90", 75), ("P95", 75), ("P90 / P50", CW - 440)], rows, rh=15, size=8.2, head_h=18)
        d.para(f"Use the multi-year values for energy over the period (for example a {n}-year P90 for long-term planning). Use the single-year values for one specific year, such as a CFE compliance year.", M, CW, size=7.5, color="mute")

        d.new_page(ctx, "Method and assumptions", "Method")
        d.bullets(method_bullets())
        if ctx.get("appendix"):
            for Yy in Ys:
                main_page(Yy, f"Year {Yy['year']}")
        title = f"Lifetime yield report - {ctx['meta']['project'] or 'PV'} - Years {y0}-{y1}"

    pdf = d.render(ctx["meta"]["generated"], title)
    return (pdf, list(d.labels)) if with_labels else pdf


def page_labels_count(pdf_bytes: bytes) -> int:
    import pypdfium2 as pdfium
    return len(pdfium.PdfDocument(pdf_bytes))


def render_page_png(pdf_bytes: bytes, index: int, scale: float = 2.0) -> bytes:
    """Render one PDF page to PNG for the in-app preview."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf_bytes)
    page = doc[index]
    img = page.render(scale=scale).to_pil()
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()
