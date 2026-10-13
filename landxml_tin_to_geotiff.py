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
        self.actions = []

    def initGui(self):
        provider = LandXMLTinToGeoTIFFProvider()
        if QgsApplication.processingRegistry().addProvider(provider):
            self.provider = provider
        if self.iface is None:
            return
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
        viewer_action = QAction(icon, "LandXML Profile Viewer", self.iface.mainWindow())
        viewer_action.setToolTip("Show existing-ground and design profiles, cut/fill and exports")
        viewer_action.triggered.connect(self.show_viewer)
        pick_action = QAction(icon, "Pick Alignment Profile on Map", self.iface.mainWindow())
        pick_action.setToolTip("Click an alignment in the map to open its long-section at that station")
        pick_action.triggered.connect(self.pick_on_map)
        for action in (viewer_action, pick_action):
            self.iface.addPluginToMenu(MENU, action)
            self.actions.append(action)
        self.iface.addToolBarIcon(pick_action)

    def _ensure_viewer(self):
        if self.viewer is None:
            from .gui.profile_viewer import ProfileViewerDock

            self.viewer = ProfileViewerDock(self.iface, self.iface.mainWindow())
            self.iface.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.viewer)
        self.viewer.show()
        self.viewer.raise_()
        return self.viewer

    def show_viewer(self):
        self._ensure_viewer()

    def pick_on_map(self):
        self._ensure_viewer().pick_button.setChecked(True)

    def unload(self):
        for action in self.actions:
            if self.iface is not None:
                self.iface.removePluginMenu(MENU, action)
                self.iface.removeToolBarIcon(action)
        self.actions = []
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
