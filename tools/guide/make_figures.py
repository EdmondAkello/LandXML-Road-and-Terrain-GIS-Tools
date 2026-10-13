"""Produce the user-guide figures from the synthetic demo road.

Run with the QGIS Python (offscreen):

    QT_QPA_PLATFORM=offscreen python tools/guide/make_figures.py <demo_road.xml> <output folder>

Writes PNG figures and the HTML reports that tools/guide/capture.js turns
into report figures. Uses the plugin from this checkout.
"""

import importlib.util
import os
import subprocess  # nosec B404 - developer tool
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
spec = importlib.util.spec_from_file_location("landxml_plugin", os.path.join(ROOT, "__init__.py"), submodule_search_locations=[ROOT])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

from qgis.core import (  # noqa: E402
    QgsApplication,
    QgsCategorizedSymbolRenderer,
    QgsColorRampShader,
    QgsCoordinateReferenceSystem,
    QgsLineSymbol,
    QgsMapRendererParallelJob,
    QgsMapSettings,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsRasterLayer,
    QgsRasterShader,
    QgsRendererCategory,
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
    context = QgsProcessingContext()
    result = ALG[name].processAlgorithm(params, context, QgsProcessingFeedback())
    return result, context


def path(name):
    return os.path.join(OUT, name)


# ---- processing outputs ---------------------------------------------------
gpkg_dir = path("gpkg")
run("landxml_complete_road_design", METHOD=SWAP, OUTPUT_CRS=CRS, RESOLUTION=5, CONTOUR_INTERVAL=5, OUTPUT_DIR=gpkg_dir)
run("landxml_profiles_to_vector", METHOD=SWAP, OUTPUT_CRS=CRS, VERTICAL_EXAGGERATION=5, MAP_OFFSET=150,
    OUTPUT=path("profile_graph.gpkg"), MAP_OUTPUT=path("profile_map.gpkg"), MAP_CONTROL_POINTS=path("profile_map_controls.gpkg"))
run("landxml_design_review", METHOD=SWAP, OUTPUT_CRS=CRS, TERRAIN=1, OUTPUT=path("review.gpkg"), MAP_OUTPUT=path("review_points.gpkg"),
    REPORT=path("review.html"), PROFILE_TXT=path("recommended_profile.txt"), PROFILE_LANDXML=path("recommended_profile.xml"))
run("landxml_design_report", STANDARD=1, TERRAIN=1, TITLE="Demo Road — design report", OUTPUT=path("design_report.html"))
run("landxml_profile_cut_fill", METHOD=SWAP, OUTPUT_CRS=CRS, FORMATION_WIDTH=10, OUTPUT=path("cutfill.gpkg"), REPORT=path("profile_cut_fill.html"))
run("landxml_corridor_quantities", METHOD=SWAP, OUTPUT_CRS=CRS, OUTPUT=path("quantities.gpkg"), SIDE_SLOPES=path("side_slopes.gpkg"),
    REPORT=path("corridor_quantities.html"))
run("landxml_cross_section_sheets", PAGE=1, PER_PAGE=6, TITLE="Demo Road", RANGE="1300-1520", OUTPUT=path("sheets.pdf"),
    REPORT=path("sections.html"), REPORT_LIMIT=40)
run("inspect_landxml", OUTPUT=path("inspect.json"))

# ---- map figure -----------------------------------------------------------
files = sorted(os.listdir(gpkg_dir))
print("complete export:", files)
dem_name = next(f for f in files if f.endswith(".tif") and "contour" not in f.lower())
dem = QgsRasterLayer(os.path.join(gpkg_dir, dem_name), "DEM")
gpkg = os.path.join(gpkg_dir, next(f for f in files if f.endswith(".gpkg") and "contour" not in f.lower()))
contours_file = next((os.path.join(gpkg_dir, f) for f in files if "contour" in f.lower()), None)

stats = dem.dataProvider().bandStatistics(1)
ramp = QgsColorRampShader(stats.minimumValue, stats.maximumValue)
ramp.setColorRampType(QgsColorRampShader.Type.Interpolated)
ramp.setColorRampItemList([
    QgsColorRampShader.ColorRampItem(stats.minimumValue, QColor("#2b7a4b")),
    QgsColorRampShader.ColorRampItem((stats.minimumValue + stats.maximumValue) / 2, QColor("#d9c98a")),
    QgsColorRampShader.ColorRampItem(stats.maximumValue, QColor("#a0643c")),
])
shader = QgsRasterShader()
shader.setRasterShaderFunction(ramp)
dem.setRenderer(QgsSingleBandPseudoColorRenderer(dem.dataProvider(), 1, shader))
dem.renderer().setOpacity(0.8)


def vector(source, layer=None, name=None):
    uri = f"{source}|layername={layer}" if layer else source
    lyr = QgsVectorLayer(uri, name or layer or os.path.basename(source), "ogr")
    if not lyr.isValid():
        print("invalid layer", uri)
    return lyr


def label(layer, expression, size=8, color="#1d2330", placement=None):
    settings = QgsPalLayerSettings()
    settings.fieldName = expression
    settings.isExpression = True
    fmt = QgsTextFormat()
    fmt.setFont(QFont("DejaVu Sans"))
    fmt.setSize(size)
    fmt.setColor(QColor(color))
    buffer = QgsTextBufferSettings()
    buffer.setEnabled(True)
    buffer.setSize(0.8)
    buffer.setColor(QColor("white"))
    fmt.setBuffer(buffer)
    settings.setFormat(fmt)
    if placement is not None:
        settings.placement = placement
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)


