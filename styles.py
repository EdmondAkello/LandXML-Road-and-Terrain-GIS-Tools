"""Default road-review styles, persisted in GeoPackages after sinks close."""

import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsCategorizedSymbolRenderer, QgsLineSymbol, QgsMapLayer, QgsMarkerSymbol,
    QgsPalLayerSettings, QgsProcessingAlgorithm, QgsProcessingUtils, QgsProviderRegistry,
    QgsRendererCategory, QgsRuleBasedRenderer, QgsSingleSymbolRenderer, QgsTextBufferSettings, QgsTextFormat,
    QgsVectorLayerSimpleLabeling, QgsWkbTypes,
)
from .landxml.catalog import _tokens
from .landxml.quantity_units import LINEAR_UNITS
from .landxml.reports import station_label


PRESETS = {
    "alignments": ("#0072b2", 0.65),
    "alignment_elements": ("#0072b2", 0.45),
    "centerlines_3d": ("#0072b2", 0.65),
    "station_points": ("#333333", 1.8),
    "profiles": ("#0072b2", 0.5),
    "profile_controls": ("#d55e00", 2.2),
    "cross_sections": ("#7b3294", 0.35),
    "cross_sections_2d": ("#7b3294", 0.35),
    "section_points": ("#7b3294", 1.3),
    "surface_boundary": ("#666666", 0.45),
    "breaklines": ("#a6611a", 0.3),
    "feature_lines": ("#009e73", 0.35),
    "contours": ("#4d7854", 0.2),
}


def _symbol(layer, color, size):
    if layer.geometryType() == QgsWkbTypes.GeometryType.PointGeometry:
        return QgsMarkerSymbol.createSimple({"name": "circle", "color": color, "size": str(size),
                                             "outline_color": "white", "outline_width": "0.2"})
    return QgsLineSymbol.createSimple({"line_color": color, "line_width": str(size)})


def station_text(station, horizontal_unit):
    """Reuse source-unit stationing, omitting decimal zeros on whole stations."""
    label = station_label(station, horizontal_unit)
    if not label:
        return ""
    if (horizontal_unit or "").lower() not in LINEAR_UNITS:
        return f"{station:g}"
    return label.removesuffix(".00")


