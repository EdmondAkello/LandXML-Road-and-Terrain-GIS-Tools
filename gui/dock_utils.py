"""Shared behaviour for the plugin's large docks.

* Content sits in a scroll area, so a dock keeps a small minimum size and docks at the bottom of the
  QGIS window instead of taking most of its height.
* Qt's own floating mode is switched off. Closing a floating dock window ran through
  ``QDockWidget::setFloating`` and crashed QGIS 4.2 (Qt 6.11) on Windows. Instead, **Pop out** moves
  the dock's content into an ordinary top-level window that can be moved to another display and
  maximised; closing that window (or **Dock back**) returns the content to the dock.
"""

import os

from qgis.PyQt.QtCore import QSettings, Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (QDockWidget, QHBoxLayout, QPushButton, QScrollArea, QToolButton, QVBoxLayout,
                                 QWidget)

ICON_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "icons")


def icon(name):
    """An icon from the plugin's icons folder (``plan_editor``, ``section_editor``…)."""
    return QIcon(os.path.join(ICON_DIR, name + ".svg"))


class PopOutWindow(QWidget):
    """A top-level window holding a dock's content while it is popped out."""

    def __init__(self, dock):
        super().__init__(None, Qt.WindowType.Window)
        self.dock = dock
        self.setWindowTitle(dock.windowTitle())
        self.setObjectName(dock.objectName() + "PopOut")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._closing = False

    def closeEvent(self, event):                   # noqa: N802 - Qt API
        if not self._closing:
            self._closing = True
            try:
                self.dock.dock_back()
            except RuntimeError:                    # the dock was deleted with the plugin
                pass
        event.accept()


def setup_dock(dock, body, name):
    """Install ``body`` in ``dock`` with a scroll area, a pop-out button and no Qt floating.

    ``name`` is the icon name. Adds ``dock.pop_out()``, ``dock.dock_back()`` and ``dock.popped_out``.
    """
    dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable
                     | QDockWidget.DockWidgetFeature.DockWidgetMovable)
    dock.setWindowIcon(icon(name))
    holder = QWidget()
    outer = QVBoxLayout(holder)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    bar = QHBoxLayout()
    bar.setContentsMargins(4, 2, 4, 0)
    bar.addStretch(1)
    pop = QToolButton()
    pop.setText("Pop out")
    pop.setToolTip("Open this panel in its own window, to move to another display and maximise. "
                   "Close that window to dock it back.")
    pop.setAutoRaise(True)
    bar.addWidget(pop)
    outer.addLayout(bar)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setWidget(body)
    body.setMinimumSize(body.minimumSizeHint())
    outer.addWidget(scroll, 1)
    dock.setWidget(holder)
    dock.setMinimumHeight(140)
    dock._content = holder
    dock._pop_button = pop
    dock._window = None
    dock.popped_out = False

    def pop_out():
        if dock.popped_out:
            if dock._window is not None:
                dock._window.raise_()
                dock._window.activateWindow()
            return dock._window
        window = PopOutWindow(dock)
        dock.setWidget(QWidget())
        window.layout().addWidget(holder)
        holder.show()
        pop.setText("Dock back")
        dock._window = window
        dock.popped_out = True
        geometry = QSettings().value("LandXMLRoadTerrain/popout/" + dock.objectName())
        if geometry is not None:
            window.restoreGeometry(geometry)
        else:
            window.resize(1400, 900)
        window.show()
        dock.hide()
        return window

    def dock_back():
        window = dock._window
        if window is None:
            return
        QSettings().setValue("LandXMLRoadTerrain/popout/" + dock.objectName(), window.saveGeometry())
        dock._window = None
        dock.popped_out = False
        window.layout().removeWidget(holder)
        dock.setWidget(holder)
        pop.setText("Pop out")
        dock.show()
        dock.raise_()
        if not window._closing:
            window._closing = True
            window.close()
        window.deleteLater()

    def toggle():
        dock_back() if dock.popped_out else pop_out()

    pop.clicked.connect(toggle)
    dock.pop_out = pop_out
    dock.dock_back = dock_back
    return holder


def close_pop_out(dock):
    """Close a popped-out window when the plugin unloads (content goes back to the dock first)."""
    try:
        if getattr(dock, "popped_out", False):
            dock.dock_back()
    except RuntimeError:
        pass


def reveal(dock):
    """Show a dock, or raise its popped-out window."""
    if getattr(dock, "popped_out", False) and dock._window is not None:
        dock._window.show()
        dock._window.raise_()
        dock._window.activateWindow()
        return
    dock.show()
    dock.raise_()


def push_button(text, slot, tip=None):
    b = QPushButton(text)
    b.clicked.connect(slot)
    if tip:
        b.setToolTip(tip)
    return b
