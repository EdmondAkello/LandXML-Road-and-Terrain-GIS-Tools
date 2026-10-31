"""QGIS 4 runtime checks for provider registration and engineering outputs."""

# ruff: noqa: E402 - bootstrap the plugin package from its checkout directory

from __future__ import annotations

import importlib.util
import gc
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if "landxml_plugin" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "landxml_plugin", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingFeedback,
    QgsProcessingOutputLayerDefinition,
    QgsProject,
    QgsWkbTypes,
)
from osgeo import gdal, ogr
from qgis.PyQt import sip
from qgis.testing import start_app

from landxml_plugin.landxml_tin_to_geotiff import LandXMLTinToGeoTIFFPlugin
from landxml_plugin.provider import LandXMLTinToGeoTIFFProvider
from landxml_plugin.core import rasterize_tin, surface_difference


OPENROADS = str(ROOT / "tests" / "fixtures" / "openroads" / "terrain_minimal.xml")
CIVIL3D = str(ROOT / "tests" / "fixtures" / "civil3d" / "road_minimal.xml")
CORRIDOR = str(ROOT / "tests" / "fixtures" / "civil3d" / "corridor_synthetic.xml")
ALIGNMENTS_ONLY = str(ROOT / "tests" / "fixtures" / "generic" / "alignments_only.xml")
COMPLETE_ROAD = str(ROOT / "tests" / "fixtures" / "generic" / "complete_road.xml")
GRADING = str(ROOT / "tests" / "fixtures" / "generic" / "grading_volume.xml")


class QgisProcessingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = start_app()
        cls.provider = LandXMLTinToGeoTIFFProvider()
        cls.provider.loadAlgorithms()
        cls.algorithms = {
            algorithm.name(): algorithm for algorithm in cls.provider.algorithms()
        }

    def setUp(self):
        self.context = QgsProcessingContext()
        self.feedback = QgsProcessingFeedback()

    def _params(self, path, crs):
        return {
            "INPUT": path,
            "METHOD": 0,
            "OUTPUT_CRS": QgsCoordinateReferenceSystem(crs),
        }

    def test_provider_registers_all_algorithms(self):
        self.assertEqual(len(self.algorithms), 19)
        self.assertIn("inspect_landxml", self.algorithms)

    def test_toolbox_groups_follow_workflow(self):
        groups = sorted({algorithm.group() for algorithm in self.algorithms.values()})
        self.assertEqual(groups, [
            "1. Inspect and prepare", "2. Road design extraction",
            "3. Vector extraction", "4. Terrain conversion", "5. Surface extraction",
            "6. Earthworks and quantities", "7. Design review", "8. Reports and sheets",
        ])
        for name in ("inspect_landxml", "prepare_landxml"):
            self.assertEqual(self.algorithms[name].group(), groups[0])
        for name in ("landxml_design_report", "landxml_cross_section_sheets"):
            self.assertEqual(self.algorithms[name].group(), groups[-1])
            self.assertEqual(self.algorithms[name].groupId(), "reports")

    def test_coordinate_selectors_explain_landxml_axis_order(self):
        checked = []
        for name, algorithm in self.algorithms.items():
            parameter = algorithm.parameterDefinition("METHOD")
            if parameter is None:
                continue
            checked.append(name)
            self.assertIn("OpenRoads: usually Swap X / Y", parameter.description(), name)
            for text in ("Northing, Easting", "Civil 3D", "X = Easting", "known control point"):
                self.assertIn(text, parameter.help(), name)
            self.assertEqual(parameter.defaultValue(), 0, name)
            self.assertEqual(parameter.options()[1], "Swap X / Y", name)
        self.assertGreater(len(checked), 0)

    def test_plugin_unload_after_provider_is_deleted(self):
        plugin = LandXMLTinToGeoTIFFPlugin(None)
        plugin.initGui()
        provider = plugin.provider
        self.assertIsNotNone(provider)
        QgsApplication.processingRegistry().removeProvider(provider)
        self.assertTrue(sip.isdeleted(provider))
        plugin.unload()
        self.assertIsNone(plugin.provider)

    def test_inspect_report(self):
        result = self.algorithms["inspect_landxml"].processAlgorithm(
            {"INPUT": OPENROADS}, self.context, self.feedback
        )
        report = json.loads(result["REPORT"])
        self.assertEqual(report["vendor"], "OpenRoads Designer")
        self.assertEqual(report["horizontal_unit"], "USSurveyFoot")

    def test_crs_is_required_and_units_must_match(self):
        algorithm = self.algorithms["landxml_breaklines"]
        with self.assertRaisesRegex(QgsProcessingException, "Choose an output CRS"):
            algorithm.processAlgorithm(
                {"INPUT": OPENROADS, "OUTPUT": "memory:"}, self.context, self.feedback
            )
        params = self._params(OPENROADS, "EPSG:26915")
        params["OUTPUT"] = "memory:"
        with self.assertRaisesRegex(QgsProcessingException, "USSurveyFoot"):
            algorithm.processAlgorithm(params, self.context, self.feedback)
        self.assertNotEqual(Qgis.DistanceUnit.FeetUSSurvey, Qgis.DistanceUnit.Feet)
        generic = self._params(
            str(ROOT / "tests" / "fixtures" / "generic" / "multiple.xml"),
            "EPSG:2277",
        )
        generic["OUTPUT"] = "memory:"
        with self.assertRaisesRegex(QgsProcessingException, "foot"):
            self.algorithms["landxml_alignments_to_vector"].processAlgorithm(
                generic, self.context, self.feedback
            )

    def test_openroads_breakline_attributes_and_3d(self):
        params = self._params(OPENROADS, "EPSG:2277")
        params["OUTPUT"] = "memory:"
        result = self.algorithms["landxml_breaklines"].processAlgorithm(
            params, self.context, self.feedback
        )
        layer = self.context.getMapLayer(result["OUTPUT"])
        self.assertIsNotNone(layer)
        self.assertEqual(layer.featureCount(), 1)
        feature = next(layer.getFeatures())
        self.assertEqual(feature["surface_name"], "Synthetic terrain")
        self.assertEqual(feature["feature_code"], "Breakline")
        self.assertEqual(feature["source_user_id"], "42")
        self.assertTrue(QgsWkbTypes.hasZ(feature.geometry().wkbType()))

    def test_civil3d_alignment_profile_and_sections(self):
        alignment = self._params(CIVIL3D, "EPSG:26915")
        alignment["OUTPUT"] = "memory:"
        result = self.algorithms["landxml_alignments_to_vector"].processAlgorithm(
            alignment, self.context, self.feedback
        )
        layer = self.context.getMapLayer(result["OUTPUT"])
        self.assertEqual(layer.featureCount(), 1)
        feature = next(layer.getFeatures())
        self.assertEqual(feature["geometry_type"], "compound")
        self.assertEqual(feature["source_vendor"], "Civil 3D")

        profile = self.algorithms["landxml_profiles_to_vector"].processAlgorithm(
            {
                "INPUT": CIVIL3D,
                "OUTPUT": "memory:",
                "CONTROL_POINTS": "memory:",
                "VERTICAL_EXAGGERATION": 4,
            },
            self.context,
            self.feedback,
        )
        graph = self.context.getMapLayer(profile["OUTPUT"])
        points = self.context.getMapLayer(profile["CONTROL_POINTS"])
        self.assertEqual(graph.featureCount(), 1)
        self.assertEqual(points.featureCount(), 5)
        self.assertFalse(graph.crs().isValid())
        graph_feature = next(graph.getFeatures())
        self.assertEqual(graph_feature["label_text"], "Design")
        self.assertEqual(graph_feature["vertical_exaggeration"], 4)
        self.assertAlmostEqual(graph_feature.geometry().asPolyline()[-1].y(), 8)
        controls = list(points.getFeatures())
        self.assertIn("VPI 0+100.00\nElev 0.00\nG2 +6.67%", controls[0]["label_text"])
        curve_vpi = next(
            point
            for point in controls
            if point["source_control_type"] == "ParaCurve"
            and point["control_type"] == "VPI"
        )
        self.assertAlmostEqual(curve_vpi["k_value"], 3.75)
        self.assertAlmostEqual(curve_vpi.geometry().asPoint().y(), 4)
        self.assertIn("K 3.75", curve_vpi["label_text"])
        self.assertEqual(
            sorted(point["control_type"] for point in controls),
            ["VPC", "VPI", "VPI", "VPI", "VPT"],
        )
        gc.collect()
        for destination, layer in (
            (profile["OUTPUT"], graph),
            (profile["CONTROL_POINTS"], points),
        ):
            details = self.context.layerToLoadOnCompletionDetails(destination)
            processor = details.postProcessor()
            processor.postProcessLayer(layer, self.context, self.feedback)
            self.assertTrue(layer.labelsEnabled())
            self.assertEqual(layer.labeling().settings().fieldName, "label_text")

        sections = self._params(CIVIL3D, "EPSG:26915")
        sections.update(OUTPUT="memory:", POINTS="memory:")
        result = self.algorithms["landxml_cross_sections"].processAlgorithm(
            sections, self.context, self.feedback
        )
        line_layer = self.context.getMapLayer(result["OUTPUT"])
        point_layer = self.context.getMapLayer(result["POINTS"])
        self.assertEqual(line_layer.featureCount(), 1)
        self.assertEqual(point_layer.featureCount(), 2)
        self.assertEqual(next(point_layer.getFeatures())["elevation"], 1)

    def test_profile_map_overlay_uses_alignment_coordinates(self):
        xml = """<LandXML xmlns="http://www.landxml.org/schema/LandXML-1.2" version="1.2">
  <Units><Metric linearUnit="meter"/></Units>
  <Alignments><Alignment name="Road" staStart="100" length="100">
    <CoordGeom><Line><Start>1000 2000</Start><End>1100 2000</End></Line></CoordGeom>
    <Profile><ProfAlign name="Design"><PVI>100 10</PVI><PVI>150 20</PVI><PVI>200 10</PVI></ProfAlign></Profile>
  </Alignment></Alignments>
</LandXML>"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile_map.xml"
            path.write_text(xml, encoding="utf-8")
            params = dict(
                INPUT=str(path), OUTPUT="memory:", CONTROL_POINTS="memory:",
                MAP_OUTPUT="memory:", MAP_CONTROL_POINTS="memory:",
                OUTPUT_CRS="EPSG:26915", MAP_OFFSET=25,
                VERTICAL_EXAGGERATION=2, POINT_INTERVAL=50,
            )
            result = self.algorithms["landxml_profiles_to_vector"].processAlgorithm(
                params, self.context, self.feedback
            )
            graph = self.context.getMapLayer(result["OUTPUT"])
            overlay = self.context.getMapLayer(result["MAP_OUTPUT"])
            controls = self.context.getMapLayer(result["MAP_CONTROL_POINTS"])
            self.assertFalse(graph.crs().isValid())
            self.assertEqual(overlay.crs().authid(), "EPSG:26915")
            vertices = next(overlay.getFeatures()).geometry().asPolyline()
            self.assertEqual([(p.x(), p.y()) for p in vertices],
                             [(1000, 2025), (1050, 2045), (1100, 2025)])
            self.assertEqual(controls.featureCount(), 3)
            self.assertEqual(next(controls.getFeatures())["label_text"].split()[0], "VPI")
            details = self.context.layerToLoadOnCompletionDetails(result["MAP_OUTPUT"])
            details.postProcessor().postProcessLayer(overlay, self.context, self.feedback)
            self.assertTrue(overlay.labelsEnabled())

            missing_crs = dict(params)
            missing_crs.pop("OUTPUT_CRS")
            with self.assertRaisesRegex(QgsProcessingException, "output CRS"):
                self.algorithms["landxml_profiles_to_vector"].processAlgorithm(
                    missing_crs, self.context, self.feedback
                )

    def test_3d_centerline_keeps_profile_covered_segment(self):
        xml = """<LandXML xmlns="http://www.landxml.org/schema/LandXML-1.2" version="1.2">
  <Units><Metric linearUnit="meter"/></Units>
  <Alignments><Alignment name="Partial" staStart="100" length="100">
    <CoordGeom><Line><Start>0 0</Start><End>100 0</End></Line></CoordGeom>
    <Profile><ProfAlign name="Design"><PVI>125 10</PVI><PVI>175 20</PVI></ProfAlign></Profile>
  </Alignment></Alignments>
