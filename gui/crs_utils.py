"""Design CRS handling for the editors: declared, chosen, never inferred; transforms to and from the map."""

from __future__ import annotations

from qgis.core import QgsCoordinateTransform, QgsPointXY, QgsProject
from qgis.PyQt.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                                 QVBoxLayout)
from qgis.PyQt.QtCore import Qt


def declared_epsg(path):
    """The EPSG code a LandXML file declares in <CoordinateSystem epsgCode=…>, or None (read from the head only)."""
    import re

    with open(path, "rb") as handle:
        head = handle.read(200000).decode("utf-8", "replace")
    match = re.search(r"<(?:\w+:)?CoordinateSystem\b[^>]*\bepsgCode\s*=\s*\"(\d+)\"", head)
    return int(match.group(1)) if match else None


def crs_fits(crs, xs, ys):
    """False when a geographic CRS is declared for coordinates that cannot be degrees (a contradiction to report)."""
    if crs is None or not crs.isValid():
        return False
    if crs.isGeographic():
        return all(-180 <= x <= 180 for x in xs) and all(-90 <= y <= 90 for y in ys)
    return True


def ask_crs(parent, message, preset=None, projected=True):
    """Ask for a CRS with the QGIS selector. Returns a valid CRS or None. Geographic CRSs are refused when projected."""
    from qgis.gui import QgsProjectionSelectionDialog

    while True:
        dialog = QgsProjectionSelectionDialog(parent)
        dialog.setWindowTitle("Design coordinate system")
        try:
            dialog.setMessage(message)
        except AttributeError:
            pass
        if preset is not None and preset.isValid():
            dialog.setCrs(preset)
        if not dialog.exec():
            return None
        crs = dialog.crs()
        if not crs.isValid():
            return None
        if projected and crs.isGeographic():
            message = (f"{crs.authid()} is geographic (degrees). Choose a projected CRS in metres or feet, "
                       "such as the UTM zone of the site.")
            preset = None
            continue
        return crs


class Transformer:
    """Design CRS <-> map canvas CRS."""

    def __init__(self, design_crs, canvas):
        self.design_crs = design_crs
        self.canvas = canvas
        self.refresh()

    def refresh(self):
        project = QgsProject.instance()
        target = self.canvas.mapSettings().destinationCrs() if self.canvas is not None else self.design_crs
        self.same = (not self.design_crs.isValid()) or (not target.isValid()) or target == self.design_crs
        if not self.same:
            self.forward = QgsCoordinateTransform(self.design_crs, target, project)
            self.back = QgsCoordinateTransform(target, self.design_crs, project)

    def to_map(self, x, y):
        if self.same:
            return QgsPointXY(x, y)
        return self.forward.transform(QgsPointXY(x, y))

    def to_design(self, point):
        if self.same:
            return QgsPointXY(point.x(), point.y())
        return self.back.transform(QgsPointXY(point.x(), point.y()))

    def to_layer(self, x, y, layer_crs):
        if layer_crs == self.design_crs or not layer_crs.isValid():
            return QgsPointXY(x, y)
        return QgsCoordinateTransform(self.design_crs, layer_crs, QgsProject.instance()).transform(QgsPointXY(x, y))


class PickDialog(QDialog):
    """Choose one item from grouped lists, with a filter box (alignments first, then feature lines)."""

    def __init__(self, parent, title, groups, note=""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(520, 560)
        layout = QVBoxLayout(self)
        if note:
            label = QLabel(note)
            label.setWordWrap(True)
            layout.addWidget(label)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter by name…")
        layout.addWidget(self.filter)
        self.list = QListWidget()
        layout.addWidget(self.list, 1)
        first = None
        for heading, items in groups:
            if not items:
                continue
            head = QListWidgetItem(f"{heading} ({len(items)})")
            head.setFlags(Qt.ItemFlag.NoItemFlags)
            font = head.font()
            font.setBold(True)
            head.setFont(font)
            self.list.addItem(head)
            for text, key in items:
                item = QListWidgetItem("    " + text)
                item.setData(Qt.ItemDataRole.UserRole, key)
                self.list.addItem(item)
                if first is None:
                    first = item
        if first is not None:
            self.list.setCurrentItem(first)
        self.filter.textChanged.connect(self._filter)
        self.list.itemDoubleClicked.connect(lambda _i: self.accept())
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _filter(self, text):
        text = text.lower()
        for i in range(self.list.count()):
            item = self.list.item(i)
            key = item.data(Qt.ItemDataRole.UserRole)
            if key is None:
                continue
            item.setHidden(bool(text) and text not in item.text().lower())

    def choice(self):
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None


def pick(parent, title, groups, note=""):
    """Show a PickDialog; returns the chosen key or None."""
    dialog = PickDialog(parent, title, groups, note)
    if not dialog.exec():
        return None
    return dialog.choice()


__all__ = ["declared_epsg", "crs_fits", "ask_crs", "Transformer", "PickDialog", "pick"]
