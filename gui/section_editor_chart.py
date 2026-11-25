"""The live cross-section of the Corridor Section Editor: drawn with QPainter, edited by dragging handles."""

from __future__ import annotations

import math

from qgis.PyQt.QtCore import QPointF, QRectF, Qt, pyqtSignal
from qgis.PyQt.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from qgis.PyQt.QtWidgets import QWidget

GROUND = QColor("#8a5a2b")
TOP = QColor("#202020")
CUT = QColor(214, 120, 60, 70)
FILL = QColor(70, 140, 70, 70)
SELECTED = QColor("#d81b60")
HANDLE = QColor("#1565c0")
COURSE_COLORS = ["#5d5d5d", "#8d6e63", "#bcaaa4", "#d7ccc8", "#efebe9", "#a1887f"]
SHAPE_COLORS = {"wall": "#78909c", "kerb": "#9e9e9e", "barrier": "#757575", "side_drain": "#90a4ae", "median": "#a5d6a7"}


LABELLED = {"CL", "ETW", "EPS", "ESH", "KERB_T", "FL", "SW_B", "DITCH_I", "DITCH_IF", "DAYLIGHT", "MED_C", "BENCH_E",
            "TOP_CUT", "TOE"}


def _event_point(event):
    return event.position() if hasattr(event, "position") else QPointF(event.pos())


