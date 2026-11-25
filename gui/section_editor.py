"""Corridor Section Editor dock: design the cross-section on the real ground, by station, and build the corridor.

The editor works on an existing LandXML alignment and design profile. It never edits the source file: Build writes a
new LandXML (alignment copied unchanged, CrossSects, top and datum TINs, Civil 3D feature lines), an HTML report and,
optionally, a bill of quantities in Excel. Standards give defaults and advisory checks only; every value is editable.
"""

from __future__ import annotations

import copy
import json
import os

from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsCsException,
    QgsFeature,
    QgsGeometry,
    QgsLineString,
    QgsPoint,
    QgsPointXY,
    QgsProject,
    QgsSettings,
    QgsVectorLayer,
)
from qgis.gui import QgsDockWidget, QgsMapToolEmitPoint, QgsProjectionSelectionWidget, QgsVertexMarker
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..landxml import assembly as asm
from ..landxml import corridor_build as cb
from ..landxml import corridor_quantities as cq
from ..landxml import corridor_features as cf
from ..landxml.section_standards import list_section_standards, load_section_standard, save_user_standard
from .section_editor_chart import SectionEditorChart, TimelineStrip

SETTINGS = "landxml_road_terrain/section_editor"
AXIS_CHOICES = ["Choose the axis order of the file…", "Northing first (N E Z, as Civil 3D writes)", "Easting first (E N Z)"]
UNDO_LIMIT = 200


def parse_bands(text):
    """'1:4 to 1; 1:3 to 2; 1:1.5' -> [[1, 4], [2, 3], [None, 1.5]] (heights in file units, ratios H per 1 V)."""
    out = []
    for part in (text or "").replace(",", ";").split(";"):
        part = part.strip()
        if not part:
            continue
        ratio, _sep, height = part.partition("to")
        ratio = ratio.strip()
        value = float(ratio.split(":")[1]) if ":" in ratio else float(ratio)
        out.append([float(height) if height.strip() else None, value])
    return out


def bands_text(bands):
    return "; ".join(f"1:{r:g}" + (f" to {h:g}" if h is not None else "") for h, r in bands or [])


