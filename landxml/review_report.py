"""Design review outputs: HTML section, phasing diagram and LandXML export."""

from __future__ import annotations

from datetime import datetime
import html

from .common import local_name
from .design_report import chart_svg, clip_series, strip_windows
from .reports import _anchor, _axis_ticks, _cards, _table, station_label


def escape(text):
    return html.escape(text, quote=False)


def quoteattr(text):
    return '"' + html.escape(text, quote=True) + '"'


SEVERITY_COLORS = {"High": "#c0392b", "Medium": "#d9822b", "Low": "#b7950b", "Info": "#5b6575"}
TURN_COLORS = {"left": "#7b4fbf", "right": "#2d8f8f", "": "#5b6575"}


def phasing_svg(groups, curves, findings, window, unit=None, width=1000, height=190):
    """Two-lane chainage strip: horizontal curves above, vertical curves below."""
    low, high = window
    if high <= low:
        return ""
    left, right = 70, 16
    pw = width - left - right

    def x(s):
        return left + (min(max(s, low), high) - low) / (high - low) * pw

    out = [f"<svg viewBox='0 0 {width} {height}' xmlns='http://www.w3.org/2000/svg' role='img' font-family='Arial,sans-serif'>"]
    out.append("<text x='6' y='52' font-size='11' fill='#5b6575'>Horizontal</text>")
    out.append("<text x='6' y='112' font-size='11' fill='#5b6575'>Vertical</text>")
    out.append(f"<line x1='{left}' x2='{width - right}' y1='48' y2='48' stroke='#c9d0da'/>")
    out.append(f"<line x1='{left}' x2='{width - right}' y1='108' y2='108' stroke='#c9d0da'/>")
    for item in findings:
        if item["category"] != "Phasing" or item["end"] < low or item["start"] > high:
            continue
        color = SEVERITY_COLORS.get(item["severity"], "#5b6575")
        out.append(
            f"<rect x='{x(item['start']):.1f}' y='22' width='{max(2.0, x(item['end']) - x(item['start'])):.1f}' height='112' "
            f"fill='{color}' fill-opacity='0.10' stroke='{color}' stroke-opacity='0.5' stroke-dasharray='3 2'/>"
        )
    for group in groups:
        if group["end"] < low or group["start"] > high:
            continue
        color = TURN_COLORS.get(group["turn"] or "", "#5b6575")
        y = 30 if group["turn"] == "left" else 50
        out.append(f"<rect x='{x(group['start']):.1f}' y='{y}' width='{max(1.5, x(group['end']) - x(group['start'])):.1f}' height='16' fill='{color}' fill-opacity='0.35' stroke='{color}'/>")
        if group["arc_end"] > group["arc_start"]:
            out.append(f"<rect x='{x(group['arc_start']):.1f}' y='{y}' width='{max(1.0, x(group['arc_end']) - x(group['arc_start'])):.1f}' height='16' fill='{color}' fill-opacity='0.75'/>")
        out.append(f"<text x='{x(group['mid']):.1f}' y='{y - 3 if group['turn'] == 'left' else y + 28}' font-size='9.5' text-anchor='middle' fill='{color}'>R{group['radius']:,.0f}</text>")
    for curve in curves:
        if curve["kind"] == "flat" or curve["length"] <= 0 or curve["end"] < low or curve["start"] > high:
            continue
        color = "#d9822b" if curve["kind"] == "crest" else "#3a9e5f"
        y = 90 if curve["kind"] == "crest" else 110
        out.append(f"<rect x='{x(curve['start']):.1f}' y='{y}' width='{max(1.5, x(curve['end']) - x(curve['start'])):.1f}' height='16' fill='{color}' fill-opacity='0.55' stroke='{color}'/>")
        out.append(f"<line x1='{x(curve['station']):.1f}' x2='{x(curve['station']):.1f}' y1='{y}' y2='{y + 16}' stroke='#1d2430'/>")
        label = f"K{curve['K']:.0f}" if curve["K"] else ""
        out.append(f"<text x='{x(curve['station']):.1f}' y='{y - 3 if curve['kind'] == 'crest' else y + 28}' font-size='9.5' text-anchor='middle' fill='{color}'>{label}</text>")
    for tick in _axis_ticks(low, high, 8):
        out.append(f"<line x1='{x(tick):.1f}' x2='{x(tick):.1f}' y1='140' y2='146' stroke='#9aa5b5'/>")
        out.append(f"<text x='{x(tick):.1f}' y='160' font-size='11' text-anchor='{_anchor(x(tick), width)}' fill='#5b6575'>{html.escape(station_label(tick, unit))}</text>")
    out.append(
        "<g font-size='10.5' fill='#5b6575'>"
        f"<rect x='{left}' y='172' width='12' height='9' fill='{TURN_COLORS['left']}' fill-opacity='0.75'/><text x='{left + 16}' y='180'>left curve</text>"
        f"<rect x='{left + 90}' y='172' width='12' height='9' fill='{TURN_COLORS['right']}' fill-opacity='0.75'/><text x='{left + 106}' y='180'>right curve (light = transitions)</text>"
        f"<rect x='{left + 300}' y='172' width='12' height='9' fill='#d9822b' fill-opacity='0.55'/><text x='{left + 316}' y='180'>crest</text>"
        f"<rect x='{left + 360}' y='172' width='12' height='9' fill='#3a9e5f' fill-opacity='0.55'/><text x='{left + 376}' y='180'>sag</text>"
        f"<rect x='{left + 420}' y='172' width='12' height='9' fill='#c0392b' fill-opacity='0.15' stroke='#c0392b' stroke-dasharray='3 2'/><text x='{left + 436}' y='180'>phasing finding</text></g>"
    )
    out.append("</svg>")
    return "".join(out)


