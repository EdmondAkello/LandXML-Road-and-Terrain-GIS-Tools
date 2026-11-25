"""Profile-design figures for the user guide, from the synthetic demo road (tools/guide/make_demo_landxml.py).

    QT_QPA_PLATFORM=offscreen python tools/guide/make_design_figures.py <demo_road.xml> <figure folder>

Starts a design from the ground with PVIs every 400, gives each vertical curve up to 120, checks it against Kenya
RDM at 80 km/h with a 6 % grade limit, and saves the long section in edit mode and the Design tab.
"""

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
spec = importlib.util.spec_from_file_location("landxml_plugin", os.path.join(ROOT, "__init__.py"), submodule_search_locations=[ROOT])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

from qgis.testing import start_app  # noqa: E402

app = start_app()

from qgis.PyQt.QtCore import QRectF  # noqa: E402
from qgis.PyQt.QtGui import QColor, QImage, QPainter  # noqa: E402

from landxml_plugin.gui.profile_viewer import ProfileViewerDock  # noqa: E402

SOURCE, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)
dock = ProfileViewerDock(None)
dock.load(SOURCE, "Demo Road")
panel = dock.design_panel
panel.spacing_spin.setValue(400)
panel.new_from_ground()
design = panel.design
for curve in design.curves():
    design.set_curve(curve["index"], min(120.0, 0.8 * design._room(design.pvis, curve["index"])))
panel.standard_combo.setCurrentIndex(next(i for i in range(panel.standard_combo.count()) if "Kenya" in panel.standard_combo.itemText(i)))
panel.speed_spin.setValue(80)
panel.grade_spin.setValue(6)
panel.sync()
dock.refresh(keep_view=True)
dock.chart.select_pvi(2)
image = QImage(1400, 620, QImage.Format.Format_ARGB32)
image.fill(QColor("#ffffff"))
painter = QPainter(image)
dock.chart.render(painter, QRectF(0, 0, 1400, 620))
painter.end()
image.save(os.path.join(OUT, "fig_design_chart.png"))
dock.resize(900, 1100)
dock.tabs.setCurrentWidget(panel)
dock.show()
app.processEvents()
panel.grab().save(os.path.join(OUT, "fig_design_panel.png"))
print("saved", OUT)
