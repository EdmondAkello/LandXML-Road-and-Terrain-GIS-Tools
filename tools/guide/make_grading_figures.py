"""Produce the grading figures and reports for the user guide from the synthetic grading demo.

    python tools/guide/make_grading_demo.py out/demo_grading.xml
    QT_QPA_PLATFORM=offscreen python tools/guide/make_grading_figures.py out/demo_grading.xml out
    node tools/guide/capture.js out          # report figures (fig_grading_* entries)

Writes fig_grading_map.png and fig_grading_change.png (QGIS map renders) and the
HTML reports that capture.js turns into report figures. Uses the plugin from this
checkout. All data are invented.
"""

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
spec = importlib.util.spec_from_file_location("landxml_plugin", os.path.join(ROOT, "__init__.py"), submodule_search_locations=[ROOT])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

from qgis.core import (  # noqa: E402
    QgsApplication,
    QgsColorRampShader,
    QgsCoordinateReferenceSystem,
    QgsFillSymbol,
    QgsMapRendererParallelJob,
    QgsMapSettings,
    QgsPalLayerSettings,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsRasterLayer,
    QgsRasterShader,
    QgsSingleBandPseudoColorRenderer,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtCore import QSize  # noqa: E402
from qgis.PyQt.QtGui import QColor, QFont  # noqa: E402

app = QgsApplication([], True)
app.initQgis()

from landxml_plugin.provider import LandXMLTinToGeoTIFFProvider  # noqa: E402

SOURCE, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)
CRS = QgsCoordinateReferenceSystem("EPSG:32737")
SWAP = 1  # Civil 3D stores northing first
provider = LandXMLTinToGeoTIFFProvider()
provider.loadAlgorithms()
ALG = {a.name(): a for a in provider.algorithms()}


def run(name, **params):
    params.setdefault("INPUT", SOURCE)
    params.setdefault("METHOD", SWAP)
    params.setdefault("OUTPUT_CRS", CRS)
    return ALG[name].processAlgorithm(params, QgsProcessingContext(), QgsProcessingFeedback())


def path(name):
    return os.path.join(OUT, name)


run("landxml_grading_quantities", VOLUME_SURFACE="Demo Earthworks", REPORT=path("grading_quantities.html"))
platforms = run("landxml_platform_levels", DESIGN_SURFACE="Demo FG", MIN_AREA=100,
                OUTPUT=path("platforms.gpkg"), REPORT=path("platforms.html"))["OUTPUT"]
run("landxml_basin_storage", BASIN_SURFACE="Demo Pond", REQUIRED_VOLUME=400, FREEBOARD=0.5, STAGE_STEP=0.25,
    REPORT=path("basin.html"))
run("landxml_earthwork_balance", VOLUME_SURFACES="Platform A Earthworks; Platform B Earthworks", REPORT=path("balance.html"))
zones = QgsVectorLayer(platforms, "zones", "ogr")
run("landxml_grading_revision_comparison", SURFACE_A="Demo Earthworks Rev A", SURFACE_B="Demo Earthworks",
    LABEL_A="Rev A", LABEL_B="Rev B", ZONES=zones, ZONE_FIELD="platform", OUTPUT="memory:",
    CHANGE_RASTER=path("change.tif"), RESOLUTION=1.0, REPORT=path("comparison.html"))
run("landxml_tin_to_geotiff", SURFACE="Demo Earthworks", RESOLUTION=1.0, OUTPUT=path("depth.tif"))


def diverging(layer, limit, low="#d9822b", high="#3a9e5f"):
    ramp = QgsColorRampShader(-limit, limit)
    ramp.setColorRampType(QgsColorRampShader.Type.Interpolated)
    ramp.setColorRampItemList([QgsColorRampShader.ColorRampItem(-limit, QColor(low), f"{-limit:g}"),
                               QgsColorRampShader.ColorRampItem(0, QColor("#f7f7f2"), "0"),
                               QgsColorRampShader.ColorRampItem(limit, QColor(high), f"{limit:g}")])
    shader = QgsRasterShader()
    shader.setRasterShaderFunction(ramp)
    layer.setRenderer(QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader))


def outlines(labels=True):
    layer = QgsVectorLayer(platforms, "platforms", "ogr")
    layer.renderer().setSymbol(QgsFillSymbol.createSimple(
        {"color": "0,0,0,0", "outline_color": "#1d2430", "outline_width": "0.5"}))
    if labels:
        settings = QgsPalLayerSettings()
        settings.fieldName = "platform || '  ' || format_number(level_mean, 2) || ' · ' || format_number(fall_pct, 1) || ' %'"
        settings.isExpression = True
        fmt = QgsTextFormat()
        fmt.setFont(QFont("DejaVu Sans"))
        fmt.setSize(9)
        buffer = QgsTextBufferSettings()
        buffer.setEnabled(True)
        buffer.setColor(QColor("white"))
        fmt.setBuffer(buffer)
        settings.setFormat(fmt)
        layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        layer.setLabelsEnabled(True)
    return layer


def render(layers, name, extent_layer):
    settings = QgsMapSettings()
    settings.setLayers(layers)
    settings.setDestinationCrs(CRS)
    extent = extent_layer.extent()
    extent.grow(6)
    settings.setExtent(extent)
    settings.setOutputSize(QSize(1600, 1000))
    settings.setBackgroundColor(QColor("white"))
    settings.setFlag(QgsMapSettings.Flag.Antialiasing, True)
    job = QgsMapRendererParallelJob(settings)
    job.start()
    job.waitForFinished()
    job.renderedImage().save(path(name))


depth = QgsRasterLayer(path("depth.tif"), "depth")
diverging(depth, 4.0)
render([outlines(), depth], "fig_grading_map.png", depth)
change = QgsRasterLayer(path("change.tif"), "change")
diverging(change, 0.6)
render([outlines(False), change], "fig_grading_change.png", depth)
print("done", sorted(f for f in os.listdir(OUT) if f.endswith((".png", ".html"))))
