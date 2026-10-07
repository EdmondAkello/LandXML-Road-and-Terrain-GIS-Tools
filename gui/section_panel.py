"""Cross-section tab of the profile viewer."""

from __future__ import annotations

import os

from qgis.core import QgsSettings
from qgis.PyQt.QtCore import QRectF, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QPainter
from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..landxml.corridor import guess_surfaces, read_corridor_sections, roadway_surface_refs
from ..landxml.reports import station_label
from ..landxml.section_view import build_section_view
from .section_chart import QSvgGenerator, SectionChart, SectionStyle, material_colors, section_image, write_section_sheets

SETTINGS = "landxml_road_terrain/profile_viewer"


class SectionPanel(QWidget):
    """Shows the corridor section nearest the profile chainage."""

    stationChanged = pyqtSignal(float)
    message = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.document = None
        self.alignment = None
        self.design_profile = None
        self.sections = []
        self.surface_names = []
        self._loaded_key = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.ground_combo = QComboBox()
        self.design_combo = QComboBox()
        self.previous_button = QToolButton()
        self.previous_button.setText("◀")
        self.next_button = QToolButton()
        self.next_button.setText("▶")
        self.station_label = QLabel("–")
        self.follow_check = QCheckBox("Follow cursor")
        self.follow_check.setToolTip("Show the section nearest the profile cursor while hovering")
        self.ground_only = QCheckBox("Only with ground")
        self.ground_only.setChecked(True)
        self.exaggeration = QDoubleSpinBox()
        self.exaggeration.setRange(0.1, 50)
        self.exaggeration.setValue(2.0)
        self.exaggeration.setPrefix("VE ")
        self.exaggeration.setSuffix("×")
        export = QToolButton()
        export.setText("Export")
        export.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(export)
        menu.addAction("Section as PNG…", self.export_png)
        menu.addAction("Section as SVG…", self.export_svg)
        menu.addAction("All sections as PDF sheets…", self.export_sheets)
        export.setMenu(menu)
        for widget in (
            QLabel("Ground"),
            self.ground_combo,
            QLabel("Design"),
            self.design_combo,
            self.previous_button,
            self.station_label,
            self.next_button,
            self.exaggeration,
            self.ground_only,
            self.follow_check,
        ):
            bar.addWidget(widget)
        bar.addStretch(1)
        bar.addWidget(export)
        layout.addLayout(bar)
        self.chart = SectionChart()
        layout.addWidget(self.chart, 1)
        self.previous_button.clicked.connect(lambda: self.chart.step(-1))
        self.next_button.clicked.connect(lambda: self.chart.step(1))
        self.chart.sectionChanged.connect(self._section_changed)
        self.ground_combo.currentIndexChanged.connect(self.rebuild)
        self.design_combo.currentIndexChanged.connect(self.rebuild)
        self.ground_only.toggled.connect(self.rebuild)
        self.exaggeration.valueChanged.connect(self._exaggeration_changed)

    def has_sections(self):
        return bool(self.sections)

    def set_alignment(self, document, alignment, profiles, design_profile):
        """Load sections lazily for the alignment (cached per file and alignment)."""
        key = (getattr(document, "path", None), alignment)
        self.document = document
        self.alignment = alignment
        self.design_profile = design_profile
        if key == self._loaded_key:
            self.rebuild()
            return
        self._loaded_key = key
        self.sections, _warnings, self.surface_names, _shapes = read_corridor_sections(document.root, alignment)
        ground, design = guess_surfaces(
            self.sections, [p for p in profiles if p.kind == "surface"], roadway_surface_refs(document.root)
        )
        for combo, default in ((self.ground_combo, ground), (self.design_combo, design)):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("(none)", None)
            for name in self.surface_names:
                combo.addItem(name, name)
            if default in self.surface_names:
                combo.setCurrentIndex(self.surface_names.index(default) + 1)
            combo.blockSignals(False)
        self.rebuild()

    def views(self, require_ground=None):
        ground = self.ground_combo.currentData()
        design = self.design_combo.currentData()
        require = self.ground_only.isChecked() if require_ground is None else require_ground
        result = []
        for section in self.sections:
            if require and ground and section.surface(ground) is None:
                continue
            grade = self.design_profile.elevation_at(section.station) if self.design_profile else None
            result.append(build_section_view(section, ground, design, grade, True))
        return result

    def rebuild(self):
        station = self.chart.current()["station"] if self.chart.current() else None
        unit = self.document.horizontal_unit if self.document else None
        self.chart.set_views(self.views(), unit, self.exaggeration.value())
        if station is not None:
            self.chart.show_station(station)
        self._update_label()

    def _exaggeration_changed(self, value):
        self.chart.style.exaggeration = value
        self.chart.update()

    def show_station(self, station):
        if self.chart.views and station is not None:
            self.chart.show_station(station)
            self._update_label()

    def hover_station(self, station):
        if self.follow_check.isChecked():
            self.show_station(station)

    def _section_changed(self, station):
        self._update_label()
        self.stationChanged.emit(station)

    def _update_label(self):
        view = self.chart.current()
        unit = self.document.horizontal_unit if self.document else None
        total = len(self.chart.views)
        self.station_label.setText(
            f"CH {station_label(view['station'], unit)}  ({self.chart.index + 1}/{total})" if view else "No sections"
        )

    # ----------------------------------------------------------- export
    def _path(self, title, suffix, filters):
        settings = QgsSettings()
        view = self.chart.current()
        base = "sections" if suffix == ".pdf" else f"section_{station_label(view['station'] if view else 0).replace('+', '_')}"
        start = os.path.join(settings.value(f"{SETTINGS}/export_dir", ""), base + suffix)
        path, _ = QFileDialog.getSaveFileName(self, title, start, filters)
        if path:
            settings.setValue(f"{SETTINGS}/export_dir", os.path.dirname(path))
            if not path.lower().endswith(suffix):
                path += suffix
        return path

    def _style(self):
        return SectionStyle(None, self.exaggeration.value(), self.document.horizontal_unit if self.document else None,
                            False, material_colors(self.chart.views))

    def write_png(self, path):
        view = self.chart.current()
        return view is not None and section_image(view, self._style(), 1400, 620, 2.0).save(path, "PNG")

    def write_svg(self, path, width=1000, height=460):
        view = self.chart.current()
        if view is None or QSvgGenerator is None:
            return False
        from .section_chart import draw_section

        generator = QSvgGenerator()
        generator.setFileName(path)
        generator.setSize(QSize(width, height))
        generator.setViewBox(QRectF(0, 0, width, height))
        painter = QPainter(generator)
        draw_section(painter, QRectF(0, 0, width, height), view, self._style())
        painter.end()
        return True

    def write_sheets(self, path, page="A3 landscape", per_page=6):
        views = self.views()
        return write_section_sheets(
            path,
            views,
            page,
            per_page,
            self.exaggeration.value(),
            self.document.horizontal_unit if self.document else None,
            "Cross-sections",
            self.alignment or "",
        )

    def export_png(self):
        path = self._path("Export section", ".png", "PNG image (*.png)")
        if path and self.write_png(path):
            self.message.emit(f"Saved {path}")

    def export_svg(self):
        path = self._path("Export section", ".svg", "SVG drawing (*.svg)")
        if path and self.write_svg(path):
            self.message.emit(f"Saved {path}")

    def export_sheets(self):
        if not self.chart.views:
            return
        path = self._path("Export section sheets", ".pdf", "PDF (*.pdf)")
        if not path:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            pages = self.write_sheets(path)
        finally:
            QApplication.restoreOverrideCursor()
        self.message.emit(f"Saved {len(self.chart.views)} sections on {pages} sheets: {path}")