</LandXML>"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "partial.xml"
            path.write_text(xml, encoding="utf-8")
            params = self._params(str(path), "EPSG:26915")
            params.update(OUTPUT="memory:", SEGMENT=5)
            result = self.algorithms["landxml_3d_centerlines"].processAlgorithm(
                params, self.context, self.feedback
            )
            layer = self.context.getMapLayer(result["OUTPUT"])
            self.assertEqual(layer.featureCount(), 1)
            feature = next(layer.getFeatures())
            vertices = list(feature.geometry().vertices())
            self.assertEqual(len(vertices), 2)
            self.assertAlmostEqual(vertices[0].x(), 25)
            self.assertAlmostEqual(vertices[0].z(), 10)
            self.assertAlmostEqual(vertices[-1].x(), 75)
            self.assertAlmostEqual(vertices[-1].z(), 20)
            self.assertEqual(float(feature["sta_start"]), 125)
            self.assertEqual(float(feature["sta_end"]), 175)

            complete = self._params(str(path), "EPSG:26915")
            complete["OUTPUT_DIR"] = str(Path(directory) / "complete")
            for key in (
                "INCLUDE_ALIGNMENTS",
                "INCLUDE_PROFILES",
                "INCLUDE_STATIONS",
                "INCLUDE_CROSSSECTIONS",
                "INCLUDE_FEATURELINES",
                "INCLUDE_BREAKLINES",
                "INCLUDE_SURFACE_BOUNDARY",
                "INCLUDE_DEM",
                "INCLUDE_CONTOURS",
            ):
                complete[key] = False
            complete["INCLUDE_CENTERLINES"] = True
            self.algorithms["landxml_complete_road_design"].processAlgorithm(
                complete, self.context, self.feedback
            )
            package = ogr.Open(str(Path(complete["OUTPUT_DIR"]) / "partial_GIS.gpkg"))
            centerlines = package.GetLayerByName("centerlines_3d")
            self.assertEqual(centerlines.GetFeatureCount(), 1)
            exported = next(iter(centerlines))
            geometry = exported.GetGeometryRef()
            self.assertAlmostEqual(geometry.GetPoint(0)[0], 25)
            self.assertAlmostEqual(geometry.GetPoint(0)[2], 10)
            self.assertAlmostEqual(geometry.GetPoint(geometry.GetPointCount() - 1)[0], 75)
            self.assertAlmostEqual(geometry.GetPoint(geometry.GetPointCount() - 1)[2], 20)
            package = None

    def test_station_points_use_even_stations(self):
        xml = """<LandXML xmlns="http://www.landxml.org/schema/LandXML-1.2" version="1.2">
  <Units><Metric linearUnit="meter"/></Units>
  <Alignments><Alignment name="Offset" staStart="103.19" length="60">
    <CoordGeom><Line><Start>0 0</Start><End>60 0</End></Line></CoordGeom>
  </Alignment></Alignments>
</LandXML>"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stations.xml"
            path.write_text(xml, encoding="utf-8")
            params = self._params(str(path), "EPSG:26915")
            params.update(OUTPUT="memory:", INTERVAL=20)
            algorithm = self.algorithms["landxml_station_points"]
            result = algorithm.processAlgorithm(params, self.context, self.feedback)
            layer = self.context.getMapLayer(result["OUTPUT"])
            features = sorted(layer.getFeatures(), key=lambda feature: feature["station"])
            self.assertEqual([feature["station"] for feature in features], [120, 140, 160])
            for feature, x in zip(features, (16.81, 36.81, 56.81)):
                self.assertAlmostEqual(feature.geometry().asPoint().x(), x)

            params["INCLUDE_ENDPOINT"] = True
            result = algorithm.processAlgorithm(params, self.context, self.feedback)
            layer = self.context.getMapLayer(result["OUTPUT"])
            self.assertEqual(
                sorted(round(feature["station"], 2) for feature in layer.getFeatures()),
                [120, 140, 160, 163.19],
            )

            complete = self._params(str(path), "EPSG:26915")
            complete.update(OUTPUT_DIR=str(Path(directory) / "complete"), STATION_INTERVAL=20)
            for key in (
                "INCLUDE_ALIGNMENTS",
                "INCLUDE_CENTERLINES",
                "INCLUDE_PROFILES",
                "INCLUDE_CROSSSECTIONS",
                "INCLUDE_FEATURELINES",
                "INCLUDE_BREAKLINES",
                "INCLUDE_SURFACE_BOUNDARY",
                "INCLUDE_DEM",
                "INCLUDE_CONTOURS",
            ):
                complete[key] = False
            complete["INCLUDE_STATIONS"] = True
            self.algorithms["landxml_complete_road_design"].processAlgorithm(
                complete, self.context, self.feedback
            )
            package = ogr.Open(str(Path(complete["OUTPUT_DIR"]) / "stations_GIS.gpkg"))
            stations = package.GetLayerByName("station_points")
            self.assertEqual(
                sorted(feature.GetField("station") for feature in stations),
                [120, 140, 160],
            )
            package = None

    def test_large_grid_warns_before_allocation_and_continues(self):
        vertices = np.array([[0, 0, 1], [24660, 0, 1], [0, 11283, 1]])
        faces = np.array([[0, 1, 2]])
        # Stop at allocation so the regression does not require a gigabyte of RAM.
        with patch(
            "landxml_plugin.core.np.full", side_effect=MemoryError("allocation reached")
        ) as allocate:
            with self.assertRaisesRegex(MemoryError, "allocation reached"):
                with self.assertWarnsRegex(RuntimeWarning, "Large raster.*1.04 GiB"):
                    rasterize_tin(vertices, faces, 1)
            self.assertEqual(allocate.call_args.args[0], (11283, 24660))

        with self.assertRaisesRegex(ValueError, "exceeding the safety limit"):
            rasterize_tin(vertices, faces, 1, max_cells=250_000_000)

    def test_grid_warning_threshold_and_surface_difference(self):
        vertices = np.array([[0., 0., 1.], [2., 0., 1.], [0., 2., 1.]])
        faces = np.array([[0, 1, 2]])
        for threshold, expected in ((3, 1), (4, 0), (0, 0)):
            with self.subTest(threshold=threshold):
                messages = []
                grid, *_ = rasterize_tin(vertices, faces, 1, warn_cells=threshold, warning=messages.append)
                self.assertEqual(grid.shape, (2, 2))
                self.assertEqual(len(messages), expected)
        messages = []
        difference, _, _, stats = surface_difference(
            (vertices, faces), (vertices + [0, 0, 1], faces), 1, warn_cells=3, warning=messages.append,
        )
        self.assertEqual(len(messages), 1)
        self.assertEqual(stats["fill_volume"], 3)

    def test_grid_warning_setting_reaches_all_raster_tools(self):
        for name in ("landxml_tin_to_geotiff", "landxml_complete_road_design", "landxml_surface_cut_fill"):
            algorithm = self.algorithms[name]
            self.assertEqual(algorithm.parameterDefinition("GRID_WARNING").defaultValue(), 250)
            for threshold in (0.001, 0):
                with self.subTest(tool=name, threshold=threshold), tempfile.TemporaryDirectory() as directory:
                    params = self._params(CORRIDOR, "EPSG:32637")
                    params.update(
                        RESOLUTION=0.1, GRID_WARNING=threshold, SURFACE="Design",
                        BASE_SURFACE="EG", COMPARE_SURFACE="Design",
                        OUTPUT=str(Path(directory) / "grid.tif"), OUTPUT_DIR=directory,
                        INCLUDE_DEM=True, INCLUDE_CONTOURS=False,
                    )
                    with patch.object(self.feedback, "pushWarning") as warning:
                        algorithm.processAlgorithm(params, self.context, self.feedback)
                    grid_messages = [args[0] for args, _ in warning.call_args_list if args[0].startswith("Large raster:")]
                    self.assertEqual(bool(grid_messages), bool(threshold))
                    self.assertTrue(list(Path(directory).glob("*.tif")))

    def test_grading_quantities_exact_volumes_and_reconciliation(self):
        algorithm = self.algorithms["landxml_grading_quantities"]
        with tempfile.TemporaryDirectory() as directory:
            params = self._params(GRADING, "EPSG:26915")
            params.update(
                CIVIL3D_CUT=100.004, CIVIL3D_FILL=99.995, CIVIL3D_AREA=200.0, TOLERANCE=0.01,
                DEPTH_BANDS="-1, 0, 0.5",
                REPORT=str(Path(directory) / "grading.html"), BANDS_CSV=str(Path(directory) / "bands.csv"),
            )
            result = algorithm.processAlgorithm(params, self.context, self.feedback)
            self.assertAlmostEqual(result["CUT_VOLUME"], 100.0, places=6)
            self.assertAlmostEqual(result["FILL_VOLUME"], 100.0, places=6)
            self.assertAlmostEqual(result["NET_VOLUME"], 0.0, places=6)
            self.assertAlmostEqual(result["PLAN_AREA"], 200.0, places=6)
            self.assertEqual(result["CHECK"], "PASS")
            self.assertEqual(result["VOLUME_UNIT"], "m³")
            report = Path(directory, "grading.html").read_text(encoding="utf-8")
            self.assertIn("Pad Earthworks", report)
            self.assertIn("Reconciliation: PASS", report)
            rows = Path(directory, "bands.csv").read_text(encoding="utf-8").splitlines()
            self.assertEqual(rows[0], "band,depth_from_m,depth_to_m,area_m²")
            self.assertEqual(len(rows), 5)

            params.update(CIVIL3D_FILL=101.0, REPORT="", BANDS_CSV="")
            self.assertEqual(algorithm.processAlgorithm(params, self.context, self.feedback)["CHECK"], "FAIL")
            for key in ("CIVIL3D_CUT", "CIVIL3D_FILL", "CIVIL3D_AREA"):
                params.pop(key)
            self.assertEqual(algorithm.processAlgorithm(params, self.context, self.feedback)["CHECK"], "NOT CHECKED")

            cubic_yards = dict(params, VOLUME_UNITS=1)
            result = algorithm.processAlgorithm(cubic_yards, self.context, self.feedback)
            self.assertEqual(result["VOLUME_UNIT"], "CY")
            self.assertAlmostEqual(result["CUT_VOLUME"], 100.0 / 0.9144 ** 3, places=6)
            self.assertAlmostEqual(result["PLAN_AREA"], 200.0, places=6)

    def test_grading_quantities_surface_choice(self):
        algorithm = self.algorithms["landxml_grading_quantities"]
        params = self._params(GRADING, "EPSG:26915")
        result = algorithm.processAlgorithm(dict(params, VOLUME_SURFACE=""), self.context, self.feedback)
        self.assertAlmostEqual(result["CUT_VOLUME"], 100.0, places=6)
        warnings = []
        feedback = QgsProcessingFeedback()
        feedback.pushWarning = warnings.append
        algorithm.processAlgorithm(dict(params, VOLUME_SURFACE="Pad EG"), self.context, feedback)
        self.assertTrue(any("elevation surface" in text for text in warnings))
        with self.assertRaises(QgsProcessingException):
            algorithm.processAlgorithm(dict(params, VOLUME_SURFACE="", INPUT=CIVIL3D), self.context, self.feedback)
        with self.assertRaises(QgsProcessingException):
            algorithm.processAlgorithm(dict(params, DEPTH_BANDS="1, x"), self.context, self.feedback)

    def test_complete_export_only_retains_available_layers(self):
        road_layers = {"alignments", "alignment_elements", "station_points"}
        for source, crs, expected, rasters in (
            (ALIGNMENTS_ONLY, "EPSG:26915", road_layers, 0),
            (OPENROADS, "EPSG:2277", {"breaklines", "surface_boundary"}, 1),
            (COMPLETE_ROAD, "EPSG:26915", road_layers | {
                "profiles", "profile_controls", "centerlines_3d", "cross_sections",
                "feature_lines", "breaklines", "surface_boundary",
            }, 2),
        ):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                self.context = QgsProcessingContext()
                project = QgsProject()
                self.context.setProject(project)
                params = self._params(source, crs)
                params.update(OUTPUT_DIR=directory, RESOLUTION=1, CONTOUR_INTERVAL=1)
                self.algorithms["landxml_complete_road_design"].processAlgorithm(
                    params, self.context, self.feedback
                )
                package = ogr.Open(str(Path(directory) / (Path(source).stem + "_GIS.gpkg")))
                self.assertEqual({layer.GetName() for layer in package}, expected)
                self.assertTrue(all(layer.GetFeatureCount() > 0 for layer in package))
                package = None
                queued = self.context.layersToLoadOnCompletion()
                self.assertEqual(
                    {uri.split("|layername=")[1] for uri in queued if "_GIS.gpkg|" in uri},
                    expected,
                )
                self.assertEqual(len(list(Path(directory).glob("*.tif"))), rasters)
                self.assertEqual(len(list(Path(directory).glob("*_Contours.gpkg"))), rasters)

    def test_complete_export_availability_respects_filters(self):
        with tempfile.TemporaryDirectory() as directory:
            params = self._params(COMPLETE_ROAD, "EPSG:26915")
            params.update(OUTPUT_DIR=directory, ALIGNMENT="Short", SURFACE="Design",
                          RESOLUTION=1, CONTOUR_INTERVAL=1)
            with patch.object(self.feedback, "pushInfo") as info:
                self.algorithms["landxml_complete_road_design"].processAlgorithm(
                    params, self.context, self.feedback
                )
            package = ogr.Open(str(Path(directory) / "complete_road_GIS.gpkg"))
            self.assertEqual({layer.GetName() for layer in package}, {
                "alignments", "alignment_elements", "surface_boundary",
            })
            self.assertEqual(next(iter(package.GetLayerByName("alignments"))).GetField("name"), "Short")
            package = None
            messages = "\n".join(call.args[0] for call in info.call_args_list)
            for name in ("station_points", "profiles", "centerlines_3d", "cross_sections", "feature_lines", "breaklines"):
                self.assertIn(name, messages)
            self.assertEqual(len(list(Path(directory).glob("*.tif"))), 1)

    def test_complete_export_unchecked_outputs_stay_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            params = self._params(COMPLETE_ROAD, "EPSG:26915")
            params["OUTPUT_DIR"] = directory
            for parameter in self.algorithms["landxml_complete_road_design"].parameterDefinitions():
                if parameter.name().startswith("INCLUDE_"):
                    params[parameter.name()] = False
            params["INCLUDE_CENTERLINES"] = True
            self.algorithms["landxml_complete_road_design"].processAlgorithm(
                params, self.context, self.feedback
            )
            package = ogr.Open(str(Path(directory) / "complete_road_GIS.gpkg"))
            self.assertEqual({layer.GetName() for layer in package}, {"centerlines_3d"})
            self.assertEqual(package.GetLayer(0).GetFeatureCount(), 1)
            package = None
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_complete_export_no_available_requested_data_leaves_no_empty_package(self):
        project = QgsProject()
        self.context.setProject(project)
        with tempfile.TemporaryDirectory() as directory:
            params = self._params(ALIGNMENTS_ONLY, "EPSG:26915")
            params.update(OUTPUT_DIR=directory, INCLUDE_ALIGNMENTS=False, INCLUDE_STATIONS=False)
            self.algorithms["landxml_complete_road_design"].processAlgorithm(
                params, self.context, self.feedback
            )
            self.assertEqual(list(Path(directory).iterdir()), [])
            self.assertEqual(self.context.layersToLoadOnCompletion(), {})

    def test_complete_export_drops_empty_contours_and_keeps_dem(self):
        project = QgsProject()
        self.context.setProject(project)
        with tempfile.TemporaryDirectory() as directory:
            params = self._params(OPENROADS, "EPSG:2277")
            params.update(OUTPUT_DIR=directory, RESOLUTION=1, CONTOUR_INTERVAL=1000)
            self.algorithms["landxml_complete_road_design"].processAlgorithm(
                params, self.context, self.feedback
            )
            self.assertEqual(list(Path(directory).glob("*_Contours.gpkg")), [])
            self.assertEqual(len(list(Path(directory).glob("*.tif"))), 1)
            self.assertFalse(any("_Contours" in uri for uri in self.context.layersToLoadOnCompletion()))

    def test_complete_export_invalid_profile_still_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "invalid.xml"
            source.write_text(Path(COMPLETE_ROAD).read_text().replace("<PVI>0 1</PVI>", "<PVI>bad</PVI>"))
            params = self._params(str(source), "EPSG:26915")
            params["OUTPUT_DIR"] = str(Path(directory) / "export")
            with self.assertRaisesRegex(QgsProcessingException, "Profile 'Design' is invalid"):
                self.algorithms["landxml_complete_road_design"].processAlgorithm(
                    params, self.context, self.feedback
                )
            self.assertEqual(self.context.layersToLoadOnCompletion(), {})

    def test_complete_export_descriptive_terrain_filenames(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "combined.xml"
            source.write_text(
                Path(COMPLETE_ROAD).read_text(encoding="utf-8").replace(
                    '<Surface name="Design">', '<Surface name="FG Road/Top">'
                ), encoding="utf-8",
            )
            algorithm = self.algorithms["landxml_complete_road_design"]
            for selection in ("", "FG Road/Top"):
                folder = Path(directory) / ("all" if not selection else "selected")
                params = self._params(str(source), "EPSG:32637")
                params.update(OUTPUT_DIR=str(folder), SURFACE=selection,
                              INCLUDE_DEM=True, INCLUDE_CONTOURS=True, RESOLUTION=1, CONTOUR_INTERVAL=1)
                algorithm.processAlgorithm(params, self.context, self.feedback)
                names = {path.name for path in folder.iterdir()}
                stem = "combined_Finished_Grade_FG_Road_Top"
                self.assertIn(stem + "_DEM.tif", names)
                self.assertIn(stem + "_Contours.gpkg", names)
                self.assertIn("combined_GIS.gpkg", names)
                self.assertEqual("combined_Existing_Ground_EG_DEM.tif" in names, not bool(selection))
                raster = gdal.Open(str(folder / (stem + "_DEM.tif")))
                self.assertEqual(raster.RasterCount, 1)
                raster = None
                contours = ogr.Open(str(folder / (stem + "_Contours.gpkg")))
                self.assertIsNotNone(contours.GetLayerByName("contours"))
                contours = None

    def test_complete_export_loads_results_after_completion(self):
        sys.path.append(str(Path(QgsApplication.pkgDataPath()) / "python" / "plugins"))
        from processing.gui.Postprocessing import handleAlgorithmResults

        project = QgsProject()
        self.context.setProject(project)
        algorithm = self.algorithms["landxml_complete_road_design"]
        self.assertTrue(algorithm.parameterDefinition("LOAD_OUTPUTS").defaultValue())
        with tempfile.TemporaryDirectory() as directory:
            params = self._params(COMPLETE_ROAD, "EPSG:32637")
            params.update(OUTPUT_DIR=directory, RESOLUTION=1, CONTOUR_INTERVAL=1)
            # Existing files in the output directory are not results of this run.
            (Path(directory) / "unrelated.tif").write_text("not an exported layer")
            result = algorithm.processAlgorithm(params, self.context, self.feedback)
            queued = self.context.layersToLoadOnCompletion()
            self.assertTrue(queued)
            self.assertEqual(project.mapLayers(), {})
            self.assertFalse(any("unrelated" in uri for uri in queued))
            self.assertTrue(handleAlgorithmResults(algorithm, self.context, self.feedback, result))
            layers = list(project.mapLayers().values())
            self.assertEqual(len(layers), len(queued))
            self.assertTrue(all(layer.isValid() for layer in layers))
            names = {layer.name() for layer in layers}
            self.assertIn("complete_road_GIS — alignments", names)
            self.assertIn("complete_road_Existing_Ground_EG_DEM", names)
            self.assertIn("complete_road_Design_Contours", names)
            for layer in layers:
                self.assertEqual(layer.customProperty("landxml/source_path"), COMPLETE_ROAD)
            # Drawing order (top of the layer tree first): points, then the
            # alignment and other lines, contours, and the DEMs at the bottom.
            order = [layer.name() for layer in project.layerTreeRoot().layerOrder()]
            rank = {name: i for i, name in enumerate(order)}
            dems = [name for name in order if name.endswith("_DEM")]
            contours = [name for name in order if name.endswith("_Contours")]
            self.assertTrue(dems and contours)
            self.assertLess(max(rank[name] for name in contours), min(rank[name] for name in dems))
            self.assertLess(rank["complete_road_GIS — alignments"], min(rank[name] for name in contours))
            self.assertLess(rank["complete_road_GIS — station_points"], rank["complete_road_GIS — alignments"])
            self.assertEqual(order[-len(dems):], dems)
            project.clear()

    def test_complete_export_map_loading_can_be_disabled_or_have_no_project(self):
        project = QgsProject()
        for load, with_project in ((False, True), (True, False)):
            with self.subTest(load=load, project=with_project), tempfile.TemporaryDirectory() as directory:
                self.context = QgsProcessingContext()
                if with_project:
                    self.context.setProject(project)
                params = self._params(CORRIDOR, "EPSG:32637")
                params.update(OUTPUT_DIR=directory, LOAD_OUTPUTS=load, INCLUDE_DEM=False, INCLUDE_CONTOURS=False)
                self.algorithms["landxml_complete_road_design"].processAlgorithm(params, self.context, self.feedback)
                self.assertTrue((Path(directory) / "corridor_synthetic_GIS.gpkg").exists())
                self.assertEqual(self.context.layersToLoadOnCompletion(), {})
                self.assertEqual(project.mapLayers(), {})

    def test_complete_export_contours_only_do_not_queue_temporary_dems(self):
        project = QgsProject()
        self.context.setProject(project)
        with tempfile.TemporaryDirectory() as directory:
            params = self._params(COMPLETE_ROAD, "EPSG:32637")
            params.update(OUTPUT_DIR=directory, INCLUDE_DEM=False, INCLUDE_CONTOURS=True,
                          RESOLUTION=1, CONTOUR_INTERVAL=1)
            self.algorithms["landxml_complete_road_design"].processAlgorithm(params, self.context, self.feedback)
            queued = self.context.layersToLoadOnCompletion()
            self.assertTrue(any("_Contours.gpkg" in uri for uri in queued))
            self.assertFalse(any(uri.endswith(".tif") for uri in queued))
            self.assertEqual(list(Path(directory).glob("*.tif")), [])

    def test_failed_complete_export_does_not_queue_partial_results(self):
        project = QgsProject()
        self.context.setProject(project)
        with tempfile.TemporaryDirectory() as directory:
            params = self._params(CORRIDOR, "EPSG:32637")
            params.update(OUTPUT_DIR=directory, INCLUDE_DEM=True)
            with patch("landxml_plugin.complete_export.rasterize_tin", side_effect=ValueError("raster failed")):
                with self.assertRaisesRegex(ValueError, "raster failed"):
                    self.algorithms["landxml_complete_road_design"].processAlgorithm(params, self.context, self.feedback)
            self.assertEqual(self.context.layersToLoadOnCompletion(), {})
            self.assertEqual(project.mapLayers(), {})

    def test_cancelled_complete_export_does_not_queue_results(self):
        project = QgsProject()
        self.context.setProject(project)
        self.feedback.cancel()
        with tempfile.TemporaryDirectory() as directory:
            params = self._params(CORRIDOR, "EPSG:32637")
            params["OUTPUT_DIR"] = directory
            for parameter in self.algorithms["landxml_complete_road_design"].parameterDefinitions():
                if parameter.name().startswith("INCLUDE_"):
                    params[parameter.name()] = False
            with patch("landxml_plugin.complete_export.read_alignments", return_value=([], [])):
                with self.assertRaisesRegex(QgsProcessingException, "cancelled"):
                    self.algorithms["landxml_complete_road_design"].processAlgorithm(params, self.context, self.feedback)
            self.assertEqual(self.context.layersToLoadOnCompletion(), {})
            self.assertEqual(project.mapLayers(), {})

    def test_tin_geotiff_and_complete_export(self):
        with tempfile.TemporaryDirectory() as directory:
            raster = self._params(OPENROADS, "EPSG:2277")
            raster.update(OUTPUT=str(Path(directory) / "tiny.tif"), RESOLUTION=1)
            self.algorithms["landxml_tin_to_geotiff"].processAlgorithm(
                raster, self.context, self.feedback
            )
            dataset = gdal.Open(raster["OUTPUT"])
            self.assertIsNotNone(dataset)
            self.assertEqual(dataset.RasterCount, 1)
            self.assertIn("2277", dataset.GetProjection())
            dataset = None

            folder = Path(directory) / "complete"
            complete = self._params(OPENROADS, "EPSG:2277")
            complete["OUTPUT_DIR"] = str(folder)
            for key in (
                "INCLUDE_ALIGNMENTS",
                "INCLUDE_CENTERLINES",
                "INCLUDE_PROFILES",
                "INCLUDE_STATIONS",
                "INCLUDE_CROSSSECTIONS",
                "INCLUDE_FEATURELINES",
                "INCLUDE_SURFACE_BOUNDARY",
                "INCLUDE_DEM",
                "INCLUDE_CONTOURS",
            ):
                complete[key] = False
            complete["INCLUDE_BREAKLINES"] = True
            self.algorithms["landxml_complete_road_design"].processAlgorithm(
                complete, self.context, self.feedback
            )
            package = ogr.Open(str(folder / "terrain_minimal_GIS.gpkg"))
            self.assertIsNotNone(package)
            layer = package.GetLayerByName("breaklines")
            self.assertIsNotNone(layer)
            self.assertEqual(layer.GetFeatureCount(), 1)
            self.assertEqual(next(iter(layer)).GetField("source_user_id"), "42")
            package = None

    def test_complete_export_alignment_elements_and_profile_graph(self):
        with tempfile.TemporaryDirectory() as directory:
            params = self._params(CIVIL3D, "EPSG:26915")
            params["OUTPUT_DIR"] = directory
            for key in (
                "INCLUDE_CENTERLINES",
                "INCLUDE_STATIONS",
                "INCLUDE_CROSSSECTIONS",
                "INCLUDE_FEATURELINES",
                "INCLUDE_BREAKLINES",
                "INCLUDE_SURFACE_BOUNDARY",
                "INCLUDE_DEM",
                "INCLUDE_CONTOURS",
            ):
                params[key] = False
            params["INCLUDE_ALIGNMENTS"] = True
            params["INCLUDE_PROFILES"] = True
            self.algorithms["landxml_complete_road_design"].processAlgorithm(
                params, self.context, self.feedback
            )
            package = ogr.Open(str(Path(directory) / "road_minimal_GIS.gpkg"))
            alignments = package.GetLayerByName("alignments")
            self.assertEqual(alignments.GetFeatureCount(), 1)
            alignment = next(iter(alignments))
            self.assertEqual(alignment.GetField("source_vendor"), "Civil 3D")
            self.assertEqual(alignment.GetField("source_name"), "Road A")
            self.assertEqual(alignment.GetField("station_start"), 100)
            self.assertEqual(
                package.GetLayerByName("alignment_elements").GetFeatureCount(), 3
            )
            graph = package.GetLayerByName("profiles")
            self.assertEqual(graph.GetFeatureCount(), 1)
            self.assertIsNone(graph.GetSpatialRef())
            controls = package.GetLayerByName("profile_controls")
            self.assertEqual(controls.GetFeatureCount(), 3)
            package = None

    def _loaded(self, name="memory:"):
        # A destination Processing will load, so result post-processors attach.
        return QgsProcessingOutputLayerDefinition(name, QgsProject.instance())

    def _post_process(self, destination):
        layer = self.context.getMapLayer(destination)
        details = self.context.layerToLoadOnCompletionDetails(destination)
        details.postProcessor().postProcessLayer(layer, self.context, self.feedback)
        return layer

    def test_profile_cut_fill_tables_map_and_report(self):
        with tempfile.TemporaryDirectory() as directory:
            report = str(Path(directory) / "profile.html")
            params = self._params(CORRIDOR, "EPSG:32637")
            params.update(
                OUTPUT="memory:",
                SEGMENTS=self._loaded(),
                TRANSITIONS=self._loaded(),
                REPORT=report,
                INTERVAL=50,
                FORMATION_WIDTH=10,
                FILL_SLOPE=2,
                CUT_SLOPE=2,
                HIGH_FILL=0.9,
            )
            result = self.algorithms["landxml_profile_cut_fill"].processAlgorithm(
                params, self.context, self.feedback
            )
            table = self.context.getMapLayer(result["OUTPUT"])
            rows = sorted(table.getFeatures(), key=lambda f: f["station"])
            self.assertEqual([f["station"] for f in rows], [0, 50, 100, 150, 200])
            self.assertEqual(rows[1]["station_label"], "0+050.00")
            self.assertAlmostEqual(rows[1]["depth"], 0.5)
            self.assertEqual(rows[1]["kind"], "fill")
            self.assertEqual(rows[3]["kind"], "cut")
            segments = self._post_process(result["SEGMENTS"])
            self.assertEqual(segments.featureCount(), 2)
            self.assertEqual(segments.renderer().type(), "categorizedSymbol")
            self.assertEqual(segments.customProperty("landxml/source_path"), CORRIDOR)
            fill = next(f for f in segments.getFeatures() if f["kind"] == "fill")
            self.assertEqual(fill["severity"], "high fill")
            line = fill.geometry().asPolyline()
            self.assertEqual((line[0].x(), line[0].y()), (5000, 1000))
            self.assertEqual((line[-1].x(), line[-1].y()), (5000, 1100))
            transitions = self.context.getMapLayer(result["TRANSITIONS"])
            point = next(transitions.getFeatures())
            self.assertEqual(point["transition"], "fill→cut")
            self.assertEqual(point.geometry().asPoint().y(), 1100)
            self.assertEqual(result["TRANSITION_COUNT"], 1)
            text = Path(report).read_text(encoding="utf-8")
            self.assertIn("Preliminary level-section estimate", text)
            self.assertIn("<svg", text)

    def test_profile_cut_fill_requires_profile_pair(self):
        with self.assertRaisesRegex(QgsProcessingException, "existing-ground profile"):
            self.algorithms["landxml_profile_cut_fill"].processAlgorithm(
                {"INPUT": CIVIL3D, "OUTPUT": "memory:"}, self.context, self.feedback
            )

    def test_corridor_quantities_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            report = str(Path(directory) / "corridor.html")
            params = self._params(CORRIDOR, "EPSG:32637")
            params.update(
                OUTPUT="memory:",
                VOLUMES="memory:",
                SIDE_SLOPES=self._loaded(),
                FOOTPRINT="memory:",
                REPORT=report,
                USE_DATUM=False,
            )
            result = self.algorithms["landxml_corridor_quantities"].processAlgorithm(
                params, self.context, self.feedback
            )
            self.assertAlmostEqual(result["CUT_VOLUME"], 575)
            self.assertAlmostEqual(result["FILL_VOLUME"], 575)
            self.assertAlmostEqual(result["FILL_SLOPE_AREA"], 100 * 5 ** 0.5)
            table = self.context.getMapLayer(result["OUTPUT"])
            self.assertEqual(table.featureCount(), 5)
            first = min(table.getFeatures(), key=lambda f: f["station"])
            self.assertAlmostEqual(first["fill_area"], 12)
            self.assertAlmostEqual(first["m_pave"], 1)
            self.assertAlmostEqual(first["m_base"], 2)
            self.assertEqual(first["ground_surface"], "EG Surface")
            volumes = self.context.getMapLayer(result["VOLUMES"])
            self.assertEqual(volumes.featureCount(), 4)
            slopes = self._post_process(result["SIDE_SLOPES"])
            self.assertEqual(slopes.featureCount(), 8)
            band = next(
                f for f in slopes.getFeatures() if f["side"] == "left" and f["sta_start"] == 0
            )
            self.assertEqual(band["kind"], "fill")
            self.assertAlmostEqual(band["plan_area"], 75)
            self.assertLess(band.geometry().boundingBox().xMaximum(), 5000 - 4.99)
            footprint = self.context.getMapLayer(result["FOOTPRINT"])
            self.assertEqual(footprint.featureCount(), 4)
            self.assertIn("Corridor material quantities", Path(report).read_text(encoding="utf-8"))

    def test_corridor_quantities_without_sections(self):
        with self.assertRaisesRegex(QgsProcessingException, "cross-sections"):
            self.algorithms["landxml_corridor_quantities"].processAlgorithm(
                {"INPUT": OPENROADS, "OUTPUT": "memory:"}, self.context, self.feedback
            )

    def test_earthwork_volume_unit_selection(self):
        source = Path(CORRIDOR).read_text(encoding="utf-8")
        metric = '<Metric linearUnit="meter" verticalLinearUnit="meter" areaUnit="squareMeter" volumeUnit="cubicMeter"/>'
        for linear, crs, option, factor, label in (
            ("meter", "EPSG:32637", 0, 1.0, "m³"),
            ("foot", "+proj=utm +zone=37 +datum=WGS84 +units=ft +no_defs", 0, 1 / 27, "CY"),
            ("usSurveyFoot", "EPSG:2277", 0, (1200 / 3937 / 0.9144) ** 3, "CY"),
            ("meter", "EPSG:32637", 1, 1 / 0.9144 ** 3, "CY"),
            ("meter", "EPSG:32637", 2, 1 / 0.3048 ** 3, "CF"),
            ("usSurveyFoot", "EPSG:2277", 2, (1200 / 3937 / 0.3048) ** 3, "CF"),
            ("usSurveyFoot", "EPSG:2277", 3, (1200 / 3937) ** 3, "m³"),
        ):
            with self.subTest(unit=linear, option=option), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "source.xml"
                declaration = metric if linear == "meter" else (
                    f'<Imperial linearUnit="{linear}" verticalLinearUnit="{linear}" volumeUnit="cubicYard"/>'
                )
                # Recorded SurfVolume is already expressed in its declared volume unit.
                xml = source.replace(metric, declaration)
                if linear != "meter":
                    recorded_factor = 1 / 27 if linear == "foot" else (1200 / 3937 / 0.9144) ** 3
                    xml = xml.replace('volFill="100"', f'volFill="{100 * recorded_factor:.15g}"')
                    xml = xml.replace('volTotal="100"', f'volTotal="{100 * recorded_factor:.15g}"')
                path.write_text(xml, encoding="utf-8")
                p = self._params(str(path), crs)
                if linear == "foot":
                    p["OUTPUT_CRS"] = QgsCoordinateReferenceSystem.fromProj(crs)
                p.update(OUTPUT="memory:", VOLUMES="memory:", FOOTPRINT="memory:", USE_DATUM=False, VOLUME_UNITS=option,
                         REPORT=str(Path(directory) / "corridor.html"))
                result = self.algorithms["landxml_corridor_quantities"].processAlgorithm(p, self.context, self.feedback)
                self.assertEqual(result["VOLUME_UNIT"], label)
                self.assertAlmostEqual(result["CUT_VOLUME"], 575 * factor)
                volumes = list(self.context.getMapLayer(result["VOLUMES"]).getFeatures())
                self.assertAlmostEqual(sum(f["fill_volume"] for f in volumes), result["FILL_VOLUME"])
                self.assertTrue(all(f["volume_unit"] == label for f in volumes))
                self.assertAlmostEqual(min(f["mass_ordinate"] for f in volumes), -575 * factor)
                footprint = list(self.context.getMapLayer(result["FOOTPRINT"]).getFeatures())
                self.assertAlmostEqual(sum(f["cut_volume"] for f in footprint), result["CUT_VOLUME"])
                self.assertTrue(all(f["volume_unit"] == label for f in footprint))
                report = Path(p["REPORT"]).read_text(encoding="utf-8")
                self.assertIn(f"Cut volume ({label})", report)
                self.assertIn(f"Volume ({label})", report)  # material volumes
                p.update(OUTPUT=str(Path(directory) / "difference.tif"), REPORT=str(Path(directory) / "surface.html"),
                         BASE_SURFACE="EG", COMPARE_SURFACE="Design", RESOLUTION=1)
                result = self.algorithms["landxml_surface_cut_fill"].processAlgorithm(p, self.context, self.feedback)
                self.assertEqual(result["VOLUME_UNIT"], label)
                self.assertAlmostEqual(result["fill_volume"], 100 * factor)
                dataset = gdal.Open(p["OUTPUT"])
                self.assertAlmostEqual(float(dataset.GetRasterBand(1).ReadAsArray()[5, 5]), 1)
                dataset = None
                report = Path(p["REPORT"]).read_text(encoding="utf-8")
                self.assertIn(f"Civil 3D ({label})", report)
                self.assertIn(f"Fill volume ({label})", report)
                value = f"{100 * factor:,.2f}"
                self.assertIn(f">Fill</td><td class='num'>{value}</td><td class='num'>{value}</td><td class='num'>0.00</td>",
                              report.replace(">-0.00</td>", ">0.00</td>"))
                p.update(OUTPUT="memory:", REPORT=str(Path(directory) / "profile.html"), FORMATION_WIDTH=10)
                self.algorithms["landxml_profile_cut_fill"].processAlgorithm(p, self.context, self.feedback)
                self.assertIn(f"Cut volume ({label})", Path(p["REPORT"]).read_text(encoding="utf-8"))

    def test_earthwork_volume_selectors_default_to_automatic(self):
        for tool in ("landxml_profile_cut_fill", "landxml_corridor_quantities", "landxml_surface_cut_fill"):
            algorithm = self.algorithms[tool]
            parameter = algorithm.parameterDefinition("VOLUME_UNITS")
            self.assertEqual(parameter.defaultValue(), 0)
            self.assertEqual(parameter.options(), ["Automatic (CY for feet; m³ for metric)",
                                                 "Cubic yards (CY)", "Cubic feet (CF)", "Cubic metres (m³)"])
            p = self._params(CORRIDOR, "EPSG:32637")
            p.update(OUTPUT="memory:", VOLUME_UNITS=99, FORMATION_WIDTH=10)
            with self.subTest(tool=tool), self.assertRaisesRegex(QgsProcessingException, "Unknown volume units"):
                algorithm.processAlgorithm(p, self.context, self.feedback)

    def test_earthwork_volumes_require_explicit_vertical_units(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "no_vertical.xml"
            path.write_text(Path(CORRIDOR).read_text(encoding="utf-8").replace(' verticalLinearUnit="meter"', ''), encoding="utf-8")
            for tool in ("landxml_profile_cut_fill", "landxml_corridor_quantities", "landxml_surface_cut_fill"):
                p = self._params(str(path), "EPSG:32637")
                output = str(Path(directory) / "result.html")
                p.update(FORMATION_WIDTH=10, OUTPUT="memory:", REPORT=output)
                with self.subTest(tool=tool), self.assertRaisesRegex(QgsProcessingException, "known horizontal and vertical"):
                    self.algorithms[tool].processAlgorithm(p, self.context, self.feedback)
                self.assertFalse(Path(output).exists())

    def test_surface_cut_fill_matches_recorded_volume(self):
        with tempfile.TemporaryDirectory() as directory:
            params = self._params(CORRIDOR, "EPSG:32637")
            params.update(
                BASE_SURFACE="EG",
                COMPARE_SURFACE="Design",
                RESOLUTION=1,
                OUTPUT=str(Path(directory) / "diff.tif"),
                REPORT=str(Path(directory) / "diff.html"),
            )
            result = self.algorithms["landxml_surface_cut_fill"].processAlgorithm(
                params, self.context, self.feedback
            )
            # The hidden (i="1") triangle beyond x = 10 is excluded.
            self.assertAlmostEqual(result["fill_volume"], 100)
            self.assertEqual(result["cut_volume"], 0)
            self.assertAlmostEqual(result["compared_area"], 100)
            dataset = gdal.Open(params["OUTPUT"])
            band = dataset.GetRasterBand(1).ReadAsArray()
            self.assertAlmostEqual(float(band[5, 5]), 1)
            dataset = None
            self.assertIn("Civil 3D volume surface", Path(params["REPORT"]).read_text(encoding="utf-8"))

    def test_tin_geotiff_skips_invisible_faces(self):
        with tempfile.TemporaryDirectory() as directory:
            raster = self._params(CORRIDOR, "EPSG:32637")
            raster.update(OUTPUT=str(Path(directory) / "design.tif"), RESOLUTION=1, SURFACE="Design")
            self.algorithms["landxml_tin_to_geotiff"].processAlgorithm(
                raster, self.context, self.feedback
            )
            dataset = gdal.Open(raster["OUTPUT"])
            band = dataset.GetRasterBand(1)
            values = band.ReadAsArray()
            nodata = band.GetNoDataValue()
            self.assertEqual(values.shape, (10, 20))
            self.assertAlmostEqual(float(values[5, 5]), 101)
            self.assertEqual(float(values[8, 15]), nodata)
            dataset = None

    def test_station_relative_cross_sections_are_placed(self):
        params = self._params(CORRIDOR, "EPSG:32637")
        params.update(OUTPUT="memory:", POINTS="memory:")
        result = self.algorithms["landxml_cross_sections"].processAlgorithm(
            params, self.context, self.feedback
        )
        lines = self.context.getMapLayer(result["OUTPUT"])
        self.assertEqual(lines.featureCount(), 10)
        self.assertTrue(QgsWkbTypes.hasZ(lines.wkbType()))
        first = next(
            f for f in lines.getFeatures() if f["station"] == 0 and f["surface_name"] == "EG Surface"
        )
        self.assertEqual(first["placement"], "station-offset")
        start = next(first.geometry().vertices())
        self.assertEqual((start.x(), start.y(), start.z()), (4970, 1000, 100))
        points = self.context.getMapLayer(result["POINTS"])
        offsets = sorted({round(f["offset"], 3) for f in points.getFeatures() if f["station"] == 0})
        self.assertEqual(offsets[0], -30)

        with tempfile.TemporaryDirectory() as directory:
            complete = self._params(CORRIDOR, "EPSG:32637")
            complete["OUTPUT_DIR"] = directory
            for key in (
                "INCLUDE_ALIGNMENTS",
                "INCLUDE_CENTERLINES",
                "INCLUDE_PROFILES",
                "INCLUDE_STATIONS",
                "INCLUDE_FEATURELINES",
                "INCLUDE_BREAKLINES",
                "INCLUDE_SURFACE_BOUNDARY",
                "INCLUDE_DEM",
                "INCLUDE_CONTOURS",
            ):
                complete[key] = False
            complete["INCLUDE_CROSSSECTIONS"] = True
            self.algorithms["landxml_complete_road_design"].processAlgorithm(
                complete, self.context, self.feedback
            )
            package = ogr.Open(str(Path(directory) / "corridor_synthetic_GIS.gpkg"))
            layer = package.GetLayerByName("cross_sections")
            self.assertEqual(layer.GetFeatureCount(), 10)
            self.assertIsNone(package.GetLayerByName("cross_sections_2d"))
            package = None

    def test_alignment_output_remembers_source_for_viewer(self):
        params = self._params(CORRIDOR, "EPSG:32637")
        params["OUTPUT"] = self._loaded()
        result = self.algorithms["landxml_alignments_to_vector"].processAlgorithm(
            params, self.context, self.feedback
        )
        layer = self._post_process(result["OUTPUT"])
        self.assertEqual(layer.customProperty("landxml/source_path"), CORRIDOR)

    def test_surface_cut_fill_detects_and_matches_names(self):
        with tempfile.TemporaryDirectory() as directory:
            params = self._params(CORRIDOR, "EPSG:32637")
            params.update(RESOLUTION=1, OUTPUT=str(Path(directory) / "auto.tif"))
            result = self.algorithms["landxml_surface_cut_fill"].processAlgorithm(
                params, self.context, self.feedback
            )
            self.assertAlmostEqual(result["fill_volume"], 100)
            params.update(BASE_SURFACE="eg", COMPARE_SURFACE="desi", OUTPUT=str(Path(directory) / "typed.tif"))
            result = self.algorithms["landxml_surface_cut_fill"].processAlgorithm(
                params, self.context, self.feedback
            )
            self.assertAlmostEqual(result["fill_volume"], 100)
            params["COMPARE_SURFACE"] = "FRL"
            with self.assertRaisesRegex(QgsProcessingException, "Available: 'EG', 'Design'"):
                self.algorithms["landxml_surface_cut_fill"].processAlgorithm(params, self.context, self.feedback)

    def test_existing_tools_accept_partial_names(self):
        params = self._params(CORRIDOR, "EPSG:32637")
        params.update(OUTPUT="memory:", ALIGNMENT="synthetic")
        result = self.algorithms["landxml_alignments_to_vector"].processAlgorithm(
            params, self.context, self.feedback
        )
        self.assertEqual(self.context.getMapLayer(result["OUTPUT"]).featureCount(), 1)

    def test_cross_section_sheets_pdf_report_and_images(self):
        with tempfile.TemporaryDirectory() as directory:
            params = {
                "INPUT": CORRIDOR,
                "GROUND_SURFACE": "eg surf",
                "OUTPUT": str(Path(directory) / "sheets.pdf"),
                "REPORT": str(Path(directory) / "sections.html"),
                "PNG_FOLDER": str(Path(directory) / "png"),
                "PER_PAGE": 2,
                "TITLE": "Synthetic sheets",
            }
            result = self.algorithms["landxml_cross_section_sheets"].processAlgorithm(
                params, self.context, self.feedback
            )
            self.assertEqual(result["SECTION_COUNT"], 5)
            self.assertEqual(result["PAGE_COUNT"], 2)
            self.assertEqual(Path(params["OUTPUT"]).read_bytes()[:5], b"%PDF-")
            report = Path(params["REPORT"]).read_text(encoding="utf-8")
            self.assertIn("0+150.00", report)
            self.assertGreaterEqual(report.count("<svg"), 5)
            self.assertEqual(len(list(Path(params["PNG_FOLDER"]).glob("*.png"))), 5)
            params.update(RANGE="40-160", INTERVAL=50, OUTPUT=str(Path(directory) / "range.pdf"), REPORT="", PNG_FOLDER="")
            result = self.algorithms["landxml_cross_section_sheets"].processAlgorithm(
                params, self.context, self.feedback
            )
            self.assertEqual(result["SECTION_COUNT"], 3)

    def test_design_report(self):
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / "report.html")
            result = self.algorithms["landxml_design_report"].processAlgorithm(
                {"INPUT": CORRIDOR, "OUTPUT": output, "PLAN_AXES": 1}, self.context, self.feedback
            )
            self.assertEqual(result["ALIGNMENT_COUNT"], 1)
            text = Path(output).read_text(encoding="utf-8")
            for heading in ("Plan", "Curvature diagram", "Gradient diagram", "Long-section", "Horizontal elements"):
                self.assertIn(heading, text)

    def test_design_review_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            params = self._params(CORRIDOR, "EPSG:32637")
            params.update(
                DESIGN_SPEED=60,
                OUTPUT="memory:",
                MAP_OUTPUT="memory:",
                REPORT=str(Path(directory) / "review.html"),
                PROFILE_TXT=str(Path(directory) / "profile.txt"),
                PROFILE_LANDXML=str(Path(directory) / "profile.xml"),
            )
            result = self.algorithms["landxml_design_review"].processAlgorithm(params, self.context, self.feedback)
            table = self.context.getMapLayer(result["OUTPUT"])
            self.assertEqual(table.featureCount(), result["HIGH"] + result["MEDIUM"] + result["LOW"])
            self.assertEqual(self.context.getMapLayer(result["MAP_OUTPUT"]).featureCount(), table.featureCount())
            self.assertIn("Geometric design review", Path(params["REPORT"]).read_text(encoding="utf-8"))
            self.assertEqual(Path(params["PROFILE_TXT"]).read_text(encoding="utf-8").splitlines()[0], "0.000 101.000")
            self.assertIn("<ProfAlign", Path(params["PROFILE_LANDXML"]).read_text(encoding="utf-8"))
            params.pop("DESIGN_SPEED")
            with self.assertRaisesRegex(QgsProcessingException, "design speed"):
                self.algorithms["landxml_design_review"].processAlgorithm(params, self.context, self.feedback)

    def test_us_customary_review_and_design_report(self):
        from landxml_plugin.landxml.criteria import list_standards
        from landxml_plugin.landxml.parser import load_document

        index = [item[0] for item in list_standards()].index("aashto_2018_us_customary")
        source = Path(CORRIDOR).read_text(encoding="utf-8")
        for unit in ("foot", "usSurveyFoot"):
            with self.subTest(unit=unit), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "feet.xml"
                xml = source.replace('<Metric linearUnit="meter" verticalLinearUnit="meter" areaUnit="squareMeter" volumeUnit="cubicMeter"/>',
                                     f'<Imperial linearUnit="{unit}" speedUnit="milesPerHour"/>')
                xml = xml.replace('<Profile name="Synthetic Road">',
                                  '<Feature name="SpeedStation"><Property label="station" value="0"/>'
                                  '<Property label="speed" value="60"/></Feature><Profile name="Synthetic Road">')
                path.write_text(xml, encoding="utf-8")
                params = {
                    "INPUT": str(path), "STANDARD": index, "OUTPUT": "memory:",
                    "REPORT": str(Path(directory) / "review.html"),
                    "PROFILE_LANDXML": str(Path(directory) / "profile.xml"),
                    "PROFILE_TXT": str(Path(directory) / "profile.txt"),
                }
                self.algorithms["landxml_design_review"].processAlgorithm(params, self.context, self.feedback)
                text = Path(params["REPORT"]).read_text(encoding="utf-8")
                self.assertIn("Design speed mph", text)
                self.assertNotIn("km/h", text)
                self.assertEqual(load_document(params["PROFILE_LANDXML"]).horizontal_unit, unit)
                self.assertEqual(Path(params["PROFILE_TXT"]).read_text().splitlines()[0], "0.000 101.000")
                report = str(Path(directory) / "design.html")
                self.algorithms["landxml_design_report"].processAlgorithm(
                    {"INPUT": str(path), "STANDARD": index + 1, "OUTPUT": report}, self.context, self.feedback
                )
                self.assertIn("Design speed mph", Path(report).read_text(encoding="utf-8"))
                params["INPUT"] = CORRIDOR
                with self.assertRaisesRegex(QgsProcessingException, "requires feet"):
                    self.algorithms["landxml_design_review"].processAlgorithm(params, self.context, self.feedback)

    def test_ten_percent_superelevation_in_both_review_dialogs(self):
        from landxml_plugin.landxml.criteria import list_standards

        ids = [item[0] for item in list_standards()]
        source = Path(CORRIDOR).read_text(encoding="utf-8")
        for standard, imperial in (("aashto_2018", False), ("aashto_2018_us_customary", True)):
            with self.subTest(standard=standard), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "source.xml"
                xml = source.replace('<Metric linearUnit="meter" verticalLinearUnit="meter" areaUnit="squareMeter" volumeUnit="cubicMeter"/>',
                                     '<Imperial linearUnit="usSurveyFoot" speedUnit="milesPerHour"/>') if imperial else source
                path.write_text(xml, encoding="utf-8")
                for tool, offset in (("landxml_design_review", 0), ("landxml_design_report", 1)):
                    algorithm = self.algorithms[tool]
                    self.assertEqual(algorithm.parameterDefinition("EMAX").options(), ["4%", "6%", "8%", "10%"])
                    report = str(Path(directory) / (tool + ".html"))
                    algorithm.processAlgorithm({
                        "INPUT": str(path), "STANDARD": ids.index(standard) + offset,
                        "EMAX": 3, "DESIGN_SPEED": 60, "OUTPUT": report if offset else "memory:", "REPORT": report,
                    }, self.context, self.feedback)
                    self.assertIn("e max 10%", Path(report).read_text(encoding="utf-8"))

    def test_design_report_with_review(self):
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / "report.html")
            self.algorithms["landxml_design_report"].processAlgorithm(
                {"INPUT": CORRIDOR, "OUTPUT": output, "STANDARD": 1, "DESIGN_SPEED": 60}, self.context, self.feedback
            )
            text = Path(output).read_text(encoding="utf-8")
            self.assertIn("Geometric design review", text)
            self.assertIn("Phasing diagram", text)


if __name__ == "__main__":
    unittest.main()