class SectionEditorDock(QgsDockWidget):
    built = pyqtSignal(str, str)                 # output LandXML path, alignment name

    def __init__(self, iface=None, parent=None):
        super().__init__("Corridor Section Editor", parent)
        self.setObjectName("LandXMLCorridorSectionEditor")
        self.iface = iface
        self.canvas = iface.mapCanvas() if iface is not None else None
        self.project = None
        self.context = None
        self.project_path = None
        self.station = 0.0
        self.side = "right"
        self.assembly_name = None
        self.selected = None
        self.undo_stack, self.redo_stack = [], []
        self.section = None
        self.marker = None
        self.tool = None
        self.preview_layer = None
        self._loading = False
        self._build()

    # ================================================================== layout
    def _build(self):
        body = QWidget()
        outer = QVBoxLayout(body)
        outer.setContentsMargins(4, 4, 4, 4)
        top = QGridLayout()
        self.file_edit = QLineEdit()
        self.file_edit.setPlaceholderText("LandXML with the alignment and its design profile")
        browse = QToolButton()
        browse.setText("…")
        browse.clicked.connect(self.browse)
        self.alignment_combo = QComboBox()
        self.alignment_combo.currentIndexChanged.connect(self._alignment_changed)
        self.profile_combo = QComboBox()
        self.axis_combo = QComboBox()
        self.axis_combo.addItems(AXIS_CHOICES)
        self.ground_combo = QComboBox()
        self.ground_combo.setToolTip("Existing ground: a TIN surface in this file, a DEM (same CRS as the file), or none")
        self.crs_widget = QgsProjectionSelectionWidget()
        self.crs_widget.setToolTip("The CRS of the file's coordinates, used to show the section position on the map "
                                   "(never inferred)")
        load = QPushButton("Load")
        load.clicked.connect(self.load_context)
        top.addWidget(QLabel("LandXML"), 0, 0)
        top.addWidget(self.file_edit, 0, 1, 1, 3)
        top.addWidget(browse, 0, 4)
        top.addWidget(QLabel("Alignment"), 0, 5)
        top.addWidget(self.alignment_combo, 0, 6)
        top.addWidget(QLabel("Profile"), 0, 7)
        top.addWidget(self.profile_combo, 0, 8)
        top.addWidget(self.axis_combo, 1, 0, 1, 3)
        top.addWidget(QLabel("Ground"), 1, 3)
        top.addWidget(self.ground_combo, 1, 4, 1, 2)
        top.addWidget(QLabel("File CRS"), 1, 6)
        top.addWidget(self.crs_widget, 1, 7)
        top.addWidget(load, 1, 8)
        self.standard_combo = QComboBox()
        for sid, name, _path in list_section_standards():
            self.standard_combo.addItem(name, sid)
        self.standard_combo.currentIndexChanged.connect(self._standard_changed)
        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setRange(5, 200)
        self.speed_spin.setValue(80)
        self.speed_spin.setSuffix(" design speed")
        self.speed_spin.valueChanged.connect(lambda v: self._set_project("design_speed", float(v)))
        self.template_combo = QComboBox()
        add_tpl = QPushButton("New assembly from template")
        add_tpl.clicked.connect(self.add_template_assembly)
        buttons = QHBoxLayout()
        self.target_combo = QComboBox()
        self.target_combo.addItem("For Civil 3D", "civil3d")
        self.target_combo.addItem("For OpenRoads", "openroads")
        self.target_combo.setToolTip("How Build and Export write the feature lines: Civil 3D feature lines, or OpenRoads "
                                     "surface breaklines and plain PlanFeatures")
        self.export_button = QToolButton()
        self.export_button.setText("Export stage…")
        self.export_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        from qgis.PyQt.QtWidgets import QMenu
        menu = QMenu(self.export_button)
        for key, label in cb.STAGE_LABELS.items():
            menu.addAction(label, lambda key=key: self.export_stage(key))
        self.export_button.setMenu(menu)
        buttons.addWidget(self.target_combo)
        buttons.addWidget(self.export_button)
        for text, slot, tip in (("Open…", self.open_project, "Open a section project (JSON)"),
                                ("Save…", self.save_project, "Save the section project (JSON)"),
                                ("Undo", self.undo, "Undo (every edit and drag is one step)"),
                                ("Redo", self.redo, "Redo"),
                                ("Preview on map", self.preview_on_map, "Show the edges as a temporary map layer"),
                                ("Build…", self.build, "Write the corridor LandXML, report and bill of quantities"),
                                ("Guide", self._guide, "Open the user guide at the Section Editor")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            buttons.addWidget(b)
        top.addWidget(QLabel("Standard"), 2, 0)
        top.addWidget(self.standard_combo, 2, 1, 1, 2)
        top.addWidget(self.speed_spin, 2, 3)
        top.addWidget(self.template_combo, 2, 4, 1, 2)
        top.addWidget(add_tpl, 2, 6)
        outer.addLayout(top)
        outer.addLayout(buttons)

        split = QSplitter(Qt.Orientation.Horizontal)
        # ---- palette
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("Elements (double-click to add after the selected one)"))
        self.palette = QListWidget()
        for kind in ("lane", "shoulder", "kerb", "sidewalk", "cycle_track", "verge", "side_drain", "barrier", "custom",
                     "daylight"):
            item = QListWidgetItem(asm.LABELS[kind])
            item.setData(Qt.ItemDataRole.UserRole, kind)
            self.palette.addItem(item)
        self.palette.itemDoubleClicked.connect(lambda item: self.add_element(item.data(Qt.ItemDataRole.UserRole)))
        lv.addWidget(self.palette)
        median_btn = QPushButton("Add / remove median")
        median_btn.clicked.connect(self.toggle_median)
        lv.addWidget(median_btn)
        mirror = QPushButton("Mirror right to left")
        mirror.clicked.connect(lambda: self.mirror(True))
        unmirror = QPushButton("Edit left separately")
        unmirror.clicked.connect(lambda: self.mirror(False))
        lv.addWidget(mirror)
        lv.addWidget(unmirror)
        save_std = QPushButton("Save as standard…")
        save_std.setToolTip("Save the current standard with this project's assemblies as templates and its build-ups")
        save_std.clicked.connect(self.save_as_standard)
        lv.addWidget(save_std)
        split.addWidget(left)
        # ---- centre: chart, station, timeline
        centre = QWidget()
        cv = QVBoxLayout(centre)
        cv.setContentsMargins(0, 0, 0, 0)
        self.chart = SectionEditorChart()
        self.chart.elementClicked.connect(self._element_clicked)
        self.chart.pointClicked.connect(self._point_clicked)
        self.chart.widthDragged.connect(lambda side, eid, w: self._drag_value(side, eid, "width", w))
        self.chart.fallDragged.connect(self._fall_dragged)
        self.chart.dragFinished.connect(self._drag_finished)
        cv.addWidget(self.chart, 1)
        row = QHBoxLayout()
        prev = QToolButton()
        prev.setText("◀")
        prev.clicked.connect(lambda: self.step(-1))
        nxt = QToolButton()
        nxt.setText("▶")
        nxt.clicked.connect(lambda: self.step(1))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.valueChanged.connect(self._slider_moved)
        self.station_spin = QDoubleSpinBox()
        self.station_spin.setDecimals(3)
        self.station_spin.setRange(-1e9, 1e9)
        self.station_spin.editingFinished.connect(lambda: self.set_station(self.station_spin.value()))
        self.exag_spin = QDoubleSpinBox()
        self.exag_spin.setRange(0.5, 50)
        self.exag_spin.setValue(2.0)
        self.exag_spin.setPrefix("V:H ")
        self.exag_spin.valueChanged.connect(self._exaggeration)
        fit = QToolButton()
        fit.setText("Fit")
        fit.clicked.connect(self.chart.fit)
        self.pick_button = QToolButton()
        self.pick_button.setText("Pick on map")
        self.pick_button.setCheckable(True)
        self.pick_button.toggled.connect(self.set_picking)
        for w in (prev, self.slider, nxt, self.station_spin, self.exag_spin, fit, self.pick_button):
            row.addWidget(w)
        cv.addLayout(row)
        self.timeline = TimelineStrip()
        self.timeline.stationPicked.connect(self.set_station)
        cv.addWidget(self.timeline)
        split.addWidget(centre)
        # ---- right: tabs
        self.tabs = QTabWidget()
        self.tabs.addTab(self._element_tab(), "Element")
        self.tabs.addTab(self._regions_tab(), "Regions")
        self.tabs.addTab(self._buildups_tab(), "Build-ups")
        self.tabs.addTab(self._features_tab(), "Features")
        self.tabs.addTab(self._settings_tab(), "Settings")
        self.checks_list = QListWidget()
        self.tabs.addTab(self.checks_list, "Checks")
        self.why_text = QPlainTextEdit()
        self.why_text.setReadOnly(True)
        self.why_text.setPlaceholderText("Click a point on the section to see why it is there.")
        self.tabs.addTab(self.why_text, "Why this point")
        self.qty_table = QTableWidget(0, 3)
        self.qty_table.setHorizontalHeaderLabels(["Quantity at this station", "Value", "Unit"])
        self.qty_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tabs.addTab(self.qty_table, "Per metre")
        split.addWidget(self.tabs)
        left.setMaximumWidth(200)
        self.tabs.setMinimumWidth(300)
        self.tabs.setMaximumWidth(460)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        split.setSizes([170, 900, 380])
        outer.addWidget(split, 1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        self.setWidget(body)

    def _element_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        row = QHBoxLayout()
        self.assembly_combo = QComboBox()
        self.assembly_combo.currentIndexChanged.connect(self._assembly_changed)
        rename = QToolButton()
        rename.setText("Rename")
        rename.clicked.connect(self.rename_assembly)
        dup = QToolButton()
        dup.setText("Copy")
        dup.clicked.connect(self.copy_assembly)
        row.addWidget(QLabel("Assembly"))
        row.addWidget(self.assembly_combo, 1)
        row.addWidget(rename)
        row.addWidget(dup)
        v.addLayout(row)
        row = QHBoxLayout()
        self.side_combo = QComboBox()
        self.side_combo.addItems(["Right", "Left", "Median"])
        self.side_combo.currentIndexChanged.connect(self._side_changed)
        row.addWidget(QLabel("Side"))
        row.addWidget(self.side_combo)
        for text, slot in (("▲", lambda: self.move_element(-1)), ("▼", lambda: self.move_element(1)),
                           ("Remove", self.remove_element)):
            b = QToolButton()
            b.setText(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        v.addLayout(row)
        self.element_list = QListWidget()
        self.element_list.currentRowChanged.connect(self._element_row)
        self.element_list.setMaximumHeight(170)
        v.addWidget(self.element_list)
        self.props_area = QScrollArea()
        self.props_area.setWidgetResizable(True)
        self.props_holder = QWidget()
        self.props_form = QFormLayout(self.props_holder)
        self.props_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.props_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.props_area.setWidget(self.props_holder)
        v.addWidget(self.props_area, 1)
        return w

    def _regions_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("Assemblies by station range; a transition blends from the previous region's assembly "
                           "over its length (elements matched by id; new ones grow from zero width)."))
        self.region_table = QTableWidget(0, 4)
        self.region_table.setHorizontalHeaderLabels(["From", "To", "Assembly", "Transition"])
        self.region_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.region_table.itemChanged.connect(self._regions_edited)
        v.addWidget(self.region_table)
        row = QHBoxLayout()
        add = QPushButton("Split at this station")
        add.clicked.connect(self.split_region)
        rem = QPushButton("Remove region")
        rem.clicked.connect(self.remove_region)
        row.addWidget(add)
        row.addWidget(rem)
        v.addLayout(row)
        return w

    def _buildups_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("Named pavement build-ups: courses from the top down (thickness in file units; unit m2 or "
                           "m3 for the bill). Elements refer to a build-up by name."))
        self.buildup_table = QTableWidget(0, 5)
        self.buildup_table.setHorizontalHeaderLabels(["Build-up", "Course", "Material", "Thickness", "Unit"])
        self.buildup_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.buildup_table.itemChanged.connect(self._buildups_edited)
        v.addWidget(self.buildup_table)
        row = QHBoxLayout()
        add = QPushButton("Add course")
        add.clicked.connect(self.add_course)
        rem = QPushButton("Remove course")
        rem.clicked.connect(self.remove_course)
        row.addWidget(add)
        row.addWidget(rem)
        v.addLayout(row)
        return w

    def _features_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("Bays, lanes, ramps and junction pieces placed by chainage; they build themselves into the "
                           "sections (tapers, kerbs, narrowed lanes, drains stopped at mouths)."))
        row = QHBoxLayout()
        self.feature_type = QComboBox()
        self.feature_types = cf.load_feature_types()
        for key, spec in self.feature_types["types"].items():
            self.feature_type.addItem(spec["label"], key)
        add = QPushButton("Add at this station")
        add.clicked.connect(self.add_feature)
        row.addWidget(self.feature_type, 1)
        row.addWidget(add)
        v.addLayout(row)
        row = QHBoxLayout()
        self.keep_combo = QComboBox()
        self.keep_combo.addItem("Traffic keeps left", "left")
        self.keep_combo.addItem("Traffic keeps right", "right")
        self.keep_combo.currentIndexChanged.connect(lambda _i: self._set_path(("traffic_keeps",), self.keep_combo.currentData()))
        rem = QPushButton("Remove feature")
        rem.clicked.connect(self.remove_feature)
        row.addWidget(self.keep_combo)
        row.addWidget(rem)
        v.addLayout(row)
        self.feature_list = QListWidget()
        self.feature_list.setMaximumHeight(140)
        self.feature_list.currentRowChanged.connect(self._feature_row)
        v.addWidget(self.feature_list)
        self.feature_remark = QLabel("")
        self.feature_remark.setWordWrap(True)
        v.addWidget(self.feature_remark)
        holder = QWidget()
        self.feature_form = QFormLayout(holder)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(holder)
        v.addWidget(area, 1)
        return w

    def add_feature(self):
        if self.project is None:
            return
        kind = self.feature_type.currentData()
        at = round(self.station, 1)

        def change(p):
            p.setdefault("features", []).append(cf.new_feature(kind, at, types=self.feature_types))
            cf.assign_ids(p["features"])
        self._apply(change)
        self.feature_list.setCurrentRow(self.feature_list.count() - 1)

    def remove_feature(self):
        row = self.feature_list.currentRow()
        if row >= 0:
            self._apply(lambda p: p["features"].pop(row))

    def _fill_features(self):
        row = self.feature_list.currentRow()
        self.feature_list.blockSignals(True)
        self.feature_list.clear()
        for f in (self.project or {}).get("features", []):
            spec = self.feature_types["types"].get(f["type"], {})
            self.feature_list.addItem(f"{spec.get('label', f['type'])} at {float(f['at']):.1f} ({f.get('side', '')})")
        self.feature_list.blockSignals(False)
        if 0 <= row < self.feature_list.count():
            self.feature_list.setCurrentRow(row)
        index = self.keep_combo.findData((self.project or {}).get("traffic_keeps", "left"))
        self.keep_combo.blockSignals(True)
        self.keep_combo.setCurrentIndex(max(index, 0))
        self.keep_combo.blockSignals(False)

    def _feature_row(self, row):
        while self.feature_form.rowCount():
            self.feature_form.removeRow(0)
        features = (self.project or {}).get("features", [])
        if not 0 <= row < len(features):
            self.feature_remark.setText("")
            return
        f = features[row]
        spec = self.feature_types["types"].get(f["type"], {})
        self.feature_remark.setText(f"<b>{spec.get('label', '')}</b> ({spec.get('ref', '')}): {spec.get('remark', '')}")
        for key, value in f.items():
            if key in ("type", "id"):
                continue
            if key == "side":
                widget = QComboBox()
                options = ["left", "right", "uphill", "downhill"] if spec.get("insert") != "centre" else ["both"]
                widget.addItems(options)
                widget.setCurrentText(str(value))
                widget.currentTextChanged.connect(lambda v, r=row, k=key: self._set_feature(r, k, v))
            elif isinstance(value, bool):
                widget = QCheckBox()
                widget.setChecked(value)
                widget.toggled.connect(lambda v, r=row, k=key: self._set_feature(r, k, bool(v)))
            elif isinstance(value, (int, float)):
                widget = QDoubleSpinBox()
                widget.setRange(-1e7, 1e7)
                widget.setDecimals(2)
                widget.setValue(float(value))
                widget.editingFinished.connect(lambda w=widget, r=row, k=key: self._set_feature(r, k, float(w.value())))
            elif key == "material":
                widget = QComboBox()
                widget.addItems([m for m in self.feature_types.get("materials", {}) if m != "ref"])
                widget.setCurrentText(str(value))
                widget.currentTextChanged.connect(lambda v, r=row, k=key: self._set_feature(r, k, v))
            else:
                widget = QLineEdit(str(value))
                widget.editingFinished.connect(lambda w=widget, r=row, k=key: self._set_feature(r, k, w.text()))
            self.feature_form.addRow(key.replace("_", " "), widget)

    def _set_feature(self, row, key, value):
        features = (self.project or {}).get("features", [])
        if 0 <= row < len(features) and features[row].get(key) != value:
            self._apply(lambda p: p["features"][row].__setitem__(key, value))
            if key == "at":
                self.set_station(float(value))

    def _settings_tab(self):
        w = QWidget()
        f = QFormLayout(w)
        self.set_widgets = {}

        def combo(key, items, path):
            c = QComboBox()
            for value, label in items:
                c.addItem(label, value)
            c.currentIndexChanged.connect(lambda _i, c=c, path=path: self._set_path(path, c.currentData()))
            self.set_widgets[path] = c
            f.addRow(key, c)

        def spin(key, path, low, high, decimals=3, suffix=""):
            s = QDoubleSpinBox()
            s.setRange(low, high)
            s.setDecimals(decimals)
            s.setSuffix(suffix)
            s.editingFinished.connect(lambda s=s, path=path: self._set_path(path, float(s.value())))
            self.set_widgets[path] = s
            f.addRow(key, s)

        combo("Superelevation", [("landxml", "From the LandXML"), ("standard", "Computed from the standard"),
                                 ("none", "None (normal crossfall)")], ("superelevation", "source"))
        spin("emax", ("superelevation", "emax"), 0, 20, 1, " %")
        spin("Normal crown", ("superelevation", "normal_crown"), 0, 10, 2, " %")
        combo("Rotation axis", [("centreline", "Centreline"), ("inside_edge", "Inside edge"),
                                ("outside_edge", "Outside edge")], ("superelevation", "rotation"))
        spin("Runoff on the tangent (no spiral)", ("superelevation", "runoff_on_tangent"), 0, 1, 2)
        combo("Shoulders on superelevation", [("own", "Keep their own crossfall"), ("follow_low", "Low side follows the lane"),
                                              ("breakover", "Breakover limit on the high side"), ("follow", "Follow the lane")],
              ("superelevation", "shoulder_rule"))
        spin("Breakover limit", ("superelevation", "breakover_max"), 0, 20, 1, " %")
        combo("Curve widening", [("standard", "From the standard"), ("none", "None")], ("widening", "source"))
        combo("Widening on", [("inside", "Inside of the curve"), ("both", "Both sides (half each)")], ("widening", "placement"))
        spin("Section interval", ("stations", "interval"), 0.5, 1000, 2)
        spin("Ground sampling step", ("ground_step",), 0.05, 20, 2)
        spin("Topsoil strip depth", ("earthworks", "topsoil"), 0, 5, 3)
        spin("Unsuitable material depth", ("earthworks", "unsuitable"), 0, 10, 3)
        combo("Unsuitable under", [("fill", "Fill only"), ("footprint", "The whole footprint")], ("earthworks", "unsuitable_under"))
        c = QCheckBox("Courses run out to the side slope")
        c.toggled.connect(lambda v: self._set_path(("course_daylight",), bool(v)))
        self.set_widgets[("course_daylight",)] = c
        f.addRow(c)
        return w

    # ================================================================== file / context
    def browse(self):
        start = QgsSettings().value(f"{SETTINGS}/folder", "")
        path, _ = QFileDialog.getOpenFileName(self, "LandXML", start, "LandXML (*.xml *.landxml)")
        if path:
            QgsSettings().setValue(f"{SETTINGS}/folder", os.path.dirname(path))
            self.set_file(path)

    def set_file(self, path):
        from ..core import list_surfaces
        from ..landxml.geometry import read_alignments

        self.file_edit.setText(path)
        self._loading = True
        try:
            records, _unsupported = read_alignments(path, segment_length=5.0)
            self.alignment_combo.clear()
            for r in records:
                self.alignment_combo.addItem(r["name"])
            self.ground_combo.clear()
            for name, _node in list_surfaces(path):
                self.ground_combo.addItem(f"Surface: {name}", ("tin", path, name))
            self.ground_combo.addItem("Surface in another LandXML (Civil 3D or OpenRoads)…", ("other", "", ""))
            self.ground_combo.addItem("DEM…", ("dem", "", ""))
            self.ground_combo.addItem("No ground", ("none", "", ""))
        except (OSError, ValueError) as error:
            self._message(f"Cannot read the file: {error}", Qgis.MessageLevel.Warning)
        finally:
            self._loading = False
        self._alignment_changed()

    def _alignment_changed(self):
        if self._loading:
            return
        from ..landxml.parser import load_document

        self.profile_combo.clear()
        path = self.file_edit.text()
        if not path or not self.alignment_combo.currentText():
            return
        try:
            node = cb.alignment_element(load_document(path).root, self.alignment_combo.currentText())
            for p in cb.design_profiles(node):
                self.profile_combo.addItem(p.attrib.get("name", "?"))
        except ValueError as error:
            self._message(str(error), Qgis.MessageLevel.Warning)

    def _ground_spec(self):
        kind, path, name = self.ground_combo.currentData() or ("none", "", "")
        if kind == "dem":
            dem, _ = QFileDialog.getOpenFileName(self, "DEM (same CRS as the LandXML)", "", "Rasters (*.tif *.tiff *.img *.vrt);;All (*)")
            if not dem:
                return None
            return {"type": "dem", "path": dem}
        if kind == "other":
            from ..core import list_surfaces

            other, _ = QFileDialog.getOpenFileName(self, "LandXML with the ground surface", "", "LandXML (*.xml *.landxml)")
            if not other:
                return None
            names = [n for n, _node in list_surfaces(other)]
            if not names:
                self._message("That file has no surface.", Qgis.MessageLevel.Warning)
                return None
            name, ok = QInputDialog.getItem(self, "Ground surface", "Surface", names, 0, False)
            return {"type": "tin", "path": other, "surface": name} if ok else None
        if kind == "tin":
            return {"type": "tin", "path": path, "surface": name}
        return {"type": "none"}

    def load_context(self):
        if self.axis_combo.currentIndex() == 0:
            self._message("Choose the axis order of the file (it is never guessed).", Qgis.MessageLevel.Warning)
            return
        path = self.file_edit.text()
        if not path:
            self._message("Choose the LandXML file.", Qgis.MessageLevel.Warning)
            return
        ground = self._ground_spec()
        if ground is None:
            return
        northing_first = self.axis_combo.currentIndex() == 1
        if self.project is None or self.project.get("landxml") != path:
            std = self.standard_combo.currentData() or "generic_sections"
            template = self.template_combo.currentText() or None
            self.project = cb.new_project(path, self.alignment_combo.currentText(), self.profile_combo.currentText(), std,
                                          template, northing_first, self.speed_spin.value(), ground)
            self.undo_stack, self.redo_stack = [], []
        else:
            self.project.update(alignment=self.alignment_combo.currentText(), profile=self.profile_combo.currentText(),
                                northing_first=northing_first, ground=ground)
        self._prepare()

    def _prepare(self):
        try:
            self.context = cb.prepare(self.project)
        except (OSError, ValueError) as error:
            self.context = None
            self._message(str(error), Qgis.MessageLevel.Warning)
            self.chart.message = str(error)
            self.chart.set_section(None)
            return
        frame = self.context["frame"]
        self.station = min(max(self.station, frame.start), frame.end) if self.station else frame.start
        self.station_spin.setRange(frame.start, frame.end)
        names = list(self.project["assemblies"])
        self.assembly_name = self.assembly_name if self.assembly_name in names else (names[0] if names else None)
        for w in self.context["warnings"]:
            self._message(w, Qgis.MessageLevel.Warning)
        self._sync_ui()
        self.redraw(keep_view=False)

    # ================================================================== project edits (undoable)
    def _snapshot(self):
        if self.project is not None:
            self.undo_stack.append(json.dumps(self.project))
            self.undo_stack = self.undo_stack[-UNDO_LIMIT:]
            self.redo_stack = []

    def _apply(self, change, refresh=True):
        """Run ``change(project)`` as one undo step, then refresh the context and redraw."""
        if self.project is None:
            return
        self._snapshot()
        change(self.project)
        if refresh:
            self._refresh()

    def _refresh(self, keep_view=True):
        if self.context is None:
            return
        try:
            cb.refresh_context(self.context)
        except ValueError as error:
            self._message(str(error), Qgis.MessageLevel.Warning)
            return
        self._sync_ui(keep_props=True)
        self.redraw(keep_view=keep_view)

    def undo(self):
        if not self.undo_stack:
            return
        self.redo_stack.append(json.dumps(self.project))
        self._replace_project(json.loads(self.undo_stack.pop()))

    def redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append(json.dumps(self.project))
        self._replace_project(json.loads(self.redo_stack.pop()))

    def _replace_project(self, project):
        self.project.clear()
        self.project.update(project)
        self.context["project"] = self.project
        self._refresh()

    def _set_project(self, key, value):
        if self.project is not None and self.project.get(key) != value:
            self._apply(lambda p: p.__setitem__(key, value))

    def _set_path(self, path, value):
        if self.project is None or self._loading:
            return
        target = self.project
        for key in path[:-1]:
            target = target.setdefault(key, {})
        if target.get(path[-1]) == value:
            return

        def change(p):
            t = p
            for key in path[:-1]:
                t = t.setdefault(key, {})
            t[path[-1]] = value
        self._apply(change)

    # ---- assemblies and elements
    def _assembly(self, project=None):
        project = project or self.project
        return project["assemblies"].get(self.assembly_name) if project and self.assembly_name else None

    def _items(self, project=None, side=None):
        a = self._assembly(project)
        side = side or self.side
        if a is None:
            return None
        if side == "median":
            return [a["median"]] if a.get("median") else []
        if side == "left" and a.get("left") in (None, "mirror"):
            return a["right"]
        return a.setdefault(side, [])

    def _ensure_ids(self, items):
        asm.assign_ids({"right": items})

    def add_element(self, kind):
        if self._assembly() is None or self.side == "median":
            return
        row = self.element_list.currentRow()

        def change(p):
            items = self._items(p)
            new = asm.element(kind)
            new["id"] = ""
            position = row + 1 if row >= 0 else len(items)
            if kind != "daylight" and items and items[-1]["type"] == "daylight" and position >= len(items):
                position = len(items) - 1                   # keep the daylight last
            items.insert(position, new)
            self._ensure_ids(items)
        self._apply(change)
        self.element_list.setCurrentRow(row + 1 if row >= 0 else self.element_list.count() - 1)

    def remove_element(self):
        row = self.element_list.currentRow()
        if row < 0 or self.side == "median":
            return
        self._apply(lambda p: self._items(p).pop(row))

    def move_element(self, step):
        row = self.element_list.currentRow()
        if row < 0 or self.side == "median":
            return
        items = self._items()
        other = row + step
        if not 0 <= other < len(items):
            return

        def change(p):
            its = self._items(p)
            its[row], its[other] = its[other], its[row]
        self._apply(change)
        self.element_list.setCurrentRow(other)

    def toggle_median(self):
        if self._assembly() is None:
            return

        def change(p):
            a = self._assembly(p)
            a["median"] = None if a.get("median") else asm.element("median", id="median")
            asm.assign_ids(a)
        self._apply(change)

    def mirror(self, on):
        if self._assembly() is None:
            return

        def change(p):
            a = self._assembly(p)
            a["left"] = "mirror" if on else copy.deepcopy(a["right"])
            asm.assign_ids(a)
        self._apply(change)

    def add_template_assembly(self):
        if self.project is None:
            self._message("Load the alignment first.", Qgis.MessageLevel.Warning)
            return
        name = self.template_combo.currentText()
        if not name:
            return
        std = load_section_standard(self.project["standard"])
        label = name
        k = 2
        while label in self.project["assemblies"]:
            label = f"{name} ({k})"
            k += 1
        self._apply(lambda p: cb.add_template(p, std, name, label))
        self.assembly_name = label
        self._sync_ui()

    def rename_assembly(self):
        if self.assembly_name is None:
            return
        name, ok = QInputDialog.getText(self, "Rename assembly", "Name", text=self.assembly_name)
        if not ok or not name or name in self.project["assemblies"]:
            return
        old = self.assembly_name

        def change(p):
            p["assemblies"][name] = p["assemblies"].pop(old)
            for r in p["regions"]:
                if r["assembly"] == old:
                    r["assembly"] = name
        self.assembly_name = name
        self._apply(change)

    def copy_assembly(self):
        if self.assembly_name is None:
            return
        name = f"{self.assembly_name} copy"
        self._apply(lambda p: p["assemblies"].__setitem__(name, copy.deepcopy(p["assemblies"][self.assembly_name])))
        self.assembly_name = name
        self._sync_ui()

    def _standard_changed(self):
        sid = self.standard_combo.currentData()
        try:
            std = load_section_standard(sid)
        except ValueError:
            return
        self.template_combo.clear()
        self.template_combo.addItems(list(std.templates()))
        if self.project is not None and self.project.get("standard") != sid and not self._loading:
            self._apply(lambda p: p.__setitem__("standard", sid))

    def save_as_standard(self):
        if self.project is None:
            return
        std = load_section_standard(self.project["standard"])
        data = std.as_dict()
        name, ok = QInputDialog.getText(self, "Save as standard", "Name of the new standard",
                                        text=f"{std.name} (my templates)")
        if not ok or not name:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save standard", "", "JSON (*.json)")
        if not path:
            return
        data["id"] = os.path.splitext(os.path.basename(path))[0]
        data["name"] = name
        data["buildups"] = copy.deepcopy(self.project.get("buildups", {}))
        for aname, a in self.project["assemblies"].items():
            data.setdefault("templates", {})[aname] = {"ref": "User template", "median": a.get("median"),
                                                       "right": a["right"], "left": a.get("left", "mirror")}
        save_user_standard(data, path)
        self._message(f"Saved the standard to {path}. Pick it with 'Open…' in a project or add the folder to the list.")

    # ---- element list and properties
    def _side_changed(self, index):
        self.side = ["right", "left", "median"][index]
        self._fill_elements()

    def _assembly_changed(self, _index):
        if self._loading:
            return
        self.assembly_name = self.assembly_combo.currentText() or None
        self._fill_elements()

    def _fill_elements(self, keep_row=False):
        row = self.element_list.currentRow()
        self.element_list.blockSignals(True)
        self.element_list.clear()
        for k, e in enumerate(self._items() or []):
            label = e.get("name") or asm.LABELS.get(e["type"], e["type"])
            extra = f"  {float(e['width']):g}" if "width" in e and e["type"] != "median" else ""
            self.element_list.addItem(f"{k + 1}. {label}{extra}")
        self.element_list.blockSignals(False)
        if keep_row and 0 <= row < self.element_list.count():
            self.element_list.setCurrentRow(row)
        elif self.element_list.count():
            self.element_list.setCurrentRow(0)
        else:
            self._fill_props(None)

    def _element_row(self, row):
        items = self._items() or []
        element = items[row] if 0 <= row < len(items) else None
        self.selected = (self.side, element.get("id")) if element else None
        self.chart.selected = self.selected
        self.chart.update()
        self._fill_props(element)

    def _element_clicked(self, side, eid):
        index = {"right": 0, "left": 1, "median": 2}[side]
        if self.side_combo.currentIndex() != index:
            self.side_combo.setCurrentIndex(index)
        items = self._items() or []
        row = next((k for k, e in enumerate(items) if e.get("id") == eid), -1)
        if row >= 0:
            self.element_list.setCurrentRow(row)

    def _fill_props(self, element):
        while self.props_form.rowCount():
            self.props_form.removeRow(0)
        if element is None:
            return
        full = asm.complete(element)
        std = self.context["standard"] if self.context else None
        unit = "m" if not self.context or self.context["unit"] == "meter" else "ft"
        self.props_form.addRow(QLabel(f"<b>{asm.LABELS.get(element['type'], element['type'])}</b> "
                                      f"(id {element.get('id', '')})"))
        for key, default, kind, label in asm.SCHEMA[element["type"]]:
            value = full.get(key, default)
            widget = None
            if kind in ("length", "percent", "ratio"):
                widget = QDoubleSpinBox()
                widget.setDecimals(3)
                widget.setRange(-1e6 if kind == "percent" else 0.0, 1e6)
                widget.setSuffix({"length": f" {unit}", "percent": " %", "ratio": " H:1V"}[kind])
                widget.setValue(float(value or 0.0))
                limit = std.limit(element["type"], key, full.get("kind")) if std else None
                if limit:
                    widget.setToolTip(f"{std.name}: {limit.get('min', '–')} to {limit.get('max', '–')} "
                                      f"({limit.get('ref', '')}). {limit.get('note', '')} Advisory only.")
                widget.editingFinished.connect(lambda w=widget, k=key: self._set_element(k, float(w.value())))
            elif kind == "bool":
                widget = QCheckBox()
                widget.setChecked(bool(value))
                widget.toggled.connect(lambda v, k=key: self._set_element(k, bool(v)))
            elif kind == "text":
                widget = QLineEdit(str(value or ""))
                widget.editingFinished.connect(lambda w=widget, k=key: self._set_element(k, w.text()))
            elif kind in ("buildup", "drain", "material") or isinstance(kind, list):
                widget = QComboBox()
                if isinstance(kind, list):
                    options = kind
                elif kind == "buildup":
                    options = [""] + list((self.project or {}).get("buildups", {}))
                elif kind == "drain":
                    options = [""] + (std.side_drain_names() if std else [])
                else:
                    options = (std.materials() if std else []) or ["earth"]
                for o in options:
                    widget.addItem(o or "(none)", o)
                index = widget.findData(value)
                widget.setCurrentIndex(max(index, 0))
                widget.currentIndexChanged.connect(lambda _i, w=widget, k=key: self._set_element(k, w.currentData()))
            elif kind == "bands":
                widget = QLineEdit(bands_text(value))
                widget.setPlaceholderText("e.g. 1:4 to 1; 1:3 to 2; 1:1.5   (empty = the standard's bands)")
                widget.editingFinished.connect(lambda w=widget, k=key: self._set_bands(k, w.text()))
            elif kind == "steps":
                widget = QLineEdit(json.dumps(value))
                widget.setToolTip("[[width, fall %, rise (or null), code], ...] - any shape you need")
                widget.editingFinished.connect(lambda w=widget, k=key: self._set_json(k, w.text()))
            if widget is not None:
                self.props_form.addRow(label, widget)

    def _set_element(self, key, value):
        row = self.element_list.currentRow()
        items = self._items() or []
        if not 0 <= row < len(items) or items[row].get(key) == value:
            return

        def change(p):
            target = self._items(p)[row]
            target[key] = value
            if target["type"] == "side_drain" and key in ("shape", "foreslope", "depth", "bottom", "backslope"):
                target[f"_set_{key}"] = True                  # a user value wins over the standard drain type
            if target["type"] == "side_drain" and key == "drain_type":
                for k in ("shape", "foreslope", "depth", "bottom", "backslope"):
                    target.pop(f"_set_{k}", None)
        self._apply(change)

    def _set_bands(self, key, text):
        try:
            bands = parse_bands(text)
        except ValueError:
            self._message("Bands are written like '1:4 to 1; 1:3 to 2; 1:1.5'.", Qgis.MessageLevel.Warning)
            return
        self._set_element(key, bands)

    def _set_json(self, key, text):
        try:
            self._set_element(key, json.loads(text))
        except ValueError:
            self._message("Not valid JSON.", Qgis.MessageLevel.Warning)

    # ---- drags from the chart
    def _drag_value(self, side, eid, key, value):
        items = self._items(side=side if side != "median" else "median") or []
        if side == "left" and (self._assembly() or {}).get("left") in (None, "mirror"):
            items = self._assembly()["right"]
        element = next((e for e in items if e.get("id") == eid), None)
        if element is None or element.get(key) == value:
            return
        if not getattr(self, "_dragging", False):
            self._snapshot()                 # one undo step for the whole drag
            self._dragging = True
        element[key] = value
        self._refresh()

    def _fall_dragged(self, side, eid, fall):
        items = self._items(side=side) or []
        element = next((e for e in items if e.get("id") == eid), None)
        if element is None:
            return
        key = "slope" if element["type"] == "verge" else "crossfall"
        if element["type"] in ("sidewalk", "cycle_track"):
            fall = -fall
        self._drag_value(side, eid, key, fall)

    def _drag_finished(self):
        self._dragging = False
        self._fill_elements(keep_row=True)

    # ---- regions
    def _fill_regions(self):
        self.region_table.blockSignals(True)
        self.region_table.setRowCount(0)
        for r in (self.project or {}).get("regions", []):
            row = self.region_table.rowCount()
            self.region_table.insertRow(row)
            for col, value in enumerate((r.get("start"), r.get("end"), r.get("assembly"), r.get("transition", 0))):
                text = "" if value is None else (f"{value:g}" if isinstance(value, (int, float)) else str(value))
                self.region_table.setItem(row, col, QTableWidgetItem(text))
        self.region_table.blockSignals(False)

    def _regions_edited(self, _item):
        regions = []
        try:
            for row in range(self.region_table.rowCount()):
                def cell(col):
                    item = self.region_table.item(row, col)
                    return item.text().strip() if item else ""
                start, end = cell(0), cell(1)
                regions.append({"start": float(start) if start else None, "end": float(end) if end else None,
                                "assembly": cell(2), "transition": float(cell(3) or 0)})
        except ValueError:
            self._message("Region stations and transitions must be numbers.", Qgis.MessageLevel.Warning)
            return
        self._apply(lambda p: p.__setitem__("regions", regions))

    def split_region(self):
        if self.context is None:
            return
        s = round(self.station, 3)

        def change(p):
            regions = cb.resolve_regions(p, self.context["frame"].start, self.context["frame"].end)
            out = []
            for r in regions:
                if r["start"] < s < r["end"]:
                    out.append(dict(r, end=s))
                    out.append(dict(r, start=s, transition=0.0))
                else:
                    out.append(r)
            p["regions"] = out
        self._apply(change)

    def remove_region(self):
        row = self.region_table.currentRow()
        if row < 0 or len(self.project.get("regions", [])) <= 1:
            return

        def change(p):
            removed = p["regions"].pop(row)
            if row > 0:
                p["regions"][row - 1]["end"] = removed["end"]
            elif p["regions"]:
                p["regions"][0]["start"] = removed["start"]
        self._apply(change)

    # ---- build-ups
    def _fill_buildups(self):
        self.buildup_table.blockSignals(True)
        self.buildup_table.setRowCount(0)
        for name, b in (self.project or {}).get("buildups", {}).items():
            for c in b.get("courses", []):
                row = self.buildup_table.rowCount()
                self.buildup_table.insertRow(row)
                for col, value in enumerate((name, c.get("name", ""), c.get("material", ""), c.get("thickness", 0),
                                             c.get("unit", "m3"))):
                    self.buildup_table.setItem(row, col, QTableWidgetItem(f"{value:g}" if isinstance(value, float) else str(value)))
        self.buildup_table.blockSignals(False)

    def _buildups_edited(self, _item):
        buildups = {}
        try:
            for row in range(self.buildup_table.rowCount()):
                def cell(col):
                    item = self.buildup_table.item(row, col)
                    return item.text().strip() if item else ""
                if not cell(0):
                    continue
                buildups.setdefault(cell(0), {"courses": []})["courses"].append(
                    {"name": cell(1) or "Course", "material": cell(2), "thickness": float(cell(3) or 0), "unit": cell(4) or "m3"})
        except ValueError:
            self._message("Thickness must be a number.", Qgis.MessageLevel.Warning)
            return
        self._apply(lambda p: p.__setitem__("buildups", buildups))

    def add_course(self):
        name = "Carriageway"
        row = self.buildup_table.currentRow()
        if row >= 0 and self.buildup_table.item(row, 0):
            name = self.buildup_table.item(row, 0).text()
        self._apply(lambda p: p.setdefault("buildups", {}).setdefault(name, {"courses": []})["courses"].append(
            {"name": "New course", "material": "", "thickness": 0.1, "unit": "m3"}))

    def remove_course(self):
        row = self.buildup_table.currentRow()
        if row < 0:
            return
        name = self.buildup_table.item(row, 0).text()
        course = self.buildup_table.item(row, 1).text()

        def change(p):
            b = p["buildups"].get(name, {"courses": []})
            b["courses"] = [c for c in b["courses"] if c.get("name") != course]
            if not b["courses"]:
                p["buildups"].pop(name, None)
        self._apply(change)

    # ================================================================== UI sync and drawing
    def _sync_ui(self, keep_props=False):
        if self.project is None:
            return
        self._loading = True
        try:
            index = self.standard_combo.findData(self.project.get("standard"))
            if index >= 0:
                self.standard_combo.setCurrentIndex(index)
            self.speed_spin.blockSignals(True)
            self.speed_spin.setValue(float(self.project.get("design_speed") or 80))
            self.speed_spin.blockSignals(False)
            self.assembly_combo.blockSignals(True)
            self.assembly_combo.clear()
            self.assembly_combo.addItems(list(self.project["assemblies"]))
            if self.assembly_name:
                self.assembly_combo.setCurrentText(self.assembly_name)
            self.assembly_combo.blockSignals(False)
            for path, widget in self.set_widgets.items():
                value = self.project
                for key in path:
                    value = (value or {}).get(key) if isinstance(value, dict) else None
                widget.blockSignals(True)
                if isinstance(widget, QComboBox):
                    i = widget.findData(value)
                    widget.setCurrentIndex(max(i, 0))
                elif isinstance(widget, QCheckBox):
                    widget.setChecked(bool(value))
                elif value is not None:
                    widget.setValue(float(value))
                widget.blockSignals(False)
        finally:
            self._loading = False
        self._fill_elements(keep_row=keep_props)
        self._fill_regions()
        self._fill_buildups()
        self._fill_features()
        if self.context:
            frame, sch = self.context["frame"], self.context["schedules"]
            self.timeline.set_data(frame.start, frame.end, self.context["regions"], sch.records, sch.widen,
                                   self.context.get("features", []))

    def redraw(self, keep_view=True):
        if self.context is None:
            return
        self.status.setText("")
        try:
            self.section = cb.section_at(self.context, self.station)
        except (ValueError, KeyError, IndexError) as error:
            self._message(f"Section at {self.station:.2f}: {error}", Qgis.MessageLevel.Warning)
            return
        self.chart.set_section(self.section, asm.element_spans(self.section), keep_view=keep_view)
        self.timeline.set_station(self.station)
        self.station_spin.blockSignals(True)
        self.station_spin.setValue(self.station)
        self.station_spin.blockSignals(False)
        frame = self.context["frame"]
        self.slider.blockSignals(True)
        self.slider.setValue(int(1000 * (self.station - frame.start) / max(frame.end - frame.start, 1e-9)))
        self.slider.blockSignals(False)
        self._update_checks()
        self._update_quantities()
        self._show_marker()

    def _update_checks(self):
        self.checks_list.clear()
        a = self.context["assemblies"].get(self.section["region"]["assembly"])
        for c in asm.check_assembly(a, self.context["standard"], self.project.get("design_speed")) if a else []:
            item = QListWidgetItem(f"{c['side']}: {c['message']}")
            item.setForeground(QColor("#9a5b00"))
            self.checks_list.addItem(item)
        for w in self.section["warnings"] + self.context.get("refresh_warnings", []):
            item = QListWidgetItem(w)
            item.setForeground(QColor("#a00000"))
            self.checks_list.addItem(item)
        if not self.checks_list.count():
            self.checks_list.addItem("No departures from the standard at this station.")
        self.tabs.setTabText(5, f"Checks ({self.checks_list.count()})" if self.checks_list.count() > 1 else "Checks")

    def _update_quantities(self):
        from ..landxml.earthworks import section_areas
        import numpy as np

        rows = []
        unit = "m" if self.context["unit"] == "meter" else "ft"
        g = self.section.get("ground")
        if g is not None:
            offs, zs = g
            ok = np.isfinite(zs)
            areas = section_areas(self.section["datum"], list(zip(offs[ok], zs[ok]))) if ok.sum() > 1 else None
            if areas:
                rows += [("Cut (to the datum)", areas[0], f"{unit}²"), ("Fill", areas[1], f"{unit}²")]
        totals = {}
        for s in self.section["shapes"]:
            key = f"{s['name']} ({s['element']}, {s['side']})"
            totals[key] = totals.get(key, 0.0) + s["area"]
        rows += [(k, v, f"{unit}²") for k, v in totals.items()]
        top = self.section["top"]
        rows.append(("Footprint width", top[-1].d - top[0].d, unit))
        for side in asm.SIDES:
            dl = self.section["sides"][side]["daylight"]
            if dl.get("type") not in (None, "none"):
                rows.append((f"{side.title()} {dl['type']} height", dl.get("height", 0.0), unit))
        self.qty_table.setRowCount(len(rows))
        for r, (name, value, u) in enumerate(rows):
            self.qty_table.setItem(r, 0, QTableWidgetItem(name))
            self.qty_table.setItem(r, 1, QTableWidgetItem(f"{value:,.3f}"))
            self.qty_table.setItem(r, 2, QTableWidgetItem(u))

    def _point_clicked(self, point):
        st = self.section["states"]
        side = "left" if point.d < 0 else "right"
        state = st[side]
        lines = [f"{point.code or 'Point'} at offset {point.d:.3f}, level {point.z:.3f} (station {self.station:.3f})",
                 "", point.why or "(baseline)", ""]
        region = self.section["region"]
        lines.append(f"Region: {region['assembly']} ({region['start']:.2f} to {region['end']:.2f})"
                     + (f", transition {self.section['transition'] * 100:.0f} %" if self.section.get("transition") is not None else ""))
        if state.super_active and state.lane_fall is not None:
            lines.append(f"Superelevation on the {side}: lane fall {state.lane_fall:.2f} % ({state.fall_source})")
        else:
            lines.append("Normal crossfall (no superelevation here).")
        if state.widening:
            lines.append(f"Curve widening on the {side}: {state.widening:.3f} ({state.widening_source})")
        dl = self.section["sides"][side]["daylight"]
        if dl.get("type") not in (None, "none"):
            lines.append(f"Daylight: {dl['type']}, height {dl.get('height', 0):.2f}, slope {dl.get('source', '')}")
        if self.section.get("shift"):
            lines.append(f"Rotation about an edge: section raised by {self.section['shift']:+.3f}")
        for f in self.section.get("features") or []:
            lines.append(f"Feature here: {f['label']} ({f['side']}, {f['ref']})")
        self.why_text.setPlainText("\n".join(lines))
        self.tabs.setCurrentIndex(6)

    # ---- station
    def set_station(self, station):
        if self.context is None:
            return
        frame = self.context["frame"]
        self.station = min(max(float(station), frame.start), frame.end)
        self.redraw()

    def step(self, direction):
        interval = float((self.project.get("stations") or {}).get("interval") or 20.0) if self.project else 20.0
        self.set_station(self.station + direction * interval)

    def _slider_moved(self, value):
        if self.context is None:
            return
        frame = self.context["frame"]
        self.set_station(frame.start + (frame.end - frame.start) * value / 1000.0)

    def _exaggeration(self, value):
        self.chart.exaggeration = float(value)
        self.chart.update()

    # ================================================================== map
    def _file_crs(self):
        crs = self.crs_widget.crs()
        return crs if crs.isValid() else None

    def _show_marker(self):
        if self.canvas is None or self.section is None:
            return
        crs = self._file_crs()
        if crs is None:
            return
        x, y = self.section["xy"]
        try:
            point = QgsCoordinateTransform(crs, self.canvas.mapSettings().destinationCrs(), QgsProject.instance()).transform(
                QgsPointXY(x, y))
        except QgsCsException:
            return
        if self.marker is None:
            self.marker = QgsVertexMarker(self.canvas)
            self.marker.setColor(QColor("#d81b60"))
            self.marker.setIconSize(14)
            self.marker.setPenWidth(3)
            self.marker.setIconType(QgsVertexMarker.IconType.ICON_CROSS)
        self.marker.setCenter(point)

    def set_picking(self, on):
        if self.canvas is None:
            return
        if on:
            if self._file_crs() is None:
                self._message("Choose the file CRS to pick stations on the map.", Qgis.MessageLevel.Warning)
                self.pick_button.setChecked(False)
                return
            self.tool = QgsMapToolEmitPoint(self.canvas)
            self.tool.canvasClicked.connect(self._map_clicked)
            self.canvas.setMapTool(self.tool)
        elif self.tool is not None:
            self.canvas.unsetMapTool(self.tool)
            self.tool = None

    def _map_clicked(self, point, _button):
        if self.context is None:
            return
        crs = self._file_crs()
        try:
            p = QgsCoordinateTransform(self.canvas.mapSettings().destinationCrs(), crs, QgsProject.instance()).transform(point)
        except QgsCsException:
            return
        station, offset = self.context["frame"].station_of(p.x(), p.y())
        self._message(f"Station {station:.2f}, offset {offset:+.2f}")
        self.set_station(station)

    def preview_on_map(self):
        if self.context is None:
            return
        crs = self._file_crs()
        if crs is None:
            self._message("Choose the file CRS to preview the edges on the map.", Qgis.MessageLevel.Warning)
            return
        result = cb.build_corridor(self.project, context=self.context)
        layer = QgsVectorLayer(f"LineStringZ?crs={crs.authid()}&field=code:string&field=side:string&field=element:string",
                               "Section editor edges (preview)", "memory")
        features = []
        for line in cb.feature_lines(result):
            f = QgsFeature(layer.fields())
            f.setGeometry(QgsGeometry(QgsLineString([QgsPoint(*p) for p in line["points"]])))
            f.setAttributes([line["code"], line["side"], line["element"]])
            features.append(f)
        layer.dataProvider().addFeatures(features)
        if self.preview_layer is not None and QgsProject.instance().mapLayer(self.preview_layer):
            QgsProject.instance().removeMapLayer(self.preview_layer)
        QgsProject.instance().addMapLayer(layer)
        self.preview_layer = layer.id()
        self._message(f"Previewed {len(features)} edges from {len(result.sections)} sections.")

    # ================================================================== project files and build
    def adopt_profile(self, path, alignment, profile):
        """A design profile was saved (Profile Viewer): when it is for this project's alignment, switch the project to
        the new file and profile, keeping every assembly, region and feature (one undo step)."""
        if self.project is None or self.project.get("alignment") != alignment:
            return False

        def change(p):
            p["landxml"], p["profile"] = path, profile
            p["sha256"] = cb.file_sha256(path)
            if (p.get("ground") or {}).get("type") == "tin" and p["ground"].get("path") == self.file_edit.text():
                p["ground"]["path"] = p["ground"]["path"]          # the ground surface stays where it was
        self._snapshot()
        change(self.project)
        self.file_edit.setText(path)
        self._prepare()
        self._message(f"Now on profile '{profile}' from {os.path.basename(path)}.")
        return True

    def open_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "Section project", QgsSettings().value(f"{SETTINGS}/folder", ""),
                                              "Section project (*.json)")
        if path:
            self.load_project_file(path)

    def load_project_file(self, path):
        try:
            project = cb.load_project(path)
        except (OSError, ValueError) as error:
            self._message(f"Cannot open the project: {error}", Qgis.MessageLevel.Warning)
            return
        self.project, self.project_path = project, path
        self.undo_stack, self.redo_stack = [], []
        self._loading = True
        self.file_edit.setText(project["landxml"])
        self.axis_combo.setCurrentIndex(1 if project.get("northing_first", True) else 2)
        self._loading = False
        self.set_file(project["landxml"])
        self.alignment_combo.setCurrentText(project["alignment"])
        self.profile_combo.setCurrentText(project.get("profile", ""))
        self._prepare()

    def save_project(self):
        if self.project is None:
            return
        path = self.project_path
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, "Save section project", "", "Section project (*.json)")
            if not path:
                return
        cb.save_project(self.project, path)
        self.project_path = path
        self._message(f"Saved {path}")

    def build(self):
        if self.context is None:
            return
        start = os.path.splitext(self.project["landxml"])[0] + "_corridor.xml"
        path, _ = QFileDialog.getSaveFileName(self, "Write the corridor LandXML", start, "LandXML (*.xml)")
        if not path:
            return
        if os.path.abspath(path) == os.path.abspath(self.project["landxml"]):
            QMessageBox.warning(self, "Corridor Section Editor", "The source LandXML is never overwritten; choose a new file.")
            return
        result = cb.build_corridor(self.project, context=self.context)
        cb.write_corridor_landxml(result, path, target=self.target_combo.currentData())
        q = cq.quantities(result)
        base = os.path.splitext(path)[0]
        with open(base + "_report.html", "w", encoding="utf-8") as stream:
            stream.write(cq.report_html(result, q, title=f"{self.project['alignment']} corridor"))
        cq.write_boq(base + "_quantities.xlsx", q)
        if self.project_path is None:
            cb.save_project(self.project, base + "_project.json")
            self.project_path = base + "_project.json"
        self._message(f"Wrote {os.path.basename(path)} ({len(result.sections)} sections), the report and the quantities "
                      f"workbook; {len(result.checks)} advisory departure(s).")
        self.built.emit(path, self.project["alignment"])

    def export_stage(self, stage, path=None):
        """The design at ``stage`` for Civil 3D or OpenRoads (with the ground over the footprint)."""
        if self.context is None:
            return None
        if not path:
            start = os.path.splitext(self.project["landxml"])[0] + f"_{stage}_{self.target_combo.currentData()}.xml"
            path, _ = QFileDialog.getSaveFileName(self, "Export the design", start, "LandXML (*.xml)")
            if not path:
                return None
        if os.path.abspath(path) == os.path.abspath(self.project["landxml"]):
            self._message("The source LandXML is never overwritten; choose a new file.", Qgis.MessageLevel.Warning)
            return None
        result = cb.build_corridor(self.project, context=self.context)
        cb.write_corridor_landxml(result, path, include_ground_surface=True, target=self.target_combo.currentData(),
                                  stage=stage)
        self._message(f"Exported the {stage} stage to {path}.")
        return path

    def _guide(self):
        from ..project_settings import open_guide

        open_guide("section-editor")

    def _message(self, text, level=Qgis.MessageLevel.Info):
        self.status.setText(text)
        if self.iface is not None and level != Qgis.MessageLevel.Info:
            try:
                self.iface.messageBar().pushMessage("Section Editor", text, level, 6)
            except (AttributeError, RuntimeError):
                pass

    def cleanup(self):
        self.set_picking(False)
        if self.marker is not None and self.canvas is not None:
            self.canvas.scene().removeItem(self.marker)
            self.marker = None


__all__ = ["SectionEditorDock", "parse_bands", "bands_text"]

