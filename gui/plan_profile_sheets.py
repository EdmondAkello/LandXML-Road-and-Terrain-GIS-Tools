"""Plan & profile PDF sheets for the geometric design review (QPainter, Qt 5 and Qt 6).

One A3 landscape sheet per chainage window: a plan strip with numbered
findings, a banded long section (design, recommended and ground lines with
design speed, curvature and grade bands) and the findings for that window.
The drawing data comes from :mod:`landxml.plan_profile`.
"""

from __future__ import annotations

from datetime import datetime
import math

from qgis.PyQt.QtCore import QPointF, QRectF, Qt
from qgis.PyQt.QtGui import QBrush, QColor, QFont, QFontMetrics, QPageLayout, QPageSize, QPainter, QPainterPath, QPdfWriter, QPen, QPolygonF

from ..landxml.plan_profile import (
    CREST_COLOR,
    DESIGN_COLOR,
    GROUND_COLOR,
    RECOMMENDED_COLOR,
    SAG_COLOR,
    SEVERITY_COLORS,
    TURN_COLORS,
    badge_rows,
    finding_label,
    level_range,
)
from ..landxml.reports import _axis_ticks, station_label

INK = "#1d2430"
MUTED = "#5b6575"
GRID = "#e3e7ec"
FRAME = "#9aa5b5"
WIDTH = 1600.0
MAX_ROWS = 9


def _font(size, bold=False):
    font = QFont("Arial")
    font.setStyleHint(QFont.StyleHint.SansSerif)
    font.setPixelSize(int(size))
    font.setBold(bold)
    return font


def _text(painter, x, y, text, size=11, color=INK, align="left", bold=False):
    painter.setFont(_font(size, bold))
    painter.setPen(QColor(color))
    width = QFontMetrics(painter.font()).horizontalAdvance(text)
    dx = {"left": 0.0, "center": -width / 2.0, "right": -width}[align]
    painter.drawText(QPointF(x + dx, y), text)
    return width


def _elide(text, size, width):
    metrics = QFontMetrics(_font(size))
    return metrics.elidedText(text, Qt.TextElideMode.ElideRight, int(width))


def _badge(painter, x, y, number, severity, radius=9.0):
    painter.setPen(QPen(QColor("#ffffff"), 1.2))
    painter.setBrush(QBrush(QColor(SEVERITY_COLORS.get(severity, MUTED))))
    painter.drawEllipse(QPointF(x, y), radius, radius)
    _text(painter, x, y + radius * 0.42, str(number), radius * 1.15, "#ffffff", "center", True)


def finding_lanes(findings, low, high, count=6):
    """Greedy lane per finding so overlapping chainage ranges stack instead of hiding each other."""
    ends = []
    lanes = [0] * len(findings)
    gap = (high - low) * 0.004
    for i in sorted(range(len(findings)), key=lambda k: (findings[k]["start"], findings[k]["end"])):
        f = findings[i]
        for lane, end in enumerate(ends):
            if f["start"] > end + gap:
                ends[lane] = f["end"]
                lanes[i] = lane
                break
        else:
            if len(ends) < count:
                ends.append(f["end"])
                lanes[i] = len(ends) - 1
            else:
                lanes[i] = count - 1
    return lanes


