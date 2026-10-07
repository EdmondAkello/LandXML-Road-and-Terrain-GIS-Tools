"""Long-section viewer dock: existing ground vs design, linked to the map."""

from __future__ import annotations

import math
import os

from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsCsException,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsSettings,
)
from qgis.gui import QgsDockWidget, QgsVertexMarker
from qgis.PyQt.QtCore import QRectF, QSize, Qt
from qgis.PyQt.QtGui import QColor, QGuiApplication, QImage, QPageLayout, QPageSize, QPainter, QPdfWriter
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..landxml.earthworks import (
    compare_profiles,
    cut_fill_segments,
    exclude_ranges,
    parse_station_ranges,
    regular_rows,
    summarize_segments,
)
from ..landxml.common import descendants
from ..landxml.longsection import default_pair, read_alignment_profiles
from ..landxml.parser import load_document
from ..landxml.profile import profile_control_points, read_profile_controls
from ..landxml.reports import (
    CUT_COLOR,
    DESIGN_COLOR,
    FILL_COLOR,
    GROUND_COLOR,
    profile_report_html,
    station_label,
    write_csv,
)
from .map_tool import SOURCE_PROPERTY, AlignmentPickTool, alignment_field
from .profile_chart import EXTRA_COLORS, ProfileChart
from .section_panel import SectionPanel

try:  # QtSvg is optional in some Qt builds.
    from qgis.PyQt.QtSvg import QSvgGenerator
except ImportError:  # pragma: no cover - depends on the Qt build
    QSvgGenerator = None

SETTINGS = "landxml_road_terrain/profile_viewer"
START_FIELDS = ("sta_start", "station_start")
END_FIELDS = ("sta_end", "station_end")


def _number(feature, names):
    fields = feature.fields().names()
    for name in names:
        if name in fields:
            try:
                value = float(feature[name])
            except (TypeError, ValueError):
                continue
            if math.isfinite(value):
                return value
    return None


