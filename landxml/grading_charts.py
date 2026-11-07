"""Inline SVG charts for the grading reports (no dependencies, print-safe).

Charts share the report style: cut in orange and fill in green, as in the
profile and corridor reports.
"""

from __future__ import annotations

import html
import math

from .reports import CUT_COLOR, FILL_COLOR, _axis_ticks

INK, MUTED, GRID = "#1d2430", "#5b6575", "#eef0f3"
FLAG, OK, WATER = "#c0392b", "#1f5f8b", "#3d8fd1"


def _fmt(value):
    return f"{value:,.0f}" if abs(value) >= 100 else f"{value:,.2f}".rstrip("0").rstrip(".")


def _legend(items):
    return "<div class='legend'>" + "".join(
        f"<span><i style='background:{color}'></i>{html.escape(label)}</span>" for label, color in items
    ) + "</div>"


def bars_svg(categories, series, unit="", width=1000, height=320):
    """Grouped vertical bars; ``series`` is [(label, colour, values)] with one value per category.

    Negative values hang below a zero line, so changes can be shown with the same chart.
    """
    values = [v for _label, _color, vals in series for v in vals] or [0.0]
    low, high = min(0.0, min(values)), max(0.0, max(values))
    if high == low:
        high = low + 1.0
    pad = (high - low) * 0.08
    low, high = (low - pad if low < 0 else low), high + pad
    left, right, top, bottom = 80, 16, 16, 52
    plot_w, plot_h = width - left - right, height - top - bottom
    y = lambda v: top + (high - v) / (high - low) * plot_h  # noqa: E731
    parts = [f"<svg viewBox='0 0 {width} {height}' xmlns='http://www.w3.org/2000/svg' role='img'>"]
    for tick in _axis_ticks(low, high, 6):
        parts.append(f"<line x1='{left}' x2='{width - right}' y1='{y(tick):.1f}' y2='{y(tick):.1f}' stroke='{GRID}'/>")
        parts.append(f"<text x='{left - 6}' y='{y(tick) + 4:.1f}' font-size='11' text-anchor='end' fill='{MUTED}'>{_fmt(tick)}</text>")
    parts.append(f"<line x1='{left}' x2='{width - right}' y1='{y(0):.1f}' y2='{y(0):.1f}' stroke='{MUTED}'/>")
    group = plot_w / max(len(categories), 1)
    bar = min(48.0, group * 0.8 / max(len(series), 1))
    for i, name in enumerate(categories):
        x0 = left + i * group + (group - bar * len(series)) / 2
        for j, (_label, color, vals) in enumerate(series):
            v = vals[i]
            y0, y1 = sorted((y(0), y(v)))
            parts.append(f"<rect x='{x0 + j * bar:.1f}' y='{y0:.1f}' width='{bar - 2:.1f}' height='{max(y1 - y0, 0.5):.1f}' fill='{color}'>"
                         f"<title>{html.escape(str(name))}: {_fmt(v)} {html.escape(unit)}</title></rect>")
        parts.append(f"<text x='{left + (i + 0.5) * group:.1f}' y='{height - bottom + 18}' font-size='12' text-anchor='middle' "
                     f"fill='{INK}'>{html.escape(str(name))[:28]}</text>")
    if unit:
        parts.append(f"<text x='14' y='{top + plot_h / 2:.1f}' font-size='11' fill='{MUTED}' transform='rotate(-90 14 {top + plot_h / 2:.1f})' "
                     f"text-anchor='middle'>{html.escape(unit)}</text>")
    parts.append("</svg>")
    return "".join(parts) + _legend([(label, color) for label, color, _ in series])