def draw_plan(painter, rect, plan, unit=None):
    painter.setPen(QPen(QColor(FRAME), 0.8))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(rect)
    _text(painter, rect.left() + 8, rect.top() + 16, "PLAN", 12, MUTED, bold=True)
    if not plan:
        _text(painter, rect.center().x(), rect.center().y(), "No horizontal geometry in this window", 12, MUTED, "center")
        return
    u0, v0, u1, v1 = plan["bounds"]
    inner = rect.adjusted(256, 30, -64, -30)
    span_u, span_v = max(u1 - u0, 1e-6), max(v1 - v0, 1e-6)
    scale = min(inner.width() / span_u, inner.height() / span_v)
    cu, cv = (u0 + u1) / 2.0, (v0 + v1) / 2.0

    def p(u, v):
        return QPointF(inner.center().x() + (u - cu) * scale, inner.center().y() - (v - cv) * scale)

    painter.save()
    painter.setClipRect(rect)
    # Carriageway corridor (schematic, 12 m) and centreline
    corridor = QPen(QColor("#dfe5ec"), max(12.0 * scale, 6.0))
    corridor.setCapStyle(Qt.PenCapStyle.FlatCap)
    path = QPainterPath(p(plan["line"][0][1], plan["line"][0][2]))
    for _s, u, v in plan["line"][1:]:
        path.lineTo(p(u, v))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(corridor)
    painter.drawPath(path)
    painter.setPen(QPen(QColor(INK), 1.6))
    painter.drawPath(path)
    for tick in plan["ticks"]:
        centre = p(tick["u"], tick["v"])
        # Normal on the sheet (sheet y grows downward)
        nx, ny = tick["nu"], -tick["nv"]
        norm = math.hypot(nx, ny) or 1.0
        nx, ny = nx / norm, ny / norm
        size = 11.0 if tick["major"] else 6.0
        painter.setPen(QPen(QColor(INK), 1.0))
        painter.drawLine(QPointF(centre.x() - nx * size, centre.y() - ny * size), QPointF(centre.x() + nx * size, centre.y() + ny * size))
        if tick["major"] or len(plan["ticks"]) <= 12:
            lx, ly = centre.x() - nx * (size + 14), centre.y() - ny * (size + 14)
            _text(painter, lx, ly + 4, station_label(tick["station"], unit), 10.5, MUTED, "center")
    placed = []
    for marker in plan["markers"]:
        centre = p(marker["u"], marker["v"])
        nx, ny = marker["nu"], -marker["nv"]
        norm = math.hypot(nx, ny) or 1.0
        side = marker["side"] or 1.0
        distance = 26.0
        for _attempt in range(6):
            mx, my = centre.x() + nx / norm * distance * side, centre.y() + ny / norm * distance * side
            if all(math.hypot(mx - qx, my - qy) >= 18.0 for qx, qy in placed):
                break
            distance += 18.0
        placed.append((centre.x() + nx / norm * distance * side, centre.y() + ny / norm * distance * side))
        mx, my = centre.x() + nx / norm * distance * side, centre.y() + ny / norm * distance * side
        painter.setPen(QPen(QColor(SEVERITY_COLORS.get(marker["severity"], MUTED)), 1.0))
        painter.drawLine(centre, QPointF(mx, my))
        _badge(painter, mx, my, marker["number"], marker["severity"], 8.5)
    painter.restore()
    # North arrow (grid north of the source coordinates)
    ax, ay = rect.right() - 34, rect.top() + 44
    angle = plan["north"]
    dx, dy = math.cos(angle), -math.sin(angle)
    tip = QPointF(ax + dx * 18, ay + dy * 18)
    tail = QPointF(ax - dx * 18, ay - dy * 18)
    left = QPointF(ax - dy * 6 - dx * 6, ay + dx * 6 - dy * 6)
    right = QPointF(ax + dy * 6 - dx * 6, ay - dx * 6 - dy * 6)
    painter.setPen(QPen(QColor(INK), 1.2))
    painter.drawLine(tail, tip)
    painter.setBrush(QBrush(QColor(INK)))
    painter.drawPolygon(QPolygonF([tip, left, right]))
    _text(painter, ax + dx * 30, ay + dy * 30 + 4, "N", 12, INK, "center", True)


