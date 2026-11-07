"""Plan and profile sheet data for the geometric design review (pure Python).

The review result is cut into chainage windows. Each window carries what a
plan & profile sheet draws: the plan strip in local coordinates, the design,
recommended and ground long section, changed PVIs, vertical curves, finding
ranges, and the design speed, curvature and grade bands. The PDF renderer
(``gui/plan_profile_sheets.py``) and the inline HTML long section
(:func:`long_section_svg`) both draw from these dictionaries, so the sheets
and the report agree.
"""

from __future__ import annotations

import html
import math

from .reports import _anchor, _axis_ticks, station_label
from .review import profile_changes

SEVERITY_COLORS = {"High": "#c0392b", "Medium": "#d9822b", "Low": "#b7950b", "Info": "#5b6575"}
DESIGN_COLOR = "#1d2430"
RECOMMENDED_COLOR = "#1f5fbf"
GROUND_COLOR = "#8a6d3b"
TURN_COLORS = {"left": "#7b4fbf", "right": "#2d8f8f"}
CREST_COLOR = "#d9822b"
SAG_COLOR = "#3a9e5f"


def sheet_windows(start, end, length):
    """Chainage windows of ``length`` aligned to round chainages.

    The first and last windows are partial; a last window shorter than a
    fifth of ``length`` joins the one before it.
    """
    if end <= start:
        return []
    if not length or length <= 0 or end - start <= length * 1.05:
        return [(start, end)]
    windows = []
    low = start
    edge = math.floor(start / length) * length + length
    while low < end - 1e-6:
        high = min(edge, end)
        if high - low > 1e-6:
            windows.append((low, high))
        low = high
        edge += length
    if len(windows) > 1 and windows[-1][1] - windows[-1][0] < 0.2 * length:
        windows[-2] = (windows[-2][0], windows[-1][1])
        windows.pop()
    return windows


def review_extent(result):
    """Chainage range covered by the reviewed alignment and profile."""
    groups = result.get("groups") or []
    model = result.get("model")
    stations = [g["start"] for g in groups] + [g["end"] for g in groups]
    if model is not None and model.pvis:
        stations += [model.pvis[0]["station"], model.pvis[-1]["station"]]
    record = result.get("record") or {}
    for key in ("sta_start", "sta_end"):
        value = record.get(key)
        if isinstance(value, (int, float)):
            stations.append(float(value))
    return (min(stations), max(stations)) if stations else (0.0, 0.0)


def numbered_findings(result):
    """Findings numbered from 1 in report order; numbers match across sheets."""
    return [dict(f, number=i) for i, f in enumerate(result.get("findings") or [], 1)]


def _stations(low, high, step, extra=()):
    count = max(1, int(math.ceil((high - low) / step)))
    values = {low + (high - low) * i / count for i in range(count + 1)}
    values.update(s for s in extra if low < s < high)
    return sorted(values)


def profile_rows(result, low, high, step=2.0):
    """(station, design, recommended, ground) rows; None where a line is absent."""
    model = result.get("model")
    recommended = result.get("recommended") if result.get("applied") else None
    ground = result.get("ground")
    extra = []
    for profile in (model, recommended):
        if profile is None:
            continue
        for p in profile.pvis:
            extra += [p["station"], p["station"] - p["length"] / 2.0, p["station"] + p["length"] / 2.0]
    rows = []
    for s in _stations(low, high, step, extra):
        def level(profile):
            if profile is None or not profile.pvis:
                return None
            if s < profile.pvis[0]["station"] - 1e-6 or s > profile.pvis[-1]["station"] + 1e-6:
                return None
            return profile.elevation(s)

        g = ground.elevation_at(s) if ground is not None else None
        rows.append((s, level(model), level(recommended), g))
    return rows


def grade_segments(model, low, high):
    """(start, end, grade %) for each tangent overlapping the window."""
    if model is None or len(model.pvis) < 2:
        return []
    result = []
    for i in range(len(model.pvis) - 1):
        a, b = model.pvis[i]["station"], model.pvis[i + 1]["station"]
        if b <= low or a >= high or b <= a:
            continue
        result.append((max(a, low), min(b, high), model.grade(i, i + 1) * 100.0))
    return result


def _clip(items, low, high):
    return [item for item in items if item["end"] >= low and item["start"] <= high]


