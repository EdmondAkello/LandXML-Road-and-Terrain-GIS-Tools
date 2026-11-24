"""Build the 3D scene of the synthetic demo road for the user-guide figures.

Run with the QGIS Python (offscreen), then capture the viewer with tools/guide/capture_scene.py:

    QT_QPA_PLATFORM=offscreen python tools/guide/make_scene_figures.py <demo_road.xml> <output folder>
    python tools/guide/capture_scene.py <output folder>/scene/viewer.html docs/user-guide/figures

The scene has the demo road built as a feasibility road, a context road from a line layer, a stream ribbon and
grass and shrubs over the whole area; all of it is invented. Also saves the scene dialog with its setup summary
(fig_scene_dialog.png) when the Processing dialog can be built offscreen.
"""

import importlib.util
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
spec = importlib.util.spec_from_file_location("landxml_plugin", os.path.join(ROOT, "__init__.py"), submodule_search_locations=[ROOT])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

from qgis.core import (  # noqa: E402
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsRectangle,
    QgsVectorLayer,
)

app = QgsApplication([], True)
app.initQgis()

from landxml_plugin.provider import LandXMLTinToGeoTIFFProvider  # noqa: E402
from landxml_plugin.landxml.geometry import read_alignments  # noqa: E402

SOURCE, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)
CRS = "EPSG:32737"
provider = LandXMLTinToGeoTIFFProvider()
provider.loadAlgorithms()
ALG = {a.name(): a for a in provider.algorithms()}["landxml_3d_scene"]


def layer(kind, name, geometries):
    found = QgsVectorLayer(f"{kind}?crs={CRS}", name, "memory")
    features = []
    for geometry in geometries:
        feature = QgsFeature()
        feature.setGeometry(geometry)
        features.append(feature)
    found.dataProvider().addFeatures(features)
    return found


# the demo alignment in map order (the file is northing first), to place the extra layers beside it
road = read_alignments(SOURCE, 5.0)[0][0]
plan = [(e, n) for n, e in road["points"]]
xs, ys = [p[0] for p in plan], [p[1] for p in plan]
box = QgsRectangle(min(xs) - 400, min(ys) - 400, max(xs) + 400, max(ys) + 400)
first = int(len(plan) * 0.18)
ax, ay = plan[first]
bx, by = plan[first + 12]
nx, ny = -(by - ay), bx - ax
size = math.hypot(nx, ny)
nx, ny = nx / size, ny / size
context_road = layer("LineString", "Farm road", [QgsGeometry.fromPolylineXY(
    [QgsPointXY(ax + nx * t * 60 + (bx - ax) * 0.02 * t * t, ay + ny * t * 60 + (by - ay) * 0.02 * t * t) for t in range(-6, 7)])])
stream = layer("LineString", "Stream", [QgsGeometry.fromPolylineXY(
    [QgsPointXY(plan[first + 40][0] + nx * t * 45 + 25 * math.sin(t), plan[first + 40][1] + ny * t * 45 + 25 * math.cos(t * 0.7))
     for t in range(-6, 7)])])
cover = layer("Polygon", "Grassland", [QgsGeometry.fromRect(box)])

PARAMS = dict(INPUT=SOURCE, METHOD=1, OUTPUT_CRS=QgsCoordinateReferenceSystem(CRS), SURFACES="EG", VERTICAL_UNIT=1,
              CORRIDOR="Demo Road", CORRIDOR_WIDTH=250.0, SYN_GENERATE=True, SYNTHETIC="Demo Road",
              SYN_STRUCTURES="0+700 = box 2 x 2.0 x 2.0", DRIVE_LINES=[context_road], WATERWAYS=[stream], WATER_WIDTH=6.0,
              VEGETATION=[cover], VEG_SPACING=4.0, MAX_TRIANGLES=400000, TITLE="Demo road: 3D scene",
              OUTPUT_FOLDER=os.path.join(OUT, "scene"))
result = ALG.processAlgorithm(PARAMS, QgsProcessingContext(), QgsProcessingFeedback())
print("viewer:", result["VIEWER"])

# the dialog with its setup summary, when this QGIS can build the Processing dialog offscreen
try:
    dialog = ALG.createCustomParametersWidget(None)
    values = {key: value for key, value in PARAMS.items() if key not in ("OUTPUT_FOLDER",)}
    dialog.setParameters(values)
    if hasattr(dialog, "_setup_summary"):
        dialog._setup_summary()
    dialog.resize(900, 1000)
    dialog.show()
    app.processEvents()
    dialog.grab().save(os.path.join(OUT, "fig_scene_dialog.png"))
    print("dialog: saved")
except Exception as error:  # noqa: BLE001 - a figure is optional; the scene is the result
    print(f"dialog: not saved ({error})")