def draw_long_section(painter, rect, data, unit=None, vertical_unit=""):
    """Profile panel plus speed, curvature and grade bands; returns the x mapping."""
    low, high = data["window"]
    label_w = 88.0
    band_h = 34.0
    bands = ["Design speed", "Horizontal", "Vertical", "Grade %"]
    lane_h = 74.0
    lane_band = QRectF(rect.left() + label_w, rect.top(), rect.width() - label_w, lane_h)
    plot = QRectF(lane_band.left(), lane_band.bottom(), lane_band.width(), rect.height() - lane_h - band_h * len(bands) - 26)
    z0, z1 = level_range(data["rows"])

    def x(s):
        return plot.left() + (min(max(s, low), high) - low) / (high - low) * plot.width()

    def y(z):
        return plot.bottom() - (z - z0) / (z1 - z0) * plot.height()

    painter.setPen(QPen(QColor(FRAME), 0.8))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(lane_band)
    _text(painter, lane_band.left() - 6, lane_band.center().y() + 4, "Findings", 10.5, MUTED, "right")
    painter.setBrush(QBrush(QColor("#fbfcfd")))
    painter.drawRect(plot)
    _text(painter, rect.left() + 4, plot.top() + 16, "PROFILE", 12, MUTED, bold=True)
    if vertical_unit:
        _text(painter, rect.left() + 4, plot.top() + 32, f"Level ({vertical_unit})", 10, MUTED)
    lanes = finding_lanes(data["findings"], low, high, 4)
    for f, lane in zip(data["findings"], lanes):
        color = QColor(SEVERITY_COLORS.get(f["severity"], MUTED))
        x0, x1 = x(f["start"]), max(x(f["start"]) + 2.0, x(f["end"]))
        shade = QColor(color)
        shade.setAlphaF(0.045)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(shade))
        painter.drawRect(QRectF(x0, plot.top(), x1 - x0, plot.height()))
        painter.setBrush(QBrush(color))
        painter.drawRect(QRectF(x0, lane_band.top() + 56 + lane * 4, x1 - x0, 2.6))
    for tick in _axis_ticks(z0, z1, 6):
        painter.setPen(QPen(QColor(GRID), 0.6))
        painter.drawLine(QPointF(plot.left(), y(tick)), QPointF(plot.right(), y(tick)))
        if y(tick) > plot.top() + 40:
            _text(painter, plot.left() - 6, y(tick) + 4, f"{tick:,.1f}", 10, MUTED, "right")
    bottom = plot.bottom() + band_h * len(bands)
    ticks = _axis_ticks(low, high, 10)
    for tick in ticks:
        painter.setPen(QPen(QColor(GRID), 0.6))
        painter.drawLine(QPointF(x(tick), lane_band.top()), QPointF(x(tick), bottom))

    def polyline(index, color, width, dash=False):
        pen = QPen(QColor(color), width)
        if dash:
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        current = []
        for row in data["rows"] + [(None, None, None, None)]:
            if row[0] is None or row[index] is None:
                if len(current) > 1:
                    painter.drawPolyline(QPolygonF(current))
                current = []
                continue
            current.append(QPointF(x(row[0]), y(row[index])))

    if data["has_ground"]:
        polyline(3, GROUND_COLOR, 1.3)
    polyline(1, DESIGN_COLOR, 1.9)
    if data["has_recommended"]:
        polyline(2, RECOMMENDED_COLOR, 1.9, True)
    for change in data["changed"]:
        px, py = x(change["station"]), y(change["elevation"])
        painter.setPen(QPen(QColor(RECOMMENDED_COLOR), 1.6))
        painter.setBrush(QBrush(QColor("#ffffff")))
        painter.drawEllipse(QPointF(px, py), 4.5, 4.5)
        _text(painter, px + 7, py - 7, f"PVI {change['pvi']}", 10, RECOMMENDED_COLOR)
    for f, row in zip(data["findings"], badge_rows([x((max(f["start"], low) + min(f["end"], high)) / 2.0) for f in data["findings"]], 18.0)):
        mid = (max(f["start"], low) + min(f["end"], high)) / 2.0
        _badge(painter, x(mid), lane_band.top() + 11 + row * 17, f["number"], f["severity"], 8.0)
    # Bands
    for i, name in enumerate(bands):
        band = QRectF(plot.left(), plot.bottom() + band_h * i, plot.width(), band_h)
        painter.setPen(QPen(QColor(FRAME), 0.8))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(band)
        _text(painter, plot.left() - 6, band.center().y() + 4, name, 10.5, MUTED, "right")
    band_top = plot.bottom()
    mid = band_top + band_h / 2
    for a, b, v in data["speeds"]:
        painter.setPen(QPen(QColor(INK), 1.4))
        painter.drawLine(QPointF(x(a), mid + 6), QPointF(x(b), mid + 6))
        painter.drawLine(QPointF(x(a), band_top + 6), QPointF(x(a), band_top + band_h - 6))
        _text(painter, (x(a) + x(b)) / 2, mid + 1, f"{v:g} {data['speed_unit']}", 11, INK, "center")
    band_top = plot.bottom() + band_h
    mid = band_top + band_h / 2
    painter.setPen(QPen(QColor(MUTED), 0.8))
    painter.drawLine(QPointF(plot.left(), mid), QPointF(plot.right(), mid))
    for g in data["groups"]:
        color = QColor(TURN_COLORS.get(g.get("turn") or "", MUTED))
        sign = -1 if g.get("turn") == "right" else 1
        y1 = mid - sign * 12
        fill = QColor(color)
        fill.setAlphaF(0.25)
        painter.setPen(QPen(color, 1.2))
        painter.setBrush(QBrush(fill))
        painter.drawPolygon(QPolygonF([QPointF(x(g["start"]), mid), QPointF(x(g["arc_start"]), y1),
                                       QPointF(x(g["arc_end"]), y1), QPointF(x(g["end"]), mid)]))
        text = f"R{g['radius']:,.0f} {'R' if g.get('turn') == 'right' else 'L'}"
        _text(painter, (x(g["arc_start"]) + x(g["arc_end"])) / 2, mid + 13 if sign > 0 else mid - 4, text, 10, color.name(), "center")
    band_top = plot.bottom() + 2 * band_h
    mid = band_top + band_h / 2
    painter.setPen(QPen(QColor(MUTED), 0.8))
    painter.drawLine(QPointF(plot.left(), mid), QPointF(plot.right(), mid))
    for c in data["curves"]:
        color = QColor(CREST_COLOR if c["kind"] == "crest" else SAG_COLOR)
        crest = c["kind"] == "crest"
        y1 = mid - 12 if crest else mid + 12
        fill = QColor(color)
        fill.setAlphaF(0.35)
        a, b = x(c["start"]), x(c["end"])
        painter.setPen(QPen(color, 1.2))
        painter.setBrush(QBrush(fill))
        painter.drawPolygon(QPolygonF([QPointF(a, mid), QPointF(x(c["station"]), y1), QPointF(b, mid)]))
        name = "Crest" if crest else "Sag"
        label = f"{name} K{c['K']:.0f} L{c['length']:.0f}" if c["K"] else f"L{c['length']:.0f}"
        _text(painter, x(c["station"]), mid + 13 if crest else mid - 4, label, 10, color.name(), "center")
    # Phasing: horizontal and vertical curves sit in adjacent bands; phasing findings outline both.
    top_h = plot.bottom() + band_h
    for f in data["findings"]:
        if f.get("category") != "Phasing":
            continue
        pen = QPen(QColor(SEVERITY_COLORS.get(f["severity"], MUTED)), 1.3)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        a, b = x(f["start"]), max(x(f["start"]) + 3.0, x(f["end"]))
        painter.drawRect(QRectF(a, top_h + 2, b - a, 2 * band_h - 4))
        _text(painter, a + 3, top_h + 12, str(f["number"]), 9.5, SEVERITY_COLORS.get(f["severity"], MUTED), bold=True)
    band_top = plot.bottom() + 3 * band_h
    for a, b, g in data["grades"]:
        painter.setPen(QPen(QColor(FRAME), 0.8))
        painter.drawLine(QPointF(x(a), band_top + 4), QPointF(x(a), band_top + band_h - 4))
        if x(b) - x(a) > 40:
            _text(painter, (x(a) + x(b)) / 2, band_top + band_h / 2 + 4, f"{g:+.2f}", 11, INK, "center")
    for tick in ticks:
        painter.setPen(QPen(QColor(FRAME), 0.8))
        painter.drawLine(QPointF(x(tick), bottom), QPointF(x(tick), bottom + 6))
        align = "left" if x(tick) - plot.left() < 30 else "right" if plot.right() - x(tick) < 30 else "center"
        _text(painter, x(tick), bottom + 19, station_label(tick, unit), 10.5, MUTED, align)


