"""Road design report: plan, curvature, gradient, long-section and
superelevation diagrams with element tables, as one self-contained HTML page.

All charts are plain SVG generated here (no QGIS), in source units.
"""

from __future__ import annotations

import html
import math

from .common import descendants, local_name
from .reports import (
    CUT_COLOR,
    DESIGN_COLOR,
    FILL_COLOR,
    _anchor,
    _axis_ticks,
    _cards,
    _document,
    _table,
    depth_svg,
    profile_svg,
    station_label,
)
from .stationing import StationedPolyline

TYPE_COLORS = {"line": "#5b6575", "curve": "#1f5fbf", "spiral": "#d9822b"}
LEFT_COLOR, RIGHT_COLOR = "#7b4fbf", "#2d8f8f"


# ------------------------------------------------------------------ data


def horizontal_elements(alignment):
    """Element table rows from a read_alignments() record."""
    start = float(alignment["sta_start"] or 0.0)
    rows, station = [], start
    for index, segment in enumerate(alignment["segments"], 1):
        length = segment.get("length") or 0.0
        kind = segment["geometry_type"]
        turn = {"cw": "right", "ccw": "left"}.get((segment.get("direction") or "").lower(), "")
        row = {
            "index": index,
            "type": kind,
            "start": station,
            "end": station + length,
            "length": length,
            "radius": segment.get("radius"),
            "radius_start": segment.get("radius_start"),
            "radius_end": segment.get("radius_end"),
            "turn": turn,
            "parameter": None,
        }
        if kind == "spiral":
            radius = row["radius_end"] or row["radius_start"]
            if radius and length:
                row["parameter"] = math.sqrt(radius * length)
        rows.append(row)
        station += length
    return rows


def curvature_series(elements):
    """(station, 1000/R) breakpoints; right turns positive, left negative."""
    points = []
    for row in elements:
        sign = 1.0 if row["turn"] == "right" else -1.0 if row["turn"] == "left" else 0.0
        if row["type"] == "curve" and row["radius"]:
            k = sign * 1000.0 / row["radius"]
            points += [(row["start"], k), (row["end"], k)]
        elif row["type"] == "spiral":
            k0 = sign * 1000.0 / row["radius_start"] if row["radius_start"] else 0.0
            k1 = sign * 1000.0 / row["radius_end"] if row["radius_end"] else 0.0
            points += [(row["start"], k0), (row["end"], k1)]
        else:
            points += [(row["start"], 0.0), (row["end"], 0.0)]
    return points


def grade_series(controls):
    """(station, grade %) steps between consecutive profile controls."""
    points = []
    for a, b in zip(controls, controls[1:]):
        grade = (b["elevation"] - a["elevation"]) / (b["station"] - a["station"]) * 100.0
        points += [(a["station"], grade), (b["station"], grade)]
    return points


def design_speeds(alignment, root=None):
    """(station, speed) pairs from Civil 3D SpeedStation features or Roadway speeds."""
    speeds = []
    for feature in descendants(alignment, "Feature"):
        if (feature.attrib.get("name") or "").lower() != "speedstation":
            continue
        values = {}
        for prop in feature:
            if local_name(prop.tag) == "Property":
                values[(prop.attrib.get("label") or "").lower()] = prop.attrib.get("value")
        try:
            speeds.append((float(values["station"]), float(values["speed"])))
        except (KeyError, TypeError, ValueError):
            continue
    if not speeds and root is not None:
        name = alignment.attrib.get("name")
        for roadway in descendants(root, "Roadway"):
            if name not in (roadway.attrib.get("alignmentRefs") or "").split():
                continue
            for speed in descendants(roadway, "DesignSpeed"):
                try:
                    speeds.append((float(speed.attrib["staStart"]), float(speed.attrib["speed"])))
                except (KeyError, ValueError):
                    continue
    # Stable sort by station; where Civil 3D lists two speeds at one station the later entry applies.
    latest = {}
    for station, speed in sorted(speeds, key=lambda pair: pair[0]):
        latest[station] = speed
    return sorted(latest.items())