def depth_bands_svg(bands, units, width=1000, height=300):
    """Plan area per depth band: cut bands orange, fill bands green, the band containing zero grey."""
    rows = [row for row in bands if row["area"] > 0]
    if not rows:
        return ""
    colors = []
    for row in rows:
        high, low = row["high"], row["low"]
        if high is not None and high <= 0:
            colors.append(CUT_COLOR)
        elif low is not None and low >= 0:
            colors.append(FILL_COLOR)
        else:
            colors.append("#9aa5b5")
    left, right, top, bottom = 80, 16, 16, 52
    plot_w, plot_h = width - left - right, height - top - bottom
    peak = max(row["area"] for row in rows) * 1.08
    parts = [f"<svg viewBox='0 0 {width} {height}' xmlns='http://www.w3.org/2000/svg' role='img'>"]
    for tick in _axis_ticks(0, peak, 5):
        yy = top + plot_h - tick / peak * plot_h
        parts.append(f"<line x1='{left}' x2='{width - right}' y1='{yy:.1f}' y2='{yy:.1f}' stroke='{GRID}'/>")
        parts.append(f"<text x='{left - 6}' y='{yy + 4:.1f}' font-size='11' text-anchor='end' fill='{MUTED}'>{_fmt(tick)}</text>")
    step = plot_w / len(rows)
    for i, (row, color) in enumerate(zip(rows, colors)):
        h = row["area"] / peak * plot_h
        parts.append(f"<rect x='{left + i * step + 3:.1f}' y='{top + plot_h - h:.1f}' width='{step - 6:.1f}' height='{h:.1f}' fill='{color}'>"
                     f"<title>{html.escape(row['band'])} {html.escape(units.depth)}: {_fmt(row['area'])} {html.escape(units.area)}</title></rect>")
        parts.append(f"<text x='{left + (i + 0.5) * step:.1f}' y='{height - bottom + 18}' font-size='11' text-anchor='middle' "
                     f"fill='{INK}'>{html.escape(row['band'])}</text>")
    parts.append(f"<text x='{left + plot_w / 2:.1f}' y='{height - 10}' font-size='11' text-anchor='middle' fill='{MUTED}'>"
                 f"Depth band ({html.escape(units.depth)}; negative = cut)</text>")
    parts.append(f"<text x='14' y='{top + plot_h / 2:.1f}' font-size='11' fill='{MUTED}' transform='rotate(-90 14 {top + plot_h / 2:.1f})' "
                 f"text-anchor='middle'>Plan area ({html.escape(units.area)})</text></svg>")
    return "".join(parts) + _legend([("Cut", CUT_COLOR), ("Fill", FILL_COLOR), ("Band across zero", "#9aa5b5")])


def stage_storage_svg(summary, units, width=1000, height=360):
    """Storage against level, with the freeboard level, rim and required volume marked."""
    rows = summary["rows"]
    if not rows:
        return ""
    levels = [summary["floor"]] + [row["level"] for row in rows]
    volumes = [0.0] + [row["volume"] for row in rows]
    vmax = max(volumes + [summary["required_volume"] or 0.0]) * 1.08 or 1.0
    zlow, zhigh = summary["floor"], summary["rim"] + (summary["rim"] - summary["floor"]) * 0.05
    left, right, top, bottom = 80, 24, 16, 52
    plot_w, plot_h = width - left - right, height - top - bottom
    x = lambda v: left + v / vmax * plot_w  # noqa: E731
    y = lambda z: top + (zhigh - z) / (zhigh - zlow) * plot_h  # noqa: E731
    parts = [f"<svg viewBox='0 0 {width} {height}' xmlns='http://www.w3.org/2000/svg' role='img'>"]
    for tick in _axis_ticks(0, vmax, 6):
        parts.append(f"<line x1='{x(tick):.1f}' x2='{x(tick):.1f}' y1='{top}' y2='{top + plot_h}' stroke='{GRID}'/>")
        parts.append(f"<text x='{x(tick):.1f}' y='{top + plot_h + 16}' font-size='11' text-anchor='middle' fill='{MUTED}'>{_fmt(tick)}</text>")
    for tick in _axis_ticks(zlow, zhigh, 6):
        parts.append(f"<line x1='{left}' x2='{width - right}' y1='{y(tick):.1f}' y2='{y(tick):.1f}' stroke='{GRID}'/>")
        parts.append(f"<text x='{left - 6}' y='{y(tick) + 4:.1f}' font-size='11' text-anchor='end' fill='{MUTED}'>{tick:.2f}</text>")
    path = " ".join(f"{'M' if i == 0 else 'L'}{x(v):.1f},{y(z):.1f}" for i, (v, z) in enumerate(zip(volumes, levels)))
    parts.append(f"<path d='{path}' fill='none' stroke='{WATER}' stroke-width='2.5'/>")
    for z, label, color in ((summary["rim"], "Rim", INK), (summary["design_level"], "Freeboard level", OK)):
        if z > summary["floor"]:
            parts.append(f"<line x1='{left}' x2='{width - right}' y1='{y(z):.1f}' y2='{y(z):.1f}' stroke='{color}' stroke-dasharray='6 4'/>")
            parts.append(f"<text x='{width - right - 4}' y='{y(z) - 5:.1f}' font-size='11' text-anchor='end' fill='{color}'>{label} {z:.2f}</text>")
    if summary["required_volume"]:
        rx = x(summary["required_volume"])
        parts.append(f"<line x1='{rx:.1f}' x2='{rx:.1f}' y1='{top}' y2='{top + plot_h}' stroke='{FLAG}' stroke-dasharray='6 4'/>")
        anchor = "end" if rx > left + plot_w * 0.8 else "start"
        parts.append(f"<text x='{rx + (-4 if anchor == 'end' else 4):.1f}' y='{top + plot_h - 8:.1f}' font-size='11' text-anchor='{anchor}' "
                     f"fill='{FLAG}'>Required {_fmt(summary['required_volume'])}</text>")
    parts.append(f"<text x='{left + plot_w / 2:.1f}' y='{height - 10}' font-size='11' text-anchor='middle' fill='{MUTED}'>"
                 f"Storage ({html.escape(units.volume)})</text>")
    parts.append(f"<text x='14' y='{top + plot_h / 2:.1f}' font-size='11' fill='{MUTED}' transform='rotate(-90 14 {top + plot_h / 2:.1f})' "
                 f"text-anchor='middle'>Level ({html.escape(units.depth)})</text></svg>")
    return "".join(parts) + _legend([("Storage below level", WATER), ("Required storage", FLAG)])