def draw_findings(painter, rect, findings, unit=None):
    painter.setPen(QPen(QColor(FRAME), 0.8))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(rect)
    _text(painter, rect.left() + 8, rect.top() + 17, "FINDINGS IN THIS WINDOW", 12, MUTED, bold=True)
    columns = [("No.", 40), ("Chainage", 150), ("Issue", 0.42), ("Recommendation", 0.58)]
    fixed = sum(w for _n, w in columns if w > 1)
    free = rect.width() - 16 - fixed
    widths = [w if w > 1 else free * w for _n, w in columns]
    top = rect.top() + 26
    row_h = (rect.height() - 34) / (MAX_ROWS + 1)
    cursor = rect.left() + 8
    for (name, _w), width in zip(columns, widths):
        _text(painter, cursor, top + row_h * 0.7, name, 10.5, MUTED, bold=True)
        cursor += width
    painter.setPen(QPen(QColor(GRID), 0.8))
    painter.drawLine(QPointF(rect.left() + 8, top + row_h), QPointF(rect.right() - 8, top + row_h))
    if not findings:
        _text(painter, rect.left() + 8, top + row_h * 1.8, "No findings in this chainage window.", 11, MUTED)
        return
    order = {"High": 0, "Medium": 1, "Low": 2}
    ranked = sorted(findings, key=lambda f: (order.get(f["severity"], 3), f["number"]))
    shown = ranked if len(ranked) <= MAX_ROWS else sorted(ranked[: MAX_ROWS - 1], key=lambda f: f["number"])
    for i, f in enumerate(shown, 1):
        y = top + row_h * i
        _badge(painter, rect.left() + 20, y + row_h / 2, f["number"], f["severity"], 8.0)
        cursor = rect.left() + 8 + widths[0]
        _text(painter, cursor, y + row_h * 0.68, finding_label(f, unit), 10.5)
        cursor += widths[1]
        title = f"{f['code']} {f['title']}" if f.get("code") else f["title"]
        _text(painter, cursor, y + row_h * 0.68, _elide(title, 10.5, widths[2] - 10), 10.5)
        cursor += widths[2]
        _text(painter, cursor, y + row_h * 0.68, _elide(f["recommendation"] or "—", 10.5, widths[3] - 10), 10.5)
    if len(shown) < len(findings):
        rest = len(findings) - len(shown)
        _text(painter, rect.left() + 8 + widths[0], top + row_h * MAX_ROWS + row_h * 0.68,
              f"+ {rest} more finding(s) in this window: see the findings table or the HTML report.", 10.5, MUTED)