# ---------------------------------------------------------------- charts


def clip_series(points, low, high):
    """Clip (station, value) breakpoints to [low, high], interpolating the ends."""
    result = []
    for (s0, v0), (s1, v1) in zip(points, points[1:]):
        if s1 < low or s0 > high:
            continue
        a = max(s0, low)
        b = min(s1, high)
        if s1 == s0:
            if low <= s0 <= high:
                result += [(s0, v0), (s1, v1)]
            continue
        va = v0 + (v1 - v0) * (a - s0) / (s1 - s0)
        vb = v0 + (v1 - v0) * (b - s0) / (s1 - s0)
        result += [(a, va), (b, vb)]
    return result


def strip_windows(start, end, strip):
    """Chainage windows for long alignments (one chart row per window)."""
    if not strip or end - start <= strip * 1.2:
        return [(start, end)]
    windows, low = [], start
    while low < end - 1e-6:
        high = min(low + strip, end)
        if end - high < strip * 0.2:
            high = end
        windows.append((low, high))
        low = high
    return windows


def chart_svg(series, unit=None, y_label="", width=1000, height=240, zero=True, labels=(), bands=(), fmt="{:.1f}"):
    """Line chart over chainage. ``series``: (name, color, points, fill)."""
    all_points = [p for _n, _c, points, _f in series for p in points]
    if len(all_points) < 2:
        return ""
    s0, s1 = min(p[0] for p in all_points), max(p[0] for p in all_points)
    v0, v1 = min(p[1] for p in all_points), max(p[1] for p in all_points)
    if zero:
        v0, v1 = min(v0, 0.0), max(v1, 0.0)
    pad = (v1 - v0) * 0.12 or 1.0
    v0, v1 = v0 - pad, v1 + pad
    left, right, top, bottom = 64, 16, 22, 36
    pw, ph = width - left - right, height - top - bottom

    def x(s):
        return left + (s - s0) / ((s1 - s0) or 1) * pw

    def y(v):
        return top + (v1 - v) / ((v1 - v0) or 1) * ph

    out = [f"<svg viewBox='0 0 {width} {height}' xmlns='http://www.w3.org/2000/svg' role='img' font-family='Arial,sans-serif'>"]
    for start, end, color in bands:
        out.append(
            f"<rect x='{x(max(start, s0)):.1f}' y='{top}' width='{max(0.0, x(min(end, s1)) - x(max(start, s0))):.1f}' height='{ph}' fill='{color}' fill-opacity='0.08'/>"
        )
    for tick in _axis_ticks(v0, v1, 5):
        out.append(f"<line x1='{left}' x2='{width - right}' y1='{y(tick):.1f}' y2='{y(tick):.1f}' stroke='#eef0f3'/>")
        out.append(f"<text x='{left - 6}' y='{y(tick) + 4:.1f}' font-size='11' text-anchor='end' fill='#5b6575'>{fmt.format(tick)}</text>")
    for tick in _axis_ticks(s0, s1, 8):
        out.append(f"<line y1='{top}' y2='{top + ph}' x1='{x(tick):.1f}' x2='{x(tick):.1f}' stroke='#eef0f3'/>")
        out.append(
            f"<text x='{x(tick):.1f}' y='{height - 14}' font-size='11' text-anchor='{_anchor(x(tick), width)}' fill='#5b6575'>{html.escape(station_label(tick, unit))}</text>"
        )
    if zero and v0 < 0 < v1:
        out.append(f"<line x1='{left}' x2='{width - right}' y1='{y(0):.1f}' y2='{y(0):.1f}' stroke='#9aa5b5'/>")
    for _name, color, points, fill in series:
        path = " ".join(f"{x(s):.1f},{y(v):.1f}" for s, v in points)
        if fill:
            base = y(0) if v0 < 0 < v1 else y(v0)
            area = f"{x(points[0][0]):.1f},{base:.1f} {path} {x(points[-1][0]):.1f},{base:.1f}"
            out.append(f"<polygon points='{area}' fill='{color}' fill-opacity='0.15'/>")
        out.append(f"<polyline points='{path}' fill='none' stroke='{color}' stroke-width='1.7'/>")
    placed = []
    for station, value, text, color in labels:
        px, py = x(station), y(value) + (-6 if value >= 0 else 14)
        width_estimate = len(text) * 6.2
        if any(abs(px - qx) < (width_estimate + qw) / 2 and abs(py - qy) < 12 for qx, qy, qw in placed):
            continue
        placed.append((px, py, width_estimate))
        out.append(f"<text x='{px:.1f}' y='{py:.1f}' font-size='10.5' text-anchor='middle' fill='{color}'>{html.escape(text)}</text>")
    out.append(f"<text x='{left + 4}' y='{top - 7}' font-size='11' fill='#5b6575'>{html.escape(y_label)}</text>")
    legend = [(name, color) for name, color, _p, _f in series if name]
    cursor = width - right
    for name, color in reversed(legend):
        cursor -= len(name) * 6.2 + 26
        out.append(f"<line x1='{cursor:.1f}' x2='{cursor + 16:.1f}' y1='{top - 11}' y2='{top - 11}' stroke='{color}' stroke-width='2.4'/>")
        out.append(f"<text x='{cursor + 20:.1f}' y='{top - 7}' font-size='11' fill='#5b6575'>{html.escape(name)}</text>")
    out.append("</svg>")
    return "".join(out)


