"""The live setup summary of the 3D scene dialog (no QGIS)."""

# ruff: noqa: E402 - bootstrap the plugin package from its checkout directory

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if "landxml_plugin" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "landxml_plugin", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

from landxml_plugin.landxml.catalog import Catalog
from landxml_plugin.landxml.setup_summary import setup_summary

ROAD = {"SYN_GENERATE": True, "SYNTHETIC": "Access Road", "SYN_CARRIAGEWAY": 7.0, "SYN_SHOULDER": 1.5, "SYN_LANES": 2.0,
        "SYN_CUT_SLOPE": 1.5, "SYN_FILL_SLOPE": 2.0, "CORRIDOR_WIDTH": 0.0}


class SummaryTests(unittest.TestCase):
    def test_a_road_is_described_with_its_section_slopes_and_auto_width(self):
        text = "\n".join(setup_summary(ROAD))
        self.assertIn("'Access Road'", text)
        self.assertIn("carriageway 7 m, 3.50 m a lane", text)
        self.assertIn("shoulders 1.5 m each side", text)
        self.assertIn("cut 1.5H:1V, fill 2H:1V", text)
        self.assertIn("Scene width: Auto", text)

    def test_units_follow_the_output_crs(self):
        self.assertIn("7 ft", "\n".join(setup_summary(ROAD, unit="ft")))

    def test_off_or_empty_means_a_terrain_scene(self):
        self.assertIn("Terrain scene only", setup_summary({**ROAD, "SYN_GENERATE": False})[0])
        self.assertIn("Terrain scene only", setup_summary({"SYN_GENERATE": True, "SYNTHETIC": ""})[0])

    def test_a_manual_width_is_stated(self):
        self.assertIn("80 m either side", "\n".join(setup_summary({**ROAD, "CORRIDOR_WIDTH": 80.0})))

    def test_the_catalogue_flags_a_missing_alignment_or_profile(self):
        catalog = Catalog(alignments=["Other"], design_profiles={"Other": []})
        self.assertIn("is not an alignment", "\n".join(setup_summary(ROAD, catalog=catalog)))
        catalog = Catalog(alignments=["Access Road"], design_profiles={"Access Road": []})
        self.assertIn("no design profile", "\n".join(setup_summary(ROAD, catalog=catalog)))
        self.assertNotIn("no design profile", "\n".join(setup_summary({**ROAD, "SYN_AT_GRADE": True}, catalog=catalog)))
        catalog = Catalog(alignments=["Access Road"], design_profiles={"Access Road": ["FG"]})
        self.assertNotIn("no design profile", "\n".join(setup_summary(ROAD, catalog=catalog)))

    def test_extras_and_odd_values_do_not_raise(self):
        text = "\n".join(setup_summary({"VEGETATION": ["x"], "WATERWAYS": ["y"], "WATER": "EG = 100", "SYN_CARRIAGEWAY": "abc"}))
        self.assertIn("vegetation, a waterway ribbon, water levels", text)


if __name__ == "__main__":
    unittest.main()