def review_section_html(name, result, unit=None, settings="", strip=5000.0):
    findings = result["findings"]
    groups = result["groups"]
    model, recommended = result["model"], result["recommended"]
    curves = model.curves() if model else []
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in ("High", "Medium", "Low")}
    parts = [f"<h3>Geometric design review — {html.escape(result['criteria'].name)}</h3>"]
    parts.append(f"<p class='meta'>{html.escape(settings)}</p>")
    parts.append(
        "<p class='note'>Recommendations are computed values to apply and verify in the design software; the source design is not changed. "
        "Each finding cites its criteria table. Only geometric elements under the designer's control are checked.</p>"
    )
    parts.append(
        _cards(
            [
                ("High (safety / absolute minimum)", str(counts["High"])),
                ("Medium (desirable / standard)", str(counts["Medium"])),
                ("Low (aesthetic / advisory)", str(counts["Low"])),
                ("Profile changes proposed", str(len(result.get("applied", ())))),
            ]
        )
    )
    categories = sorted({f["category"] for f in findings})
    parts.append(
        _table(
            ["Category", "High", "Medium", "Low"],
            [(c, *(sum(1 for f in findings if f["category"] == c and f["severity"] == s) for s in ("High", "Medium", "Low"))) for c in categories],
        )
    )
    speed_sections = result.get("speed_sections") or []
    if speed_sections:
        parts.append("<h3>Design speed sections</h3>")
        rows = []
        for index, (low, high, speed) in enumerate(speed_sections):
            step = speed - speed_sections[index - 1][2] if index else None
            rows.append((station_label(low, unit), station_label(high, unit), high - low, speed, f"{step:+g}" if step else "—"))
        parts.append(_table(["From", "To", "Length", "Design speed km/h", "Change km/h"], rows, 0))
        if len(speed_sections) == 1:
            parts.append("<p class='note'>One design speed applies to the whole alignment. Define speed stations in Civil 3D "
                         "(Alignment Properties → Design Criteria) before exporting, or enter speeds by chainage, where terrain changes.</p>")
    extent = [g["start"] for g in groups] + [g["end"] for g in groups] + [c["start"] for c in curves] + [c["end"] for c in curves]
    if model:
        extent += [model.pvis[0]["station"], model.pvis[-1]["station"]]
    start, end = (min(extent), max(extent)) if extent else (0.0, 0.0)
    if end > start:
        parts.append("<h3>Phasing diagram</h3>")
        for low, high in strip_windows(start, end, strip):
            parts.append(phasing_svg(groups, curves, findings, (low, high), unit))
    if model and recommended and result.get("applied"):
        delta = [(s, recommended.elevation(s) - model.elevation(s)) for s, _z in model.samples(5.0)]
        parts.append("<h3>Recommended profile — change in elevation</h3>")
        for low, high in strip_windows(start, end, strip):
            clipped = clip_series(delta, low, high)
            if clipped and max(abs(v) for _s, v in clipped) > 1e-3:
                parts.append(chart_svg([("Recommended − design", "#1f5fbf", clipped, True)], unit, "Δ elevation (m)", fmt="{:+.2f}"))
        rows = []
        original = {round(p["station"], 3): p for p in model.pvis}
        for index, p in enumerate(recommended.pvis):
            g_in = recommended.grade(index - 1, index) * 100 if index else None
            g_out = recommended.grade(index, index + 1) * 100 if index < len(recommended.pvis) - 1 else None
            changed = round(p["station"], 3) not in original or abs(original[round(p["station"], 3)]["length"] - p["length"]) > 1e-6
            rows.append(
                (
                    station_label(p["station"], unit) + (" ●" if changed else ""),
                    p["elevation"],
                    p["length"] or None,
                    g_in,
                    g_out,
                )
            )
        parts.append("<h3>Recommended PVIs (● changed)</h3><div class='scroll'>")
        parts.append(_table(["PVI", "Elevation", "Curve length", "Grade in %", "Grade out %"], rows, 3))
        parts.append("</div>")
    barriers = [f for f in findings if f["code"] == "R1"]
    if barriers:
        parts.append("<h3>Safety barrier schedule</h3>")
        parts.append(
            _table(
                ["Side", "From", "To", "Status", "Max fill height", "Recommendation"],
                [
                    (f["side"], station_label(f["start"], unit), station_label(f["end"], unit), f["title"].split("(")[0].replace("Embankment barrier ", ""),
                     f["value"], f["recommendation"])
                    for f in barriers
                ],
            )
        )
    parts.append("<h3>Findings and recommendations</h3><div class='scroll'>")
    body = []
    for f in findings:
        color = SEVERITY_COLORS.get(f["severity"], "#5b6575")
        body.append(
            "<tr>"
            f"<td><span style='color:{color};font-weight:600'>{f['severity']}</span></td>"
            f"<td>{html.escape(f['category'])}</td>"
            f"<td>{html.escape(station_label(f['start'], unit))}</td>"
            f"<td>{html.escape(station_label(f['end'], unit))}</td>"
            f"<td style='text-align:left'>{html.escape(f['title'])}<br><span class='meta'>{html.escape(f['detail'])}</span></td>"
            f"<td style='text-align:left'>{html.escape(f['recommendation'])}</td>"
            f"<td style='text-align:left'>{html.escape(f['reference'])}</td>"
            "</tr>"
        )
    parts.append(
        "<table><thead><tr><th>Severity</th><th>Category</th><th>From</th><th>To</th><th>Issue</th><th>Recommendation</th><th>Reference</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table></div>"
    )
    return "".join(parts)