def plan_svg(alignment, elements, unit=None, swap=True, width=1000, height=560):
    """Schematic plan with element colours, chainage ticks, north arrow and scale bar."""
    distances = alignment.get("vertex_distances")
    points = [(p[1], p[0]) if swap else (p[0], p[1]) for p in alignment["points"]]
    if len(points) < 2:
        return ""
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    span = max(x1 - x0, y1 - y0) or 1.0
    margin = 46
    scale = min((width - 2 * margin) / ((x1 - x0) or span), (height - 2 * margin) / ((y1 - y0) or span))
    ox = margin + ((width - 2 * margin) - (x1 - x0) * scale) / 2
    oy = margin + ((height - 2 * margin) - (y1 - y0) * scale) / 2

    def X(x):
        return ox + (x - x0) * scale

    def Y(y):
        return height - (oy + (y - y0) * scale)

    out = [f"<svg viewBox='0 0 {width} {height}' xmlns='http://www.w3.org/2000/svg' role='img' font-family='Arial,sans-serif'>"]
    out.append(f"<rect x='0' y='0' width='{width}' height='{height}' fill='#fbfcfd'/>")
    start = float(alignment["sta_start"] or 0.0)
    stations = [start + d for d in distances]
    for row in elements:
        part = [p for p, s in zip(points, stations) if row["start"] - 1e-6 <= s <= row["end"] + 1e-6]
        if len(part) >= 2:
            path = " ".join(f"{X(px):.1f},{Y(py):.1f}" for px, py in part)
            out.append(
                f"<polyline points='{path}' fill='none' stroke='{TYPE_COLORS.get(row['type'], '#5b6575')}' stroke-width='{3 if row['type'] != 'line' else 2.2}' stroke-linecap='round'/>"
            )
    polyline = StationedPolyline(points, distances, start)
    total = polyline.station_end - polyline.station_start
    ticks = [t for t in _axis_ticks(polyline.station_start, polyline.station_end, 12) if polyline.station_start <= t <= polyline.station_end]
    tick_length = 7 / scale
    for tick in ticks:
        frame = polyline.frame(tick)
        if frame is None:
            continue
        px, py, ux, uy = frame
        nx, ny = -uy, ux
        out.append(
            f"<line x1='{X(px - nx * tick_length):.1f}' y1='{Y(py - ny * tick_length):.1f}' x2='{X(px + nx * tick_length):.1f}' y2='{Y(py + ny * tick_length):.1f}' stroke='#1d2430' stroke-width='1.2'/>"
        )
        lx, ly = X(px + nx * tick_length * 2.6), Y(py + ny * tick_length * 2.6)
        out.append(f"<text x='{lx:.1f}' y='{ly + 4:.1f}' font-size='10.5' text-anchor='middle' fill='#1d2430'>{html.escape(station_label(tick, unit))}</text>")
    for point, label in ((points[0], "Start"), (points[-1], "End")):
        out.append(f"<circle cx='{X(point[0]):.1f}' cy='{Y(point[1]):.1f}' r='4.5' fill='#ffffff' stroke='#1d2430' stroke-width='1.5'/>")
        out.append(f"<text x='{X(point[0]) + 8:.1f}' y='{Y(point[1]) - 8:.1f}' font-size='11.5' font-weight='bold' fill='#1d2430'>{label}</text>")
    # North arrow and scale bar.
    out.append(
        f"<g transform='translate({width - 34},{48})'><polygon points='0,-22 7,6 0,1 -7,6' fill='#1d2430'/>"
        "<text x='0' y='20' font-size='12' text-anchor='middle' fill='#1d2430'>N</text></g>"
    )
    bar = 10 ** math.floor(math.log10(max(total / 5, 1e-9)))
    bar = next(b * bar for b in (1, 2, 5, 10) if b * bar * scale >= 80)
    out.append(f"<line x1='{margin}' x2='{margin + bar * scale:.1f}' y1='{height - 18}' y2='{height - 18}' stroke='#1d2430' stroke-width='3'/>")
    out.append(f"<text x='{margin}' y='{height - 26}' font-size='11' fill='#1d2430'>{bar:g} {html.escape(unit or 'units')}</text>")
    legend_x = margin
    for kind, color in TYPE_COLORS.items():
        out.append(f"<line x1='{legend_x}' x2='{legend_x + 18}' y1='18' y2='18' stroke='{color}' stroke-width='3'/>")
        out.append(f"<text x='{legend_x + 23}' y='22' font-size='11' fill='#5b6575'>{kind.capitalize()}</text>")
        legend_x += 80
    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------- report