def draw_legend(painter, rect, data):
    painter.setPen(QPen(QColor(FRAME), 0.8))
    painter.setBrush(QBrush(QColor(255, 255, 255, 235)))
    painter.drawRect(rect)
    items = [("Design profile", DESIGN_COLOR, "line")]
    if data["has_recommended"]:
        items.append(("Recommended profile", RECOMMENDED_COLOR, "dash"))
    if data["has_ground"]:
        items.append(("Existing ground", GROUND_COLOR, "line"))
    items += [("Changed PVI", RECOMMENDED_COLOR, "pvi"), ("Crest / sag curve", CREST_COLOR, "line"),
              ("Left / right curve", TURN_COLORS["left"], "area"),
              ("Phasing finding (both bands)", SEVERITY_COLORS["Medium"], "phasing"), ("Finding (High · Medium · Low)", SEVERITY_COLORS["High"], "badge")]
    y = rect.top() + 18
    for label, color, kind in items:
        x = rect.left() + 10
        if kind in ("line", "dash"):
            pen = QPen(QColor(color), 1.9)
            if kind == "dash":
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(QPointF(x, y - 4), QPointF(x + 24, y - 4))
            if label.startswith("Crest"):
                painter.setPen(QPen(QColor(SAG_COLOR), 1.9))
                painter.drawLine(QPointF(x + 14, y - 4), QPointF(x + 24, y - 4))
        elif kind == "pvi":
            painter.setPen(QPen(QColor(color), 1.6))
            painter.setBrush(QBrush(QColor("#ffffff")))
            painter.drawEllipse(QPointF(x + 12, y - 4), 4.5, 4.5)
        elif kind == "phasing":
            pen = QPen(QColor(color), 1.3)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(x, y - 10, 24, 10))
        elif kind == "area":
            for offset, name in ((0, "left"), (13, "right")):
                fill = QColor(TURN_COLORS[name])
                fill.setAlphaF(0.4)
                painter.setPen(QPen(QColor(TURN_COLORS[name]), 1.0))
                painter.setBrush(QBrush(fill))
                painter.drawRect(QRectF(x + offset, y - 9, 11, 9))
        else:
            for offset, severity in ((5, "High"), (14, "Medium"), (23, "Low")):
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(QColor(SEVERITY_COLORS[severity])))
                painter.drawEllipse(QPointF(x + offset, y - 4), 4.5, 4.5)
        _text(painter, x + 34, y, label, 10.5)
        y += 19