class ProfileViewerDock(QgsDockWidget):
    def __init__(self, iface=None, parent=None):
        super().__init__("LandXML Profile Viewer", parent)
        self.setObjectName("LandXMLProfileViewer")
        self.iface = iface
        self.canvas = iface.mapCanvas() if iface is not None else None
        self.document = None
        self.path = None
        self.alignments = {}
        self.result = None
        self.link = None
        self.marker = None
        self.tool = None
        self._previous_tool = None
        self._build()

    # ---------------------------------------------------------- layout
    def _build(self):
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(6, 6, 6, 6)

        file_row = QHBoxLayout()
        self.file_edit = QLineEdit()
        self.file_edit.setPlaceholderText("LandXML file…")
        self.file_edit.setReadOnly(True)
        browse = QPushButton("Open…")
        browse.clicked.connect(self.browse)
        self.pick_button = QToolButton()
        self.pick_button.setText("Pick on map")
        self.pick_button.setToolTip(
            "Click an alignment (or a layer created from one) in the map to show its profiles at that station."
        )
        self.pick_button.setCheckable(True)
        self.pick_button.toggled.connect(self.set_picking)
        self.pick_button.setEnabled(self.canvas is not None)
        file_row.addWidget(self.file_edit, 1)
        file_row.addWidget(browse)
        file_row.addWidget(self.pick_button)
        layout.addLayout(file_row)

        grid = QGridLayout()
        self.alignment_combo = QComboBox()
        self.design_combo = QComboBox()
        self.ground_combo = QComboBox()
        self.exclude_edit = QLineEdit()
        self.exclude_edit.setPlaceholderText("e.g. 1200-1450; 2010-2030 (bridges)")
        grid.addWidget(QLabel("Alignment"), 0, 0)
        grid.addWidget(self.alignment_combo, 0, 1, 1, 3)
        grid.addWidget(QLabel("Design"), 1, 0)
        grid.addWidget(self.design_combo, 1, 1)
        grid.addWidget(QLabel("Existing"), 1, 2)
        grid.addWidget(self.ground_combo, 1, 3)
        self.tolerance_spin = QDoubleSpinBox()
        self.tolerance_spin.setRange(0.0, 10.0)
        self.tolerance_spin.setDecimals(2)
        self.tolerance_spin.setSingleStep(0.05)
        self.tolerance_spin.setToolTip(
            "Depths within ± this value are treated as on grade, so small flips do not count as transitions."
        )
        grid.addWidget(QLabel("Exclude"), 2, 0)
        grid.addWidget(self.exclude_edit, 2, 1)
        grid.addWidget(QLabel("Grade tol."), 2, 2)
        grid.addWidget(self.tolerance_spin, 2, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        layout.addLayout(grid)

        options = QHBoxLayout()
        self.others_check = QCheckBox("Other profiles")
        self.depth_check = QCheckBox("Depth panel")
        self.depth_check.setChecked(True)
        self.shade_check = QCheckBox("Shade cut/fill")
        self.shade_check.setChecked(True)
        self.controls_check = QCheckBox("VPIs")
        self.controls_check.setChecked(True)
        for widget in (self.others_check, self.depth_check, self.shade_check, self.controls_check):
            options.addWidget(widget)
        options.addStretch(1)
        fit = QToolButton()
        fit.setText("Fit")
        fit.clicked.connect(lambda: self.chart.fit())
        self.export_button = QToolButton()
        self.export_button.setText("Export")
        self.export_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.export_button)
        for label, handler in (
            ("Chart as PNG image…", self.export_png),
            ("Chart as SVG drawing…", self.export_svg),
            ("Chart as PDF (A3 landscape)…", self.export_pdf),
            ("Copy chart to clipboard", self.copy_image),
            (None, None),
            ("Station table as CSV…", self.export_samples_csv),
            ("Cut/fill segments as CSV…", self.export_segments_csv),
            ("HTML report…", self.export_html),
        ):
            if label is None:
                menu.addSeparator()
            else:
                menu.addAction(label, handler)
        self.export_button.setMenu(menu)
        options.addWidget(fit)
        options.addWidget(self.export_button)
        layout.addLayout(options)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.chart = ProfileChart()
        splitter.addWidget(self.chart)
        lower = QWidget()
        lower_layout = QVBoxLayout(lower)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lower_layout.addWidget(self.summary_label)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["From", "To", "Type", "Length", "Max depth", "Flag"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self.segment_selected)
        lower_layout.addWidget(self.table)
        self.tabs = QTabWidget()
        self.tabs.addTab(lower, "Cut/fill segments")
        self.section_panel = SectionPanel()
        self.tabs.addTab(self.section_panel, "Cross-sections")
        self.tabs.setTabEnabled(1, False)
        self.tabs.currentChanged.connect(self._tab_changed)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        self.setWidget(body)

        self.alignment_combo.currentIndexChanged.connect(self.alignment_changed)
        self.design_combo.currentIndexChanged.connect(self.refresh)
        self.ground_combo.currentIndexChanged.connect(self.refresh)
        self.exclude_edit.editingFinished.connect(self.refresh)
        self.tolerance_spin.valueChanged.connect(self.refresh)
        self.others_check.toggled.connect(self.refresh)
        self.depth_check.toggled.connect(self._toggle_view)
        self.shade_check.toggled.connect(self._toggle_view)
        self.controls_check.toggled.connect(self._toggle_view)
        self.chart.stationHovered.connect(self.show_map_marker)
        self.chart.stationClicked.connect(self.center_map)
        self.chart.stationClicked.connect(self._show_section)
        self.chart.stationHovered.connect(self.section_panel.hover_station)
        self.section_panel.stationChanged.connect(self._section_station)
        self.section_panel.message.connect(lambda text: self._message(text, Qgis.MessageLevel.Success))
        self._update_summary()

    def _toggle_view(self):
        self.chart.show_depth = self.depth_check.isChecked()
        self.chart.show_shading = self.shade_check.isChecked()
        self.chart.show_controls = self.controls_check.isChecked()
        self.chart.update()

    # ------------------------------------------------------- loading
    def browse(self):
        settings = QgsSettings()
        start = settings.value(f"{SETTINGS}/last_dir", "")
        path, _ = QFileDialog.getOpenFileName(self, "Open LandXML", start, "LandXML (*.xml *.landxml)")
        if path:
            settings.setValue(f"{SETTINGS}/last_dir", os.path.dirname(path))
            self.load(path)

    def load(self, path, alignment=None):
        if path != self.path or self.document is None:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            error = None
            try:
                document = load_document(path)
            except ValueError as exc:
                error = exc
            finally:
                QApplication.restoreOverrideCursor()
            if error is not None:
                self._message(str(error), Qgis.MessageLevel.Critical)
                return False
            self.document = document
            self.path = path
            self.file_edit.setText(path)
            self.file_edit.setToolTip(path)
            self.alignments = {}
            for element in document.alignments():
                name = element.attrib.get("name") or f"Alignment {len(self.alignments) + 1}"
                if name not in self.alignments:
                    self.alignments[name] = element
            self.alignment_combo.blockSignals(True)
            self.alignment_combo.clear()
            self.alignment_combo.addItems(list(self.alignments))
            self.alignment_combo.blockSignals(False)
            self.link = None
        if alignment and alignment in self.alignments:
            self.alignment_combo.setCurrentText(alignment)
        self.alignment_changed()
        return True

    def alignment_changed(self):
        name = self.alignment_combo.currentText()
        element = self.alignments.get(name)
        self.profiles = read_alignment_profiles(element, 2.0) if element is not None else []
        design, ground = default_pair(self.profiles)
        for combo, kind_default in ((self.design_combo, design), (self.ground_combo, ground)):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("(none)", None)
            for index, profile in enumerate(self.profiles):
                combo.addItem(profile.label, index)
            if kind_default is not None:
                combo.setCurrentIndex(self.profiles.index(kind_default) + 1)
            combo.blockSignals(False)
        self.chart.set_marker(None)
        self._sections_stale = True
        has_sections = element is not None and next(descendants(element, "CrossSect"), None) is not None
        self.tabs.setTabEnabled(1, has_sections)
        self.tabs.setTabToolTip(1, "" if has_sections else "This alignment has no cross-sections in the LandXML file.")
        if not has_sections and self.tabs.currentIndex() == 1:
            self.tabs.setCurrentIndex(0)
        self.refresh()
        if has_sections and self.tabs.currentIndex() == 1:
            self._load_sections()

    def _load_sections(self):
        if not getattr(self, "_sections_stale", False) or not self.tabs.isTabEnabled(1):
            return
        self._sections_stale = False
        design = next((p for p in self.profiles if p.kind == "design"), None)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.section_panel.set_alignment(self.document, self.alignment_combo.currentText(), self.profiles, design)
        finally:
            QApplication.restoreOverrideCursor()

    def _tab_changed(self, index):
        if index == 1:
            self._load_sections()
            if self.chart.marker_station is not None:
                self.section_panel.show_station(self.chart.marker_station)

    def _show_section(self, station):
        if self.tabs.isTabEnabled(1):
            self._load_sections()
            self.section_panel.show_station(station)

    def _section_station(self, station):
        self.chart.set_marker(station)
        self.show_map_marker(station)

    def _selected(self, combo):
        index = combo.currentData()
        return self.profiles[index] if index is not None and index < len(self.profiles) else None

    # -------------------------------------------------------- analysis
    def refresh(self):
        design = self._selected(self.design_combo)
        ground = self._selected(self.ground_combo)
        lines, runs, segments, transitions, controls = [], [], [], [], []
        try:
            excluded = parse_station_ranges(self.exclude_edit.text())
            self.exclude_edit.setStyleSheet("")
        except ValueError as exc:
            excluded = []
            self.exclude_edit.setStyleSheet("color: #b3261e")
            self.exclude_edit.setToolTip(str(exc))
        if ground is not None:
            lines.append(dict(name=ground.name, parts=ground.parts, color=GROUND_COLOR, width=1.5))
        if design is not None:
            lines.append(dict(name=design.name, parts=design.parts, color=DESIGN_COLOR, width=1.8))
            if design.kind == "design" and design.element is not None:
                try:
                    controls = profile_control_points(read_profile_controls(design.element))
                    controls = [c for c in controls if c["control_type"] == "VPI"]
                except ValueError:
                    controls = []
        if self.others_check.isChecked():
            extra = [p for p in self.profiles if p is not design and p is not ground]
            for index, profile in enumerate(extra):
                lines.append(
                    dict(
                        name=profile.name,
                        parts=profile.parts,
                        color=EXTRA_COLORS[index % len(EXTRA_COLORS)],
                        width=1.2,
                        dashed=True,
                    )
                )
        if design is not None and ground is not None and design is not ground:
            runs = exclude_ranges(compare_profiles(design, ground), excluded)
            segments, transitions = cut_fill_segments(runs, self.tolerance_spin.value())
        alignment = self.alignment_combo.currentText()
        title = alignment
        if design is not None and ground is not None:
            title = f"{alignment} — {design.name} vs {ground.name}"
        self.result = dict(
            alignment=alignment,
            design=design.name if design else "",
            ground=ground.name if ground else "",
            runs=runs,
            segments=segments,
            transitions=transitions,
            summary=summarize_segments(segments),
            excluded="; ".join(f"{a:g}-{b:g}" for a, b in excluded),
        )
        unit = self.document.horizontal_unit if self.document else None
        self.chart.set_data(lines, runs, transitions, controls, title, unit)
        self._toggle_view()
        self._fill_table(segments, unit)
        self._update_summary()

    def _fill_table(self, segments, unit):
        self.table.setRowCount(len(segments))
        colors = {"cut": QColor(CUT_COLOR), "fill": QColor(FILL_COLOR)}
        for row, segment in enumerate(segments):
            values = [
                station_label(segment["start"], unit),
                station_label(segment["end"], unit),
                segment["kind"],
                f"{segment['length']:.1f}",
                f"{segment['max_depth']:.2f}",
                segment["severity"],
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 2 and segment["kind"] in colors:
                    item.setForeground(colors[segment["kind"]])
                self.table.setItem(row, column, item)

    def _update_summary(self):
        if not self.result or not self.result["runs"]:
            self.summary_label.setText(
                "Choose a design profile and an existing-ground profile to compare."
                if self.document
                else "Open a LandXML file, or press <b>Pick on map</b> and click an alignment."
            )
            return
        s = self.result["summary"]
        self.summary_label.setText(
            f"Compared {s['covered_length']:,.1f} · <span style='color:{CUT_COLOR}'>cut {s['cut_length']:,.1f} "
            f"({s['cut_percent']:.0f}%) max {s['max_cut']:.2f}</span> · <span style='color:{FILL_COLOR}'>fill "
            f"{s['fill_length']:,.1f} ({s['fill_percent']:.0f}%) max {s['max_fill']:.2f}</span> · "
            f"{s['transitions']} cut↔fill transitions"
        )

    def segment_selected(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows or not self.result:
            return
        segment = self.result["segments"][rows[0].row()]
        self.chart.zoom_to(segment["start"], segment["end"])
        self.chart.set_marker(segment["max_station"])
        self.show_map_marker(segment["max_station"])

    # --------------------------------------------------------- map link
    def set_picking(self, enabled):
        if self.canvas is None:
            return
        if enabled:
            if self.tool is None:
                self.tool = AlignmentPickTool(self.canvas)
                self.tool.picked.connect(self.picked)
                self.tool.missed.connect(
                    lambda: self._message("No alignment-based line or point layer feature near the click.")
                )
                self.tool.deactivated.connect(lambda: self.pick_button.setChecked(False))
            self._previous_tool = self.canvas.mapTool()
            self.canvas.setMapTool(self.tool)
        elif self.canvas.mapTool() is self.tool:
            self.canvas.unsetMapTool(self.tool)

    def picked(self, layer, feature, layer_point):
        alignment = feature[alignment_field(layer)]
        alignment = "" if alignment is None else str(alignment)
        path = layer.customProperty(SOURCE_PROPERTY) or None
        if path and not os.path.isfile(path):
            path = None
        if path is None and self.document is not None and alignment in self.alignments:
            path = self.path
        if path is None:
            settings = QgsSettings()
            path, _ = QFileDialog.getOpenFileName(
                self,
                f"LandXML file for alignment '{alignment}'",
                settings.value(f"{SETTINGS}/last_dir", ""),
                "LandXML (*.xml *.landxml)",
            )
            if not path:
                return
            settings.setValue(f"{SETTINGS}/last_dir", os.path.dirname(path))
            layer.setCustomProperty(SOURCE_PROPERTY, path)
        if not self.load(path, alignment):
            return
        if alignment not in self.alignments:
            self._message(f"Alignment '{alignment}' is not in {os.path.basename(path)}.", Qgis.MessageLevel.Warning)
            return
        station = self.station_for(layer, feature, layer_point)
        if not self.isVisible():
            self.show()
        self.raise_()
        if station is not None:
            self.chart.set_marker(station)
            self.show_map_marker(station)
            if self.tabs.currentIndex() == 1:
                self._show_section(station)

    def station_for(self, layer, feature, layer_point):
        """Station from attributes and position along the clicked line."""
        direct = _number(feature, ("station",))
        geometry = feature.geometry()
        if direct is not None or layer.geometryType() != Qgis.GeometryType.Line:
            self.link = None
            return direct
        element = self.alignments.get(self.alignment_combo.currentText())
        start = _number(feature, START_FIELDS)
        end = _number(feature, END_FIELDS)
        if start is None and element is not None:
            try:
                start = float(element.attrib.get("staStart"))
            except (TypeError, ValueError):
                start = None
        if end is None and start is not None:
            length = _number(feature, ("length",))
            if length is None and element is not None:
                try:
                    length = float(element.attrib.get("length"))
                except (TypeError, ValueError):
                    length = None
            end = start + length if length is not None else None
        if start is None or end is None or geometry.length() <= 0:
            self.link = None
            return None
        self.link = dict(layer=layer, geometry=QgsGeometry(geometry), start=start, end=end)
        along = geometry.lineLocatePoint(QgsGeometry.fromPointXY(QgsPointXY(layer_point)))
        return start + (end - start) * along / geometry.length()

    def _map_point(self, station):
        link = self.link
        if link is None or self.canvas is None or link["end"] == link["start"]:
            return None
        fraction = (station - link["start"]) / (link["end"] - link["start"])
        if not -1e-6 <= fraction <= 1 + 1e-6:
            return None
        geometry = link["geometry"]
        point = geometry.interpolate(min(max(fraction, 0.0), 1.0) * geometry.length())
        if point is None or point.isEmpty():
            return None
        transform = QgsCoordinateTransform(
            link["layer"].crs(), self.canvas.mapSettings().destinationCrs(), QgsProject.instance()
        )
        try:
            return transform.transform(point.asPoint())
        except QgsCsException:
            return None

    def show_map_marker(self, station):
        if self.canvas is None:
            return
        point = self._map_point(station) if station is not None else None
        if point is None:
            if self.marker is not None:
                self.marker.hide()
            return
        if self.marker is None:
            self.marker = QgsVertexMarker(self.canvas)
            self.marker.setIconType(QgsVertexMarker.IconType.ICON_CIRCLE)
            self.marker.setColor(QColor("#d23c3c"))
            self.marker.setIconSize(14)
            self.marker.setPenWidth(3)
        self.marker.setCenter(point)
        self.marker.show()

    def center_map(self, station):
        self.chart.set_marker(station)
        point = self._map_point(station)
        if point is not None and self.canvas is not None:
            self.canvas.setCenter(point)
            self.canvas.refresh()
            self.show_map_marker(station)

    # ---------------------------------------------------------- export
    def _save_path(self, title, suffix, filters):
        settings = QgsSettings()
        base = os.path.splitext(os.path.basename(self.path or "profile"))[0]
        alignment = "".join(ch if ch.isalnum() else "_" for ch in self.alignment_combo.currentText())[:40]
        start = os.path.join(settings.value(f"{SETTINGS}/export_dir", ""), f"{base}_{alignment}{suffix}")
        path, _ = QFileDialog.getSaveFileName(self, title, start, filters)
        if path:
            settings.setValue(f"{SETTINGS}/export_dir", os.path.dirname(path))
            if not path.lower().endswith(suffix):
                path += suffix
        return path

    def chart_image(self, width=1800, height=900, scale=2.0):
        image = QImage(int(width * scale), int(height * scale), QImage.Format.Format_ARGB32)
        image.fill(QColor("#ffffff"))
        painter = QPainter(image)
        painter.scale(scale, scale)
        self.chart.render(painter, QRectF(0, 0, width, height))
        painter.end()
        return image

    def write_png(self, path, width=1800, height=900):
        return self.chart_image(width, height).save(path, "PNG")

    def write_svg(self, path, width=1400, height=700):
        if QSvgGenerator is None:
            raise RuntimeError("SVG export needs the QtSvg module.")
        generator = QSvgGenerator()
        generator.setFileName(path)
        generator.setSize(QSize(width, height))
        generator.setViewBox(QRectF(0, 0, width, height))
        generator.setTitle(self.chart.title)
        painter = QPainter(generator)
        self.chart.render(painter, QRectF(0, 0, width, height))
        painter.end()
        return True

    def write_pdf(self, path):
        writer = QPdfWriter(path)
        writer.setPageSize(QPageSize(QPageSize.PageSizeId.A3))
        writer.setPageOrientation(QPageLayout.Orientation.Landscape)
        writer.setResolution(150)
        writer.setTitle(self.chart.title)
        painter = QPainter(writer)
        viewport = painter.viewport()
        width, height = 1600.0, 1600.0 * viewport.height() / max(viewport.width(), 1)
        painter.scale(viewport.width() / width, viewport.width() / width)
        self.chart.render(painter, QRectF(0, 0, width, height - 30))
        painter.setPen(QColor("#5b6575"))
        footer = self.summary_label.text()
        for tag in ("<b>", "</b>", "</span>"):
            footer = footer.replace(tag, "")
        while "<span" in footer:
            start = footer.index("<span")
            footer = footer[:start] + footer[footer.index(">", start) + 1 :]
        painter.drawText(QRectF(10, height - 26, width - 20, 22), int(0), f"{os.path.basename(self.path or '')} · {footer}")
        painter.end()
        return True

    def sample_rows(self, interval):
        unit = self.document.horizontal_unit if self.document else None
        rows = []
        for row in regular_rows(self.result["runs"], interval):
            kind = "fill" if row["depth"] > 0 else "cut" if row["depth"] < 0 else "grade"
            rows.append(
                (
                    station_label(row["station"], unit),
                    f"{row['station']:.3f}",
                    f"{row['design']:.3f}",
                    f"{row['ground']:.3f}",
                    f"{row['depth']:.3f}",
                    kind,
                )
            )
        return rows

    def _require_result(self):
        if not self.result or not self.result["runs"]:
            self._message("Choose a design and an existing-ground profile first.", Qgis.MessageLevel.Warning)
            return False
        return True

    def export_png(self):
        path = self._save_path("Export chart", ".png", "PNG image (*.png)")
        if path and self.write_png(path):
            self._message(f"Saved {path}", Qgis.MessageLevel.Success)

    def export_svg(self):
        path = self._save_path("Export chart", ".svg", "SVG drawing (*.svg)")
        if path:
            try:
                self.write_svg(path)
                self._message(f"Saved {path}", Qgis.MessageLevel.Success)
            except RuntimeError as exc:
                self._message(str(exc), Qgis.MessageLevel.Warning)

    def export_pdf(self):
        path = self._save_path("Export chart", ".pdf", "PDF (*.pdf)")
        if path and self.write_pdf(path):
            self._message(f"Saved {path}", Qgis.MessageLevel.Success)

    def copy_image(self):
        QGuiApplication.clipboard().setImage(self.chart_image(1400, 700, 1.5))
        self._message("Chart copied to the clipboard.", Qgis.MessageLevel.Success)

    def export_samples_csv(self):
        if not self._require_result():
            return
        interval, ok = QInputDialog.getDouble(self, "Station table", "Station interval", 20.0, 0.01, 100000.0, 2)
        if not ok:
            return
        path = self._save_path("Export station table", ".csv", "CSV (*.csv)")
        if path:
            write_csv(path, ["station_label", "station", "design", "ground", "depth", "kind"], self.sample_rows(interval))
            self._message(f"Saved {path}", Qgis.MessageLevel.Success)

    def export_segments_csv(self):
        if not self._require_result():
            return
        path = self._save_path("Export cut/fill segments", ".csv", "CSV (*.csv)")
        if not path:
            return
        unit = self.document.horizontal_unit if self.document else None
        write_csv(
            path,
            ["from", "to", "sta_start", "sta_end", "kind", "length", "max_depth", "max_station", "mean_depth", "flag"],
            [
                (
                    station_label(s["start"], unit),
                    station_label(s["end"], unit),
                    f"{s['start']:.3f}",
                    f"{s['end']:.3f}",
                    s["kind"],
                    f"{s['length']:.3f}",
                    f"{s['max_depth']:.3f}",
                    f"{s['max_station']:.3f}",
                    f"{s['mean_depth']:.3f}",
                    s["severity"],
                )
                for s in self.result["segments"]
            ],
        )
        self._message(f"Saved {path}", Qgis.MessageLevel.Success)

    def export_html(self):
        if not self._require_result():
            return
        path = self._save_path("Export report", ".html", "HTML (*.html)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(
                profile_report_html(
                    [self.result],
                    self.document.horizontal_unit,
                    self.document.vertical_unit,
                    os.path.basename(self.path or ""),
                )
            )
        self._message(f"Saved {path}", Qgis.MessageLevel.Success)

    # ----------------------------------------------------------- misc
    def _message(self, text, level=Qgis.MessageLevel.Info):
        if self.iface is not None:
            self.iface.messageBar().pushMessage("LandXML Profile Viewer", text, level, 5)
        elif level == Qgis.MessageLevel.Critical:
            QMessageBox.warning(self, "LandXML Profile Viewer", text)

    def cleanup(self):
        if self.canvas is not None:
            if self.tool is not None and self.canvas.mapTool() is self.tool:
                self.canvas.unsetMapTool(self.tool)
            if self.marker is not None:
                self.canvas.scene().removeItem(self.marker)
                self.marker = None