def sheet_data(result, window, plan=None, step=2.0):
    """Everything one plan & profile sheet draws for ``window`` = (low, high)."""
    low, high = window
    model = result.get("model")
    recommended = result.get("recommended") if result.get("applied") else None
    shown = recommended or model
    findings = [f for f in numbered_findings(result) if f["end"] >= low and f["start"] <= high]
    changed = []
    if model is not None and recommended is not None:
        for row in profile_changes(model, recommended):
            if low <= row[2] <= high or low <= row[1] <= high:
                changed.append(
                    {"pvi": row[0], "station_old": row[1], "station": row[2], "elevation_old": row[3], "elevation": row[4],
                     "length_old": row[5], "length": row[6]}
                )
    curves = [c for c in (shown.curves() if shown else []) if c["kind"] != "flat" and low <= c["station"] <= high]
    speeds = [(max(a, low), min(b, high), v) for a, b, v in (result.get("speed_sections") or []) if b > low and a < high]
    rows = profile_rows(result, low, high, step)
    return {
        "window": (low, high),
        "rows": rows,
        "has_recommended": recommended is not None,
        "has_ground": any(r[3] is not None for r in rows),
        "changed": changed,
        "curves": curves,
        "groups": _clip(result.get("groups") or [], low, high),
        "speeds": speeds,
        "grades": grade_segments(shown, low, high),
        "findings": findings,
        "plan": plan_strip(plan, low, high, findings) if plan is not None else None,
        "speed_unit": result["criteria"].speed_unit if result.get("criteria") is not None else "",
    }


def plan_points(polyline, low, high, step=5.0, swap=False):
    """(station, x, y) along the alignment, with X/Y swapped for northing-first files."""
    stations = _stations(low, high, step, getattr(polyline, "stations", ()))
    result = []
    for s in stations:
        point = polyline.point_at(s)
        if point is None:
            continue
        x, y = point
        result.append((s, y, x) if swap else (s, x, y))
    return result


class PlanSource:
    """An alignment polyline plus whether its X/Y are stored northing-first."""

    def __init__(self, polyline, swap=False):
        self.polyline = polyline
        self.swap = swap

    def point(self, station, offset=0.0):
        point = self.polyline.point_at(station, offset)
        if point is None:
            return None
        return (point[1], point[0]) if self.swap else point


def plan_strip(source, low, high, findings=()):
    """Plan strip in local metres, rotated so the window's chord runs left to right.

    Returns centreline points, chainage ticks (with a unit normal pointing to
    the right of travel), finding markers, and the rotation of grid north.
    """
    polyline = source.polyline
    points = plan_points(polyline, low, high, max((high - low) / 400.0, 1.0), source.swap)
    if len(points) < 2:
        return None
    (_s0, x0, y0), (_s1, x1, y1) = points[0], points[-1]
    angle = math.atan2(y1 - y0, x1 - x0)
    if math.hypot(x1 - x0, y1 - y0) < 1e-6:
        angle = 0.0
    cos_a, sin_a = math.cos(-angle), math.sin(-angle)
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0

    def local(x, y):
        dx, dy = x - cx, y - cy
        return dx * cos_a - dy * sin_a, dx * sin_a + dy * cos_a

    line = [(s, *local(x, y)) for s, x, y in points]
    spacing = _tick_spacing(high - low)
    ticks = []
    first = math.ceil(low / spacing - 1e-9) * spacing
    s = first
    while s <= high + 1e-6:
        centre = source.point(s)
        right = source.point(s, 1.0)
        if centre is not None and right is not None:
            u, v = local(*centre)
            ru, rv = local(*right)
            ticks.append({"station": s, "u": u, "v": v, "nu": ru - u, "nv": rv - v, "major": _is_major(s, spacing)})
        s += spacing
    markers = []
    for f in findings:
        mid = (max(f["start"], low) + min(f["end"], high)) / 2.0
        side = {"left": -1.0, "right": 1.0}.get((f.get("side") or "").lower(), 0.0)
        centre = source.point(mid)
        if centre is None:
            continue
        u, v = local(*centre)
        right = source.point(mid, 1.0)
        nu, nv = (local(*right)[0] - u, local(*right)[1] - v) if right is not None else (0.0, -1.0)
        markers.append({"number": f["number"], "severity": f["severity"], "u": u, "v": v, "nu": nu, "nv": nv, "side": side})
    us = [p[1] for p in line]
    vs = [p[2] for p in line]
    return {
        "line": line,
        "ticks": ticks,
        "markers": markers,
        "bounds": (min(us), min(vs), max(us), max(vs)),
        # Direction of grid north on the sheet, radians anticlockwise from +u.
        "north": math.pi / 2.0 - angle,
    }


