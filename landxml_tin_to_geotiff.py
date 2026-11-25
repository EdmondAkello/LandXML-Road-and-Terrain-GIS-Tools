import os

from qgis.PyQt import sip
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QMenu
try:  # Qt 6 moved QAction to QtGui.
    from qgis.PyQt.QtGui import QAction
except ImportError:  # Qt 5
    from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsApplication

from .gui.dock_utils import close_pop_out, reveal
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
        self.grading_editor = None
        self.workflow = None
        self.results = {}
        self.actions = []
        self.toolbar_action = None
        self.toolbar_menu = None

    def initGui(self):
        provider = LandXMLTinToGeoTIFFProvider()
        if QgsApplication.processingRegistry().addProvider(provider):
            self.provider = provider
        if self.iface is None:
            return
        main_icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
        window = self.iface.mainWindow()

        def make(icon_name, text, tip, slot):
            action = QAction(QIcon(os.path.join(os.path.dirname(__file__), "icons", icon_name + ".svg")), text, window)
            action.setToolTip(tip)
            action.triggered.connect(slot)
            return action

        workflow_action = make("workflow", "Workflow (Road and Site Design Steps)",
                               "The road and site design steps in order, each one click from the last: ground, plan, "
                               "profile, sections, reports, export", self.show_workflow)
        viewer_action = make("profile_viewer", "LandXML Profile Viewer and Designer",
                             "Long sections of a LandXML alignment: existing ground and design profiles, cut/fill, "
                             "cross-sections, and a Design tab to draw a new vertical profile and save it to LandXML",
                             self.show_viewer)
        pick_action = make("pick_profile", "Pick Alignment Profile on Map",
                           "Click an alignment in the map to open its long-section at that station", self.pick_on_map)
        plan_action = make("plan_editor", "Plan Editor (Horizontal Alignment)",
                           "Design a horizontal alignment on the map by its intersection points, with live curve "
                           "fitting, and save it to LandXML", self.show_plan_editor)
        editor_action = make("section_editor", "Corridor Section Editor",
                             "Design the road cross-section on the real ground (lanes, shoulders, kerbs, footways, "
                             "drains, medians, daylight) and build the corridor to LandXML with quantities",
                             self.show_section_editor)
        grading_action = make("grading_editor", "Grading Editor (Pads and Graded Lines)",
                              "Draw and edit pads and graded lines (ramps, roads, bunds, swales) on the map with a level "
                              "at every vertex, live cut and fill, and build them to LandXML for Civil 3D",
                              self.show_grading_editor)
        locator_action = make("locator", "Station && Offset Locator",
                              "Locate map clicks or entered station/offset positions along a LandXML alignment",
                              self.show_locator)
        guide_action = make("guide", "User Guide", "Open the user guide (installed with the plugin; works offline)",
                            self.show_guide)
        tools = [workflow_action, viewer_action, pick_action, plan_action, editor_action, grading_action, locator_action,
                 guide_action]
        for action in tools:
            self.iface.addPluginToMenu(MENU, action)
            self.actions.append(action)
        # One toolbar button: its drop-down lists every tool with its own icon.
        self.toolbar_menu = QMenu("LandXML Road && Terrain", window)
        for action in tools:
            if action in (guide_action, viewer_action):
                self.toolbar_menu.addSeparator()
            self.toolbar_menu.addAction(action)
        self.toolbar_action = QAction(main_icon, "LandXML Road && Terrain", window)
        self.toolbar_action.setToolTip("LandXML Road & Terrain: Profile Viewer, Plan Editor, Corridor Section Editor, "
                                       "Station & Offset Locator and the User Guide")
        self.toolbar_action.setMenu(self.toolbar_menu)
        self.toolbar_action.triggered.connect(self._toolbar_clicked)
        self.iface.addToolBarIcon(self.toolbar_action)
        try:                                     # open the menu on a plain click too
            from qgis.PyQt.QtWidgets import QToolButton
            button = self.iface.pluginToolBar().widgetForAction(self.toolbar_action)
            if isinstance(button, QToolButton):
                button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        except (AttributeError, RuntimeError, TypeError):
            pass
        self._welcome_once()

    def _toolbar_clicked(self, *_args):
        menu = getattr(self, "toolbar_menu", None)
        if menu is not None:
            from qgis.PyQt.QtGui import QCursor
            menu.popup(QCursor.pos())

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
            self.viewer.design_panel.saved.connect(lambda path, alignment, _p: self._record("profile", path, alignment))
            self.iface.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.viewer)
        reveal(self.viewer)
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
            self.section_editor.design_profile.connect(lambda path, alignment: self._ensure_viewer().design(path, alignment))
            self.section_editor.built.connect(lambda path, alignment: self._record("corridor", path, alignment))
            self.iface.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.section_editor)
        reveal(self.section_editor)
        return self.section_editor

    def _profile_saved(self, path, alignment, profile):
        """A design profile saved in the Profile Viewer goes on into an open Section Editor on that alignment."""
        if self.section_editor is not None and not sip.isdeleted(self.section_editor):
            self.section_editor.adopt_profile(path, alignment, profile)

    def show_plan_editor(self):
        if self.plan_editor is None:
            from .gui.plan_editor import PlanEditorDock

            self.plan_editor = PlanEditorDock(self.iface, self.iface.mainWindow())
            self.plan_editor.design_profile.connect(lambda path, name: self._ensure_viewer().design(path, name))
            self.plan_editor.saved.connect(lambda path, name: self._record("plan", path, name))
            self.plan_editor.to_section_editor.connect(lambda path, _name: self.show_section_editor().set_file(path))
            self.iface.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.plan_editor)
        reveal(self.plan_editor)
        return self.plan_editor

    def show_grading_editor(self):
        if self.grading_editor is None:
            from .gui.grading_editor import GradingEditorDock

            self.grading_editor = GradingEditorDock(self.iface, self.iface.mainWindow())
            self.grading_editor.built.connect(lambda path: self._record("site", path, ""))
            self.iface.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.grading_editor)
        reveal(self.grading_editor)
        return self.grading_editor

    def _record(self, kind, path, name):
        """A step's result (the file an editor wrote), for the workflow panel."""
        self.results[kind] = (path, name)
        if self.workflow is not None and not sip.isdeleted(self.workflow):
            self.workflow.set_file(kind, path, name)

    def show_workflow(self):
        if self.workflow is None:
            from .gui.workflow import WorkflowDock

            self.workflow = WorkflowDock(self, self.iface.mainWindow())
            for kind, (path, name) in self.results.items():
                self.workflow.set_file(kind, path, name)
            self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.workflow)
        reveal(self.workflow)
        return self.workflow

    def show_locator(self):
        if self.locator is None:
            from .gui.station_locator import StationOffsetDock

            self.locator = StationOffsetDock(self.iface, self.iface.mainWindow())
            self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.locator)
        reveal(self.locator)

    def unload(self):
        for action in self.actions:
            if self.iface is not None:
                self.iface.removePluginMenu(MENU, action)
        self.actions = []
        if getattr(self, "toolbar_action", None) is not None:
            if self.iface is not None:
                self.iface.removeToolBarIcon(self.toolbar_action)
            self.toolbar_action = None
            self.toolbar_menu = None
        if self.locator is not None:
            if not sip.isdeleted(self.locator):
                close_pop_out(self.locator)
                self.locator.cleanup()
                self.iface.removeDockWidget(self.locator)
                self.locator.deleteLater()
            self.locator = None
        if self.plan_editor is not None:
            if not sip.isdeleted(self.plan_editor):
                close_pop_out(self.plan_editor)
                self.plan_editor.cleanup()
                if self.iface is not None:
                    self.iface.removeDockWidget(self.plan_editor)
                self.plan_editor.deleteLater()
            self.plan_editor = None
        if self.workflow is not None:
            if not sip.isdeleted(self.workflow):
                close_pop_out(self.workflow)
                if self.iface is not None:
                    self.iface.removeDockWidget(self.workflow)
                self.workflow.deleteLater()
            self.workflow = None
        if self.grading_editor is not None:
            if not sip.isdeleted(self.grading_editor):
                close_pop_out(self.grading_editor)
                self.grading_editor.cleanup()
                if self.iface is not None:
                    self.iface.removeDockWidget(self.grading_editor)
                self.grading_editor.deleteLater()
            self.grading_editor = None
        if self.section_editor is not None:
            if not sip.isdeleted(self.section_editor):
                close_pop_out(self.section_editor)
                self.section_editor.cleanup()
                if self.iface is not None:
                    self.iface.removeDockWidget(self.section_editor)
                self.section_editor.deleteLater()
            self.section_editor = None
        if self.viewer is not None:
            if not sip.isdeleted(self.viewer):
                close_pop_out(self.viewer)
                self.viewer.cleanup()
                if self.iface is not None:
                    self.iface.removeDockWidget(self.viewer)
                self.viewer.deleteLater()
            self.viewer = None
        provider = self.provider
        self.provider = None
        if provider is not None and not sip.isdeleted(provider):
            QgsApplication.processingRegistry().removeProvider(provider)
