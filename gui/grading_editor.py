"""Grading Editor: draw and edit pads and graded lines on the map, with a level at every vertex and live cut and fill.

* **Draw pad** / **Draw line**: click the vertices, right-click (or double-click) to finish, Esc to cancel. A new
  vertex takes the level of the one before it (the first one: the ground there).
* **Edit**: drag a vertex; Ctrl/Shift+click an edge to insert a vertex; right-click a vertex to delete it; click an
  object to select it. Every change is one undo step.
* The vertex table edits X, Y and Z, and shows the ground and the cut or fill at each vertex. Level tools set all
  vertices to a level, raise them, set them on the ground plus an offset, or grade them from a vertex.
* The preview (a cut/fill layer, the daylight line and the totals) updates after each edit. **Build** writes the
  LandXML (existing ground and design TINs, outlines with their vertex levels and daylight lines as feature lines,
  for Civil 3D and OpenRoads), the design GeoTIFF, a quantities report and the objects (JSON), and loads them.
"""

from __future__ import annotations

import copy
import json
import math
import os
import tempfile

import numpy as np
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsMapLayerProxyModel,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsSettings,
    QgsUnitTypes,
    QgsWkbTypes,
)
from qgis.gui import (QgsDockWidget, QgsMapLayerComboBox, QgsMapTool, QgsProjectionSelectionWidget, QgsRubberBand,
                      QgsVertexMarker)
from qgis.PyQt.QtCore import Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
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
    QVBoxLayout,
    QWidget,
)

from ..landxml import grading_objects as go
from .crs_utils import Transformer, ask_crs
from .dock_utils import setup_dock

SETTINGS = "landxml_road_terrain/grading_editor"
COLOURS = {"pad": QColor("#1565c0"), "line": QColor("#6a1b9a")}


class GradingEditTool(QgsMapTool):
    def __init__(self, dock):
        super().__init__(dock.canvas)
        self.dock = dock
        self.drag = None
        self.setCursor(Qt.CursorShape.CrossCursor)

    def _tol(self):
        return QgsMapTool.searchRadiusMU(self.canvas()) * 1.5

    def _vertex_at(self, p):
        best = None
        for k, obj in enumerate(self.dock.objects):
            for i, v in enumerate(obj["vertices"]):
                q = self.dock.to_map(v[0], v[1])
                d = math.hypot(q.x() - p.x(), q.y() - p.y())
                if d <= self._tol() and (best is None or d < best[0]):
                    best = (d, k, i)
        return best[1:] if best else None

    def _edge_at(self, p):
        best = None
        for k, obj in enumerate(self.dock.objects):
            v = obj["vertices"]
            n = len(v)
            pairs = [(i, (i + 1) % n) for i in range(n)] if obj["kind"] == "pad" else [(i, i + 1) for i in range(n - 1)]
            for i, j in pairs:
                a, b = self.dock.to_map(v[i][0], v[i][1]), self.dock.to_map(v[j][0], v[j][1])
                ax, ay, bx, by = a.x(), a.y(), b.x(), b.y()
                L2 = (bx - ax) ** 2 + (by - ay) ** 2 or 1e-12
                t = max(0.0, min(1.0, ((p.x() - ax) * (bx - ax) + (p.y() - ay) * (by - ay)) / L2))
                d = math.hypot(ax + t * (bx - ax) - p.x(), ay + t * (by - ay) - p.y())
                if d <= self._tol() and (best is None or d < best[0]):
                    best = (d, k, i, t)
        return best[1:] if best else None

    def canvasPressEvent(self, event):              # noqa: N802 - Qt API
        p = event.mapPoint()
        dock = self.dock
        if dock.drawing is not None:
            if event.button() == Qt.MouseButton.RightButton:
                dock.finish_drawing()
            elif event.button() == Qt.MouseButton.LeftButton:
                dock.add_drawing_vertex(p)
            return
        hit = self._vertex_at(p)
        if event.button() == Qt.MouseButton.RightButton:
            if hit is not None:
                dock.delete_vertex(*hit)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if hit is not None:
            dock.select(hit[0], hit[1])
            dock.snapshot()
            self.drag = hit
            return
        edge = self._edge_at(p)
        if edge is not None:
            k, i, t = edge
            if event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
                dock.insert_vertex(k, i, t)
            else:
                dock.select(k)
            return
        dock.select_at(p)

    def canvasDoubleClickEvent(self, event):         # noqa: N802 - Qt API
        if self.dock.drawing is not None:
            self.dock.finish_drawing()

    def canvasMoveEvent(self, event):                # noqa: N802 - Qt API
        if self.drag is None:
            self.dock.rubber_to(event.mapPoint())
            return
        k, i = self.drag
        d = self.dock.to_design(event.mapPoint())
        v = self.dock.objects[k]["vertices"][i]
        v[0], v[1] = d.x(), d.y()
        self.dock.redraw(preview=False)

    def canvasReleaseEvent(self, _event):            # noqa: N802 - Qt API
        if self.drag is not None:
            self.drag = None
            self.dock.changed()

    def keyPressEvent(self, event):                  # noqa: N802 - Qt API
        if event.key() == Qt.Key.Key_Escape and self.dock.drawing is not None:
            self.dock.cancel_drawing()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.dock.drawing is not None:
            self.dock.finish_drawing()

    def deactivate(self):
        try:
            self.dock.tool_off()
        except (AttributeError, RuntimeError):
            pass
        super().deactivate()