# ------------------------------------------------------------- LandXML


def _serialize(element, indent="  ", level=0):
    tag = local_name(element.tag)
    attributes = "".join(f" {local_name(k)}={quoteattr(str(v))}" for k, v in element.attrib.items())
    children = list(element)
    pad = indent * level
    text = (element.text or "").strip()
    if not children:
        return f"{pad}<{tag}{attributes}>{escape(text)}</{tag}>" if text else f"{pad}<{tag}{attributes}/>"
    inner = "\n".join(_serialize(child, indent, level + 1) for child in children)
    return f"{pad}<{tag}{attributes}>{escape(text)}\n{inner}\n{pad}</{tag}>"


def recommended_landxml(alignment_element, model, profile_name, unit="meter"):
    """LandXML 1.2 with the alignment geometry and the recommended ProfAlign."""
    now = datetime.now()
    geometry = next((child for child in alignment_element if local_name(child.tag) == "CoordGeom"), None)
    attributes = {k: v for k, v in alignment_element.attrib.items() if local_name(k) in ("name", "length", "staStart", "desc")}
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<LandXML xmlns="http://www.landxml.org/schema/LandXML-1.2" version="1.2" date="{now:%Y-%m-%d}" time="{now:%H:%M:%S}">',
        f'  <Units><Metric linearUnit={quoteattr(unit or "meter")} areaUnit="squareMeter" volumeUnit="cubicMeter" angularUnit="decimal degrees" directionUnit="decimal degrees"/></Units>',
        '  <Application name="LandXML Road &amp; Terrain GIS Tools" desc="Geometric design review: recommended profile"/>',
        "  <Alignments>",
        "    <Alignment" + "".join(f" {k}={quoteattr(str(v))}" for k, v in attributes.items()) + ">",
    ]
    if geometry is not None:
        lines.append(_serialize(geometry, level=3))
    lines.append(f"      <Profile name={quoteattr(attributes.get('name', 'Profile'))}>")
    lines.append(f"        <ProfAlign name={quoteattr(profile_name)}>")
    for index, p in enumerate(model.pvis):
        if 0 < index < len(model.pvis) - 1 and p["length"] > 0:
            lines.append(f'          <ParaCurve length="{p["length"]:.4f}">{p["station"]:.6f} {p["elevation"]:.6f}</ParaCurve>')
        else:
            lines.append(f"          <PVI>{p['station']:.6f} {p['elevation']:.6f}</PVI>")
    lines += ["        </ProfAlign>", "      </Profile>", "    </Alignment>", "  </Alignments>", "</LandXML>"]
    return "\n".join(lines) + "\n"
