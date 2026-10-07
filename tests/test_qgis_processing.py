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
    QgsProcessingOutputLayerDefinition,
    QgsProject,
    QgsWkbTypes,
)
from osgeo import gdal, ogr
from qgis.PyQt import sip

from landxml_plugin.landxml_tin_to_geotiff import LandXMLTinToGeoTIFFPlugin
from landxml_plugin.provider import LandXMLTinToGeoTIFFProvider


OPENROADS = str(ROOT / "tests" / "fixtures" / "openroads" / "terrain_minimal.xml")
CIVIL3D = str(ROOT / "tests" / "fixtures" / "civil3d" / "road_minimal.xml")
CORRIDOR = str(ROOT / "tests" / "fixtures" / "civil3d" / "corridor_synthetic.xml")


class QgisProcessingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QgsApplication.instance()
        if cls.app is None:
            # GUI-enabled so the profile viewer tests can share this instance.
            cls.app = QgsApplication([], True)
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
        self.assertEqual(len(self.algorithms), 16)
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


if __name__ == "__main__":
    unittest.main()
