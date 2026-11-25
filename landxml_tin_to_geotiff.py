import os

from qgis.PyQt import sip
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
try:  # Qt 6 moved QAction to QtGui.
    from qgis.PyQt.QtGui import QAction
except ImportError:  # Qt 5
    from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsApplication

from .provider import LandXMLTinToGeoTIFFProvider

MENU = "LandXML Road && Terrain"


class LandXMLTinToGeoTIFFPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.viewer = None
        self.locator = None
        self.section_editor = None
        self.plan_editor = None
        self.actions = []

    def initGui(self):
        provider = LandXMLTinToGeoTIFFProvider()
        if QgsApplication.processingRegistry().addProvider(provider):
            self.provider = provider
        if self.iface is None:
            return
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
        viewer_action = QAction(icon, "LandXML Profile Viewer and Designer", self.iface.mainWindow())
        viewer_action.setToolTip("Long sections of a LandXML alignment: existing ground and design profiles, cut/fill, "
                                 "cross-sections, and a Design tab to draw a new vertical profile and save it to LandXML")
        viewer_action.triggered.connect(self.show_viewer)
        pick_action = QAction(icon, "Pick Alignment Profile on Map", self.iface.mainWindow())
        pick_action.setToolTip("Click an alignment in the map to open its long-section at that station")
        pick_action.triggered.connect(self.pick_on_map)
        guide_action = QAction(icon, "User Guide", self.iface.mainWindow())
        guide_action.setToolTip("Open the user guide (installed with the plugin; works offline)")
        guide_action.triggered.connect(self.show_guide)
        editor_action = QAction(icon, "Corridor Section Editor", self.iface.mainWindow())
        editor_action.setToolTip("Design the road cross-section on the real ground (lanes, shoulders, kerbs, footways, "
                                 "drains, medians, daylight) and build the corridor to LandXML with quantities")
        editor_action.triggered.connect(self.show_section_editor)
        plan_action = QAction(icon, "Plan Editor (Horizontal Alignment)", self.iface.mainWindow())
        plan_action.setToolTip("Design a horizontal alignment on the map by its intersection points, with live curve "
                               "fitting, and save it to LandXML")
        plan_action.triggered.connect(self.show_plan_editor)
        locator_action = QAction(icon, "Station && Offset Locator", self.iface.mainWindow())
        locator_action.setToolTip("Locate map clicks or entered station/offset positions along a LandXML alignment")
        locator_action.triggered.connect(self.show_locator)
        for action in (viewer_action, pick_action, plan_action, editor_action, locator_action, guide_action):
            self.iface.addPluginToMenu(MENU, action)
            self.actions.append(action)
        self.iface.addToolBarIcon(viewer_action)
        self.iface.addToolBarIcon(pick_action)
        self.iface.addToolBarIcon(plan_action)
        self.iface.addToolBarIcon(editor_action)
        self._welcome_once()

    def _welcome_once(self):
        """Once for each new version: a message bar naming what the plugin offers, with buttons to try it."""
        from qgis.core import QgsSettings

        from .project_settings import plugin_version

        version = plugin_version()
        settings = QgsSettings()
        key = "LandXMLRoadTerrain/welcomed_version"
        if not version or settings.value(key, "") == version:
            return
        settings.setValue(key, version)
        try:
            from qgis.core import Qgis
            from qgis.PyQt.QtWidgets import QPushButton

            bar = self.iface.messageBar()
            item = bar.createMessage(
                f"LandXML Road & Terrain GIS Tools {version}",
                "Processing tools are in the Toolbox under 'LandXML Road & Terrain'. The Profile Viewer (toolbar) shows long "
                "sections and, in its Design tab, lets you draw a vertical profile and save it to LandXML.")
            for text, slot in (("Open Profile Viewer", self.show_viewer), ("User Guide", self.show_guide)):
                button = QPushButton(text)
                button.clicked.connect(slot)
                item.layout().addWidget(button)
            bar.pushWidget(item, Qgis.MessageLevel.Info, 30)
        except (AttributeError, RuntimeError, TypeError):      # no message bar (a script or a test without the GUI)
            pass

    def _ensure_viewer(self):
        if self.viewer is None:
            from .gui.profile_viewer import ProfileViewerDock

            self.viewer = ProfileViewerDock(self.iface, self.iface.mainWindow())
            self.viewer.design_panel.saved.connect(self._profile_saved)
            self.iface.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.viewer)
        self.viewer.show()
        self.viewer.raise_()
        return self.viewer

    def show_viewer(self):
        self._ensure_viewer()

    def pick_on_map(self):
        self._ensure_viewer().pick_button.setChecked(True)

    def show_guide(self, *_args):
        from .project_settings import open_guide

        open_guide()

    def show_section_editor(self):
        if self.section_editor is None:
            from .gui.section_editor import SectionEditorDock

            self.section_editor = SectionEditorDock(self.iface, self.iface.mainWindow())
            self.section_editor.built.connect(lambda path, alignment: self._ensure_viewer().load(path, alignment))
            self.iface.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.section_editor)
        self.section_editor.show()
        self.section_editor.raise_()
        return self.section_editor

    def _profile_saved(self, path, alignment, profile):
        """A design profile saved in the Profile Viewer goes on into an open Section Editor on that alignment."""
        if self.section_editor is not None and not sip.isdeleted(self.section_editor):
            self.section_editor.adopt_profile(path, alignment, profile)

    def show_plan_editor(self):
        if self.plan_editor is None:
            from .gui.plan_editor import PlanEditorDock

            self.plan_editor = PlanEditorDock(self.iface, self.iface.mainWindow())
            self.plan_editor.saved.connect(lambda path, name: self._ensure_viewer().load(path, name))
            self.plan_editor.to_section_editor.connect(lambda path, _name: self.show_section_editor().set_file(path))
            self.iface.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.plan_editor)
        self.plan_editor.show()
        self.plan_editor.raise_()
        return self.plan_editor

    def show_locator(self):
        if self.locator is None:
            from .gui.station_locator import StationOffsetDock

            self.locator = StationOffsetDock(self.iface, self.iface.mainWindow())
            self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.locator)
        self.locator.show()
        self.locator.raise_()

    def unload(self):
        for action in self.actions:
            if self.iface is not None:
                self.iface.removePluginMenu(MENU, action)
                self.iface.removeToolBarIcon(action)
        self.actions = []
        if self.locator is not None:
            if not sip.isdeleted(self.locator):
                self.locator.cleanup()
                self.iface.removeDockWidget(self.locator)
                self.locator.deleteLater()
            self.locator = None
        if self.plan_editor is not None:
            if not sip.isdeleted(self.plan_editor):
                self.plan_editor.cleanup()
                if self.iface is not None:
                    self.iface.removeDockWidget(self.plan_editor)
                self.plan_editor.deleteLater()
            self.plan_editor = None
        if self.section_editor is not None:
            if not sip.isdeleted(self.section_editor):
                self.section_editor.cleanup()
                if self.iface is not None:
                    self.iface.removeDockWidget(self.section_editor)
                self.section_editor.deleteLater()
            self.section_editor = None
        if self.viewer is not None:
            if not sip.isdeleted(self.viewer):
                self.viewer.cleanup()
                if self.iface is not None:
                    self.iface.removeDockWidget(self.viewer)
                self.viewer.deleteLater()
            self.viewer = None
        provider = self.provider
        self.provider = None
        if provider is not None and not sip.isdeleted(provider):
            QgsApplication.processingRegistry().removeProvider(provider)
