"""Interactive station/offset lookup using source-distance alignment stationing."""

import math

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCsException,
    QgsPointXY,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingFeedback,
    QgsProject,
)
from qgis.gui import (
    QgsDockWidget,
    QgsMapToolEmitPoint,
    QgsProjectionSelectionWidget,
    QgsVertexMarker,
)
from qgis.PyQt import sip
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..alignment_algorithm import LandXMLAlignmentsAlgorithm, METHODS
from ..core import transform_vertices
from ..landxml.common import descendants
from ..landxml.geometry import read_alignments
from ..landxml.parser import load_document
from ..landxml.reports import station_label
from ..landxml.stationing import StationedPolyline, parse_station
from ..processing_common import coordinate_choices, coordinate_param


class StationOffsetDock(QgsDockWidget):
    def __init__(self, iface, parent=None):
        super().__init__("Station & Offset Locator", parent)
        self.setObjectName("LandXMLStationOffsetLocator")
        self.canvas = iface.mapCanvas()
        self.path = None
        self.document = None
        self.polyline = None
        self.markers = []
        self._previous_tool = None
        self.tool = QgsMapToolEmitPoint(self.canvas)
        self.tool.setCursor(Qt.CursorShape.CrossCursor)
        self.tool.canvasClicked.connect(self.map_clicked)
        self.tool.deactivated.connect(self.tool_deactivated)
        self.algorithm = LandXMLAlignmentsAlgorithm()
        self.algorithm.initAlgorithm()
        self._build()
        self.canvas.destinationCrsChanged.connect(self.invalidate)
        QgsProject.instance().cleared.connect(self.clear)

    def _build(self):
        body = QWidget()
        layout = QVBoxLayout(body)
        row = QHBoxLayout()
        self.file_edit = QLineEdit()
        self.file_edit.setReadOnly(True)
        self.file_edit.setPlaceholderText("LandXML file")
        browse = QPushButton("Open…")
        browse.clicked.connect(self.browse)
        row.addWidget(self.file_edit)
        row.addWidget(browse)
        layout.addLayout(row)
        form = QFormLayout()
        self.alignment_combo = QComboBox()
        form.addRow("Alignment", self.alignment_combo)
        self.method_combo = QComboBox()
        self.method_combo.addItem("Choose coordinate interpretation…", None)
        for index, label in enumerate(METHODS):
            self.method_combo.addItem(label, index)
        coordinate = coordinate_param("METHOD", METHODS)
        self.method_combo.setToolTip(coordinate.help())
        form.addRow(coordinate.description(), self.method_combo)
        self.output_crs = QgsProjectionSelectionWidget()
        self.source_crs = QgsProjectionSelectionWidget()
        for widget in (self.output_crs, self.source_crs):
            widget.setCrs(QgsCoordinateReferenceSystem())
            widget.crsChanged.connect(self.invalidate)
        form.addRow("Output CRS (same as imported alignment)", self.output_crs)
        form.addRow("Source CRS (reprojection only)", self.source_crs)
        self.numbers = {}
        for key, label, low, high, default, decimals in (
            ("DX", "Delta X / Easting", -1e9, 1e9, 0, 4),
            ("DY", "Delta Y / Northing", -1e9, 1e9, 0, 4),
            ("ROTATION", "Rotation (degrees)", -360, 360, 0, 8),
            ("SCALE", "Scale factor", 1e-8, 1000, 1, 10),
            ("SEGMENT", "Maximum curve segment (source units)", 0.001, 10000, 0.1, 3),
        ):
            spin = QDoubleSpinBox()
            spin.setDecimals(decimals)
            spin.setRange(low, high)
            spin.setValue(default)
            spin.valueChanged.connect(self.invalidate)
            self.numbers[key] = spin
            form.addRow(label, spin)
        self.station_edit = QLineEdit()
        self.station_edit.setPlaceholderText("Numeric or engineering station (e.g. 803+19.359 ft)")
        self.offset_edit = QLineEdit("0")
        form.addRow("Station", self.station_edit)
        form.addRow("Offset (− left / + right)", self.offset_edit)
        layout.addLayout(form)
        row = QHBoxLayout()
        self.pick_button = QToolButton()
        self.pick_button.setText("Click map")
        self.pick_button.setCheckable(True)
        self.pick_button.toggled.connect(self.set_picking)
        locate = QPushButton("Locate station / offset")
        locate.clicked.connect(self.locate_entered)
        row.addWidget(self.pick_button)
        row.addWidget(locate)
        layout.addLayout(row)
        self.result_label = QLabel(
            "Open LandXML and select the same coordinate settings used for import."
        )
        self.result_label.setTextFormat(Qt.TextFormat.PlainText)
        self.result_label.setWordWrap(True)
        self.result_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.result_label)
        self.setWidget(body)
        self.alignment_combo.currentIndexChanged.connect(self.invalidate)
        self.method_combo.currentIndexChanged.connect(self.invalidate)

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open LandXML", self.path or "", "LandXML (*.xml *.landxml)"
        )
        if path:
            self.load(path)

    def load(self, path):
        self.clear()
        try:
            document = load_document(path)
            # Use the parser's records for names, including unnamed alignments.
            alignments, warnings = read_alignments(path)
            if not alignments:
                raise ValueError("No supported, connected horizontal alignments. " + "; ".join(warnings))
            names = [
                a.attrib.get("name", f"Alignment {i + 1}")
                for i, a in enumerate(document.alignments())
            ]
            if len(set(names)) != len(names):
                raise ValueError("Alignment names must be unique for station/offset lookup.")
        except ValueError as exc:
            self.result_label.setText(str(exc))
            return False
        self.path, self.document = path, document
        self.file_edit.setText(path)
        for alignment in alignments:
            self.alignment_combo.addItem(alignment["name"])
        unit = document.horizontal_unit or "undeclared source units"
        self.result_label.setText(f"Units: {unit}. Select coordinate settings. " + "; ".join(warnings))
        return True

    def prepare(self):
        if self.polyline is not None:
            return
        if not self.path or self.method_combo.currentData() is None:
            raise ValueError("Open LandXML and explicitly choose coordinate interpretation and CRS.")
        parameters = {key: spin.value() for key, spin in self.numbers.items()}
        parameters.update(
            METHOD=self.method_combo.currentData(),
            OUTPUT_CRS=self.output_crs.crs(),
            SOURCE_CRS=self.source_crs.crs(),
        )
        context = QgsProcessingContext()
        context.setProject(QgsProject.instance())
        method, output, source, params, _document = coordinate_choices(
            self.algorithm, parameters, context, QgsProcessingFeedback(), self.path
        )
        crs = source if method == "reproject" else output
        if crs.isGeographic():
            raise ValueError("Station/offset lookup requires a projected calculation CRS with linear units.")
        if not self.canvas.mapSettings().destinationCrs().isValid():
            raise ValueError("Set a valid map CRS before locating positions.")
        element = next(
            a for i, a in enumerate(self.document.alignments())
            if a.attrib.get("name", f"Alignment {i + 1}") == self.alignment_combo.currentText()
        )
        if next(descendants(element, "StaEquation"), None) is not None:
            raise ValueError("Station equations are not supported by this locator.")
        # shortcut: sampled curve normals; reduce segment length for tight curves or large offsets.
        records, warnings = read_alignments(self.path, self.numbers["SEGMENT"].value())
        record = next((a for a in records if a["name"] == self.alignment_combo.currentText()), None)
        if record is None:
            raise ValueError("Selected alignment could not be sampled. " + "; ".join(warnings))
        if any(not math.isfinite(float(value)) for point in record["points"] for value in point):
            raise ValueError("Alignment coordinates must be finite.")
        if method != "reproject":
            record["points"] = transform_vertices(record["points"], method, **params).tolist()
        polyline = StationedPolyline.from_alignment(record)
        if polyline is None or not all(math.isfinite(s) for s in polyline.stations):
            raise ValueError("Selected alignment needs a finite declared start station.")
        self.transform = QgsCoordinateTransform(
            crs, self.canvas.mapSettings().destinationCrs(), QgsProject.instance()
        )
        self.offset_scale = params["scale"] if method == "helmert" else 1.0
        self.polyline = polyline

    def map_clicked(self, point, button=Qt.MouseButton.LeftButton):
        if button != Qt.MouseButton.LeftButton:
            return
        try:
            self.prepare()
            point = self.transform.transform(point, Qgis.TransformDirection.Reverse)
            if not all(math.isfinite(v) for v in (point.x(), point.y())):
                raise ValueError("Map position must have finite coordinates.")
            station, offset, distance = self.polyline.locate(point.x(), point.y())
            self.show_position(station, offset / self.offset_scale)
            self.offset_edit.setText(f"{offset / self.offset_scale:.3f}")
            # Beyond endpoints the perpendicular offset differs from distance.
            self.result_label.setText(
                self.result_label.text() + f"; nearest distance {distance / self.offset_scale:.3f}"
            )
        except (ValueError, QgsProcessingException, QgsCsException) as exc:
            self.fail(str(exc))

    def locate_entered(self):
        try:
            unit = self.document.horizontal_unit if self.document else None
            station = parse_station(self.station_edit.text(), unit)
            offset = float(self.offset_edit.text())
            if not all(math.isfinite(v) for v in (station, offset)):
                raise ValueError("Station and offset must be finite numbers.")
            self.prepare()
            point = self.show_position(station, offset)
            self.canvas.setCenter(point)
            self.canvas.refresh()
        except (ValueError, QgsProcessingException, QgsCsException) as exc:
            self.fail(str(exc))

    def show_position(self, station, offset):
        foot = self.polyline.point_at(station)
        target = self.polyline.point_at(station, offset * self.offset_scale)
        if target is None:
            raise ValueError(f"Station must be between {self.polyline.station_start:g} and {self.polyline.station_end:g}.")
        points = [self.transform.transform(QgsPointXY(*p)) for p in (foot, target)]
        if not all(math.isfinite(v) for p in points for v in (p.x(), p.y())):
            raise ValueError("Coordinate transformation returned an invalid position.")
        self.hide_markers()
        if not self.markers:
            for color, icon in (
                ("#d23c3c", QgsVertexMarker.IconType.ICON_CIRCLE),
                ("#2166ac", QgsVertexMarker.IconType.ICON_CROSS),
            ):
                marker = QgsVertexMarker(self.canvas)
                marker.setColor(QColor(color))
                marker.setIconType(icon)
                marker.setIconSize(12)
                marker.setPenWidth(3)
                self.markers.append(marker)
        for marker, point in zip(self.markers, points):
            marker.setCenter(point)
            marker.show()
        side = "right" if offset > 0 else "left" if offset < 0 else "on alignment"
        unit = self.document.horizontal_unit or "undeclared source units"
        label = station_label(station, unit, decimals=3) if self.document.horizontal_unit else f"{station:.3f}"
        self.station_edit.setText(label)
        self.result_label.setText(
            f"Station {label}; offset {offset:+.3f} {side}; units: {unit}"
        )
        return points[1]

    def hide_markers(self):
        for marker in self.markers:
            marker.hide()

    def fail(self, message):
        self.hide_markers()
        self.result_label.setText(message)

    def invalidate(self, *_args):
        self.polyline = None
        self.hide_markers()
        self.result_label.setText("Settings changed. Click map or locate a station to recalculate.")

    def set_picking(self, enabled):
        if enabled:
            try:
                self.prepare()
            except (ValueError, QgsProcessingException, QgsCsException) as exc:
                self.fail(str(exc))
                self.pick_button.setChecked(False)
                return
            self._previous_tool = self.canvas.mapTool()
            self.canvas.setMapTool(self.tool)
        elif self.canvas.mapTool() is self.tool:
            self.canvas.unsetMapTool(self.tool)
            if self._previous_tool is not None and not sip.isdeleted(self._previous_tool):
                self.canvas.setMapTool(self._previous_tool)

    def tool_deactivated(self):
        self.pick_button.blockSignals(True)
        self.pick_button.setChecked(False)
        self.pick_button.blockSignals(False)

    def clear(self):
        self.pick_button.setChecked(False)
        self.invalidate()
        self.path = self.document = None
        self.alignment_combo.clear()
        self.file_edit.clear()

    def hideEvent(self, event):
        self.pick_button.setChecked(False)
        self.hide_markers()
        super().hideEvent(event)

    def cleanup(self):
        self.clear()
        self.canvas.destinationCrsChanged.disconnect(self.invalidate)
        QgsProject.instance().cleared.disconnect(self.clear)
        for marker in self.markers:
            self.canvas.scene().removeItem(marker)
        self.markers = []
        self.tool.deleteLater()