def _fmt_radius(value):
    return "∞" if value is None else f"{value:,.1f}"


def alignment_section(item, unit, normal_crown, strip=0.0):
    """HTML for one alignment. ``item`` keys: name, alignment, elements,
    controls, profile_name, runs, transitions, ground_name, speeds,
    superelevation (records), slopes ((left, right)), swap."""
    elements = item["elements"]
    controls = item["controls"]
    curves = [e for e in elements if e["type"] == "curve" and e["radius"]]
    spirals = [e for e in elements if e["type"] == "spiral"]
    grades = [abs(b) for _a, b in grade_series(controls)[::2]] if len(controls) > 1 else []
    crests = [c["k_value"] for c in controls if c.get("curve_type") == "crest" and c.get("k_value")]
    sags = [c["k_value"] for c in controls if c.get("curve_type") == "sag" and c.get("k_value")]
    length = elements[-1]["end"] - elements[0]["start"] if elements else 0.0
    speeds = sorted({speed for _s, speed in item["speeds"]})
    cards = [
        ("Length", f"{length:,.1f} {unit or ''}".strip()),
        ("Chainage", f"{station_label(elements[0]['start'], unit)} – {station_label(elements[-1]['end'], unit)}" if elements else "–"),
        ("Curves / spirals", f"{len(curves)} / {len(spirals)}"),
        ("Minimum radius", _fmt_radius(min((e["radius"] for e in curves), default=None)) if curves else "–"),
        ("Maximum grade", f"{max(grades):.2f}%" if grades else "–"),
        ("Minimum K crest / sag", f"{min(crests):.1f} / {min(sags):.1f}" if crests and sags else (f"{min(crests or sags):.1f}" if crests or sags else "–")),
        ("Design speed", " / ".join(f"{s:g}" for s in speeds) if speeds else "not recorded"),
        ("Max superelevation", f"{max(abs(r['rate']) for r in item['superelevation']):.1f}%" if item["superelevation"] else "none recorded"),
    ]
    parts = [f"<section class='alignment'><h2>{html.escape(item['name'])}</h2>", _cards(cards)]
    parts.append("<h3>Plan</h3>")
    parts.append(plan_svg(item["alignment"], elements, unit, item.get("swap", True)))
    parts.append(
        "<p class='note'>Schematic plan from the stored coordinates"
        + (" with northing/easting swapped to easting/northing" if item.get("swap", True) else " as stored")
        + "; it is not georeferenced. Ticks mark chainage.</p>"
    )
    curve_labels = [((e["start"] + e["end"]) / 2, 1000.0 / e["radius"] * (1 if e["turn"] == "right" else -1), f"R{e['radius']:,.0f}", DESIGN_COLOR) for e in curves]
    bands = [(e["start"], e["end"], TYPE_COLORS["spiral"]) for e in spirals]
    start_all = elements[0]["start"] if elements else 0.0
    end_all = elements[-1]["end"] if elements else 0.0
    windows = strip_windows(start_all, end_all, strip)
    if len(windows) > 1:
        parts.append(f"<p class='note'>Long-alignment charts are split into {len(windows)} strips of about {strip:g} {html.escape(unit or 'units')}.</p>")

    def within(labels, low, high):
        return [label for label in labels if low <= label[0] <= high]

    parts.append("<h3>Curvature diagram</h3>")
    curvature = curvature_series(elements)
    for low, high in windows:
        parts.append(
            chart_svg(
                [("", DESIGN_COLOR, clip_series(curvature, low, high), True)],
                unit,
                "1000 / R  (right +, left −)",
                labels=within(curve_labels, low, high),
                bands=bands,
            )
        )
    if controls:
        grade_points = grade_series(controls)
        grade_labels = [((a[0] + b[0]) / 2, a[1], f"{a[1]:+.2f}%", "#1d2430") for a, b in zip(grade_points[::2], grade_points[1::2])]
        curve_bands = [
            (c["station"] - c["curve_length"] / 2, c["station"] + c["curve_length"] / 2, CUT_COLOR if c["curve_type"] == "crest" else FILL_COLOR)
            for c in controls
            if c.get("curve_length")
        ]
        parts.append(f"<h3>Gradient diagram — {html.escape(item['profile_name'] or '')}</h3>")
        for low, high in windows:
            parts.append(
                chart_svg(
                    [("", "#1d2430", clip_series(grade_points, low, high), False)],
                    unit,
                    "Grade %",
                    labels=within(grade_labels, low, high),
                    bands=curve_bands,
                    fmt="{:+.1f}",
                )
            )
        parts.append("<p class='note'>Shaded bands are vertical curves (orange crest, green sag).</p>")
    if item.get("runs"):
        parts.append(f"<h3>Long-section — {html.escape(item['profile_name'] or '')} vs {html.escape(item['ground_name'] or '')}</h3>")
        for low, high in windows:
            runs = clip_runs(item["runs"], low, high)
            if not runs:
                continue
            marks = [t for t in item.get("transitions", ()) if low <= t["station"] <= high]
            parts.append(profile_svg(runs, unit, transitions=marks if len(marks) <= 40 else ()))
            parts.append(depth_svg(runs, unit))
    left, right = item["slopes"]
    if left or right:
        start = elements[0]["start"] if elements else min(p[0] for p in left + right)
        end = elements[-1]["end"] if elements else max(p[0] for p in left + right)
        nc = -abs(normal_crown)
        left_line = [(start, nc)] + left + [(end, nc)]
        right_line = [(start, nc)] + right + [(end, nc)]
        parts.append("<h3>Superelevation diagram</h3>")
        for low, high in windows:
            parts.append(
                chart_svg(
                    [
                        ("Left lane", LEFT_COLOR, clip_series(left_line, low, high), False),
                        ("Right lane", RIGHT_COLOR, clip_series(right_line, low, high), False),
                    ],
                    unit,
                    "Outward cross-slope %",
                    fmt="{:+.1f}",
                )
            )
        parts.append(
            f"<p class='note'>Normal crown {abs(normal_crown):g}% (an input; LandXML does not store it). "
            "Positive slopes rise away from the centreline. Transition stations and full rates come from the file.</p>"
        )
    parts.append("<h3>Horizontal elements</h3><div class='scroll'>")
    parts.append(
        _table(
            ["#", "Type", "Start", "End", "Length", "Radius", "Spiral R start", "Spiral R end", "Spiral A", "Turn"],
            [
                (
                    e["index"],
                    e["type"],
                    station_label(e["start"], unit),
                    station_label(e["end"], unit),
                    e["length"],
                    e["radius"],
                    _fmt_radius(e["radius_start"]) if e["type"] == "spiral" else None,
                    _fmt_radius(e["radius_end"]) if e["type"] == "spiral" else None,
                    e["parameter"],
                    e["turn"],
                )
                for e in elements
            ],
        )
    )
    parts.append("</div>")
    if controls:
        parts.append("<h3>Vertical controls</h3><div class='scroll'>")
        parts.append(
            _table(
                ["Chainage", "Elevation", "Grade in %", "Grade out %", "Curve length", "Type", "K"],
                [
                    (
                        station_label(c["station"], unit),
                        c["elevation"],
                        c["grade_in"] * 100 if c["grade_in"] is not None else None,
                        c["grade_out"] * 100 if c["grade_out"] is not None else None,
                        c["curve_length"],
                        c.get("curve_type") or "",
                        c.get("k_value"),
                    )
                    for c in controls
                ],
                3,
            )
        )
        parts.append("</div>")
    if item["superelevation"]:
        parts.append("<h3>Superelevation transitions</h3><div class='scroll'>")
        parts.append(
            _table(
                ["Begin runout", "Begin runoff", "Full super", "Rate %", "End full", "End runoff", "End runout"],
                [
                    (
                        station_label(r["begin_runout"], unit),
                        station_label(r["begin_runoff"], unit),
                        station_label(r["full_start"], unit),
                        r["rate"],
                        station_label(r["full_end"], unit),
                        station_label(r["end_runoff"], unit),
                        station_label(r["end_runout"], unit),
                    )
                    for r in item["superelevation"]
                ],
            )
        )
        parts.append("</div>")
    if item.get("review"):
        from .review_report import review_section_html

        result, settings = item["review"]
        parts.append(review_section_html(item["name"], result, unit, settings, strip or 5000.0))
    parts.append("</section>")
    return "".join(parts)


PRINT_STYLE = (
    "<style>h3{font-size:15px;margin:22px 0 6px}"
    "@media print{.scroll{max-height:none;overflow:visible}section.alignment{break-before:page}"
    "svg,table{break-inside:avoid}body{margin:0}}</style>"
)


def clip_runs(runs, low, high):
    from .earthworks import exclude_ranges

    return exclude_ranges(runs, [(-math.inf, low), (high, math.inf)])


def design_report_html(items, unit, title, source_file, normal_crown=2.5, strip=0.0):
    body = [
        PRINT_STYLE,
        f"<p class='meta'>Source: {html.escape(source_file)} · {len(items)} alignment(s). "
        "Print this page to PDF for a paginated report.</p>",
    ]
    body += [alignment_section(item, unit, normal_crown, strip) for item in items]
    return _document(title or "Road design report", "".join(body))
