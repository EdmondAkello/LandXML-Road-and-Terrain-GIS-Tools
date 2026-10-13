"""Map tool: click an alignment-derived feature to open its long-section."""

from __future__ import annotations

from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsCsException,
    QgsFeatureRequest,
    QgsGeometry,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.gui import QgsMapTool
from qgis.PyQt.QtCore import Qt, pyqtSignal

ALIGNMENT_FIELDS = ("alignment_name", "alignment")
SOURCE_PROPERTY = "landxml/source_path"


def alignment_field(layer):
    names = [field.name() for field in layer.fields()]
    return next((name for name in ALIGNMENT_FIELDS if name in names), None)


class AlignmentPickTool(QgsMapTool):
    """Emit (layer, feature, point in layer CRS) for the nearest pickable feature."""

    picked = pyqtSignal(object, object, object)
    missed = pyqtSignal()

    def __init__(self, canvas):
        super().__init__(canvas)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def candidate_layers(self):
        layers = []
        for layer in self.canvas().layers():
            if not isinstance(layer, QgsVectorLayer):
                continue
            if layer.geometryType() not in (Qgis.GeometryType.Line, Qgis.GeometryType.Point):
                continue
            if alignment_field(layer) is None:
                continue
            layers.append(layer)
        return layers

    def canvasReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        result = self.nearest(event.mapPoint())
        if result is None:
            self.missed.emit()
        else:
            self.picked.emit(*result)

    def nearest(self, map_point):
        canvas = self.canvas()
        radius = QgsMapTool.searchRadiusMU(canvas)
        best = None
        for layer in self.candidate_layers():
            transform = QgsCoordinateTransform(
                canvas.mapSettings().destinationCrs(), layer.crs(), QgsProject.instance()
            )
            try:
                point = transform.transform(map_point)
                corner = transform.transform(map_point.x() + radius, map_point.y())
            except QgsCsException:
                continue
            layer_radius = max(abs(corner.x() - point.x()), 1e-9)
            rect = QgsRectangle(
                point.x() - layer_radius, point.y() - layer_radius, point.x() + layer_radius, point.y() + layer_radius
            )
            target = QgsGeometry.fromPointXY(point)
            request = QgsFeatureRequest().setFilterRect(rect).setLimit(200)
            for feature in layer.getFeatures(request):
                geometry = feature.geometry()
                if geometry is None or geometry.isEmpty():
                    continue
                distance = geometry.distance(target) / layer_radius
                # Prefer lines (they carry stationing) over points at similar distance.
                if layer.geometryType() == Qgis.GeometryType.Point:
                    distance += 0.25
                if distance <= 1.25 and (best is None or distance < best[0]):
                    best = (distance, layer, feature, point)
        return None if best is None else best[1:]
