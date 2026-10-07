"""CSV and self-contained HTML reports for earthworks results (no QGIS)."""

from __future__ import annotations

import csv
from datetime import datetime
import html
import math

CUT_COLOR = "#d9822b"
FILL_COLOR = "#3a9e5f"
GROUND_COLOR = "#8c5a2b"
DESIGN_COLOR = "#1f5fbf"


def station_label(station, horizontal_unit=None):
    """Format 1234.5 as 1+234.50 (metric) or 12+34.50 (feet/undeclared)."""
    if station is None or not math.isfinite(station):
        return ""
    unit = (horizontal_unit or "").lower()
    base = 1000 if unit in {"meter", "metre", "kilometer", "kilometre", "millimeter", "centimeter"} else 100
    width = 3 if base == 1000 else 2
    rounded = round(station, 2)
    sign = "-" if rounded < 0 else ""
    rounded = abs(rounded)
    major = math.floor(rounded / base + 1e-12)
    minor = rounded - major * base
    return f"{sign}{major}+{minor:0{width + 3}.2f}"


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        for row in rows:
            writer.writerow(["" if value is None else value for value in row])


def _fmt(value, digits=2):
    if value is None:
        return "–"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,d}"
    if isinstance(value, float):
        return f"{value:,.{digits}f}"
    return html.escape(str(value))


def _table(header, rows, digits=2):
    head = "".join(f"<th>{html.escape(str(item))}</th>" for item in header)
    body = "".join(
        "<tr>" + "".join(f"<td>{_fmt(value, digits)}</td>" for value in row) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _cards(items):
    return '<div class="cards">' + "".join(
        f'<div class="card"><div class="k">{html.escape(label)}</div><div class="v">{html.escape(value)}</div></div>'
        for label, value in items
    ) + "</div>"


_STYLE = """
:root{--fg:#1d2430;--muted:#5b6575;--line:#d8dde5;--bg:#fff;--soft:#f4f6f9}
body{font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:var(--fg);background:var(--bg);margin:24px auto;max-width:1100px;padding:0 16px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 8px;border-bottom:1px solid var(--line);padding-bottom:4px}
.meta{color:var(--muted);font-size:13px}.note{background:var(--soft);border-left:3px solid #9aa5b5;padding:8px 12px;font-size:13px;color:var(--muted)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:12px 0}
.card{border:1px solid var(--line);border-radius:8px;padding:10px 12px}.card .k{color:var(--muted);font-size:12px}.card .v{font-size:18px;font-weight:600;font-variant-numeric:tabular-nums}
table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}th,td{border-bottom:1px solid var(--line);padding:4px 8px;text-align:right}th{background:var(--soft);position:sticky;top:0}
td:first-child,th:first-child{text-align:left}.scroll{max-height:420px;overflow:auto;border:1px solid var(--line)}
svg{width:100%;height:auto;border:1px solid var(--line);border-radius:6px;background:#fff}
.legend span{display:inline-block;margin-right:14px;font-size:12px;color:var(--muted)}.legend i{display:inline-block;width:12px;height:12px;margin-right:4px;vertical-align:-1px;border-radius:2px}
"""


def _document(title, body):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{_STYLE}</style></head><body>"
        f"<h1>{html.escape(title)}</h1><div class='meta'>Generated {stamp} by LandXML Road &amp; Terrain GIS Tools</div>"
        f"{body}</body></html>"
    )


def _anchor(position, width, edge=40):
    """Keep chainage labels at the chart edges inside the drawing."""
    if position > width - edge:
        return "end"
    if position < edge:
        return "start"
    return "middle"