layers = []
alignments = vector(gpkg, "alignments")
alignments.setRenderer(alignments.renderer().clone())
alignments.renderer().setSymbol(QgsLineSymbol.createSimple({"color": "#1f2a44", "width": "1.4"}))
stations = vector(gpkg, "station_points")
stations.setSubsetString('"station" % 200 = 0') if "station" in [f.name() for f in stations.fields()] else None
stations.renderer().setSymbol(QgsMarkerSymbol.createSimple({"name": "circle", "color": "white", "outline_color": "#1f2a44", "size": "1.8"}))
station_field = next((f.name() for f in stations.fields() if f.name() in ("station_label", "label", "chainage")), "station")
label(stations, f'"{station_field}"', 7)
sections = vector(gpkg, "cross_sections")
sections.renderer().setSymbol(QgsLineSymbol.createSimple({"color": "#8a93a6", "width": "0.15"}))
profile_map = vector(path("profile_map.gpkg"))
profile_map.renderer().setSymbol(QgsLineSymbol.createSimple({"color": "#1f5fbf", "width": "0.6"}))
points = vector(path("review_points.gpkg"))
colors = {"High": "#c0392b", "Medium": "#d9822b", "Low": "#b7950b"}
categories = [QgsRendererCategory(k, QgsMarkerSymbol.createSimple({"name": "diamond", "color": c, "outline_color": "white", "size": "3.4"}), k)
              for k, c in colors.items()]
points.setRenderer(QgsCategorizedSymbolRenderer("severity", categories))
code_field = "code" if "code" in [f.name() for f in points.fields()] else "category"
label(points, f'"{code_field}"', 7, "#7a1f14")
layers = [points, stations, alignments, sections]
if contours_file:
    contours = vector(contours_file)
    contours.renderer().setSymbol(QgsLineSymbol.createSimple({"color": "#6b5a3a", "width": "0.12"}))
    layers.append(contours)
layers += [dem]

settings = QgsMapSettings()
settings.setLayers(layers)
settings.setDestinationCrs(CRS)
extent = alignments.extent()
extent.grow(260)
settings.setExtent(extent)
settings.setOutputSize(QSize(1800, 1400))
settings.setBackgroundColor(QColor("white"))
settings.setFlag(QgsMapSettings.Flag.Antialiasing, True)
job = QgsMapRendererParallelJob(settings)
job.start()
job.waitForFinished()
job.renderedImage().save(path("fig_map.png"))

# ---- profile viewer dock --------------------------------------------------
from landxml_plugin.gui.profile_viewer import ProfileViewerDock  # noqa: E402

dock = ProfileViewerDock(None)
dock.resize(1500, 860)
dock.load(SOURCE)
dock.file_edit.setText("D:/Projects/Demo/Demo_Road.xml")  # neutral path for the figure
dock.show()
app.processEvents()
dock.tabs.setCurrentIndex(0)
dock.chart.set_marker(1240)
app.processEvents()
dock.grab().save(path("fig_viewer_profile.png"))
dock.tabs.setCurrentIndex(1)
dock.chart.zoom_to(900, 1600, margin=0)
dock._show_section(1240)
dock.chart.set_marker(1240)
app.processEvents()
dock.grab().save(path("fig_viewer_sections.png"))

# ---- processing dialog ----------------------------------------------------
try:
    import qgis.utils
    from qgis.testing.mocked import get_iface

    qgis.utils.iface = get_iface()
    from processing.core.Processing import Processing

    Processing.initialize()
    QgsApplication.processingRegistry().addProvider(provider)
    from processing.gui.AlgorithmDialog import AlgorithmDialog

    algorithm = QgsApplication.processingRegistry().algorithmById(provider.id() + ":landxml_design_review")
    dialog = AlgorithmDialog(algorithm.create(), False, None)
    dialog.resize(1000, 900)
    panel = dialog.mainWidget()
    panel.wrappers["INPUT"].setParameterValue(SOURCE, dialog.processingContext())
    dialog.show()
    for _ in range(30):
        app.processEvents()
    dialog.grab().save(path("fig_dialog_review.png"))
except Exception as error:  # noqa: BLE001 - figure is optional
    print("dialog figure skipped:", type(error).__name__, error)

# ---- cross-section sheet page --------------------------------------------
subprocess.run(["pdftoppm", "-png", "-r", "110", "-f", "1", "-l", "1", path("sheets.pdf"), path("fig_sheet")], check=False)  # nosec B603 B607
print("done", sorted(f for f in os.listdir(OUT) if f.startswith("fig_")))