class SectionEditorChart(QWidget):
    """Draws one section dict (from ``corridor_build.section_at``). Signals carry element ids and new values;
    the dock applies them to the project, so every edit goes through its undo stack."""

    elementClicked = pyqtSignal(str, str)            # side, element id
    pointClicked = pyqtSignal(object)                # the Point (for "why this point")
    widthDragged = pyqtSignal(str, str, float)       # side, element id, new width
    fallDragged = pyqtSignal(str, str, float)        # side, element id, new crossfall %
    dragFinished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.section = None
        self.exaggeration = 2.0
        self.selected = None                 # (side, element id)
        self.element_spans = {}              # (side, id) -> (outer point, inner point, label)
        self.view = None                     # (x0, x1, z0, z1)
        self._drag = None
        self._hover = None
        self.message = "Open a LandXML file and choose an alignment and profile."
        self.setMouseTracking(True)
        self.setMinimumHeight(260)

    # ------------------------------------------------------------------ data
    def set_section(self, section, spans=None, keep_view=True):
        self.section = section
        self.element_spans = spans or {}
        if section is None:
            self.view = None
        elif not keep_view or self.view is None:
            self.fit()
        self.update()

    def fit(self):
        if not self.section:
            return
        top = self.section["top"]
        xs = [p.d for p in top]
        zs = [p.z for p in top]
        g = self.section.get("ground")
        if g is not None:
            offs, gz = g
            lo, hi = min(xs) - 3, max(xs) + 3
            for o, z in zip(offs, gz):
                if lo <= o <= hi and math.isfinite(z):
                    zs.append(float(z))
        x0, x1 = min(xs) - 2, max(xs) + 2
        z0, z1 = min(zs) - 0.5, max(zs) + 0.5
        need = (x1 - x0) / 3.0 / self.exaggeration        # keep a useful height at the default aspect
        if z1 - z0 < need:
            mid = (z0 + z1) / 2
            z0, z1 = mid - need / 2, mid + need / 2
        self.view = (x0, x1, z0, z1)
        self.update()

    # ------------------------------------------------------------------ geometry
    def _frame(self):
        rect = QRectF(self.rect()).adjusted(46, 12, -12, -28)
        x0, x1, z0, z1 = self.view or (-10.0, 10.0, -5.0, 5.0)     # nothing loaded yet: any frame does
        sx = rect.width() / max(x1 - x0, 1e-6)
        sz = rect.height() / max(z1 - z0, 1e-6)
        # keep the vertical exaggeration: the smaller scale wins, centred
        s = min(sx, sz / self.exaggeration)
        cx, cz = (x0 + x1) / 2, (z0 + z1) / 2
        return rect, s, s * self.exaggeration, cx, cz

    def to_px(self, d, z):
        rect, sx, sz, cx, cz = self._frame()
        return QPointF(rect.center().x() + (d - cx) * sx, rect.center().y() - (z - cz) * sz)

    def to_world(self, point):
        rect, sx, sz, cx, cz = self._frame()
        return cx + (point.x() - rect.center().x()) / sx, cz - (point.y() - rect.center().y()) / sz

    # ------------------------------------------------------------------ paint
    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        if not self.section or self.view is None:
            painter.setPen(QColor("#666"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.message)
            return
        self.render_section(painter)
        painter.end()

    def render(self, painter, rect=None):                       # noqa: A003 - mirrors ProfileChart.render
        if rect is not None:
            painter.save()
            painter.translate(rect.topLeft())
        self.render_section(painter)
        if rect is not None:
            painter.restore()

    def render_section(self, painter):
        sec = self.section
        grade = sec["grade"]
        self._grid(painter)
        g = sec.get("ground")
        top = sec["top"]
        # cut/fill shading between the datum (earthworks line) and the ground
        if g is not None:
            offs, gz = g
            datum = sec["datum"]
            lo, hi = top[0].d, top[-1].d
            pts = [(o, z) for o, z in zip(offs, gz) if lo - 0.01 <= o <= hi + 0.01 and math.isfinite(z)]
            if len(pts) > 1:
                import numpy as np
                xs = sorted({p[0] for p in pts} | {d for d, _ in datum if lo <= d <= hi})
                dz = [float(np.interp(x, [p[0] for p in datum], [p[1] for p in datum])) for x in xs]
                gzs = [float(np.interp(x, [p[0] for p in pts], [p[1] for p in pts])) for x in xs]
                for (a, b, ga, gb, xa, xb) in zip(dz, dz[1:], gzs, gzs[1:], xs, xs[1:]):
                    color = FILL if (a + b) / 2 > (ga + gb) / 2 else CUT
                    poly = QPolygonF([self.to_px(xa, a), self.to_px(xb, b), self.to_px(xb, gb), self.to_px(xa, ga)])
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(color)
                    painter.drawPolygon(poly)
        # courses and shapes
        course_index = {}
        for shape in sec["shapes"]:
            if shape.get("buildup"):
                key = shape["name"]
                course_index.setdefault(key, len(course_index))
                color = QColor(COURSE_COLORS[course_index[key] % len(COURSE_COLORS)])
            else:
                color = QColor(SHAPE_COLORS.get(shape.get("type"), "#b0bec5"))
            color.setAlpha(200)
            poly = QPolygonF([self.to_px(d, z + grade) for d, z in shape["ring"]])
            painter.setPen(QPen(QColor("#555"), 0.6))
            painter.setBrush(QBrush(color))
            painter.drawPolygon(poly)
        # ground
        if g is not None:
            offs, gz = g
            x0, x1 = self.view[0], self.view[1]
            path = QPainterPath()
            started = False
            for o, z in zip(offs, gz):
                if not (x0 - 5 <= o <= x1 + 5) or not math.isfinite(z):
                    started = False
                    continue
                p = self.to_px(o, z)
                if started:
                    path.lineTo(p)
                else:
                    path.moveTo(p)
                    started = True
            painter.setPen(QPen(GROUND, 1.6))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        # datum
        painter.setPen(QPen(QColor("#1e88e5"), 1.0, Qt.PenStyle.DashLine))
        painter.drawPolyline(QPolygonF([self.to_px(d, z) for d, z in sec["datum"]]))
        # selected element span
        if self.selected and self.selected in self.element_spans:
            a, b, _label = self.element_spans[self.selected]
            painter.setPen(QPen(SELECTED, 4))
            painter.drawLine(self.to_px(a.d, a.z), self.to_px(b.d, b.z))
        # top surface and points
        painter.setPen(QPen(TOP, 1.8))
        painter.drawPolyline(QPolygonF([self.to_px(p.d, p.z) for p in top]))
        small = QFont(self.font())
        small.setPointSizeF(max(small.pointSizeF() - 2, 6))
        painter.setFont(small)
        for p in top:
            q = self.to_px(p.d, p.z)
            painter.setPen(QPen(TOP, 1))
            painter.setBrush(QColor("#ffffff"))
            painter.drawEllipse(q, 2.5, 2.5)
            if p.code in LABELLED:
                painter.setPen(QColor("#37474f"))
                painter.save()
                painter.translate(q + QPointF(2, -6))
                painter.rotate(-60)
                painter.drawText(QPointF(0, 0), p.code)
                painter.restore()
        for side in ("left", "right"):
            for b in sec["sides"][side]["barriers"]:
                q = self.to_px(b.d, b.z)
                painter.setPen(QPen(QColor("#424242"), 3))
                painter.drawLine(q, q + QPointF(0, -14))
        # handles of the selected element: width (outer point) and fall (mid-span)
        if self.selected and self.selected in self.element_spans:
            a, b, _label = self.element_spans[self.selected]
            painter.setPen(QPen(HANDLE, 1.5))
            painter.setBrush(QColor("#bbdefb"))
            painter.drawRect(QRectF(self.to_px(b.d, b.z) - QPointF(5, 5), self.to_px(b.d, b.z) + QPointF(5, 5)))
            mid = self.to_px((a.d + b.d) / 2, (a.z + b.z) / 2)
            painter.drawEllipse(mid, 5, 5)
        # dimension strip: element widths along the bottom
        painter.setFont(small)
        rect = self._frame()[0]
        y = rect.bottom() + 16
        for (side, _eid), (a, b, label) in self.element_spans.items():
            pa, pb = self.to_px(a.d, 0), self.to_px(b.d, 0)
            if abs(pb.x() - pa.x()) < 14:
                continue
            painter.setPen(QPen(QColor("#90a4ae"), 1))
            painter.drawLine(QPointF(pa.x(), y), QPointF(pb.x(), y))
            painter.drawLine(QPointF(pa.x(), y - 3), QPointF(pa.x(), y + 3))
            painter.drawLine(QPointF(pb.x(), y - 3), QPointF(pb.x(), y + 3))
            painter.setPen(QColor("#455a64"))
            text = f"{abs(b.d - a.d):.2f}"
            painter.drawText(QRectF(min(pa.x(), pb.x()), y - 14, abs(pb.x() - pa.x()), 12),
                             Qt.AlignmentFlag.AlignCenter, text)
        # header
        painter.setFont(self.font())
        painter.setPen(QColor("#263238"))
        st = sec["station"]
        region = sec.get("region") or {}
        extra = f"  transition {sec['transition'] * 100:.0f} %" if sec.get("transition") is not None else ""
        painter.drawText(QPointF(52, 26), f"Station {st:.2f}   grade {grade:.3f}   {region.get('assembly', '')}{extra}"
                                          f"   V:H {self.exaggeration:g}:1")
        if self._hover is not None:
            painter.setPen(QColor("#37474f"))
            painter.drawText(QPointF(52, 44), self._hover)

    def _grid(self, painter):
        rect, sx, sz, cx, cz = self._frame()
        x0, z1 = self.to_world(rect.topLeft())
        x1, z0 = self.to_world(rect.bottomRight())
        painter.setPen(QPen(QColor("#eceff1"), 1))
        step = _nice((x1 - x0) / 10)
        x = math.floor(x0 / step) * step
        small = QFont(self.font())
        small.setPointSizeF(max(small.pointSizeF() - 2, 6))
        painter.setFont(small)
        while x <= x1:
            p = self.to_px(x, z0)
            painter.setPen(QPen(QColor("#eceff1"), 1))
            painter.drawLine(QPointF(p.x(), rect.top()), QPointF(p.x(), rect.bottom()))
            painter.setPen(QColor("#90a4ae"))
            painter.drawText(QPointF(p.x() - 10, rect.bottom() + 26), f"{x:g}")
            x += step
        zstep = _nice((z1 - z0) / 6)
        z = math.floor(z0 / zstep) * zstep
        while z <= z1:
            p = self.to_px(x0, z)
            painter.setPen(QPen(QColor("#eceff1"), 1))
            painter.drawLine(QPointF(rect.left(), p.y()), QPointF(rect.right(), p.y()))
            painter.setPen(QColor("#90a4ae"))
            painter.drawText(QRectF(0, p.y() - 7, 44, 14), Qt.AlignmentFlag.AlignRight, f"{z:.1f}")
            z += zstep
        painter.setPen(QPen(QColor("#b0bec5"), 1))
        painter.drawRect(rect)

    # ------------------------------------------------------------------ interaction
    def _nearest_point(self, pos, radius=8):
        if not self.section:
            return None
        best = None
        for p in self.section["top"]:
            q = self.to_px(p.d, p.z)
            dist = math.hypot(q.x() - pos.x(), q.y() - pos.y())
            if dist <= radius and (best is None or dist < best[0]):
                best = (dist, p)
        return best[1] if best else None

    def _span_at(self, pos):
        d, _z = self.to_world(pos)
        best = None
        for key, (a, b, _label) in self.element_spans.items():
            lo, hi = min(a.d, b.d), max(a.d, b.d)
            if lo - 1e-6 <= d <= hi + 1e-6:
                width = hi - lo
                if best is None or width < best[0]:
                    best = (width, key)
        return best[1] if best else None

    def mousePressEvent(self, event):
        if not self.section or event.button() != Qt.MouseButton.LeftButton:
            return
        pos = _event_point(event)
        if self.selected in self.element_spans:
            a, b, _label = self.element_spans[self.selected]
            outer = self.to_px(b.d, b.z)
            mid = self.to_px((a.d + b.d) / 2, (a.z + b.z) / 2)
            if math.hypot(outer.x() - pos.x(), outer.y() - pos.y()) <= 7:
                self._drag = ("width", self.selected, a, b)
                return
            if math.hypot(mid.x() - pos.x(), mid.y() - pos.y()) <= 7:
                self._drag = ("fall", self.selected, a, b)
                return
        point = self._nearest_point(pos)
        if point is not None:
            self.pointClicked.emit(point)
        span = self._span_at(pos)
        if span is not None:
            self.selected = span
            self.elementClicked.emit(*span)
            self.update()

    def mouseMoveEvent(self, event):
        pos = _event_point(event)
        if self._drag is not None:
            kind, key, a, b = self._drag
            d, z = self.to_world(pos)
            if kind == "width":
                width = round(abs(d - a.d), 3)
                if not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                    width = round(width / 0.05) * 0.05                 # snap to 50 mm unless Shift is held
                self.widthDragged.emit(key[0], key[1], max(width, 0.0))
            else:
                run = max(abs(b.d - a.d) / 2, 1e-6)
                fall = (a.z - z) / run * 100.0
                fall = round(fall, 2 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1)
                self.fallDragged.emit(key[0], key[1], fall)
            return
        if not self.section or self.view is None:
            return                                          # nothing loaded: no hover read-out
        point = self._nearest_point(pos)
        d, z = self.to_world(pos)
        self._hover = (f"{point.code or 'point'}  offset {point.d:.3f}  level {point.z:.3f}" if point is not None
                       else f"offset {d:.2f}  level {z:.2f}")
        self.update()

    def mouseReleaseEvent(self, _event):
        if self._drag is not None:
            self._drag = None
            self.dragFinished.emit()

    def wheelEvent(self, event):
        if self.view is None:
            return
        delta = event.angleDelta().y()
        factor = 0.85 if delta > 0 else 1 / 0.85
        d, z = self.to_world(_event_point(event))
        x0, x1, z0, z1 = self.view
        self.view = (d + (x0 - d) * factor, d + (x1 - d) * factor, z + (z0 - z) * factor, z + (z1 - z) * factor)
        self.update()

    def mouseDoubleClickEvent(self, _event):
        self.fit()


def _nice(value):
    if value <= 0:
        return 1.0
    exp = math.floor(math.log10(value))
    base = value / 10 ** exp
    nice = 1 if base < 1.5 else 2 if base < 3.5 else 5 if base < 7.5 else 10
    return nice * 10 ** exp


class TimelineStrip(QWidget):
    """The chainage timeline: regions, transitions, superelevation and widening bands, and the current station."""

    stationPicked = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.start, self.end = 0.0, 1.0
        self.regions, self.records, self.widen, self.features = [], [], [], []
        self.station = None
        self.setMinimumHeight(82)
        self.setMouseTracking(True)

    def set_data(self, start, end, regions, records, widen, features=()):
        self.start, self.end = start, end
        self.regions, self.records, self.widen, self.features = regions, records, widen, list(features)
        self.update()

    def set_station(self, station):
        self.station = station
        self.update()

    def _x(self, s, rect):
        return rect.left() + (s - self.start) / max(self.end - self.start, 1e-9) * rect.width()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(46, 4, -12, -4)
        painter.fillRect(self.rect(), QColor("#fafafa"))
        rows = [("Regions", rect.top(), 18), ("Super", rect.top() + 22, 16), ("Widen", rect.top() + 42, 12),
                ("Features", rect.top() + 58, 14)]
        small = QFont(self.font())
        small.setPointSizeF(max(small.pointSizeF() - 2, 6))
        painter.setFont(small)
        for label, y, h in rows:
            painter.setPen(QColor("#78909c"))
            painter.drawText(QRectF(0, y, 44, h), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)
        palette = ["#c5cae9", "#c8e6c9", "#ffe0b2", "#f8bbd0", "#b2ebf2", "#d7ccc8"]
        names = []
        for r in self.regions:
            if r["assembly"] not in names:
                names.append(r["assembly"])
            x0, x1 = self._x(r["start"], rect), self._x(r["end"], rect)
            color = QColor(palette[names.index(r["assembly"]) % len(palette)])
            painter.fillRect(QRectF(x0, rows[0][1], x1 - x0, rows[0][2]), color)
            t = float(r.get("transition") or 0)
            if t > 0:
                xt = self._x(r["start"] + t, rect)
                poly = QPolygonF([QPointF(x0, rows[0][1] + rows[0][2]), QPointF(xt, rows[0][1]), QPointF(xt, rows[0][1] + rows[0][2])])
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(0, 0, 0, 40))
                painter.drawPolygon(poly)
            painter.setPen(QColor("#37474f"))
            painter.drawText(QRectF(x0 + 2, rows[0][1], max(x1 - x0 - 4, 0), rows[0][2]),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, r["assembly"])
        y, h = rows[1][1], rows[1][2]
        for r in self.records:
            pts = [(r["begin_runout"], 0), (r["begin_runoff"], 0.2), (r["full_start"], 1), (r["full_end"], 1),
                   (r["end_runoff"], 0.2), (r["end_runout"], 0)]
            poly = QPolygonF([QPointF(self._x(s, rect), y + h - f * h) for s, f in pts])
            painter.setPen(QPen(QColor("#1565c0"), 1))
            painter.setBrush(QColor(21, 101, 192, 60) if r["rate"] > 0 else QColor(198, 40, 40, 60))
            painter.drawPolygon(poly)
        y, h = rows[2][1], rows[2][2]
        for w in self.widen:
            a, b, c, d = w["stations"]
            poly = QPolygonF([QPointF(self._x(a, rect), y + h), QPointF(self._x(b, rect), y), QPointF(self._x(c, rect), y),
                              QPointF(self._x(d, rect), y + h)])
            painter.setPen(QPen(QColor("#6a1b9a"), 1))
            painter.setBrush(QColor(106, 27, 154, 60))
            painter.drawPolygon(poly)
        y, h = rows[3][1], rows[3][2]
        for f in self.features:
            a, b, c, d = f["spans"]
            color = QColor(0, 121, 107, 70) if f.get("side") in ("left", "both") else QColor(230, 81, 0, 70)
            poly = QPolygonF([QPointF(self._x(a, rect), y + h), QPointF(self._x(b, rect), y), QPointF(self._x(c, rect), y),
                              QPointF(self._x(d, rect), y + h)])
            painter.setPen(QPen(color.darker(200), 1))
            painter.setBrush(color)
            painter.drawPolygon(poly)
            painter.setPen(QColor("#263238"))
            painter.drawText(QPointF(self._x(b, rect) + 2, y + h - 3), f.get("label", "").split(" ")[0])
        if self.station is not None:
            x = self._x(self.station, rect)
            painter.setPen(QPen(QColor("#d81b60"), 2))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        painter.end()

    def mousePressEvent(self, event):
        rect = QRectF(self.rect()).adjusted(46, 4, -12, -4)
        x = _event_point(event).x()
        s = self.start + (x - rect.left()) / max(rect.width(), 1) * (self.end - self.start)
        self.stationPicked.emit(min(max(s, self.start), self.end))

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.mousePressEvent(event)