def _tick_spacing(span):
    for spacing in (10, 20, 50, 100, 200, 500, 1000, 2000, 5000):
        if span / spacing <= 12:
            return float(spacing)
    return 10000.0


def _is_major(station, spacing):
    big = spacing * 5 if spacing < 100 else max(spacing, 500.0)
    return abs(station / big - round(station / big)) < 1e-6


def badge_rows(positions, spacing, count=3):
    """Row per badge so neighbouring badges do not overlap (``count`` rows at most)."""
    last = {}
    rows = [0] * len(positions)
    for i in sorted(range(len(positions)), key=lambda k: positions[k]):
        row = 0
        while row < count - 1 and positions[i] - last.get(row, -1e9) < spacing:
            row += 1
        last[row] = positions[i]
        rows[i] = row
    return rows


def level_range(rows):
    values = [v for row in rows for v in row[1:] if v is not None]
    if not values:
        return 0.0, 1.0
    low, high = min(values), max(values)
    pad = max((high - low) * 0.08, 0.5)
    return low - pad, high + pad


def finding_label(finding, unit=None):
    span = station_label(finding["start"], unit)
    if abs(finding["end"] - finding["start"]) > 1e-6:
        span += f" – {station_label(finding['end'], unit)}"
    return span


# ------------------------------------------------------------- HTML (SVG)