def _decimate(run, max_points):
    if len(run) <= max_points:
        return run
    step = max(1, len(run) // max_points)
    kept = [row for index, row in enumerate(run) if index % step == 0 or row["depth"] == 0.0]
    if kept[-1] is not run[-1]:
        kept.append(run[-1])
    return kept


def _axis_ticks(low, high, count=8):
    span = high - low
    if span <= 0:
        return [low]
    raw = span / count
    magnitude = 10 ** math.floor(math.log10(raw))
    step = min((m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw), default=raw)
    first = math.ceil(low / step) * step
    ticks = []
    value = first
    while value <= high + 1e-9:
        ticks.append(value)
        value += step
    return ticks


def profile_svg(runs, horizontal_unit=None, width=1000, height=380, max_points=1500, transitions=()):
    """Long-section SVG: ground, design and shaded cut/fill between them."""
    rows = [row for run in runs for row in run]
    if not rows:
        return ""
    s0, s1 = min(r["station"] for r in rows), max(r["station"] for r in rows)
    z0 = min(min(r["design"], r["ground"]) for r in rows)
    z1 = max(max(r["design"], r["ground"]) for r in rows)
    pad = (z1 - z0) * 0.08 or 1.0
    z0, z1 = z0 - pad, z1 + pad
    left, right, top, bottom = 64, 16, 14, 40
    pw, ph = width - left - right, height - top - bottom

    def x(station):
        return left + (station - s0) / ((s1 - s0) or 1) * pw

    def y(z):
        return top + (z1 - z) / ((z1 - z0) or 1) * ph

    parts = [f"<svg viewBox='0 0 {width} {height}' xmlns='http://www.w3.org/2000/svg' role='img'>"]
    for tick in _axis_ticks(z0, z1, 6):
        parts.append(f"<line x1='{left}' x2='{width - right}' y1='{y(tick):.1f}' y2='{y(tick):.1f}' stroke='#eef0f3'/>")
        parts.append(f"<text x='{left - 6}' y='{y(tick) + 4:.1f}' font-size='11' text-anchor='end' fill='#5b6575'>{tick:.1f}</text>")
    for tick in _axis_ticks(s0, s1, 8):
        parts.append(f"<line y1='{top}' y2='{top + ph}' x1='{x(tick):.1f}' x2='{x(tick):.1f}' stroke='#eef0f3'/>")
        parts.append(
            f"<text x='{x(tick):.1f}' y='{height - 18}' font-size='11' text-anchor='{_anchor(x(tick), width)}' fill='#5b6575'>{html.escape(station_label(tick, horizontal_unit))}</text>"
        )
    per_run = max(50, max_points // max(1, len(runs)))
    for run in runs:
        rows = _decimate(run, per_run)
        segment = [rows[0]]
        for a, b in zip(rows, rows[1:]):
            segment.append(b)
            boundary = b["depth"] == 0.0 or b is rows[-1]
            if boundary and len(segment) >= 2:
                total = sum(r["depth"] for r in segment)
                color = FILL_COLOR if total > 0 else CUT_COLOR
                outline = [(x(r["station"]), y(r["design"])) for r in segment] + [
                    (x(r["station"]), y(r["ground"])) for r in reversed(segment)
                ]
                points = " ".join(f"{px:.1f},{py:.1f}" for px, py in outline)
                parts.append(f"<polygon points='{points}' fill='{color}' fill-opacity='0.45' stroke='none'/>")
                segment = [b]
        for key, color, dash in (("ground", GROUND_COLOR, ""), ("design", DESIGN_COLOR, "")):
            points = " ".join(f"{x(r['station']):.1f},{y(r[key]):.1f}" for r in rows)
            parts.append(f"<polyline points='{points}' fill='none' stroke='{color}' stroke-width='1.6'{dash}/>")
    for item in transitions:
        parts.append(
            f"<circle cx='{x(item['station']):.1f}' cy='{y(item['elevation']):.1f}' r='2.6' fill='#1d2430'/>"
        )
    parts.append(f"<text x='{left}' y='{height - 3}' font-size='11' fill='#5b6575'>Station</text>")
    parts.append(
        f"<text x='12' y='{top + ph / 2:.0f}' font-size='11' fill='#5b6575' transform='rotate(-90 12 {top + ph / 2:.0f})' text-anchor='middle'>Elevation</text>"
    )
    parts.append("</svg>")
    legend = (
        f"<div class='legend'><span><i style='background:{GROUND_COLOR}'></i>Ground</span>"
        f"<span><i style='background:{DESIGN_COLOR}'></i>Design</span>"
        f"<span><i style='background:{FILL_COLOR};opacity:.6'></i>Fill</span>"
        f"<span><i style='background:{CUT_COLOR};opacity:.6'></i>Cut</span></div>"
    )
    return "".join(parts) + legend


def depth_svg(runs, horizontal_unit=None, width=1000, height=220, max_points=2000):
    """Depth diagram: fill above the axis (green), cut below (orange)."""
    rows = [row for run in runs for row in run]
    if not rows:
        return ""
    s0, s1 = min(r["station"] for r in rows), max(r["station"] for r in rows)
    d1 = max(0.5, max(r["depth"] for r in rows))
    d0 = min(-0.5, min(r["depth"] for r in rows))
    left, right, top, bottom = 64, 16, 10, 36
    pw, ph = width - left - right, height - top - bottom

    def x(s):
        return left + (s - s0) / ((s1 - s0) or 1) * pw

    def y(d):
        return top + (d1 - d) / ((d1 - d0) or 1) * ph

    parts = [f"<svg viewBox='0 0 {width} {height}' xmlns='http://www.w3.org/2000/svg' role='img'>"]
    for tick in _axis_ticks(d0, d1, 5):
        parts.append(f"<line x1='{left}' x2='{width - right}' y1='{y(tick):.1f}' y2='{y(tick):.1f}' stroke='#eef0f3'/>")
        parts.append(f"<text x='{left - 6}' y='{y(tick) + 4:.1f}' font-size='11' text-anchor='end' fill='#5b6575'>{tick:+.1f}</text>")
    for tick in _axis_ticks(s0, s1, 8):
        parts.append(
            f"<text x='{x(tick):.1f}' y='{height - 14}' font-size='11' text-anchor='{_anchor(x(tick), width)}' fill='#5b6575'>{html.escape(station_label(tick, horizontal_unit))}</text>"
        )
    per_run = max(50, max_points // max(1, len(runs)))
    for run in runs:
        rows = _decimate(run, per_run)
        for clip, color in ((max, FILL_COLOR), (min, CUT_COLOR)):
            outline = [(x(rows[0]["station"]), y(0))]
            outline += [(x(r["station"]), y(clip(r["depth"], 0.0))) for r in rows]
            outline.append((x(rows[-1]["station"]), y(0)))
            points = " ".join(f"{px:.1f},{py:.1f}" for px, py in outline)
            parts.append(f"<polygon points='{points}' fill='{color}' fill-opacity='0.8'/>")
    parts.append(f"<line x1='{left}' x2='{width - right}' y1='{y(0):.1f}' y2='{y(0):.1f}' stroke='#5b6575'/>")
    parts.append(f"<text x='{left + 4}' y='{top + 12}' font-size='11' fill='#5b6575'>Depth (design − ground)</text></svg>")
    return "".join(parts)


def line_svg(points, horizontal_unit=None, label="", width=1000, height=260, color="#6b4fbb"):
    if len(points) < 2:
        return ""
    s0, s1 = min(p[0] for p in points), max(p[0] for p in points)
    v0, v1 = min(0.0, min(p[1] for p in points)), max(0.0, max(p[1] for p in points))
    pad = (v1 - v0) * 0.08 or 1.0
    v0, v1 = v0 - pad, v1 + pad
    left, right, top, bottom = 80, 16, 14, 40
    pw, ph = width - left - right, height - top - bottom

    def x(s):
        return left + (s - s0) / ((s1 - s0) or 1) * pw

    def y(v):
        return top + (v1 - v) / ((v1 - v0) or 1) * ph

    parts = [f"<svg viewBox='0 0 {width} {height}' xmlns='http://www.w3.org/2000/svg' role='img'>"]
    for tick in _axis_ticks(v0, v1, 5):
        parts.append(f"<line x1='{left}' x2='{width - right}' y1='{y(tick):.1f}' y2='{y(tick):.1f}' stroke='#eef0f3'/>")
        parts.append(f"<text x='{left - 6}' y='{y(tick) + 4:.1f}' font-size='11' text-anchor='end' fill='#5b6575'>{tick:,.0f}</text>")
    for tick in _axis_ticks(s0, s1, 8):
        parts.append(
            f"<text x='{x(tick):.1f}' y='{height - 18}' font-size='11' text-anchor='{_anchor(x(tick), width)}' fill='#5b6575'>{html.escape(station_label(tick, horizontal_unit))}</text>"
        )
    parts.append(f"<line x1='{left}' x2='{width - right}' y1='{y(0):.1f}' y2='{y(0):.1f}' stroke='#9aa5b5'/>")
    path = " ".join(f"{x(s):.1f},{y(v):.1f}" for s, v in points)
    parts.append(f"<polyline points='{path}' fill='none' stroke='{color}' stroke-width='1.8'/>")
    parts.append(f"<text x='{left + 4}' y='{top + 12}' font-size='11' fill='#5b6575'>{html.escape(label)}</text></svg>")
    return "".join(parts)


def profile_report_html(results, horizontal_unit=None, vertical_unit=None, source_file=""):
    """``results`` is a list of dicts: alignment, design, ground, runs, segments,
    transitions, summary, template (optional), excluded."""
    unit = horizontal_unit or "source units"
    body = [
        "<p class='note'>Depth = design − ground (positive fill, negative cut) along the alignment centreline, "
        "computed from the LandXML profiles exactly as stored. Centreline depths do not account for ground cross-fall, "
        "pavement thickness or structures; use corridor cross-sections for quantities.</p>"
    ]
    if source_file:
        body.append(f"<p class='meta'>Source: {html.escape(source_file)}</p>")
    for item in results:
        summary = item["summary"]
        body.append(f"<h2>{html.escape(item['alignment'] or 'Alignment')}</h2>")
        body.append(
            f"<p class='meta'>Design: {html.escape(item['design'])} · Ground: {html.escape(item['ground'])}"
            + (f" · Excluded: {html.escape(item['excluded'])}" if item.get("excluded") else "")
            + "</p>"
        )
        body.append(
            _cards(
                [
                    ("Compared length", f"{summary['covered_length']:,.1f} {unit}"),
                    ("Cut length", f"{summary['cut_length']:,.1f} ({summary['cut_percent']:.0f}%)"),
                    ("Fill length", f"{summary['fill_length']:,.1f} ({summary['fill_percent']:.0f}%)"),
                    ("Max cut / fill", f"{summary['max_cut']:.2f} / {summary['max_fill']:.2f}"),
                    ("Cut↔fill transitions", f"{summary['transitions']}"),
                ]
            )
        )
        body.append(profile_svg(item["runs"], horizontal_unit, transitions=item["transitions"]))
        body.append(depth_svg(item["runs"], horizontal_unit))
        template = item.get("template")
        if template:
            body.append("<h2>Preliminary level-section estimate</h2>")
            body.append(
                f"<p class='note'>Formation width {template['formation_width']:g}, fill slope 1:{template['fill_slope']:g}, "
                f"cut slope 1:{template['cut_slope']:g} (V:H). Flat-ground approximation for early design only.</p>"
            )
            body.append(
                _cards(
                    [
                        ("Cut volume", f"{template['cut_volume']:,.0f}"),
                        ("Fill volume", f"{template['fill_volume']:,.0f}"),
                        ("Embankment slope area", f"{template['fill_slope_area']:,.0f}"),
                        ("Cut slope area", f"{template['cut_slope_area']:,.0f}"),
                    ]
                )
            )
        body.append("<h2>Cut and fill segments</h2><div class='scroll'>")
        body.append(
            _table(
                ["From", "To", "Type", "Length", "Max depth", "At", "Mean depth", "Flag"],
                [
                    (
                        station_label(s["start"], horizontal_unit),
                        station_label(s["end"], horizontal_unit),
                        s["kind"],
                        s["length"],
                        s["max_depth"],
                        station_label(s["max_station"], horizontal_unit),
                        s["mean_depth"],
                        s["severity"],
                    )
                    for s in item["segments"]
                ],
            )
        )
        body.append("</div>")
    if vertical_unit is None:
        body.append("<p class='note'>The LandXML file does not declare a vertical unit.</p>")
    return _document("Profile cut/fill analysis", "".join(body))


def corridor_report_html(results, horizontal_unit=None, source_file=""):
    """``results``: list of dicts with alignment, ground, design, quantities, settings."""
    body = [
        "<p class='note'>Areas between the ground and design cross-section lines; volumes by average end area "
        "between consecutive sections (no curvature correction). Side-slope areas are sloped surface areas "
        "between the formation hinge and the daylight point, measured on the finished design surface. "
        "Values are in source units; verify against the design software before tendering.</p>"
    ]
    if source_file:
        body.append(f"<p class='meta'>Source: {html.escape(source_file)}</p>")
    for item in results:
        q = item["quantities"]
        totals = q["totals"]
        body.append(f"<h2>{html.escape(item['alignment'] or 'Alignment')}</h2>")
        body.append(
            f"<p class='meta'>Ground: {html.escape(item['ground'] or '–')} · Design: {html.escape(item['design'] or '–')}"
            f" · {html.escape(item.get('settings', ''))}</p>"
        )
        body.append(
            _cards(
                [
                    ("Cut volume", f"{totals['cut_volume']:,.0f}"),
                    ("Fill volume", f"{totals['fill_volume']:,.0f}"),
                    ("Net (fill − factored cut)", f"{totals['net_volume']:,.0f}"),
                    ("Embankment slopes (grassing)", f"{totals['fill_slope_area']:,.0f}"),
                    ("Cut slopes (grassing)", f"{totals['cut_slope_area']:,.0f}"),
                    ("Steep slopes (stabilise)", f"{totals['steep_fill_area'] + totals['steep_cut_area']:,.0f}"),
                    ("Integrated length", f"{totals['length']:,.1f}"),
                    ("Sections with earthworks", f"{totals['earthwork_sections']} / {totals['sections']}"),
                ]
            )
        )
        if q["materials"]:
            body.append("<h2>Corridor material quantities</h2>")
            body.append(_table(["Shape", "Volume"], sorted(q["materials"].items())))
        mass = [(q["intervals"][0]["start"], 0.0)] if q["intervals"] else []
        mass += [(interval["end"], interval["mass_ordinate"]) for interval in q["intervals"]]
        if len(mass) >= 2:
            body.append("<h2>Mass haul ordinate</h2>")
            body.append(line_svg(mass, horizontal_unit, "Cumulative factored cut − fill"))
        body.append("<h2>Section intervals</h2><div class='scroll'>")
        body.append(
            _table(
                ["From", "To", "Cut vol", "Fill vol", "Emb. slope area", "Cut slope area", "Steep area", "Mass ordinate"],
                [
                    (
                        station_label(i["start"], horizontal_unit),
                        station_label(i["end"], horizontal_unit),
                        i["cut_volume"],
                        i["fill_volume"],
                        i["fill_slope_area"],
                        i["cut_slope_area"],
                        i["steep_fill_area"] + i["steep_cut_area"],
                        i["mass_ordinate"],
                    )
                    for i in q["intervals"]
                ],
            )
        )
        body.append("</div>")
        if q["skipped"]:
            reasons = {}
            for _station, reason in q["skipped"]:
                reasons[reason] = reasons.get(reason, 0) + 1
            body.append(
                "<p class='note'>Not integrated: "
                + "; ".join(f"{count} × {html.escape(reason)}" for reason, count in reasons.items())
                + "</p>"
            )
    return _document("Corridor section quantities", "".join(body))


def surface_report_html(stats, base, compare, resolution, units, civil3d=None, source_file=""):
    rows = [
        ("Cut volume", stats["cut_volume"]),
        ("Fill volume", stats["fill_volume"]),
        ("Net (fill − cut)", stats["net_volume"]),
        ("Cut area", stats["cut_area"]),
        ("Fill area", stats["fill_area"]),
        ("Compared area", stats["compared_area"]),
        ("Maximum cut depth", stats["max_cut"]),
        ("Maximum fill depth", stats["max_fill"]),
    ]
    body = [
        f"<p class='meta'>Source: {html.escape(source_file)} · Base: {html.escape(base)} · Compare: {html.escape(compare)} · "
        f"Grid {resolution:g} {html.escape(units or 'units')}</p>",
        "<p class='note'>Grid method: both TINs are evaluated at cell centres on a shared grid; triangles hidden by a "
        "surface boundary are excluded. Smaller cells converge on the TIN-to-TIN volume.</p>",
        _table(["Quantity", "Value"], rows),
    ]
    if civil3d:
        body.append("<h2>Civil 3D volume surface recorded in the LandXML</h2>")
        body.append(
            _table(
                ["Quantity", "Civil 3D", "This grid", "Difference %"],
                [
                    (
                        label,
                        civil3d[key],
                        stats[key],
                        (stats[key] - civil3d[key]) / civil3d[key] * 100.0 if civil3d[key] else None,
                    )
                    for label, key in (("Cut", "cut_volume"), ("Fill", "fill_volume"), ("Net", "net_volume"))
                    if civil3d.get(key) is not None
                ],
            )
        )
    return _document("Surface cut/fill", "".join(body))
