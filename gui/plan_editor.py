"""Plan editor: design a horizontal alignment on the map by its intersection points, with live curve fitting.

Click to add PIs, drag them, drag a curve's handle to change its radius, right-click a PI to delete it; radii and
spirals are also typed in the table. The fit is live (the writer's PI fitting); a change that cannot be fitted is
refused with the reason. Saves a new LandXML (with an optional ground profile from a DEM) and hands it to the Profile
Viewer's Design tab and the Corridor Section Editor.
"""

from __future__ import annotations

import math
import os

from qgis.core import (
    Qgis,
    QgsGeometry,
    QgsMapLayerProxyModel,
    QgsPointXY,
    QgsProject,
    QgsSettings,
    QgsUnitTypes,
    QgsWkbTypes,
)
from qgis.gui import QgsDockWidget, QgsMapLayerComboBox, QgsMapTool, QgsRubberBand, QgsVertexMarker
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..landxml.criteria import list_standards, load_criteria
from ..landxml.plan_design import PlanDesign
from ..landxml.writer import GroundProfile, write_landxml

SETTINGS = "landxml_road_terrain/plan_editor"
AXIS = ["Axis order to write…", "Northing first (as Civil 3D writes)", "Easting first"]
UNITS = {QgsUnitTypes.DistanceUnit.DistanceMeters: "meter", QgsUnitTypes.DistanceUnit.DistanceFeet: "foot"} \
    if hasattr(QgsUnitTypes, "DistanceUnit") else {}


def _event_point(event):
    return event.mapPoint()


class PlanEditTool(QgsMapTool):
    """Add, drag and delete PIs; drag a curve handle to change its radius."""

    def __init__(self, dock):
        super().__init__(dock.canvas)
        self.dock = dock
        self.drag = None
        self.setCursor(Qt.CursorShape.CrossCursor)

    def _tolerance(self):
        return QgsMapTool.searchRadiusMU(self.canvas()) * 1.5

    def _pi_at(self, p):
        design = self.dock.design
        if design is None:
            return None
        best = None
        for i, (x, y) in enumerate(design.pis):
            d = math.hypot(x - p.x(), y - p.y())
            if d <= self._tolerance() and (best is None or d < best[0]):
                best = (d, i)
        return best[1] if best else None

    def _handle_at(self, p):
        for i, (x, y) in self.dock.handles().items():
            if math.hypot(x - p.x(), y - p.y()) <= self._tolerance():
                return i
        return None

    def canvasPressEvent(self, event):
        p = _event_point(event)
        design = self.dock.design
        if event.button() == Qt.MouseButton.RightButton:
            i = self._pi_at(p)
            if i is not None:
                self.dock.edit(lambda: design.delete(i))
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if design is None:
            self.dock.start(p.x(), p.y())
            return
        handle = self._handle_at(p)
        if handle is not None:
            self.drag = ("radius", handle, design.mark())
            return
        i = self._pi_at(p)
        if i is not None:
            self.drag = ("pi", i, design.mark())
            return
        if event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
            self.dock.edit(lambda: design.insert_near(p.x(), p.y()))
        else:
            self.dock.edit(lambda: design.add(p.x(), p.y()))

    def canvasMoveEvent(self, event):
        if self.drag is None:
            return
        p = _event_point(event)
        kind, i, _mark = self.drag
        design = self.dock.design
        try:
            if kind == "pi":
                design.move(i, p.x(), p.y())
            else:
                px, py = design.pis[i]
                external = math.hypot(p.x() - px, p.y() - py)
                half = math.radians(abs(design.deflection(i))) / 2
                if half > 1e-6:
                    radius = external / (1 / math.cos(half) - 1)
                    design.set_curve(i, radius=round(max(radius, 1.0), 1))
        except ValueError as error:
            self.dock.status.setText(str(error))
            return
        self.dock.redraw(table=False)

    def canvasReleaseEvent(self, _event):
        if self.drag is not None:
            self.dock.design.squash(self.drag[2])
            self.drag = None
            self.dock.redraw()

    def deactivate(self):
        try:
            self.dock.tool_off()
        except (AttributeError, RuntimeError):          # the dock is already gone
            pass
        super().deactivate()


