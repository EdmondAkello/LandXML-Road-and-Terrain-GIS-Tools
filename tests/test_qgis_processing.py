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
    QgsWkbTypes,
)
from osgeo import gdal, ogr
from qgis.PyQt import sip

from landxml_plugin.landxml_tin_to_geotiff import LandXMLTinToGeoTIFFPlugin
from landxml_plugin.provider import LandXMLTinToGeoTIFFProvider


OPENROADS = str(ROOT / "tests" / "fixtures" / "openroads" / "terrain_minimal.xml")
CIVIL3D = str(ROOT / "tests" / "fixtures" / "civil3d" / "road_minimal.xml")


class QgisProcessingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QgsApplication([], False)
        cls.app.initQgis()
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
        self.assertEqual(len(self.algorithms), 11)
        self.assertIn("inspect_landxml", self.algorithms)

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
        self.assertIn("VPI 1+00.00\nElev 0.00\nG2 +6.67%", controls[0]["label_text"])
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


if __name__ == "__main__":
    unittest.main()