class GradingEditorDock(QgsDockWidget):
    built = pyqtSignal(str)                      # LandXML path

    def __init__(self, iface=None, parent=None):
        super().__init__("Grading Editor (Pads and Graded Lines)", parent)
        self.setObjectName("LandXMLGradingEditor")
        self.iface = iface
        self.canvas = iface.mapCanvas() if iface is not None else None
        self.objects = []
        self.current = None                      # index of the selected object
        self.drawing = None                      # object being drawn
        self.undo_stack, self.redo_stack = [], []
        self.design_crs = QgsCoordinateReferenceSystem()
        self.transformer = None
        self.tool = None
        self.bands, self.markers = [], []
        self.rubber = None
        self.preview_layer_id = None
        self.result = None
        self._filling = False
        self._tmp = tempfile.mkdtemp(prefix="grading_preview_")
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.update_preview)
        self._build()
        if self.canvas is not None:
            self.canvas.destinationCrsChanged.connect(self._canvas_crs_changed)

    # ------------------------------------------------------------------ layout
    def _build(self):
        body = QWidget()
        v = QVBoxLayout(body)
        row = QHBoxLayout()
        self.pad_button = QPushButton("Draw pad")
        self.line_button = QPushButton("Draw line")
        self.edit_button = QPushButton("Edit on map")
        for b, tip in ((self.pad_button, "Click the outline's vertices; right-click or double-click to finish"),
                       (self.line_button, "Click a ramp, road, bund crest or swale; right-click or double-click to finish"),
                       (self.edit_button, "Drag vertices; Ctrl/Shift+click an edge to insert one; right-click a vertex "
                                          "to delete it; click an object to select it")):
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setStyleSheet("QPushButton:checked { background-color: #2e7d32; color: white; }")
            row.addWidget(b)
        self.pad_button.toggled.connect(lambda on: self._mode("pad", on))
        self.line_button.toggled.connect(lambda on: self._mode("line", on))
        self.edit_button.toggled.connect(lambda on: self._mode("edit", on))
        for text, slot, tip in (("Undo", self.undo, "Undo"), ("Redo", self.redo, "Redo"),
                                ("Open…", self.open_objects, "Open grading objects (JSON)"),
                                ("Save…", self.save_objects, "Save the grading objects (JSON)"),
                                ("Build…", self.build, "Write the LandXML (ground and design TINs, feature lines), the "
                                                       "design GeoTIFF and the quantities report")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row.addWidget(b)
        v.addLayout(row)
        row = QHBoxLayout()
        self.crs_widget = QgsProjectionSelectionWidget()
        try:
            self.crs_widget.setOptionVisible(QgsProjectionSelectionWidget.CrsOption.CrsNotSet, True)
            self.crs_widget.setNotSetText("Design CRS (not set)")
        except AttributeError:
            pass
        self.crs_widget.setToolTip("The projected CRS the objects are designed and written in (metres or feet), "
                                   "chosen by you or the project, never guessed")
        self.crs_widget.crsChanged.connect(self._crs_chosen)
        self.ground = QgsMapLayerComboBox()
        self.ground.setFilters(QgsMapLayerProxyModel.Filter.RasterLayer if hasattr(QgsMapLayerProxyModel, "Filter")
                               else QgsMapLayerProxyModel.RasterLayer)
        self.ground.setAllowEmptyLayer(True)
        self.ground.setToolTip("Existing ground: a DEM layer in the project, in any CRS (it is resampled into the "
                               "design CRS)")
        self.ground.layerChanged.connect(lambda _l: self.changed(snapshot=False))
        row.addWidget(QLabel("Ground"))
        row.addWidget(self.ground, 2)
        row.addWidget(self.crs_widget, 2)
        self.totals = QLabel("Draw a pad or a line.")
        self.totals.setStyleSheet("font-weight: bold")
        row.addWidget(self.totals, 3)
        v.addLayout(row)
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("Objects (applied in this order)"))
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._list_selected)
        lv.addWidget(self.list, 1)
        lrow = QHBoxLayout()
        for text, slot in (("Up", lambda: self._move(-1)), ("Down", lambda: self._move(1)),
                           ("Copy", self._duplicate), ("Delete", self._delete_object)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            lrow.addWidget(b)
        lv.addLayout(lrow)
        split.addWidget(left)
        mid = QWidget()
        form = QFormLayout(mid)
        self.name_edit = QLineEdit()
        self.name_edit.editingFinished.connect(lambda: self._set("name", self.name_edit.text().strip() or "Object"))
        form.addRow("Name", self.name_edit)
        self.props = {}
        for key, label, lo, hi, dec, tip in (
                ("cut_slope", "Cut slope (H per 1 V)", 0.05, 50, 2, "Batter in cut: 1.5 = 1V:1.5H"),
                ("fill_slope", "Fill slope (H per 1 V)", 0.05, 50, 2, "Batter in fill"),
                ("bench_height", "Bench every (height)", 0, 100, 2, "0 = no benches"),
                ("bench_width", "Bench width", 0, 100, 2, "Width of each level bench"),
                ("max_width", "Max batter width", 0, 5000, 1, "0 = no limit; the ground is kept beyond it"),
                ("width", "Top width (lines)", 0, 500, 2, "Width of a ramp, road or bund top, centred on the line"),
                ("crossfall", "Crossfall each side % (lines)", -20, 20, 2, "Falls from the line to each edge")):
            spin = QDoubleSpinBox()
            spin.setRange(lo, hi)
            spin.setDecimals(dec)
            spin.setToolTip(tip)
            spin.editingFinished.connect(lambda k=key, s=spin: self._set(k, s.value()))
            self.props[key] = spin
            form.addRow(label, spin)
        self.benches_in = QComboBox()
        self.benches_in.addItems(["both", "cut", "fill"])
        self.benches_in.currentTextChanged.connect(lambda t: self._set("benches_in", t))
        form.addRow("Benches in", self.benches_in)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["both", "cut", "fill"])
        self.mode_combo.setToolTip("both: cut and fill; cut: only excavate (a swale or channel); fill: only build up "
                                   "(a bund)")
        self.mode_combo.currentTextChanged.connect(lambda t: self._set("mode", t))
        form.addRow("Earthworks", self.mode_combo)
        split.addWidget(mid)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["X", "Y", "Z (level)", "Ground", "Cut(−)/fill(+)"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._table_edited)
        rv.addWidget(self.table, 1)
        trow = QHBoxLayout()
        for text, how, tip in (("All at level…", "level", "Set every vertex to one level (a flat pad)"),
                               ("Raise / lower…", "raise", "Add a height to every vertex"),
                               ("Ground + offset…", "ground", "Each vertex on the ground plus an offset"),
                               ("Grade from vertex…", "grade", "Levels at a grade (%) from the selected vertex along "
                                                               "the vertices")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda _c=False, h=how: self.level_tool(h))
            trow.addWidget(b)
        rv.addLayout(trow)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 2)
        v.addWidget(split, 1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        v.addWidget(self.status)
        setup_dock(self, body, "grading_editor")
        self._fill_props()

    # ------------------------------------------------------------------ CRS
    def _crs_chosen(self, crs):
        if crs.isValid() and crs.isGeographic():
            self.status.setText(f"{crs.authid()} is geographic: choose a projected CRS for the design.")
            self._set_crs_widget(self.design_crs)
            return
        if self.objects and self.design_crs.isValid() and crs != self.design_crs:
            self.status.setText("The objects already have coordinates in " + self.design_crs.authid() + ".")
            self._set_crs_widget(self.design_crs)
            return
        self.set_design_crs(crs)

    def _set_crs_widget(self, crs):
        self.crs_widget.blockSignals(True)
        self.crs_widget.setCrs(crs)
        self.crs_widget.blockSignals(False)

    def set_design_crs(self, crs):
        self.design_crs = QgsCoordinateReferenceSystem(crs)
        self._set_crs_widget(self.design_crs)
        self.transformer = Transformer(self.design_crs, self.canvas) if self.canvas is not None else None
        self.redraw(preview=False)

    def _canvas_crs_changed(self):
        if self.transformer is not None:
            self.transformer.refresh()
        self.redraw(preview=False)

    def design_crs_ok(self, ask=False):
        if self.design_crs.isValid():
            return True
        crs = self.canvas.mapSettings().destinationCrs() if self.canvas is not None else None
        if crs is not None and crs.isValid() and not crs.isGeographic():
            self.set_design_crs(crs)
            return True
        if not ask:
            return False
        crs = ask_crs(self, "The map is in degrees. Choose the projected CRS to design in (metres or feet), for "
                            "example the UTM zone of the site.")
        if crs is None:
            return False
        self.set_design_crs(crs)
        return True

    def to_map(self, x, y):
        return self.transformer.to_map(x, y) if self.transformer is not None else QgsPointXY(x, y)

    def to_design(self, point):
        return self.transformer.to_design(point) if self.transformer is not None else QgsPointXY(point.x(), point.y())

    # ------------------------------------------------------------------ ground
    def ground_spec(self):
        layer = self.ground.currentLayer()
        if layer is None:
            return None
        crs = layer.crs()
        return {"type": "dem", "path": layer.source().split("|")[0], "name": layer.name(),
                "crs": (crs.authid() or crs.toWkt()) if crs.isValid() else None,
                "design_crs": self.design_crs.authid() or self.design_crs.toWkt()}

    def ground_at(self, points):
        """Ground levels at (n, 2) design points (NaN where there is no ground)."""
        layer = self.ground.currentLayer()
        points = np.asarray(points, float)
        out = np.full(len(points), np.nan)
        if layer is None:
            return out
        provider = layer.dataProvider()
        for k, (x, y) in enumerate(points):
            q = self.transformer.to_layer(x, y, layer.crs()) if self.transformer else QgsPointXY(x, y)
            value, ok = provider.sample(q, 1)
            if ok and value is not None and math.isfinite(value):
                out[k] = float(value)
        return out

    # ------------------------------------------------------------------ modes and drawing
    def _mode(self, mode, on):
        buttons = {"pad": self.pad_button, "line": self.line_button, "edit": self.edit_button}
        if on:
            for key, b in buttons.items():
                if key != mode and b.isChecked():
                    b.blockSignals(True)
                    b.setChecked(False)
                    b.blockSignals(False)
            if self.canvas is None or not self.design_crs_ok(ask=True):
                buttons[mode].blockSignals(True)
                buttons[mode].setChecked(False)
                buttons[mode].blockSignals(False)
                return
            self.cancel_drawing()
            if mode in ("pad", "line"):
                n = sum(1 for o in self.objects if o["kind"] == mode) + 1
                self.drawing = go.new_object(mode, f"{'Pad' if mode == 'pad' else 'Line'} {n}")
                self.status.setText("Click the vertices; right-click or double-click to finish; Esc cancels.")
            else:
                self.status.setText("Drag a vertex; Ctrl/Shift+click an edge to insert one; right-click a vertex to "
                                    "delete it; click an object to select it.")
            if self.tool is None:
                self.tool = GradingEditTool(self)
            self.canvas.setMapTool(self.tool)
        elif not any(b.isChecked() for b in buttons.values()):
            self.cancel_drawing()
            if self.tool is not None and self.canvas is not None and self.canvas.mapTool() is self.tool:
                self.canvas.unsetMapTool(self.tool)

    def tool_off(self):
        for b in (self.pad_button, self.line_button, self.edit_button):
            b.blockSignals(True)
            b.setChecked(False)
            b.blockSignals(False)
        self.cancel_drawing()

    def add_drawing_vertex(self, map_point):
        d = self.to_design(map_point)
        verts = self.drawing["vertices"]
        if verts:
            z = verts[-1][2]
        else:
            g = self.ground_at([(d.x(), d.y())])[0]
            z = round(float(g), 2) if math.isfinite(g) else 0.0
        verts.append([d.x(), d.y(), z])
        self.redraw(preview=False)

    def rubber_to(self, map_point):
        if self.drawing is None or not self.drawing["vertices"] or self.canvas is None:
            return
        if self.rubber is None:
            self.rubber = QgsRubberBand(self.canvas, QgsWkbTypes.GeometryType.LineGeometry)
            self.rubber.setColor(QColor(120, 120, 120, 200))
            self.rubber.setLineStyle(Qt.PenStyle.DashLine)
        self.rubber.reset(QgsWkbTypes.GeometryType.LineGeometry)
        last = self.drawing["vertices"][-1]
        self.rubber.addPoint(self.to_map(last[0], last[1]))
        self.rubber.addPoint(map_point)
        if self.drawing["kind"] == "pad" and len(self.drawing["vertices"]) > 1:
            first = self.drawing["vertices"][0]
            self.rubber.addPoint(self.to_map(first[0], first[1]))

    def finish_drawing(self):
        obj, self.drawing = self.drawing, None
        self._clear_rubber()
        if obj is None:
            return
        if not go.valid(obj):
            self.status.setText("A pad needs 3 or more vertices, a line 2 or more.")
            self.redraw(preview=False)
            return
        self.snapshot()
        self.objects.append(obj)
        self.current = len(self.objects) - 1
        self.edit_button.setChecked(True)               # straight into editing
        self.changed(snapshot=False)
        self.status.setText(f"{obj['name']} added. Set its levels in the vertex table or with the level tools.")

    def cancel_drawing(self):
        if self.drawing is not None:
            self.drawing = None
            self._clear_rubber()
            self.redraw(preview=False)

    def _clear_rubber(self):
        if self.rubber is not None and self.canvas is not None:
            self.canvas.scene().removeItem(self.rubber)
        self.rubber = None

    # ------------------------------------------------------------------ edits
    def snapshot(self):
        self.undo_stack.append(json.dumps(self.objects))
        self.undo_stack = self.undo_stack[-200:]
        self.redo_stack = []

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(json.dumps(self.objects))
            self.objects = json.loads(self.undo_stack.pop())
            self._after_history()

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(json.dumps(self.objects))
            self.objects = json.loads(self.redo_stack.pop())
            self._after_history()

    def _after_history(self):
        if self.current is not None and self.current >= len(self.objects):
            self.current = len(self.objects) - 1 if self.objects else None
        self.changed(snapshot=False)

    def changed(self, snapshot=False):
        if snapshot:
            self.snapshot()
        self._fill_list()
        self._fill_props()
        self._fill_table()
        self.redraw(preview=False)
        self._timer.start()

    def select(self, k, vertex=None):
        self.current = k
        self._fill_list()
        self._fill_props()
        self._fill_table()
        if vertex is not None:
            self.table.selectRow(vertex)
        self.redraw(preview=False)

    def select_at(self, map_point):
        from ..landxml.grading import _inside_many
        d = self.to_design(map_point)
        for k in reversed(range(len(self.objects))):
            o = self.objects[k]
            if o["kind"] == "pad" and _inside_many(np.array([[d.x(), d.y()]]), np.asarray(o["vertices"])[:, :2])[0]:
                self.select(k)
                return

    def insert_vertex(self, k, i, t):
        obj = self.objects[k]
        v = obj["vertices"]
        j = (i + 1) % len(v)
        new = [v[i][c] + t * (v[j][c] - v[i][c]) for c in range(3)]
        self.snapshot()
        v.insert(i + 1, new)
        self.current = k
        self.changed()

    def delete_vertex(self, k, i):
        obj = self.objects[k]
        if len(obj["vertices"]) <= (3 if obj["kind"] == "pad" else 2):
            self.status.setText("That would leave too few vertices; delete the object instead.")
            return
        self.snapshot()
        del obj["vertices"][i]
        self.current = k
        self.changed()

    def _set(self, key, value):
        if self._filling or self.current is None:
            return
        obj = self.objects[self.current]
        if obj.get(key) == value:
            return
        self.snapshot()
        obj[key] = value
        self.changed()

    def _move(self, step):
        k = self.current
        if k is None or not 0 <= k + step < len(self.objects):
            return
        self.snapshot()
        self.objects[k], self.objects[k + step] = self.objects[k + step], self.objects[k]
        self.current = k + step
        self.changed()

    def _duplicate(self):
        if self.current is None:
            return
        self.snapshot()
        obj = copy.deepcopy(self.objects[self.current])
        obj["name"] += " copy"
        self.objects.insert(self.current + 1, obj)
        self.current += 1
        self.changed()

    def _delete_object(self):
        if self.current is None:
            return
        self.snapshot()
        del self.objects[self.current]
        self.current = min(self.current, len(self.objects) - 1) if self.objects else None
        self.changed()

    def level_tool(self, how):
        if self.current is None:
            self.status.setText("Select an object first.")
            return
        prompts = {"level": ("All at level", "Level", None), "raise": ("Raise / lower", "Height (negative lowers)", 0.0),
                   "ground": ("Ground + offset", "Offset above the ground (negative below)", 0.0),
                   "grade": ("Grade from vertex", "Grade % (positive rises along the vertices)", 1.0)}
        title, label, default = prompts[how]
        obj = self.objects[self.current]
        if default is None:
            default = obj["vertices"][0][2]
        value, ok = QInputDialog.getDouble(self, title, label, float(default), -100000, 100000, 3)
        if not ok:
            return
        index = max(self.table.currentRow(), 0)
        self.snapshot()
        try:
            go.set_levels(obj, how, value, ground=self.ground_at, index=index)
        except ValueError as error:
            self.undo_stack.pop()
            self.status.setText(str(error))
            return
        self.changed()

    def _table_edited(self, item):
        if self._filling or self.current is None or item.column() > 2:
            return
        try:
            value = float(item.text())
        except ValueError:
            self._fill_table()
            return
        self.snapshot()
        self.objects[self.current]["vertices"][item.row()][item.column()] = value
        self.changed()

    # ------------------------------------------------------------------ panels
    def _fill_list(self):
        self.list.blockSignals(True)
        self.list.clear()
        for o in self.objects:
            self.list.addItem(f"{o['name']}  [{'pad' if o['kind'] == 'pad' else 'line'}, {len(o['vertices'])} vertices]")
        if self.current is not None:
            self.list.setCurrentRow(self.current)
        self.list.blockSignals(False)

    def _list_selected(self, row):
        if row >= 0:
            self.select(row)

    def _fill_props(self):
        self._filling = True
        obj = self.objects[self.current] if self.current is not None else None
        for key, spin in self.props.items():
            spin.setEnabled(obj is not None and (key not in ("width", "crossfall") or obj["kind"] == "line"))
            spin.setValue(float(obj.get(key, go.DEFAULTS.get(key, 0)) or 0) if obj else go.DEFAULTS.get(key, 0) or 0)
        self.name_edit.setText(obj["name"] if obj else "")
        self.benches_in.setCurrentText(obj.get("benches_in", "both") if obj else "both")
        self.mode_combo.setCurrentText(obj.get("mode", "both") if obj else "both")
        self.mode_combo.setEnabled(obj is not None and obj["kind"] == "line")
        self._filling = False

    def _fill_table(self):
        self._filling = True
        self.table.setRowCount(0)
        if self.current is not None:
            verts = self.objects[self.current]["vertices"]
            ground = self.ground_at([v[:2] for v in verts]) if verts else []
            for i, v in enumerate(verts):
                self.table.insertRow(i)
                g = ground[i]
                cells = [f"{v[0]:.3f}", f"{v[1]:.3f}", f"{v[2]:.3f}", f"{g:.3f}" if math.isfinite(g) else "–",
                         f"{v[2] - g:+.3f}" if math.isfinite(g) else "–"]
                for c, text in enumerate(cells):
                    item = QTableWidgetItem(text)
                    if c > 2:
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                        if c == 4 and math.isfinite(g):
                            item.setForeground(QColor("#b3261e") if v[2] < g else QColor("#1b5e20"))
                    self.table.setItem(i, c, item)
        self._filling = False

    # ------------------------------------------------------------------ drawing on the map
    def redraw(self, preview=True):
        if self.canvas is None:
            return
        for item in self.bands + self.markers:
            self.canvas.scene().removeItem(item)
        self.bands, self.markers = [], []
        shown = list(enumerate(self.objects)) + ([(-1, self.drawing)] if self.drawing else [])
        for k, obj in shown:
            verts = obj["vertices"]
            if not verts:
                continue
            band = QgsRubberBand(self.canvas, QgsWkbTypes.GeometryType.LineGeometry)
            colour = QColor(COLOURS[obj["kind"]])
            band.setColor(colour)
            band.setWidth(3 if k == self.current or k == -1 else 2)
            for x, y, _z in verts:
                band.addPoint(self.to_map(x, y))
            if obj["kind"] == "pad" and len(verts) > 2:
                band.addPoint(self.to_map(verts[0][0], verts[0][1]))
            self.bands.append(band)
            for x, y, _z in verts:
                m = QgsVertexMarker(self.canvas)
                m.setCenter(self.to_map(x, y))
                m.setIconType(QgsVertexMarker.IconType.ICON_CIRCLE)
                m.setColor(QColor("#d81b60") if k == self.current else colour)
                m.setIconSize(9)
                m.setPenWidth(2)
                self.markers.append(m)
        if self.result is not None and self.result.get("daylight"):
            for line in self.result["daylight"]:
                band = QgsRubberBand(self.canvas, QgsWkbTypes.GeometryType.LineGeometry)
                band.setColor(QColor("#ef6c00"))
                band.setWidth(1)
                band.setLineStyle(Qt.PenStyle.DashLine)
                for x, y, _z in line:
                    band.addPoint(self.to_map(x, y))
                self.bands.append(band)
        if preview:
            self._timer.start()

    def update_preview(self):
        """Cut and fill on a coarse grid: totals, daylight lines and a cut/fill layer."""
        valid = [o for o in self.objects if go.valid(o)]
        spec = self.ground_spec()
        if not valid:
            self.totals.setText("Draw a pad or a line.")
            self.result = None
            return
        if spec is None:
            self.totals.setText("Choose the ground (a DEM layer) to see cut and fill.")
            return
        try:
            ground, design, rows, cell = go.preview(valid, spec)
        except (ValueError, RuntimeError) as error:
            self.totals.setText(f"Preview: {error}")
            return
        cut, fill = sum(r["cut"] for r in rows), sum(r["fill"] for r in rows)
        u = "m" if self.design_crs.mapUnits() != QgsUnitTypes.DistanceUnit.DistanceFeet else "ft"
        self.totals.setText(f"Cut {cut:,.0f} · Fill {fill:,.0f} · Net {fill - cut:+,.0f} {u}³  (preview, {cell:.1f} {u} grid)")
        self.result = {"rows": rows, "cut": cut, "fill": fill, "daylight": go.daylight_lines(ground, design)}
        self._preview_layer(ground, design, cell)
        self._fill_table()
        self.redraw(preview=False)

    def _preview_layer(self, ground, design, cell):
        if self.iface is None or not hasattr(self.iface, "mainWindow") or self.iface.mainWindow() is None:
            return
        from ..core import write_geotiff
        from qgis.core import QgsColorRampShader, QgsRasterShader, QgsSingleBandPseudoColorRenderer

        depth = design.z - ground.z
        depth = np.where(np.abs(depth) > 0.005, depth, np.nan)
        path = os.path.join(self._tmp, f"cutfill_{abs(hash(json.dumps(self.objects))) % 10 ** 8}.tif")
        write_geotiff(path, np.where(np.isfinite(depth), depth, -9999.0), design.x0, design.y0, cell,
                      wkt=self.design_crs.toWkt(), nodata=-9999.0)
        project = QgsProject.instance()
        old = project.mapLayer(self.preview_layer_id or "")
        layer = QgsRasterLayer(path, "Grading preview: cut (red) / fill (green)")
        if not layer.isValid():
            return
        span = max(float(np.nanmax(np.abs(depth))) if np.isfinite(depth).any() else 1.0, 0.1)
        ramp = QgsColorRampShader(-span, span)
        ramp.setColorRampType(QgsColorRampShader.Type.Interpolated)
        ramp.setColorRampItemList([QgsColorRampShader.ColorRampItem(-span, QColor("#b71c1c"), f"cut {span:.1f}"),
                                   QgsColorRampShader.ColorRampItem(0, QColor(255, 255, 255, 0), "0"),
                                   QgsColorRampShader.ColorRampItem(span, QColor("#1b5e20"), f"fill {span:.1f}")])
        shader = QgsRasterShader()
        shader.setRasterShaderFunction(ramp)
        renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
        layer.setRenderer(renderer)
        layer.renderer().setOpacity(0.6)
        if old is not None:
            project.removeMapLayer(old.id())
        project.addMapLayer(layer, False)
        project.layerTreeRoot().insertLayer(0, layer)
        self.preview_layer_id = layer.id()

    # ------------------------------------------------------------------ files
    def save_objects(self, path=None):
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, "Save grading objects", QgsSettings().value(f"{SETTINGS}/folder", ""),
                                                  "Grading objects (*.json)")
            if not path:
                return None
        QgsSettings().setValue(f"{SETTINGS}/folder", os.path.dirname(path))
        go.save(path, self.objects, self.ground_spec(), self._epsg())
        self.status.setText(f"Saved {path}")
        return path

    def open_objects(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Open grading objects", QgsSettings().value(f"{SETTINGS}/folder", ""),
                                                  "Grading objects (*.json)")
            if not path:
                return
        objects, _ground, epsg = go.load(path)
        if epsg:
            self.set_design_crs(QgsCoordinateReferenceSystem(f"EPSG:{epsg}"))
        elif not self.design_crs_ok(ask=True):
            return
        self.snapshot()
        self.objects = objects
        self.current = 0 if objects else None
        self.changed()

    def _epsg(self):
        authid = self.design_crs.authid()
        return int(authid.split(":")[1]) if authid.upper().startswith("EPSG:") else None

    def build(self, path=None):
        spec = self.ground_spec()
        if spec is None:
            self.status.setText("Choose the ground (a DEM layer) first.")
            return None
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, "Build grading (LandXML)", QgsSettings().value(f"{SETTINGS}/folder", ""),
                                                  "LandXML (*.xml)")
            if not path:
                return None
        QgsSettings().setValue(f"{SETTINGS}/folder", os.path.dirname(path))
        stem = os.path.splitext(path)[0]
        unit = "foot" if self.design_crs.mapUnits() == QgsUnitTypes.DistanceUnit.DistanceFeet else "meter"
        try:
            result = go.build(self.objects, spec, path, unit=unit, epsg=self._epsg(), wkt=self.design_crs.toWkt(),
                              ground_name=spec.get("name", "ground"), dem_path=stem + "_design.tif",
                              report_path=stem + "_quantities.html", json_path=stem + "_objects.json",
                              dxf_path=stem + ".dxf")
        except (ValueError, RuntimeError) as error:
            self.status.setText(f"Build: {error}")
            return None
        self.status.setText(f"Built {path}: cut {result['cut']:,.0f}, fill {result['fill']:,.0f} on a "
                            f"{result['cell']:.2f} grid. Design GeoTIFF, DXF, report and objects written beside it. "
                            "In Civil 3D: Insert > LandXML (northing first, the design CRS).")
        if self.iface is not None and hasattr(self.iface, "mainWindow") and self.iface.mainWindow() is not None:
            layer = QgsRasterLayer(stem + "_design.tif", os.path.basename(stem) + " design")
            if layer.isValid():
                QgsProject.instance().addMapLayer(layer)
        self.built.emit(path)
        return path

    def cleanup(self):
        self._timer.stop()
        self.tool_off()
        if self.tool is not None and self.canvas is not None and self.canvas.mapTool() is self.tool:
            self.canvas.unsetMapTool(self.tool)
        self.tool = None
        if self.canvas is not None:
            for item in self.bands + self.markers:
                self.canvas.scene().removeItem(item)
        self.bands, self.markers = [], []
        self._clear_rubber()
        layer = QgsProject.instance().mapLayer(self.preview_layer_id or "")
        if layer is not None:
            QgsProject.instance().removeMapLayer(layer.id())


__all__ = ["GradingEditorDock", "GradingEditTool"]