def long_section_svg(data, unit=None, width=1000):
    """Banded long section (profile, design speed, curvature, grade) as inline SVG."""
    low, high = data["window"]
    if high <= low or not data["rows"]:
        return ""
    left, right = 78, 14
    pw = width - left - right
    top, profile_h = 14, 230
    band_h = 34
    bands = ["Design speed", "Horizontal", "Vertical", "Grade %"]
    axis_y = top + profile_h + band_h * len(bands)
    height = axis_y + 58
    z0, z1 = level_range(data["rows"])

    def x(s):
        return left + (min(max(s, low), high) - low) / (high - low) * pw

    def y(z):
        return top + profile_h - (z - z0) / (z1 - z0) * profile_h

    out = [f"<svg class='longsection' viewBox='0 0 {width} {height}' xmlns='http://www.w3.org/2000/svg' role='img' "
           "font-family='Arial,sans-serif'>"]
    out.append(f"<rect x='{left}' y='{top}' width='{pw}' height='{profile_h}' fill='#fbfcfd' stroke='#c9d0da'/>")
    for f in data["findings"]:
        color = SEVERITY_COLORS.get(f["severity"], "#5b6575")
        x0, x1 = x(f["start"]), x(f["end"])
        out.append(f"<rect x='{x0:.1f}' y='{top}' width='{max(2.0, x1 - x0):.1f}' height='{profile_h}' fill='{color}' fill-opacity='0.04'/>")
    for tick in _axis_ticks(z0, z1, 6):
        out.append(f"<line x1='{left}' x2='{left + pw}' y1='{y(tick):.1f}' y2='{y(tick):.1f}' stroke='#edf0f4'/>")
        out.append(f"<text x='{left - 6}' y='{y(tick) + 4:.1f}' font-size='10.5' text-anchor='end' fill='#5b6575'>{tick:,.1f}</text>")
    for tick in _axis_ticks(low, high, 8):
        out.append(f"<line x1='{x(tick):.1f}' x2='{x(tick):.1f}' y1='{top}' y2='{axis_y}' stroke='#edf0f4'/>")

    def path(index, color, dash="", widthpx=1.6):
        segments, current = [], []
        for row in data["rows"]:
            if row[index] is None:
                if current:
                    segments.append(current)
                current = []
                continue
            current.append(f"{x(row[0]):.1f},{y(row[index]):.1f}")
        if current:
            segments.append(current)
        dash_attr = f" stroke-dasharray='{dash}'" if dash else ""
        return "".join(
            f"<polyline points='{' '.join(s)}' fill='none' stroke='{color}' stroke-width='{widthpx}'{dash_attr}/>" for s in segments if len(s) > 1
        )

    if data["has_ground"]:
        out.append(path(3, GROUND_COLOR, "", 1.2))
    out.append(path(1, DESIGN_COLOR, "", 1.8))
    if data["has_recommended"]:
        out.append(path(2, RECOMMENDED_COLOR, "6 3", 1.8))
    for p in data["changed"]:
        px, py = x(p["station"]), y(p["elevation"])
        out.append(f"<circle cx='{px:.1f}' cy='{py:.1f}' r='4' fill='#fff' stroke='{RECOMMENDED_COLOR}' stroke-width='1.6'/>")
        out.append(f"<text x='{px + 6:.1f}' y='{py - 6:.1f}' font-size='10' fill='{RECOMMENDED_COLOR}'>PVI {p['pvi']}</text>")
    positions = [x((max(f["start"], low) + min(f["end"], high)) / 2.0) for f in data["findings"]]
    for f, mx, row in zip(data["findings"], positions, badge_rows(positions, 17.0)):
        color = SEVERITY_COLORS.get(f["severity"], "#5b6575")
        cy = top + 10 + row * 17
        out.append(f"<circle cx='{mx:.1f}' cy='{cy}' r='8' fill='{color}' stroke='#fff'/>")
        out.append(f"<text x='{mx:.1f}' y='{cy + 3.5}' font-size='9.5' font-weight='bold' text-anchor='middle' fill='#fff'>{f['number']}</text>")
    # Bands
    for i, name in enumerate(bands):
        by = top + profile_h + band_h * i
        out.append(f"<rect x='{left}' y='{by}' width='{pw}' height='{band_h}' fill='#fff' stroke='#c9d0da'/>")
        out.append(f"<text x='{left - 6}' y='{by + band_h / 2 + 4}' font-size='10.5' text-anchor='end' fill='#5b6575'>{name}</text>")
    by = top + profile_h
    for a, b, v in data["speeds"]:
        out.append(f"<line x1='{x(a):.1f}' x2='{x(b):.1f}' y1='{by + band_h / 2:.1f}' y2='{by + band_h / 2:.1f}' stroke='#1d2430' stroke-width='1.4'/>")
        out.append(f"<line x1='{x(a):.1f}' x2='{x(a):.1f}' y1='{by + 6}' y2='{by + band_h - 6}' stroke='#1d2430'/>")
        out.append(f"<text x='{(x(a) + x(b)) / 2:.1f}' y='{by + band_h / 2 - 4:.1f}' font-size='10.5' text-anchor='middle' fill='#1d2430'>{v:g} {html.escape(data['speed_unit'])}</text>")
    by = top + profile_h + band_h
    mid = by + band_h / 2
    out.append(f"<line x1='{left}' x2='{left + pw}' y1='{mid}' y2='{mid}' stroke='#5b6575'/>")
    for g in data["groups"]:
        color = TURN_COLORS.get(g.get("turn") or "", "#5b6575")
        sign = -1 if g.get("turn") == "right" else 1
        y1 = mid - sign * 11
        a, b = x(g["start"]), x(g["end"])
        aa, ab = x(g["arc_start"]), x(g["arc_end"])
        out.append(f"<polyline points='{a:.1f},{mid} {aa:.1f},{y1} {ab:.1f},{y1} {b:.1f},{mid}' fill='{color}' fill-opacity='0.25' stroke='{color}' stroke-width='1.2'/>")
        text = f"R{g['radius']:,.0f} {'R' if g.get('turn') == 'right' else 'L'}"
        ty = mid + 12 if sign > 0 else mid - 3
        out.append(f"<text x='{(aa + ab) / 2:.1f}' y='{ty:.1f}' font-size='9.5' text-anchor='middle' fill='{color}'>{html.escape(text)}</text>")
    by = top + profile_h + 2 * band_h
    mid = by + band_h / 2
    out.append(f"<line x1='{left}' x2='{left + pw}' y1='{mid}' y2='{mid}' stroke='#5b6575'/>")
    for c in data["curves"]:
        crest = c["kind"] == "crest"
        color = CREST_COLOR if crest else SAG_COLOR
        y1 = mid - 11 if crest else mid + 11
        out.append(f"<polygon points='{x(c['start']):.1f},{mid} {x(c['station']):.1f},{y1} {x(c['end']):.1f},{mid}' fill='{color}' fill-opacity='0.35' stroke='{color}'/>")
        ty = mid + 12 if crest else mid - 4
        name = "Crest" if crest else "Sag"
        label = f"{name} K{c['K']:.0f} L{c['length']:.0f}" if c["K"] else f"L{c['length']:.0f}"
        out.append(f"<text x='{x(c['station']):.1f}' y='{ty:.1f}' font-size='9.5' text-anchor='middle' fill='{color}'>{html.escape(label)}</text>")
    hy = top + profile_h + band_h
    for f in data["findings"]:
        if f.get("category") != "Phasing":
            continue
        color = SEVERITY_COLORS.get(f["severity"], "#5b6575")
        x0, x1 = x(f["start"]), max(x(f["start"]) + 3.0, x(f["end"]))
        out.append(f"<rect x='{x0:.1f}' y='{hy + 2}' width='{x1 - x0:.1f}' height='{2 * band_h - 4}' fill='none' stroke='{color}' stroke-width='1.3' stroke-dasharray='4 2'/>")
        out.append(f"<text x='{x0 + 3:.1f}' y='{hy + 12}' font-size='9' font-weight='bold' fill='{color}'>{f['number']}</text>")
    by = top + profile_h + 3 * band_h
    for a, b, g in data["grades"]:
        out.append(f"<line x1='{x(a):.1f}' x2='{x(a):.1f}' y1='{by + 4}' y2='{by + band_h - 4}' stroke='#9aa5b5'/>")
        if x(b) - x(a) > 34:
            out.append(f"<text x='{(x(a) + x(b)) / 2:.1f}' y='{by + band_h / 2 + 4:.1f}' font-size='10.5' text-anchor='middle' fill='#1d2430'>{g:+.2f}</text>")
    for tick in _axis_ticks(low, high, 8):
        out.append(f"<line x1='{x(tick):.1f}' x2='{x(tick):.1f}' y1='{axis_y}' y2='{axis_y + 6}' stroke='#9aa5b5'/>")
        out.append(f"<text x='{x(tick):.1f}' y='{axis_y + 19}' font-size='11' text-anchor='{_anchor(x(tick), width)}' fill='#5b6575'>{html.escape(station_label(tick, unit))}</text>")
    legend = [("Design", DESIGN_COLOR, "")]
    if data["has_recommended"]:
        legend.append(("Recommended", RECOMMENDED_COLOR, "6 3"))
    if data["has_ground"]:
        legend.append(("Existing ground", GROUND_COLOR, ""))
    cursor = left
    ly = axis_y + 42
    out.append("<g font-size='10.5' fill='#5b6575'>")
    for name, color, dash in legend:
        dash_attr = f" stroke-dasharray='{dash}'" if dash else ""
        out.append(f"<line x1='{cursor}' x2='{cursor + 22}' y1='{ly - 4}' y2='{ly - 4}' stroke='{color}' stroke-width='1.8'{dash_attr}/>")
        out.append(f"<text x='{cursor + 27}' y='{ly}'>{name}</text>")
        cursor += 40 + 6.2 * len(name)
    if data["changed"]:
        out.append(f"<circle cx='{cursor + 5}' cy='{ly - 4}' r='4' fill='#fff' stroke='{RECOMMENDED_COLOR}' stroke-width='1.6'/>")
        out.append(f"<text x='{cursor + 14}' y='{ly}'>changed PVI</text>")
        cursor += 95
    out.append(f"<circle cx='{cursor + 7}' cy='{ly - 4}' r='7' fill='{SEVERITY_COLORS['High']}'/><text x='{cursor + 19}' y='{ly}'>finding no. (shaded range)</text>")
    cursor += 180
    out.append(f"<rect x='{cursor}' y='{ly - 10}' width='20' height='10' fill='none' stroke='{SEVERITY_COLORS['Medium']}' stroke-dasharray='4 2'/>"
               f"<text x='{cursor + 26}' y='{ly}'>phasing finding (horizontal and vertical bands)</text>")
    out.append("</g></svg>")
    return "".join(out)
