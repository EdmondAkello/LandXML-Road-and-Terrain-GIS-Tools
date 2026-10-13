"""Interactive long-section chart drawn with QPainter (Qt 5 and Qt 6).

The same ``render`` routine paints the widget and every export (PNG, SVG,
PDF), so exported figures match the screen. No plotting library is needed.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
import math

from qgis.PyQt.QtCore import QPointF, QRectF, Qt, pyqtSignal
from qgis.PyQt.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPolygonF
from qgis.PyQt.QtWidgets import QSizePolicy, QWidget

from ..landxml.reports import CUT_COLOR, DESIGN_COLOR, FILL_COLOR, station_label

EXTRA_COLORS = ["#7b4fbf", "#c2417a", "#2d8f8f", "#9a7b1c", "#4f6bbf", "#bf6a4f"]


def nice_ticks(low, high, count):
    span = high - low
    if span <= 0 or not math.isfinite(span):
        return [low]
    raw = span / max(count, 1)
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next((m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw), raw)
    first = math.ceil(low / step - 1e-9) * step
    ticks, value = [], first
    while value <= high + step * 1e-9:
        ticks.append(round(value, 10))
        value += step
    return ticks


def _event_point(event):
    return event.position() if hasattr(event, "position") else QPointF(event.pos())


class ProfileChart(QWidget):
    """Design and surface profiles with shaded cut/fill and a depth panel."""

    stationHovered = pyqtSignal(object)  # float station or None
    stationClicked = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumHeight(260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.title = ""
        self.horizontal_unit = None
        self.lines = []  # dicts: name, parts, color, width, role
        self.runs = []
        self.transitions = []
        self.controls = []
        self.marker_station = None
        self.show_depth = True
        self.show_shading = True
        self.show_controls = True
        self._hover = None
        self._drag = None
        self._full = None
        self._view = None

    # ----------------------------------------------------------- data
    def set_data(self, lines, runs=(), transitions=(), controls=(), title="", horizontal_unit=None):
        self.lines = list(lines)
        self.runs = list(runs)
        self.transitions = list(transitions)
        self.controls = list(controls)
        self.title = title
        self.horizontal_unit = horizontal_unit
        stations = [p[0] for line in self.lines for part in line["parts"] for p in part]
        self._full = (min(stations), max(stations)) if stations else None
        self._view = self._full
        self._hover = None
        self.update()

    def clear(self):
        self.set_data([])

    def fit(self):
        self._view = self._full
        self.update()

    def zoom_to(self, start, end, margin=0.1):
        if self._full is None or end <= start:
            return
        pad = (end - start) * margin
        self._view = (max(self._full[0], start - pad), min(self._full[1], end + pad))
        self.update()

    def set_marker(self, station):
        self.marker_station = station
        if station is not None and self._view is not None and not self._view[0] <= station <= self._view[1]:
            span = self._view[1] - self._view[0]
            self._view = (station - span / 2, station + span / 2)
        self.update()

    @property
    def view(self):
        return self._view

    # ------------------------------------------------------- geometry
    def _layout(self, rect):
        left, right, top, bottom = 72.0, 18.0, 30.0, 40.0
        plot = QRectF(rect.left() + left, rect.top() + top, rect.width() - left - right, rect.height() - top - bottom)
        depth = None
        if self.show_depth and self.runs and plot.height() > 220:
            depth_height = max(70.0, plot.height() * 0.26)
            depth = QRectF(plot.left(), plot.bottom() - depth_height, plot.width(), depth_height)
            plot = QRectF(plot.left(), plot.top(), plot.width(), plot.height() - depth_height - 34.0)
        return plot, depth

    def _visible(self, part, low, high):
        stations = [p[0] for p in part]
        i0 = max(0, bisect_left(stations, low) - 1)
        i1 = min(len(part), bisect_right(stations, high) + 1)
        return part[i0:i1]

    def _y_range(self, low, high):
        values = []
        for line in self.lines:
            for part in line["parts"]:
                values.extend(p[1] for p in self._visible(part, low, high))
        if not values:
            return 0.0, 1.0
        z0, z1 = min(values), max(values)
        pad = max((z1 - z0) * 0.08, 0.5)
        return z0 - pad, z1 + pad

    def _depth_range(self, low, high):
        depths = [r["depth"] for run in self.runs for r in self._visible_rows(run, low, high)]
        if not depths:
            return -1.0, 1.0
        return min(-0.5, min(depths) * 1.1), max(0.5, max(depths) * 1.1)

    def _visible_rows(self, run, low, high):
        stations = [r["station"] for r in run]
        i0 = max(0, bisect_left(stations, low) - 1)
        i1 = min(len(run), bisect_right(stations, high) + 1)
        return run[i0:i1]

    @staticmethod
    def _thin(points, limit, keep=None):
        if len(points) <= limit:
            return points
        step = max(1, len(points) // limit)
        return [p for i, p in enumerate(points) if i % step == 0 or (keep and keep(p))] + [points[-1]]

    # --------------------------------------------------------- render
    def render(self, painter, rect, interactive=False):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(rect, QColor("#ffffff"))
        base_font = QFont(painter.font())
        base_font.setPointSizeF(8.5)
        painter.setFont(base_font)
        metrics = QFontMetrics(base_font)
        if self._view is None:
            painter.setPen(QColor("#5b6575"))
            message = "Open a LandXML file or pick an alignment on the map"
            painter.drawText(
                QPointF(rect.center().x() - metrics.horizontalAdvance(message) / 2, rect.center().y()),
                message,
            )
            painter.restore()
            return
        low, high = self._view
        plot, depth_rect = self._layout(rect)
        z0, z1 = self._y_range(low, high)

        def x(s):
            return plot.left() + (s - low) / ((high - low) or 1.0) * plot.width()

        def y(z):
            return plot.top() + (z1 - z) / ((z1 - z0) or 1.0) * plot.height()

        grid = QPen(QColor("#e9edf2"))
        text_pen = QPen(QColor("#5b6575"))
        for tick in nice_ticks(z0, z1, max(3, int(plot.height() / 45))):
            painter.setPen(grid)
            painter.drawLine(QPointF(plot.left(), y(tick)), QPointF(plot.right(), y(tick)))
            painter.setPen(text_pen)
            label = f"{tick:.1f}"
            painter.drawText(QPointF(plot.left() - 6 - metrics.horizontalAdvance(label), y(tick) + 4), label)
        station_ticks = nice_ticks(low, high, max(3, int(plot.width() / 110)))
        bottom_rect = depth_rect or plot
        for tick in station_ticks:
            painter.setPen(grid)
            painter.drawLine(QPointF(x(tick), plot.top()), QPointF(x(tick), plot.bottom()))
            if depth_rect is not None:
                painter.drawLine(QPointF(x(tick), depth_rect.top()), QPointF(x(tick), depth_rect.bottom()))
            painter.setPen(text_pen)
            label = station_label(tick, self.horizontal_unit)
            label_width = metrics.horizontalAdvance(label)
            label_x = min(max(x(tick) - label_width / 2, rect.left() + 2), rect.right() - label_width - 2)
            painter.drawText(QPointF(label_x, bottom_rect.bottom() + 16), label)

        painter.save()
        painter.setClipRect(plot)
        width_limit = int(plot.width() * 2) + 10
        if self.show_shading:
            for run in self.runs:
                rows = self._thin(self._visible_rows(run, low, high), width_limit, lambda r: r["depth"] == 0.0)
                self._shade(painter, rows, x, y)
        for line in self.lines:
            pen = QPen(QColor(line["color"]), line.get("width", 1.6))
            if line.get("dashed"):
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for part in line["parts"]:
                visible = self._thin(self._visible(part, low, high), width_limit)
                if len(visible) >= 2:
                    painter.drawPolyline(QPolygonF([QPointF(x(s), y(z)) for s, z in visible]))
        painter.setPen(QPen(QColor("#1d2430"), 1))
        painter.setBrush(QBrush(QColor("#1d2430")))
        for item in self.transitions:
            if low <= item["station"] <= high:
                painter.drawEllipse(QPointF(x(item["station"]), y(item["elevation"])), 2.6, 2.6)
        visible_controls = [c for c in self.controls if low <= c["station"] <= high]
        if self.show_controls and visible_controls:
            painter.setPen(QPen(QColor(DESIGN_COLOR), 1))
            label_them = len(visible_controls) <= max(4, int(plot.width() / 90))
            for control in visible_controls:
                px, py = x(control["station"]), y(control["elevation"])
                painter.setBrush(QBrush(QColor("#ffffff")))
                painter.drawPolygon(QPolygonF([QPointF(px, py - 5), QPointF(px - 4, py + 3), QPointF(px + 4, py + 3)]))
                if label_them:
                    text = control["control_type"]
                    if control.get("k_value"):
                        text += f" K{control['k_value']:.0f}"
                    painter.drawText(QPointF(px + 5, py - 6), text)
        if self.marker_station is not None and low <= self.marker_station <= high:
            painter.setPen(QPen(QColor("#d23c3c"), 1.4, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(x(self.marker_station), plot.top()), QPointF(x(self.marker_station), plot.bottom()))
        painter.restore()

        painter.setPen(QPen(QColor("#9aa5b5"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(plot)
        ve = (plot.height() / ((z1 - z0) or 1)) / (plot.width() / ((high - low) or 1))
        legend = [(line["name"], line["color"]) for line in self.lines]
        cursor_x = plot.right()
        ve_text = f"VE ≈ {ve:.1f}×"
        painter.setPen(text_pen)
        cursor_x -= metrics.horizontalAdvance(ve_text)
        painter.drawText(QPointF(cursor_x, rect.top() + 18), ve_text)
        for name, color in reversed(legend[:6]):
            label = name if len(name) <= 28 else name[:26] + "…"
            cursor_x -= metrics.horizontalAdvance(label) + 30
            painter.setPen(QPen(QColor(color), 2.4))
            painter.drawLine(QPointF(cursor_x, rect.top() + 14), QPointF(cursor_x + 14, rect.top() + 14))
            painter.setPen(text_pen)
            painter.drawText(QPointF(cursor_x + 18, rect.top() + 18), label)
        title_font = QFont(base_font)
        title_font.setPointSizeF(10)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QColor("#1d2430"))
        title = QFontMetrics(title_font).elidedText(
            self.title, Qt.TextElideMode.ElideRight, int(max(cursor_x - plot.left() - 16, 40))
        )
        painter.drawText(QPointF(plot.left(), rect.top() + 18), title)
        painter.setFont(base_font)

        if depth_rect is not None:
            self._render_depth(painter, depth_rect, low, high, x, metrics, text_pen, grid)

        if interactive and self._hover is not None and low <= self._hover <= high:
            self._render_hover(painter, plot, depth_rect, x, y, metrics)
        painter.restore()

    def _shade(self, painter, rows, x, y):
        if len(rows) < 2:
            return
        segment = [rows[0]]
        for row in rows[1:]:
            segment.append(row)
            if row["depth"] == 0.0 or row is rows[-1]:
                if len(segment) >= 2:
                    total = sum(r["depth"] for r in segment)
                    color = QColor(FILL_COLOR if total > 0 else CUT_COLOR)
                    color.setAlphaF(0.42)
                    polygon = QPolygonF(
                        [QPointF(x(r["station"]), y(r["design"])) for r in segment]
                        + [QPointF(x(r["station"]), y(r["ground"])) for r in reversed(segment)]
                    )
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QBrush(color))
                    painter.drawPolygon(polygon)
                segment = [row]

    def _render_depth(self, painter, rect, low, high, x, metrics, text_pen, grid):
        d0, d1 = self._depth_range(low, high)

        def y(d):
            return rect.top() + (d1 - d) / ((d1 - d0) or 1.0) * rect.height()

        for tick in nice_ticks(d0, d1, max(2, int(rect.height() / 30))):
            painter.setPen(grid)
            painter.drawLine(QPointF(rect.left(), y(tick)), QPointF(rect.right(), y(tick)))
            painter.setPen(text_pen)
            label = f"{tick:+.1f}"
            painter.drawText(QPointF(rect.left() - 6 - metrics.horizontalAdvance(label), y(tick) + 4), label)
        painter.save()
        painter.setClipRect(rect)
        limit = int(rect.width() * 2) + 10
        for run in self.runs:
            rows = self._thin(self._visible_rows(run, low, high), limit, lambda r: r["depth"] == 0.0)
            if len(rows) < 2:
                continue
            for clip, color in ((max, FILL_COLOR), (min, CUT_COLOR)):
                path = QPainterPath(QPointF(x(rows[0]["station"]), y(0)))
                for r in rows:
                    path.lineTo(QPointF(x(r["station"]), y(clip(r["depth"], 0.0))))
                path.lineTo(QPointF(x(rows[-1]["station"]), y(0)))
                path.closeSubpath()
                fill = QColor(color)
                fill.setAlphaF(0.85)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(fill))
                painter.drawPath(path)
        painter.setPen(QPen(QColor("#5b6575"), 1))
        painter.drawLine(QPointF(rect.left(), y(0)), QPointF(rect.right(), y(0)))
        if self.marker_station is not None and low <= self.marker_station <= high:
            painter.setPen(QPen(QColor("#d23c3c"), 1.4, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(x(self.marker_station), rect.top()), QPointF(x(self.marker_station), rect.bottom()))
        painter.restore()
        painter.setPen(QPen(QColor("#9aa5b5"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)
        painter.setPen(text_pen)
        painter.drawText(QPointF(rect.left() + 6, rect.top() + 14), "Depth: fill + / cut −")

    def values_at(self, station):
        result = []
        for line in self.lines:
            value = None
            for part in line["parts"]:
                if part[0][0] - 1e-9 <= station <= part[-1][0] + 1e-9:
                    stations = [p[0] for p in part]
                    i = min(max(bisect_right(stations, station), 1), len(part) - 1)
                    (s0, z0), (s1, z1) = part[i - 1], part[i]
                    value = z1 if s1 == s0 else z0 + (z1 - z0) * (station - s0) / (s1 - s0)
                    break
            result.append((line["name"], line["color"], value))
        depth = None
        for run in self.runs:
            if run[0]["station"] <= station <= run[-1]["station"]:
                stations = [r["station"] for r in run]
                i = min(max(bisect_right(stations, station), 1), len(run) - 1)
                a, b = run[i - 1], run[i]
                t = 0.0 if b["station"] == a["station"] else (station - a["station"]) / (b["station"] - a["station"])
                depth = a["depth"] + t * (b["depth"] - a["depth"])
                break
        return result, depth

    def _render_hover(self, painter, plot, depth_rect, x, y, metrics):
        station = self._hover
        painter.setPen(QPen(QColor("#1d2430"), 1, Qt.PenStyle.DotLine))
        bottom = depth_rect.bottom() if depth_rect is not None else plot.bottom()
        painter.drawLine(QPointF(x(station), plot.top()), QPointF(x(station), bottom))
        values, depth = self.values_at(station)
        lines = [(station_label(station, self.horizontal_unit), "#1d2430")]
        for name, color, value in values:
            if value is not None:
                painter.setPen(QPen(QColor(color), 1))
                painter.setBrush(QBrush(QColor("#ffffff")))
                painter.drawEllipse(QPointF(x(station), y(value)), 3.5, 3.5)
                short = name if len(name) <= 24 else name[:22] + "…"
                lines.append((f"{short}: {value:.3f}", color))
        if depth is not None:
            kind = "fill" if depth > 0 else "cut" if depth < 0 else "on grade"
            lines.append((f"Depth {depth:+.3f} ({kind})", FILL_COLOR if depth > 0 else CUT_COLOR))
        width = max(metrics.horizontalAdvance(text) for text, _ in lines) + 16
        height = len(lines) * (metrics.height() + 2) + 10
        left = x(station) + 12
        if left + width > plot.right():
            left = x(station) - width - 12
        box = QRectF(left, plot.top() + 8, width, height)
        painter.setPen(QPen(QColor("#c9d0da"), 1))
        painter.setBrush(QBrush(QColor(255, 255, 255, 235)))
        painter.drawRoundedRect(box, 4, 4)
        for index, (text, color) in enumerate(lines):
            painter.setPen(QColor(color))
            painter.drawText(QPointF(box.left() + 8, box.top() + 6 + (index + 1) * (metrics.height() + 2) - 4), text)

    # ---------------------------------------------------- interaction
    def paintEvent(self, event):
        painter = QPainter(self)
        self.render(painter, QRectF(self.rect()), interactive=True)
        painter.end()

    def _station_at(self, px):
        if self._view is None:
            return None
        plot, _depth = self._layout(QRectF(self.rect()))
        if plot.width() <= 0:
            return None
        low, high = self._view
        return low + (px - plot.left()) / plot.width() * (high - low)

    def mouseMoveEvent(self, event):
        point = _event_point(event)
        if self._drag is not None and self._view is not None:
            plot, _ = self._layout(QRectF(self.rect()))
            start_x, (low, high) = self._drag
            shift = (start_x - point.x()) / max(plot.width(), 1) * (high - low)
            self._pan_to(low + shift, high + shift)
        station = self._station_at(point.x())
        plot, depth = self._layout(QRectF(self.rect()))
        bottom = depth.bottom() if depth is not None else plot.bottom()
        inside = station is not None and plot.left() <= point.x() <= plot.right() and plot.top() <= point.y() <= bottom
        self._hover = station if inside else None
        self.stationHovered.emit(self._hover)
        self.update()

    def _pan_to(self, low, high):
        if self._full is None:
            return
        span = high - low
        if low < self._full[0]:
            low, high = self._full[0], self._full[0] + span
        if high > self._full[1]:
            low, high = self._full[1] - span, self._full[1]
        self._view = (low, high)

    def leaveEvent(self, event):
        self._hover = None
        self.stationHovered.emit(None)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._view is not None:
            self._drag = (_event_point(event).x(), self._view)
            self._press_x = _event_point(event).x()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            moved = abs(_event_point(event).x() - getattr(self, "_press_x", 0.0)) > 3
            self._drag = None
            if not moved and self._hover is not None:
                self.stationClicked.emit(float(self._hover))

    def mouseDoubleClickEvent(self, event):
        self.fit()

    def wheelEvent(self, event):
        if self._view is None or self._full is None:
            return
        steps = event.angleDelta().y() / 120.0
        if not steps:
            return
        factor = 0.8**steps
        anchor = self._station_at(_event_point(event).x())
        low, high = self._view
        if anchor is None:
            anchor = (low + high) / 2
        full_span = self._full[1] - self._full[0]
        span = min(max((high - low) * factor, min(full_span, 5.0)), full_span)
        t = (anchor - low) / ((high - low) or 1)
        self._pan_to(anchor - t * span, anchor - t * span + span)
        self.update()
