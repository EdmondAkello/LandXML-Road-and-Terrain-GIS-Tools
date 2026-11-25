"""Workflow panel: the road and site design flows as numbered steps, each one click from the last.

Road: ground → plan → profile → sections and corridor → reports and 3D → export (Civil 3D / OpenRoads / DXF /
Road Furniture Designer). Site: ground → grading objects → build → reports and 3D → export. The panel follows the
files each editor writes, so a step opens on the result of the one before it.
"""

from __future__ import annotations

import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (QFrame, QGridLayout, QLabel, QPushButton, QTabWidget, QToolButton, QVBoxLayout,
                                 QWidget, QMenu)
from qgis.gui import QgsDockWidget

from .dock_utils import icon, setup_dock

PROVIDER = "landxml_tin_to_geotiff"


class WorkflowDock(QgsDockWidget):
    """Steps call the plugin (``plugin.show_plan_editor()``…); ``set_file(kind, path, name)`` records results."""

    def __init__(self, plugin, parent=None):
        super().__init__("LandXML Road & Terrain – Workflow", parent)
        self.setObjectName("LandXMLWorkflow")
        self.plugin = plugin
        self.files = {}                         # kind -> (path, name)
        self.labels = {}
        self._build()

    def _build(self):
        body = QWidget()
        v = QVBoxLayout(body)
        tabs = QTabWidget()
        tabs.addTab(self._steps([
            ("ground", "1. Ground", "profile_viewer",
             "Load a DEM layer, or a LandXML with the survey surface (Civil 3D or OpenRoads).", None),
            ("plan", "2. Plan", "plan_editor",
             "Draw the alignment on the map, or start from an alignment, feature line or line layer.",
             self.plugin.show_plan_editor),
            ("profile", "3. Profile", "profile_viewer",
             "Design the vertical profile on the ground long section (PVIs, curves, K).", self._profile),
            ("corridor", "4. Sections and corridor", "section_editor",
             "Assemblies, regions, superelevation, features; Build writes the corridor, quantities and BoQ.",
             self._sections),
            ("reports", "5. Reports and 3D", "guide",
             "Road Design Report, design review, section sheets and the 3D scene of the built corridor.", self._reports),
            ("export", "6. Export", "workflow",
             "Civil 3D or OpenRoads (by stage), DXF, or on to the Road Furniture Designer for signs.", self._export),
        ], "road"), icon("plan_editor"), "Road")
        tabs.addTab(self._steps([
            ("site_ground", "1. Ground", "profile_viewer", "Load the DEM of the site as a layer.", None),
            ("site", "2. Pads and graded lines", "grading_editor",
             "Draw pads and ramps on the map, a level at every vertex; Build writes LandXML, GeoTIFF, DXF and quantities.",
             self.plugin.show_grading_editor),
            ("site_reports", "3. Reports and 3D", "guide",
             "Grading quantities, platform levels, Site Grading Report and the 3D scene of the built design.",
             self._site_reports),
            ("site_export", "4. Export", "workflow", "The build is already LandXML for Civil 3D and DXF; DXF of any "
                                                     "LandXML here.", self._site_export),
        ], "site"), icon("grading_editor"), "Site")
        v.addWidget(tabs)
        guide = QPushButton("User guide")
        guide.clicked.connect(self.plugin.show_guide)
        v.addWidget(guide)
        v.addStretch(1)
        setup_dock(self, body, "workflow")

    def _steps(self, steps, flow):
        page = QWidget()
        grid = QGridLayout(page)
        for row, (key, title, icon_name, text, slot) in enumerate(steps):
            b = QToolButton()
            b.setIcon(icon(icon_name))
            b.setText(title)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.setMinimumWidth(190)
            if slot is not None:
                b.clicked.connect(slot)
            else:
                b.setEnabled(False)
            grid.addWidget(b, row * 2, 0)
            label = QLabel(text)
            label.setWordWrap(True)
            grid.addWidget(label, row * 2, 1)
            status = QLabel("")
            status.setStyleSheet("color: #2e7d32")
            status.setWordWrap(True)
            grid.addWidget(status, row * 2 + 1, 1)
            self.labels[key] = status
            line = QFrame()
            line.setFrameShape(QFrame.Shape.HLine)
            grid.addWidget(line, row * 2 + 1, 0)
        grid.setColumnStretch(1, 1)
        return page

    # ------------------------------------------------------------------ results of the steps
    def set_file(self, kind, path, name=""):
        self.files[kind] = (path, name)
        if kind in self.labels:
            self.labels[kind].setText("✓ " + os.path.basename(path) + (f" – {name}" if name else ""))

    def latest_road(self):
        for kind in ("corridor", "profile", "plan"):
            if kind in self.files:
                return self.files[kind]
        return None

    # ------------------------------------------------------------------ actions
    def _profile(self):
        latest = self.files.get("profile") or self.files.get("plan")
        if latest is None:
            self.plugin.show_viewer()
            return
        self.plugin._ensure_viewer().design(*latest)

    def _sections(self):
        editor = self.plugin.show_section_editor()
        latest = self.files.get("profile") or self.files.get("plan")
        if latest is not None and editor.file_edit.text() != latest[0]:
            editor.set_file(latest[0])

    def _dialog(self, algorithm, params):
        try:
            import processing
            processing.execAlgorithmDialog(f"{PROVIDER}:{algorithm}", params)
        except (ImportError, AttributeError, RuntimeError):
            pass

    def _menu(self, items):
        menu = QMenu(self)
        for text, slot in items:
            menu.addAction(text, slot)
        from qgis.PyQt.QtGui import QCursor
        menu.exec(QCursor.pos())

    def _reports(self):
        latest = self.latest_road()
        path = latest[0] if latest else ""
        self._menu([("Road Design Report…", lambda: self._dialog("landxml_design_report", {"INPUT": path})),
                    ("Geometric Design Review…", lambda: self._dialog("landxml_design_review", {"INPUT": path})),
                    ("Cross-Section Sheets…", lambda: self._dialog("landxml_cross_section_sheets", {"INPUT": path})),
                    ("Setting-Out Data…", lambda: self._dialog("landxml_setting_out", {"INPUT": path})),
                    ("Export 3D Scene and Viewer…", lambda: self._dialog("landxml_3d_scene", {"INPUT": path})),
                    ("Open in the Profile Viewer", lambda: latest and self.plugin._ensure_viewer().load(*latest))])

    def _export(self):
        latest = self.latest_road()
        path = latest[0] if latest else ""
        self._menu([("Civil 3D or OpenRoads, by stage… (Section Editor)", self._export_stage),
                    ("DXF…", lambda: self._dialog("landxml_export_dxf", {"INPUT": path})),
                    ("Setting-out data (Excel, report, total-station CSV)…",
                     lambda: self._dialog("landxml_setting_out", {"INPUT": path})),
                    ("Road Furniture Designer (signs and markings)", self._rfd)])

    def _export_stage(self):
        editor = self.plugin.show_section_editor()
        editor.export_button.showMenu()

    def _rfd(self):
        try:
            from qgis.utils import plugins
        except ImportError:
            plugins = {}
        rfd = next((p for key, p in plugins.items() if "road_furniture" in key.lower()), None)
        latest = self.latest_road()
        if rfd is None:
            self.labels["export"].setText("The Road Furniture Designer plugin is not installed. Open the corridor "
                                          f"LandXML ({latest[0] if latest else 'Build first'}) in it once installed.")
            return
        for method in ("toggle", "show", "run"):
            if hasattr(rfd, method):
                try:
                    getattr(rfd, method)(True) if method == "toggle" else getattr(rfd, method)()
                except TypeError:
                    getattr(rfd, method)()
                break
        if latest:
            self.labels["export"].setText(f"In the Road Furniture Designer, open {latest[0]} "
                                          f"and choose the alignment '{latest[1]}'.")

    def _site_reports(self):
        path = (self.files.get("site") or ("", ""))[0]
        self._menu([("Grading Quantities…", lambda: self._dialog("landxml_grading_quantities", {"INPUT": path})),
                    ("Platform Levels and Falls…", lambda: self._dialog("landxml_platform_levels", {"INPUT": path})),
                    ("Site Grading Report…", lambda: self._dialog("landxml_grading_report", {"INPUT": path})),
                    ("Export 3D Scene and Viewer (Site Grading)…", lambda: self._dialog("landxml_export_scene",
                                                                                         {"INPUT": path}))])

    def _site_export(self):
        path = (self.files.get("site") or ("", ""))[0]
        self._dialog("landxml_export_dxf", {"INPUT": path})


__all__ = ["WorkflowDock"]