def platforms_svg(platforms, width=1000, height=520):
    """Plan sketch of platform outlines with labels and fall arrows; flagged platforms in red."""
    rings = [ring for item in platforms for outer, holes in item["polygons"] for ring in [outer, *holes]]
    if not rings:
        return ""
    xs = [p[0] for ring in rings for p in ring]
    ys = [p[1] for ring in rings for p in ring]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    pad = 20
    scale = min((width - 2 * pad) / max(x1 - x0, 1e-9), (height - 2 * pad) / max(y1 - y0, 1e-9))
    ox = (width - (x1 - x0) * scale) / 2
    oy = (height - (y1 - y0) * scale) / 2
    sx = lambda px: ox + (px - x0) * scale  # noqa: E731
    sy = lambda py: height - oy - (py - y0) * scale  # noqa: E731
    parts = [f"<svg viewBox='0 0 {width} {height}' xmlns='http://www.w3.org/2000/svg' role='img'>",
             "<defs><marker id='fall' viewBox='0 0 10 10' refX='9' refY='5' markerWidth='7' markerHeight='7' orient='auto'>"
             f"<path d='M0,0 L10,5 L0,10 z' fill='{INK}'/></marker></defs>"]
    for item in platforms:
        color = FLAG if item["below_min_fall"] else OK
        for outer, holes in item["polygons"]:
            d = " ".join("M" + " L".join(f"{sx(px):.1f},{sy(py):.1f}" for px, py in ring) + " Z" for ring in [outer, *holes])
            parts.append(f"<path d='{d}' fill='{color}' fill-opacity='0.18' fill-rule='evenodd' stroke='{color}' stroke-width='1.5'>"
                         f"<title>{item['id']}: level {item['level_mean']:.2f}, fall {100 * item['fall']:.2f} %</title></path>")
        biggest = max(item["polygons"], key=lambda poly: len(poly[0]))[0]
        cx = sum(px for px, _ in biggest) / len(biggest)
        cy = sum(py for _, py in biggest) / len(biggest)
        tx, ty = sx(cx), sy(cy)
        parts.append(f"<text x='{tx:.1f}' y='{ty - 6:.1f}' font-size='13' font-weight='600' text-anchor='middle' fill='{INK}'>{item['id']}</text>")
        parts.append(f"<text x='{tx:.1f}' y='{ty + 9:.1f}' font-size='11' text-anchor='middle' fill='{MUTED}'>"
                     f"{item['level_mean']:.2f} · {100 * item['fall']:.1f} %</text>")
        if item["fall_bearing"] is not None:
            angle = math.radians(item["fall_bearing"])
            length = 26.0
            dx, dy = math.sin(angle) * length, -math.cos(angle) * length
            parts.append(f"<line x1='{tx - dx / 2:.1f}' y1='{ty + 22 - dy / 2:.1f}' x2='{tx + dx / 2:.1f}' y2='{ty + 22 + dy / 2:.1f}' "
                         f"stroke='{INK}' stroke-width='1.5' marker-end='url(#fall)'/>")
    parts.append(f"<text x='{width - 12}' y='{height - 10}' font-size='11' text-anchor='end' fill='{MUTED}'>Arrows point downhill; north up</text></svg>")
    return "".join(parts) + _legend([("Meets minimum fall", OK), ("Below minimum fall", FLAG)])