class PlanEditorDock(QgsDockWidget):
    saved = pyqtSignal(str, str)                 # path, alignment name
    to_section_editor = pyqtSignal(str, str)

    def __init__(self, iface=None, parent=None):
        super().__init__("Plan Editor (Horizontal Alignment)", parent)
        self.setObjectName("LandXMLPlanEditor")
        self.iface = iface
        self.canvas = iface.mapCanvas() if iface is not None else None
        self.design = None
        self.tool = None
        self.saved_path = None
        self.bands = []
        self.markers = []
        self._filling = False
        self._build()

    def _build(self):
        body = QWidget()
        v = QVBoxLayout(body)
        row = QHBoxLayout()
        self.name_edit = QLineEdit("Design alignment")
        self.draw_button = QPushButton("Draw / edit on map")
        self.draw_button.setCheckable(True)
        self.draw_button.setToolTip("Click: add a PI at the end. Ctrl/Shift+click: insert into the nearest leg. Drag a PI "
                                    "or a curve handle (the square on the arc). Right-click a PI: delete it.")
        self.draw_button.toggled.connect(self.set_tool)
        for text, slot in (("From selected line", self.from_layer), ("From LandXML…", self.from_landxml),
                           ("Undo", lambda: self._history(True)), ("Redo", lambda: self._history(False)),
                           ("Save LandXML…", self.save)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.insertWidget(0, self.draw_button)
        row.insertWidget(0, self.name_edit)
        v.addLayout(row)
        row = QHBoxLayout()
        self.criteria_combo = QComboBox()
        for sid, name, _p in list_standards():
            self.criteria_combo.addItem(name, sid)
        self.criteria_combo.currentIndexChanged.connect(lambda _i: self.redraw())
        self.speed = QDoubleSpinBox()
        self.speed.setRange(10, 200)
        self.speed.setValue(80)
        self.speed.setSuffix(" design speed")
        self.speed.valueChanged.connect(lambda _v: self.redraw())
        self.emax = QDoubleSpinBox()
        self.emax.setRange(2, 12)
        self.emax.setValue(6)
        self.emax.setPrefix("emax ")
        self.emax.setSuffix(" %")
        self.emax.valueChanged.connect(lambda _v: self.redraw())
        self.radius = QDoubleSpinBox()
        self.radius.setRange(5, 100000)
        self.radius.setValue(300)
        self.radius.setPrefix("new curves R ")
        self.axis = QComboBox()
        self.axis.addItems(AXIS)
        self.dem = QgsMapLayerComboBox()
        self.dem.setFilters(QgsMapLayerProxyModel.Filter.RasterLayer if hasattr(QgsMapLayerProxyModel, "Filter")
                            else QgsMapLayerProxyModel.RasterLayer)
        self.dem.setAllowEmptyLayer(True)
        self.dem.setToolTip("Optional: a DEM sampled into a ground profile, so the Profile Viewer Design tab can start "
                            "from it")
        for w in (self.criteria_combo, self.speed, self.emax, self.radius, self.axis, QLabel("Ground"), self.dem):
            row.addWidget(w)
        v.addLayout(row)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["PI", "X", "Y", "Radius", "Spiral in", "Spiral out", "Deflection"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._table_edited)
        split.addWidget(self.table)
        right = QWidget()
        rv = QVBoxLayout(right)
        self.elements = QTableWidget(0, 5)
        self.elements.setHorizontalHeaderLabels(["Element", "From", "To", "Length", "Radius"])
        self.elements.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        rv.addWidget(self.elements, 2)
        self.checks = QListWidget()
        rv.addWidget(self.checks, 1)
        hand = QHBoxLayout()
        for text, slot in (("Open in Profile Viewer", self._to_viewer), ("Open in Section Editor", self._to_editor)):
            b = QToolButton()
            b.setText(text)
            b.clicked.connect(slot)
            hand.addWidget(b)
        rv.addLayout(hand)
        split.addWidget(right)
        v.addWidget(split, 1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        v.addWidget(self.status)
        self.setWidget(body)

    # ------------------------------------------------------------------ design
    def start(self, x, y):
        self.design = PlanDesign([[x, y]], [], self.name_edit.text() or "Design alignment")
        self.design.default_radius = self.radius.value()
        self.status.setText("Click the next point.")
        self.redraw()

    def edit(self, change):
        if self.design is None:
            return
        self.design.default_radius = self.radius.value()
        if len(self.design.pis) == 1:                   # the second point of a new design
            try:
                change()
            except (ValueError, IndexError):
                pass
            self.redraw()
            return
        message = ""
        try:
            change()
        except ValueError as error:
            message = str(error)
        self.redraw()
        if message:
            self.status.setText(message)

    def _history(self, undo):
        if self.design is not None and (self.design.undo() if undo else self.design.redo()):
            self.redraw()

    def from_layer(self):
        layer = self.iface.activeLayer() if self.iface else None
        if layer is None or not hasattr(layer, "selectedFeatures") or layer.geometryType() != Qgis.GeometryType.Line:
            self.status.setText("Select one line feature in the active layer.")
            return
        features = layer.selectedFeatures()
        if len(features) != 1:
            self.status.setText("Select exactly one line feature.")
            return
        geom = QgsGeometry(features[0].geometry())
        if self.canvas is not None and layer.crs() != self.canvas.mapSettings().destinationCrs():
            from qgis.core import QgsCoordinateTransform
            geom.transform(QgsCoordinateTransform(layer.crs(), self.canvas.mapSettings().destinationCrs(), QgsProject.instance()))
        line = geom.asMultiPolyline()[0] if geom.isMultipart() else geom.asPolyline()
        self.design = PlanDesign.from_polyline([(p.x(), p.y()) for p in line], self.radius.value(),
                                               self.name_edit.text() or "Design alignment")
        self.redraw()

    def from_landxml(self):
        """Start from an alignment, or from a feature line or breakline (Civil 3D PlanFeatures, OpenRoads breaklines):
        the line's vertices become the PIs and its levels the design profile."""
        from ..landxml.features import read_line_features
        from ..landxml.geometry import read_alignments
        from ..landxml.parser import load_document

        path, _ = QFileDialog.getOpenFileName(self, "LandXML alignment or feature lines",
                                              QgsSettings().value(f"{SETTINGS}/folder", ""), "LandXML (*.xml *.landxml)")
        if not path:
            return
        try:
            records, _bad = read_alignments(path, 2.0)
        except ValueError:                                   # no alignments: feature lines only
            records = []
        lines, _warn = read_line_features(load_document(path).root)
        choices = [f"Alignment: {r['name']}" for r in records] + \
            [f"{f.kind}: {f.name or 'unnamed'} ({len(f.points)} points)" for f in lines if len(f.points) >= 2]
        if not choices:
            self.status.setText("No alignments or feature lines in that file.")
            return
        choice, ok = QInputDialog.getItem(self, "Start from", "Alignment or feature line", choices, 0, False)
        if not ok:
            return
        axis, ok = QInputDialog.getItem(self, "Axis order", "The file stores coordinates", AXIS[1:], 0, False)
        if not ok:
            return
        swap = axis == AXIS[1]
        k = choices.index(choice)
        if k < len(records):
            record = records[k]
            segments = []
            for seg in record["segments"]:
                seg = dict(seg)
                if swap:
                    seg["points"] = [(p[1], p[0]) for p in seg["points"]]
                segments.append(seg)
            self.design = PlanDesign.from_segments(segments, record["name"], float(record.get("sta_start") or 0.0))
            self.design.levels = None
            self.name_edit.setText(record["name"])
        else:
            from ..landxml.plan_design import simplify

            feature = [f for f in lines if len(f.points) >= 2][k - len(records)]
            pts = [((p[1], p[0]) if swap else (p[0], p[1])) + ((p[2],) if len(p) > 2 else ()) for p in feature.points]
            pts = simplify(pts, 0.25)
            name = feature.name or "Feature line"
            self.design = PlanDesign.from_polyline([p[:2] for p in pts], self.radius.value(), name)
            self.design.levels = [p for p in pts if len(p) == 3] or None
            self.name_edit.setText(name)
        self.redraw(zoom=True)

    # ------------------------------------------------------------------ drawing
    def handles(self):
        """{PI index: the alignment point nearest to the PI} - the middle of its curve, dragged to change the radius."""
        out = {}
        d = self.design
        if d is None or d.alignment is None:
            return out
        pts = d.alignment.points(max(d.alignment.length / 4000, 0.5))
        for i, c in enumerate(d.curves, start=1):
            if not c or abs(d.deflection(i)) < 1e-6:
                continue
            px, py = d.pis[i]
            x, y, _s = min(pts, key=lambda p: (p[0] - px) ** 2 + (p[1] - py) ** 2)
            out[i] = (x, y)
        return out

    def redraw(self, table=True, zoom=False):
        if self.canvas is None:
            if table:
                self._fill_tables()
            return
        for band in self.bands:
            self.canvas.scene().removeItem(band)
        for m in self.markers:
            self.canvas.scene().removeItem(m)
        self.bands, self.markers = [], []
        d = self.design
        if d is None:
            return
        legs = QgsRubberBand(self.canvas, QgsWkbTypes.GeometryType.LineGeometry)
        legs.setColor(QColor(120, 120, 120, 180))
        legs.setLineStyle(Qt.PenStyle.DashLine)
        legs.setWidth(1)
        for x, y in d.pis:
            legs.addPoint(QgsPointXY(x, y))
        self.bands.append(legs)
        if d.alignment is not None:
            line = QgsRubberBand(self.canvas, QgsWkbTypes.GeometryType.LineGeometry)
            line.setColor(QColor("#1565c0"))
            line.setWidth(3)
            for x, y, _s in d.alignment.points(max(d.alignment.length / 2000, 1.0)):
                line.addPoint(QgsPointXY(x, y))
            self.bands.append(line)
        for i, (x, y) in enumerate(d.pis):
            m = QgsVertexMarker(self.canvas)
            m.setCenter(QgsPointXY(x, y))
            m.setIconType(QgsVertexMarker.IconType.ICON_CIRCLE)
            m.setColor(QColor("#d81b60"))
            m.setIconSize(10)
            m.setPenWidth(2)
            self.markers.append(m)
        for _i, (x, y) in self.handles().items():
            m = QgsVertexMarker(self.canvas)
            m.setCenter(QgsPointXY(x, y))
            m.setIconType(QgsVertexMarker.IconType.ICON_BOX)
            m.setColor(QColor("#1565c0"))
            m.setIconSize(9)
            self.markers.append(m)
        if zoom and d.alignment is not None:
            pts = d.alignment.points(10.0)
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            from qgis.core import QgsRectangle
            self.canvas.setExtent(QgsRectangle(min(xs), min(ys), max(xs), max(ys)).buffered(50))
            self.canvas.refresh()
        if table:
            self._fill_tables()

    def _fill_tables(self):
        d = self.design
        self._filling = True
        self.table.setRowCount(0)
        if d is not None:
            for i, (x, y) in enumerate(d.pis):
                self.table.insertRow(i)
                c = d.curves[i - 1] if 0 < i < len(d.pis) - 1 else None
                values = [str(i + 1), f"{x:.3f}", f"{y:.3f}", f"{c['radius']:g}" if c else "", f"{c['spiral_in']:g}" if c else "",
                          f"{c['spiral_out']:g}" if c else "",
                          f"{d.deflection(i):.3f}°" if 0 < i < len(d.pis) - 1 else ""]
                for col, text in enumerate(values):
                    item = QTableWidgetItem(text)
                    if col in (0, 6) or (col >= 3 and c is None):
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.table.setItem(i, col, item)
        self._filling = False
        self.elements.setRowCount(0)
        self.checks.clear()
        if d is None:
            return
        for k, e in enumerate(d.elements()):
            self.elements.insertRow(k)
            for col, text in enumerate((e["kind"], f"{e['start']:.3f}", f"{e['end']:.3f}", f"{e['length']:.3f}",
                                        f"{e['radius']:g}" if e["radius"] else "")):
                self.elements.setItem(k, col, QTableWidgetItem(text))
        if d.error:
            self.checks.addItem(d.error)
        try:
            criteria = load_criteria(self.criteria_combo.currentData())
        except ValueError:
            criteria = None
        for _level, message, ref in d.checks(criteria, self.speed.value(), self.emax.value()):
            self.checks.addItem(f"{message} ({ref}) - advisory")
        if d.alignment is not None:
            self.status.setText(f"{len(d.pis)} PIs, length {d.alignment.length:.3f}")

    def _table_edited(self, item):
        if self._filling or self.design is None:
            return
        row, col = item.row(), item.column()
        try:
            value = float(item.text())
        except ValueError:
            self.redraw()
            return
        d = self.design
        if col in (1, 2):
            x, y = d.pis[row]
            self.edit(lambda: d.move(row, value if col == 1 else x, value if col == 2 else y))
        elif col in (3, 4, 5):
            key = {3: "radius", 4: "spiral_in", 5: "spiral_out"}[col]
            self.edit(lambda: d.set_curve(row, **{key: value}))

    # ------------------------------------------------------------------ tool and save
    def set_tool(self, on):
        if self.canvas is None:
            return
        if on:
            crs = self.canvas.mapSettings().destinationCrs()
            if crs.isGeographic():
                self.status.setText("Set the project to a projected CRS (metres or feet) to design an alignment.")
                self.draw_button.setChecked(False)
                return
            self.tool = PlanEditTool(self)
            self.canvas.setMapTool(self.tool)
        elif self.tool is not None:
            self.canvas.unsetMapTool(self.tool)
            self.tool = None

    def tool_off(self):
        self.draw_button.blockSignals(True)
        self.draw_button.setChecked(False)
        self.draw_button.blockSignals(False)

    def save(self, path=None):
        d = self.design
        if d is None or d.alignment is None:
            self.status.setText("Nothing to save: the design does not fit." if d else "Draw an alignment first.")
            return None
        if self.axis.currentIndex() == 0:
            self.status.setText("Choose the axis order to write (Civil 3D writes northing first).")
            return None
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, "Save alignment", QgsSettings().value(f"{SETTINGS}/folder", ""),
                                                  "LandXML (*.xml)")
            if not path:
                return None
        QgsSettings().setValue(f"{SETTINGS}/folder", os.path.dirname(path))
        d.name = self.name_edit.text() or d.name
        d.refit()
        crs = self.canvas.mapSettings().destinationCrs() if self.canvas else None
        unit = "meter"
        if crs is not None and crs.mapUnits() in UNITS:
            unit = UNITS[crs.mapUnits()]
        epsg = None
        if crs is not None and crs.authid().upper().startswith("EPSG:"):
            epsg = int(crs.authid().split(":")[1])
        profiles = []
        layer = self.dem.currentLayer()
        if layer is not None:
            provider = layer.dataProvider()
            pts = d.alignment.points(10.0)
            ground = []
            for x, y, s in pts:
                value, ok = provider.sample(QgsPointXY(x, y), 1)
                if ok and value is not None and math.isfinite(value):
                    ground.append((s, float(value)))
            if len(ground) >= 2:
                profiles.append(GroundProfile(f"{d.name} ground ({layer.name()})", ground))
        if getattr(d, "levels", None) and len(d.levels) == len(d.pis):
            from ..landxml.plan_design import profile_from_points

            try:
                profiles.insert(0, profile_from_points(d.alignment, d.levels, 0.0, f"{d.name} FRL"))
            except ValueError as error:
                self.status.setText(f"Design profile from the feature line not written: {error}")
        write_landxml(path, [(d.alignment, profiles)], linear_unit=unit, epsg=epsg,
                      northing_first=self.axis.currentIndex() == 1)
        self.saved_path = path
        self.status.setText(f"Saved {path}")
        self.saved.emit(path, d.name)
        return path

    def _to_viewer(self):
        path = self.saved_path or self.save()
        if path:
            self.saved.emit(path, self.design.name)

    def _to_editor(self):
        path = self.saved_path or self.save()
        if path:
            self.to_section_editor.emit(path, self.design.name)

    def cleanup(self):
        self.set_tool(False)
        if self.canvas is not None:
            for band in self.bands:
                self.canvas.scene().removeItem(band)
            for m in self.markers:
                self.canvas.scene().removeItem(m)
        self.bands, self.markers = [], []


__all__ = ["PlanEditorDock", "PlanEditTool"]