def apply_preset(layer, kind, contour_interval=5, surface_name=""):
    """Apply a preset using only fields available on this output layer."""
    color, size = PRESETS[kind]
    fields = set(layer.fields().names())
    symbol = _symbol(layer, color, size)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    role_fields = [name for name in ("surface_name", "profile_name") if name in fields]
    if role_fields:
        text = " || ' ' || ".join(f'coalesce("{name}", \'\')' for name in role_fields)
        expression = (
            f"CASE WHEN regexp_match(lower({text}), '(^|[^a-z])(eg|egl|ogl|existing|natural)([^a-z]|$)') THEN 'Existing ground' "
            f"WHEN regexp_match(lower({text}), '(datum|subgrade|formation|bottom)') THEN 'Datum / subgrade' "
            f"WHEN regexp_match(lower({text}), '(^|[^a-z])(fg|frl|design|proposed|finished|top)([^a-z]|$)') THEN 'Design / finished' "
            "ELSE 'Other' END"
        )
        categories = [QgsRendererCategory(label, _symbol(layer, shade, size), label) for label, shade in (
            ("Existing ground", "#a6611a"), ("Design / finished", "#0072b2"),
            ("Datum / subgrade", "#7b3294"), ("Other", color),
        )]
        layer.setRenderer(QgsCategorizedSymbolRenderer(expression, categories))
    label, is_expression = None, False
    if "label_text" in fields:
        label = "label_text"
    elif kind == "station_points" and "station_label" in fields:
        label = "station_label"
    elif kind == "station_points" and "station" in fields:
        label, is_expression = 'to_string("station")', True
    elif kind == "profile_controls" and "control_type" in fields:
        label = "control_type"
    elif kind == "alignments":
        label = next((name for name in ("name", "alignment_name") if name in fields), None)
    if kind == "contours" and "elev" in fields:
        tokens = _tokens(surface_name)
        existing = bool(tokens & {"eg", "egl", "ogl", "existing", "natural", "survey"})
        if existing:
            major_color, minor_color = "#7f3b08", "#a6611a"
        elif tokens & {"datum", "subgrade", "formation", "bottom"}:
            major_color, minor_color = "#5e2372", "#9c59b6"
        elif tokens & {"fg", "frl", "design", "proposed", "finished", "top"}:
            major_color, minor_color = "#00548a", "#0072b2"
        else:
            major_color, minor_color = "#315b38", "#4d7854"
        major = f'abs("elev" / {contour_interval * 5:g} - round("elev" / {contour_interval * 5:g})) < 0.000001'
        renderer = QgsRuleBasedRenderer(_symbol(layer, color, size))
        root = renderer.rootRule()
        root.removeChildAt(0)
        root.appendChild(QgsRuleBasedRenderer.Rule(_symbol(layer, major_color, 0.5), 0, 0, major, "Index contour (every fifth)"))
        minor_symbol = _symbol(layer, minor_color, 0.28)
        if existing:
            minor_symbol.symbolLayer(0).setPenStyle(Qt.PenStyle.DashLine)
        root.appendChild(QgsRuleBasedRenderer.Rule(minor_symbol, 0, 0, "ELSE", "Minor contour"))
        layer.setRenderer(renderer)
        label, is_expression = f'CASE WHEN {major} THEN to_string(round("elev", 6)) END', True
    if label:
        settings = QgsPalLayerSettings()
        settings.fieldName, settings.isExpression = label, is_expression
        settings.placement = (QgsPalLayerSettings.Placement.OrderedPositionsAroundPoint
                              if layer.geometryType() == QgsWkbTypes.GeometryType.PointGeometry
                              else QgsPalLayerSettings.Placement.Line)
        text = QgsTextFormat()
        text.setSize(9 if "label_text" in fields else 8)
        text.setColor(QColor("#333333"))
        buffer = QgsTextBufferSettings()
        buffer.setEnabled(True)
        buffer.setSize(0.8)
        buffer.setColor(QColor("white"))
        text.setBuffer(buffer)
        settings.setFormat(text)
        layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        layer.setLabelsEnabled(True)
    layer.triggerRepaint()


class StyledAlgorithm(QgsProcessingAlgorithm):
    def set_output_style(self, destination, kind, contour_interval=5, surface_name=""):
        if destination:
            if not hasattr(self, "_style_outputs"):
                self._style_outputs = []
            self._style_outputs.append((destination, kind, contour_interval, surface_name))

    def postProcessAlgorithm(self, context, feedback):
        # Sinks have closed; styling here also covers file-only and model/script runs.
        outputs = getattr(self, "_style_outputs", [])
        self._style_outputs = []
        for destination, kind, interval, surface_name in outputs:
            if feedback.isCanceled():
                break
            layer = QgsProcessingUtils.mapLayerFromString(destination, context, typeHint=QgsProcessingUtils.LayerHint.Vector)
            if layer is None or not layer.isValid():
                feedback.pushWarning(f"Could not style exported layer: {destination}")
                continue
            apply_preset(layer, kind, interval, surface_name)
            uri = QgsProviderRegistry.instance().decodeUri(layer.providerType(), layer.source())
            if os.path.splitext(uri.get("path", ""))[1].lower() == ".gpkg":
                categories = (QgsMapLayer.StyleCategory.Symbology | QgsMapLayer.StyleCategory.Labeling
                              | QgsMapLayer.StyleCategory.Rendering)
                error = layer.saveStyleToDatabase("LandXML " + kind, "LandXML road-review preset", True, "", categories)
                if error:
                    feedback.pushWarning(f"Could not save default style for {layer.name()}: {error}")
        return {}