def draw_notes(painter, rect, notes):
    painter.setPen(QPen(QColor(FRAME), 0.8))
    painter.setBrush(QBrush(QColor(255, 255, 255, 235)))
    painter.drawRect(rect)
    _text(painter, rect.left() + 8, rect.top() + 17, "NOTES", 11, MUTED, bold=True)
    painter.setFont(_font(10))
    painter.setPen(QColor(INK))
    text_rect = rect.adjusted(8, 24, -8, -6)
    painter.drawText(text_rect, int(Qt.TextFlag.TextWordWrap) | int(Qt.AlignmentFlag.AlignTop), "\n".join(f"{i}. {n}" for i, n in enumerate(notes, 1)))


def draw_title(painter, rect, lines):
    painter.setPen(QPen(QColor(INK), 1.2))
    painter.setBrush(QBrush(QColor("#ffffff")))
    painter.drawRect(rect)
    step = (rect.height() - 10) / len(lines)
    for i, text in enumerate(lines):
        _text(painter, rect.left() + 10, rect.top() + 4 + step * (i + 0.72), _elide(text, 14 if i == 0 else 11, rect.width() - 20),
              14 if i == 0 else 11, INK if i < 2 else MUTED, bold=i == 0)
    painter.setPen(QPen(QColor(INK), 0.8))
    painter.drawLine(QPointF(rect.left(), rect.top() + step + 4), QPointF(rect.right(), rect.top() + step + 4))


def write_plan_profile_sheets(path, sheets, title="", subtitle="", unit=None, vertical_unit="", notes=(), progress=None, cancel=None):
    """Write the sheets (``plan_profile.sheet_data`` dicts) to one A3 landscape PDF; returns the page count."""
    if not sheets:
        raise ValueError("There is nothing to plot.")
    writer = QPdfWriter(path)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A3))
    writer.setPageOrientation(QPageLayout.Orientation.Landscape)
    writer.setResolution(200)
    writer.setTitle(title or "Plan and profile")
    writer.setCreator("LandXML Road & Terrain GIS Tools")
    painter = QPainter(writer)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    viewport = painter.viewport()
    height = WIDTH * viewport.height() / max(viewport.width(), 1)
    painter.scale(viewport.width() / WIDTH, viewport.width() / WIDTH)
    margin = 24.0
    border = QRectF(margin, margin, WIDTH - 2 * margin, height - 2 * margin)
    inner = border.adjusted(12, 12, -12, -12)
    stamp = datetime.now().strftime("%Y-%m-%d")
    pages = len(sheets)
    for index, data in enumerate(sheets):
        if cancel is not None and cancel():
            break
        if index:
            writer.newPage()
        painter.setPen(QPen(QColor(INK), 1.4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(border)
        plan_rect = QRectF(inner.left(), inner.top(), inner.width(), inner.height() * 0.27)
        section_rect = QRectF(inner.left(), plan_rect.bottom() + 12, inner.width(), inner.height() * 0.45)
        bottom = QRectF(inner.left(), section_rect.bottom() + 12, inner.width(), inner.bottom() - section_rect.bottom() - 12)
        draw_plan(painter, plan_rect, data["plan"], unit)
        draw_long_section(painter, section_rect, data, unit, vertical_unit)
        side_w = 430.0
        draw_findings(painter, QRectF(bottom.left(), bottom.top(), bottom.width() - side_w - 12, bottom.height()), data["findings"], unit)
        legend_h = 18 + 19 * (6 + data["has_recommended"] + data["has_ground"])
        draw_legend(painter, QRectF(plan_rect.left() + 8, plan_rect.top() + 26, 230, legend_h - 6), data)
        title_h = 104.0
        notes_rect = QRectF(bottom.right() - side_w, bottom.top(), side_w, bottom.height() - title_h - 10)
        if notes and notes_rect.height() > 40:
            draw_notes(painter, notes_rect, notes)
        low, high = data["window"]
        draw_title(
            painter,
            QRectF(bottom.right() - side_w, bottom.bottom() - title_h, side_w, title_h),
            [title or "Plan and profile", f"{data.get('alignment') or subtitle}  CH {station_label(low, unit)} – {station_label(high, unit)}".strip(),
             f"Sheet {index + 1} of {pages}  ·  {stamp}"],
        )
        if progress is not None:
            progress(int(100 * (index + 1) / pages))
    painter.end()
    return pages
