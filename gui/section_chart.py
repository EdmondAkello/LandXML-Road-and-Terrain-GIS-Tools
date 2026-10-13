"""Cross-section drawing (QPainter) shared by the viewer, PDF sheets and SVG.

One renderer draws every section so the screen, the sheets and the HTML
report look identical. Works with Qt 5 and Qt 6.
"""

from __future__ import annotations

from datetime import datetime
import math

from qgis.PyQt.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QPageLayout,
    QPageSize,
    QPainter,
    QPdfWriter,
    QPen,
    QPolygonF,
)
from qgis.PyQt.QtWidgets import QSizePolicy, QWidget

from ..landxml.reports import CUT_COLOR, DESIGN_COLOR, FILL_COLOR, GROUND_COLOR, station_label
from .profile_chart import nice_ticks

try:
    from qgis.PyQt.QtSvg import QSvgGenerator
except ImportError:  # pragma: no cover
    QSvgGenerator = None

LAYER_COLORS = ["#5f6670", "#8d949e", "#b9a77e", "#d8cfae", "#a3b8c9", "#c9b3a3", "#9fb8a0"]
DATUM_COLOR = "#2b3f8c"
OTHER_COLORS = ["#7b4fbf", "#2d8f8f", "#c2417a", "#9a7b1c"]
STEEP_COLOR = "#c0392b"
PAGE_SIZES = {
    "A4 landscape": (QPageSize.PageSizeId.A4, QPageLayout.Orientation.Landscape),
    "A3 landscape": (QPageSize.PageSizeId.A3, QPageLayout.Orientation.Landscape),
    "A4 portrait": (QPageSize.PageSizeId.A4, QPageLayout.Orientation.Portrait),
    "A3 portrait": (QPageSize.PageSizeId.A3, QPageLayout.Orientation.Portrait),
    "A1 landscape": (QPageSize.PageSizeId.A1, QPageLayout.Orientation.Landscape),
}
# (columns, rows) on a landscape sheet: wide cells suit wide, flat road sections.
LAYOUTS = {1: (1, 1), 2: (1, 2), 4: (2, 2), 6: (2, 3), 8: (2, 4), 9: (3, 3), 12: (3, 4)}


def material_colors(views):
    names = []
    for view in views:
        for name, _points in view["shapes"]:
            if name not in names:
                names.append(name)
    return {name: LAYER_COLORS[i % len(LAYER_COLORS)] for i, name in enumerate(names)}


class SectionStyle:
    def __init__(self, x_range=None, exaggeration=1.0, unit=None, compact=False, colors=None, z_span=None):
        self.x_range = x_range
        self.exaggeration = exaggeration
        self.unit = unit
        self.compact = compact
        self.colors = colors or {}
        self.z_span = z_span


def _section_z_range(view, x_range):
    values = []
    for line in [view["design"], view["ground"], view["datum"]] + [p for _n, p in view["others"]]:
        if line:
            values.extend(z for o, z in line if x_range is None or x_range[0] - 1 <= o <= x_range[1] + 1)
    for _name, points in view["shapes"]:
        values.extend(z for _o, z in points)
    if not values:
        return (0.0, 1.0)
    return min(values), max(values)


def auto_range(view, step=5.0):
    """Symmetric offset window around this section's own extent."""
    line = view["design"] or view["ground"]
    if not line:
        return (-15.0, 15.0)
    reach = max(abs(line[0][0]), abs(line[-1][0]))
    reach = math.ceil((reach + max(2.0, reach * 0.08)) / step) * step
    return (-reach, reach)


def draw_section(painter, rect, view, style, hover=None):
    """Draw one section into ``rect``; returns the (offset→x, z→y) mappers."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.fillRect(rect, QColor("#ffffff"))
    # Pixel sizes keep text proportional on screen, in PDF sheets and in SVG.
    font = QFont("Arial")
    font.setStyleHint(QFont.StyleHint.SansSerif)
    font.setPixelSize(max(9 if style.compact else 11, int(min(rect.width() / 70, rect.height() / 24, 13))))
    painter.setFont(font)
    metrics = QFontMetrics(font)
    line_height = metrics.height()

    title_height = line_height * (2.9 if style.compact else 1.8)
    left = metrics.horizontalAdvance("00000.0") + 10
    avail = QRectF(
        rect.left() + left,
        rect.top() + title_height,
        rect.width() - left - 10,
        rect.height() - title_height - line_height - 10,
    )
    x_range = style.x_range or auto_range(view)
    span_x = x_range[1] - x_range[0]
    z_low, z_high = _section_z_range(view, x_range)
    needed = max((z_high - z_low) * 1.25, style.z_span or 0.0, 1.0)
    # One scale for both axes (times the vertical exaggeration) that fits.
    sx = min(avail.width() / span_x, avail.height() / (needed * style.exaggeration))
    sz = sx * style.exaggeration
    used_w = span_x * sx
    plot = QRectF(avail.left() + (avail.width() - used_w) / 2, avail.top(), used_w, avail.height())
    z_mid = (z_low + z_high) / 2
    half = plot.height() / sz / 2
    z_low, z_high = z_mid - half, z_mid + half

    def x(o):
        return plot.left() + (o - x_range[0]) * sx

    def y(z):
        return plot.top() + (z_high - z) * sz

    grid = QPen(QColor("#e9edf2"), 0.8)
    text = QColor("#5b6575")
    for tick in nice_ticks(z_low, z_high, max(2, int(plot.height() / (line_height * 2.6)))):
        painter.setPen(grid)
        painter.drawLine(QPointF(plot.left(), y(tick)), QPointF(plot.right(), y(tick)))
        painter.setPen(text)
        label = f"{tick:.1f}"
        painter.drawText(QPointF(plot.left() - 4 - metrics.horizontalAdvance(label), y(tick) + line_height / 3), label)
    for tick in nice_ticks(x_range[0], x_range[1], max(3, int(plot.width() / (metrics.horizontalAdvance("-00.0") * 1.8)))):
        painter.setPen(grid)
        painter.drawLine(QPointF(x(tick), plot.top()), QPointF(x(tick), plot.bottom()))
        painter.setPen(text)
        label = f"{tick:g}"
        painter.drawText(QPointF(x(tick) - metrics.horizontalAdvance(label) / 2, plot.bottom() + line_height), label)

    painter.save()
    painter.setClipRect(plot)
    for kind, outline in view["regions"]:
        color = QColor(FILL_COLOR if kind == "fill" else CUT_COLOR)
        color.setAlphaF(0.38)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(color))
        painter.drawPolygon(QPolygonF([QPointF(x(o), y(z)) for o, z in outline]))
    for name, points in view["shapes"]:
        painter.setPen(QPen(QColor("#3c4350"), 0.6))
        painter.setBrush(QBrush(QColor(style.colors.get(name, LAYER_COLORS[0]))))
        painter.drawPolygon(QPolygonF([QPointF(x(o), y(z)) for o, z in points]))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for index, (name, points) in enumerate(view["others"]):
        pen = QPen(QColor(OTHER_COLORS[index % len(OTHER_COLORS)]), 1.0, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawPolyline(QPolygonF([QPointF(x(o), y(z)) for o, z in points]))
    if view["ground"]:
        painter.setPen(QPen(QColor(GROUND_COLOR), 1.5))
        painter.drawPolyline(QPolygonF([QPointF(x(o), y(z)) for o, z in view["ground"]]))
    if view["datum"]:
        painter.setPen(QPen(QColor(DATUM_COLOR), 1.1, Qt.PenStyle.DashLine))
        painter.drawPolyline(QPolygonF([QPointF(x(o), y(z)) for o, z in view["datum"]]))
    if view["design"]:
        painter.setPen(QPen(QColor(DESIGN_COLOR), 1.9))
        painter.drawPolyline(QPolygonF([QPointF(x(o), y(z)) for o, z in view["design"]]))
    painter.setPen(QPen(QColor("#9aa5b5"), 0.9, Qt.PenStyle.DashDotLine))
    painter.drawLine(QPointF(x(0), plot.top()), QPointF(x(0), plot.bottom()))
    taken = []

    def place(text_value, px, py):
        box = QRectF(px - 1, py - line_height + 2, metrics.horizontalAdvance(text_value) + 2, line_height)
        if any(box.intersects(other) for other in taken):
            return False
        taken.append(box)
        painter.drawText(QPointF(px, py), text_value)
        return True

    for offset in view["daylight"]:
        if offset is None or view["design"] is None:
            continue
        z = next((zz for oo, zz in view["design"] if abs(oo - offset) < 1e-6), None)
        if z is None:
            continue
        painter.setPen(QPen(QColor("#1d2430"), 1))
        painter.setBrush(QBrush(QColor("#ffffff")))
        painter.drawEllipse(QPointF(x(offset), y(z)), 2.4, 2.4)
        painter.setPen(text)
        label = f"{offset:+.2f}"
        place(label, x(offset) - metrics.horizontalAdvance(label) / 2, y(z) - 5)
    for label in view["slope_labels"]:
        painter.setPen(QColor(STEEP_COLOR if label["steep"] else (FILL_COLOR if label["kind"] == "fill" else CUT_COLOR)).darker(130))
        width = metrics.horizontalAdvance(label["text"])
        px = x(label["offset"]) - width / 2
        below = y(label["elevation"]) + line_height
        above = y(label["elevation"]) - 3
        first, second = (above, below) if label["kind"] == "fill" else (below, above)
        if not place(label["text"], px, first):
            place(label["text"], px, second)
    if hover is not None and x_range[0] <= hover <= x_range[1]:
        painter.setPen(QPen(QColor("#1d2430"), 1, Qt.PenStyle.DotLine))
        painter.drawLine(QPointF(x(hover), plot.top()), QPointF(x(hover), plot.bottom()))
    painter.restore()

    # Centreline levels.
    painter.setPen(QColor("#1d2430"))
    cl_lines = []
    if view["design_cl"] is not None:
        cl_lines.append((f"FRL {view['design_cl']:.3f}", DESIGN_COLOR))
    if view["ground_cl"] is not None:
        cl_lines.append((f"EG {view['ground_cl']:.3f}", GROUND_COLOR))
    for index, (label, color) in enumerate(cl_lines):
        painter.setPen(QColor(color))
        painter.drawText(QPointF(plot.left() + 5, plot.top() + line_height * (index + 1)), label)

    painter.setPen(QPen(QColor("#9aa5b5"), 0.8))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(plot)

    bold = QFont(font)
    bold.setBold(True)
    bold.setPixelSize(int(font.pixelSize() * 1.25))
    painter.setFont(bold)
    painter.setPen(QColor("#1d2430"))
    chainage = station_label(view["station"], style.unit)
    header_left, header_right = rect.left() + left, rect.right() - 10
    painter.drawText(QPointF(header_left, rect.top() + line_height * 1.25), f"CH {chainage}")
    painter.setFont(font)
    parts = []
    if view["cut_area"] is not None:
        parts.append((f"Cut {view['cut_area']:.2f} m²", CUT_COLOR))
        parts.append((f"Fill {view['fill_area']:.2f} m²", FILL_COLOR))
        if view["design_cl"] is not None and view["ground_cl"] is not None:
            depth = view["design_cl"] - view["ground_cl"]
            parts.append((f"CL {depth:+.2f}", FILL_COLOR if depth > 0 else CUT_COLOR))
    if style.exaggeration != 1:
        parts.append((f"VE {style.exaggeration:g}×", "#5b6575"))
    title_width = QFontMetrics(bold).horizontalAdvance(f"CH {chainage}")
    parts_width = sum(metrics.horizontalAdvance(label) + 10 for label, _c in parts)
    if title_width + parts_width + 12 <= header_right - header_left:
        cursor, baseline = header_right, rect.top() + line_height * 1.25
        for label, color in reversed(parts):
            cursor -= metrics.horizontalAdvance(label)
            painter.setPen(QColor(color).darker(115))
            painter.drawText(QPointF(cursor, baseline), label)
            cursor -= 10
    else:
        cursor, baseline = header_left, rect.top() + line_height * 2.35
        for label, color in parts:
            painter.setPen(QColor(color).darker(115))
            painter.drawText(QPointF(cursor, baseline), label)
            cursor += metrics.horizontalAdvance(label) + 10
    painter.restore()
    return x, y, plot


class SectionChart(QWidget):
    """Interactive single-section view with previous/next navigation."""

    sectionChanged = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.views = []
        self.index = -1
        self.style = SectionStyle()
        self._hover = None
        self._mappers = None

    def set_views(self, views, unit=None, exaggeration=1.0):
        self.views = views
        self.style = SectionStyle(None, exaggeration, unit, False, material_colors(views))
        self.index = 0 if views else -1
        self.update()

    def current(self):
        return self.views[self.index] if 0 <= self.index < len(self.views) else None

    def show_station(self, station):
        if not self.views:
            return
        stations = [view["station"] for view in self.views]
        self.index = min(range(len(stations)), key=lambda i: abs(stations[i] - station))
        self.update()
        self.sectionChanged.emit(self.views[self.index]["station"])

    def step(self, delta):
        if self.views:
            self.index = min(max(self.index + delta, 0), len(self.views) - 1)
            self.update()
            self.sectionChanged.emit(self.views[self.index]["station"])

    def paintEvent(self, event):
        painter = QPainter(self)
        view = self.current()
        if view is None:
            painter.fillRect(self.rect(), QColor("#ffffff"))
            painter.setPen(QColor("#5b6575"))
            message = "No cross-sections for this alignment"
            metrics = QFontMetrics(painter.font())
            painter.drawText(QPointF(self.width() / 2 - metrics.horizontalAdvance(message) / 2, self.height() / 2), message)
        else:
            x, y, plot = draw_section(painter, QRectF(self.rect()), view, self.style, self._hover)
            self._mappers = (plot, x)
            if self._hover is not None:
                self._draw_readout(painter, view, plot)
        painter.end()

    def _draw_readout(self, painter, view, plot):
        from ..landxml.earthworks import SectionLine

        lines = [f"Offset {self._hover:+.2f}"]
        for label, points in (("Design", view["design"]), ("Ground", view["ground"]), ("Datum", view["datum"])):
            if points:
                value = SectionLine(points).at(self._hover)
                if value is not None:
                    lines.append(f"{label} {value:.3f}")
        metrics = QFontMetrics(painter.font())
        width = max(metrics.horizontalAdvance(t) for t in lines) + 14
        height = len(lines) * (metrics.height() + 1) + 8
        box = QRectF(plot.right() - width - 6, plot.top() + 6, width, height)
        painter.setPen(QPen(QColor("#c9d0da")))
        painter.setBrush(QBrush(QColor(255, 255, 255, 235)))
        painter.drawRoundedRect(box, 4, 4)
        painter.setPen(QColor("#1d2430"))
        for i, line in enumerate(lines):
            painter.drawText(QPointF(box.left() + 7, box.top() + 4 + (i + 1) * (metrics.height() + 1) - 3), line)

    def mouseMoveEvent(self, event):
        point = event.position() if hasattr(event, "position") else QPointF(event.pos())
        if self._mappers is None or self.current() is None:
            return
        plot, _x = self._mappers
        x_range = self.style.x_range
        if plot.left() <= point.x() <= plot.right():
            self._hover = x_range[0] + (point.x() - plot.left()) / plot.width() * (x_range[1] - x_range[0])
        else:
            self._hover = None
        self.update()

    def leaveEvent(self, event):
        self._hover = None
        self.update()

    def wheelEvent(self, event):
        self.step(-1 if event.angleDelta().y() > 0 else 1)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_PageUp):
            self.step(-1)
        elif event.key() in (Qt.Key.Key_Right, Qt.Key.Key_PageDown):
            self.step(1)
        else:
            super().keyPressEvent(event)


def section_image(view, style, width=1400, height=700, scale=2.0):
    image = QImage(int(width * scale), int(height * scale), QImage.Format.Format_ARGB32)
    image.fill(QColor("#ffffff"))
    painter = QPainter(image)
    painter.scale(scale, scale)
    draw_section(painter, QRectF(0, 0, width, height), view, style)
    painter.end()
    return image


def section_svg(view, style, width=760, height=360):
    """Return the section as an SVG string (empty without QtSvg)."""
    if QSvgGenerator is None:
        return ""
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    generator = QSvgGenerator()
    generator.setOutputDevice(buffer)
    generator.setSize(QSize(width, height))
    generator.setViewBox(QRectF(0, 0, width, height))
    painter = QPainter(generator)
    draw_section(painter, QRectF(0, 0, width, height), view, style)
    painter.end()
    buffer.close()
    text = bytes(data).decode("utf-8", "replace")
    start = text.find("<svg")
    return text[start:] if start >= 0 else ""


def _legend(painter, rect, colors, metrics):
    items = [("Design", DESIGN_COLOR, "line"), ("Ground", GROUND_COLOR, "line"), ("Datum", DATUM_COLOR, "dash"),
             ("Fill", FILL_COLOR, "area"), ("Cut", CUT_COLOR, "area")]
    items += [(name, color, "layer") for name, color in colors.items()]
    cursor = rect.left()
    baseline = rect.center().y() + metrics.height() / 3
    for label, color, kind in items:
        if kind in ("line", "dash"):
            painter.setPen(QPen(QColor(color), 2, Qt.PenStyle.DashLine if kind == "dash" else Qt.PenStyle.SolidLine))
            painter.drawLine(QPointF(cursor, baseline - 4), QPointF(cursor + 16, baseline - 4))
        else:
            fill = QColor(color)
            if kind == "area":
                fill.setAlphaF(0.45)
            painter.setPen(QPen(QColor("#3c4350"), 0.5))
            painter.setBrush(QBrush(fill))
            painter.drawRect(QRectF(cursor, baseline - 9, 16, 9))
        painter.setPen(QColor("#1d2430"))
        painter.drawText(QPointF(cursor + 21, baseline), label)
        cursor += 21 + metrics.horizontalAdvance(label) + 16


def write_section_sheets(
    path,
    views,
    page="A3 landscape",
    per_page=6,
    exaggeration=2.0,
    unit=None,
    title="",
    subtitle="",
    x_range=None,
    progress=None,
    cancel=None,
):
    """Write a multi-page PDF of section sheets; returns the page count."""
    if not views:
        raise ValueError("There are no sections to plot.")
    columns, rows = LAYOUTS.get(per_page, (3, 2))
    page_id, orientation = PAGE_SIZES.get(page, PAGE_SIZES["A3 landscape"])
    if orientation == QPageLayout.Orientation.Portrait:
        columns, rows = min(columns, rows), max(columns, rows)
        if per_page > 1:
            columns, rows = 1 if per_page <= 4 else 2, math.ceil(per_page / (1 if per_page <= 4 else 2))
    colors = material_colors(views)
    # Each section fits its own window at the chosen vertical exaggeration,
    # unless a fixed offset window was requested.
    style = SectionStyle(x_range, exaggeration, unit, per_page > 2, colors)

    writer = QPdfWriter(path)
    writer.setPageSize(QPageSize(page_id))
    writer.setPageOrientation(orientation)
    writer.setResolution(200)
    writer.setTitle(title or "Cross-sections")
    writer.setCreator("LandXML Road & Terrain GIS Tools")
    painter = QPainter(writer)
    viewport = painter.viewport()
    width = 1600.0
    height = width * viewport.height() / max(viewport.width(), 1)
    scale = viewport.width() / width
    painter.scale(scale, scale)
    pages = math.ceil(len(views) / (columns * rows))
    margin = 28.0
    footer = 80.0
    cell_w = (width - 2 * margin) / columns
    cell_h = (height - 2 * margin - footer) / rows
    font = QFont("Arial")
    font.setStyleHint(QFont.StyleHint.SansSerif)
    font.setPixelSize(15)
    metrics = QFontMetrics(font)
    stamp = datetime.now().strftime("%Y-%m-%d")
    for page_index in range(pages):
        if cancel is not None and cancel():
            break
        if page_index:
            writer.newPage()
        chunk = views[page_index * columns * rows : (page_index + 1) * columns * rows]
        for i, view in enumerate(chunk):
            column, row = i % columns, i // columns
            cell = QRectF(margin + column * cell_w + 4, margin + row * cell_h + 4, cell_w - 8, cell_h - 8)
            draw_section(painter, cell, view, style)
            painter.setPen(QPen(QColor("#c9d0da"), 0.8))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(cell)
        band = QRectF(margin, height - margin - footer + 6, width - 2 * margin, footer - 6)
        painter.setPen(QPen(QColor("#1d2430"), 1))
        painter.drawRect(band)
        painter.setFont(font)
        _legend(painter, QRectF(band.left() + 10, band.top(), band.width() * 0.62, band.height()), colors, metrics)
        first = station_label(chunk[0]["station"], unit)
        last = station_label(chunk[-1]["station"], unit)
        right = [title or "Cross-sections", f"{subtitle}  CH {first} – {last}".strip(), f"Sheet {page_index + 1} of {pages} · {stamp}"]
        bold = QFont(font)
        bold.setBold(True)
        step = (band.height() - 12) / len(right)
        for index, text in enumerate(right):
            painter.setFont(bold if index == 0 else font)
            painter.setPen(QColor("#1d2430"))
            line_metrics = QFontMetrics(painter.font())
            painter.drawText(
                QPointF(band.right() - 10 - line_metrics.horizontalAdvance(text), band.top() + 2 + step * (index + 1)),
                text,
            )
        painter.setFont(font)
        if progress is not None:
            progress(int(100 * (page_index + 1) / pages))
    painter.end()
    return pages
