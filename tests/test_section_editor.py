"""Corridor Section Editor engine: standards, assemblies, corridor build, outputs and quantities (no QGIS)."""

# ruff: noqa: E402 - bootstrap the plugin package from its checkout directory

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if "landxml_plugin" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "landxml_plugin", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

from landxml_plugin.landxml import assembly as asm
from landxml_plugin.landxml import corridor_build as cb
from landxml_plugin.landxml import corridor_quantities as cq
from landxml_plugin.landxml.corridor import read_corridor_sections
from landxml_plugin.landxml.earthworks import corridor_quantities
from landxml_plugin.landxml.features import read_line_features
from landxml_plugin.landxml.parser import load_document
from landxml_plugin.landxml.section_standards import list_section_standards, load_section_standard
from landxml_plugin.landxml.writer import (
    LEFT, RIGHT, Curve, HorizontalAlignment, Line, Spiral, Surface, VerticalProfile, write_landxml,
)


def grid_surface(name, x0, y0, x1, y1, level, cell=10.0):
    xs, ys = np.arange(x0, x1 + cell, cell), np.arange(y0, y1 + cell, cell)
    X, Y = np.meshgrid(xs, ys)
    Z = level(X, Y)
    xyz = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()])
    ny, nx = X.shape
    idx = np.arange(nx * ny).reshape(ny, nx)
    a, b, c, d = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, :-1].ravel(), idx[1:, 1:].ravel()
    faces = np.concatenate([np.column_stack([a, b, d]), np.column_stack([a, d, c])])
    return Surface(name, xyz.tolist(), faces.tolist())


def straight_road(path, ground_level=100.0, design_level=102.0, northing_first=True, length=200.0):
    """A straight alignment due north at a constant level over flat ground (closed-form sections)."""
    road = HorizontalAlignment("Straight", start=(1000.0, 5000.0), bearing=0.0, elements=[Line(length)])
    profile = VerticalProfile("FRL", [(0, design_level, 0), (length, design_level, 0)])
    eg = grid_surface("EG", 900, 4900, 1100, 5000 + length + 100, lambda X, Y: np.full_like(X, ground_level))
    write_landxml(path, [(road, [profile])], northing_first=northing_first, surfaces=[eg])
    return path


def curved_road(path):
    road = HorizontalAlignment("Curved", start=(500000.0, 9900000.0), bearing=45.0, elements=[
        Line(150), Spiral(60, None, 250, RIGHT), Curve(120, 250, RIGHT), Spiral(60, 250, None, RIGHT), Line(100),
        Curve(150, 100, LEFT), Line(160)])
    profile = VerticalProfile("FRL", [(0, 1500.0, 0), (250, 1506.0, 120), (520, 1499.0, 160), (road.length, 1503.0, 0)])
    pts = np.array(road.points(5.0))
    eg = grid_surface("EG", pts[:, 0].min() - 120, pts[:, 1].min() - 120, pts[:, 0].max() + 120, pts[:, 1].max() + 120,
                      lambda X, Y: 1501.0 + 0.04 * (X - 500100) + 3 * np.sin((Y - 9900000) / 60.0))
    write_landxml(path, [(road, [profile])], northing_first=True, surfaces=[eg])
    return path


def simple_project(path, right, median=None, standard="generic_sections", northing_first=True, interval=20.0):
    project = cb.new_project(path, "Straight", "FRL", standard, template=False, northing_first=northing_first, speed=60,
                             ground={"type": "tin", "path": path, "surface": "EG"})
    project["assemblies"] = {"Test": {"median": median, "right": right, "left": "mirror"}}
    project["regions"] = [{"start": None, "end": None, "assembly": "Test", "transition": 0}]
    project["stations"]["interval"] = interval
    project["superelevation"]["source"] = "none"
    project["widening"]["source"] = "none"
    return project


class StandardsTests(unittest.TestCase):
    def test_bundled_standards_load_with_sources(self):
        ids = [item[0] for item in list_section_standards()]
        for expected in ("kenya_rdm13_2025_sections", "aashto_2018_sections", "aashto_2018_us_customary_sections",
                         "generic_sections"):
            self.assertIn(expected, ids)
        for identifier in ids:
            std = load_section_standard(identifier)
            for key, limit in std.limits.items():
                self.assertTrue(limit.get("ref"), f"{identifier} {key} has no reference")
            for name, tpl in std.templates().items():
                self.assertTrue(tpl.get("right"), f"{identifier} template {name} has no elements")
                for e in tpl["right"]:
                    self.assertIn(e["type"], asm.SCHEMA)
        self.assertEqual(ids[-1], "generic_sections")

    def test_checks_are_advisory(self):
        std = load_section_standard("kenya_rdm13_2025_sections")
        out = std.check("lane", "width", 2.9)
        self.assertEqual(out[0]["level"], "departure")
        self.assertIn("Table 4.1", out[0]["message"])
        self.assertEqual(std.check("lane", "width", 3.5), [])
        self.assertEqual(load_section_standard("generic_sections").check("lane", "width", 1.0), [])

    def test_rdm_widening_and_slope_bands(self):
        std = load_section_standard("kenya_rdm13_2025_sections")
        self.assertEqual(std.widening(300)[0], 0.0)
        self.assertEqual(std.widening(200)[0], 0.6)
        self.assertEqual(std.widening(100)[0], 0.9)
        self.assertEqual(std.widening(50)[0], 1.2)
        self.assertEqual(std.widening(30)[0], 1.5)
        self.assertEqual(std.fill_widening(4.0), 0.3)
        self.assertEqual(std.slope_bands("earth")["fill"][0], [1.0, 4.0])
        aashto = load_section_standard("aashto_2018_sections")
        self.assertAlmostEqual(aashto.relative_gradient(80), 0.5)
        self.assertAlmostEqual(aashto.widening(300, 80)[0], 0.7)       # Table 3-26, 7.2 m roadway
        self.assertEqual(aashto.widening(1000, 80)[0], 0.0)               # below the 0.6 m minimum

    def test_user_values_outside_ranges_still_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = straight_road(os.path.join(tmp, "road.xml"))
            right = [asm.element("lane", width=2.6, crossfall=0.0), asm.element("daylight", fill_bands=[[None, 1.0]])]
            project = simple_project(path, right, standard="kenya_rdm13_2025_sections")
            result = cb.build_corridor(project)
            self.assertTrue(any("Table 4.1" in c["message"] for c in result.checks))
            top = result.sections[0]["top"]
            self.assertAlmostEqual(top[-1].d, 2.6 + 2.0 * 1.0, places=6)     # the 1:1 user slope is used


class AnalyticSectionTests(unittest.TestCase):
    def test_embankment_area_and_volume_closed_form(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = straight_road(os.path.join(tmp, "road.xml"), 100.0, 102.0)
            right = [asm.element("lane", width=3.5, crossfall=0.0, buildup=""),
                     asm.element("daylight", fill_bands=[[None, 2.0]], cut_bands=[[None, 1.5]])]
            result = cb.build_corridor(simple_project(path, right))
            q = cq.quantities(result)
            h, w, n = 2.0, 7.0, 2.0
            area = h * (w + n * h)
            for row in q["rows"]:
                self.assertAlmostEqual(row["fill"], area, places=6)
                self.assertAlmostEqual(row.get("cut", 0.0), 0.0, places=9)
            self.assertAlmostEqual(q["items"]["earthworks/fill"]["quantity"], area * 200.0, places=4)
            self.assertAlmostEqual(q["items"]["earthworks/footprint"]["quantity"], (w + 2 * n * h) * 200.0, places=4)

    def test_cut_with_v_drain_closed_form(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = straight_road(os.path.join(tmp, "road.xml"), 103.0, 100.0)
            right = [asm.element("lane", width=3.0, crossfall=0.0, buildup=""),
                     asm.element("side_drain", shape="v", foreslope=2.0, depth=0.5, bottom=0.0, backslope=0.0),
                     asm.element("daylight", cut_bands=[[None, 1.0]])]
            result = cb.build_corridor(simple_project(path, right))
            sec = result.sections[0]
            codes = [p.code for p in sec["top"] if p.d > 0]
            self.assertEqual(codes, ["ETW", "DITCH_I", "DAYLIGHT"])
            inv = next(p for p in sec["top"] if p.code == "DITCH_I" and p.d > 0)
            self.assertAlmostEqual(inv.d, 4.0)
            self.assertAlmostEqual(inv.z, 99.5)
            day = sec["top"][-1]
            self.assertAlmostEqual(day.d, 4.0 + 3.5)                      # 1:1 from 99.5 to 103
            # per side: 3 deep under the lane, the drain foreslope (mean depth 3.25 over 1 m), the 1:1 back slope
            per_side = 3.0 * 3.0 + 3.25 * 1.0 + 0.5 * 3.5 * 3.5
            q = cq.quantities(result)
            self.assertAlmostEqual(q["rows"][0]["cut"], 2 * per_side, places=6)

    def test_courses_are_width_times_thickness_and_datum_drives_earthworks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = straight_road(os.path.join(tmp, "road.xml"), 100.0, 101.0)
            right = [asm.element("lane", width=3.5, crossfall=0.0, buildup="Pave"),
                     asm.element("daylight", fill_bands=[[None, 2.0]])]
            project = simple_project(path, right)
            project["buildups"] = {"Pave": {"courses": [{"name": "Surface", "thickness": 0.05, "unit": "m2"},
                                                        {"name": "Base", "thickness": 0.25, "unit": "m3"}]}}
            project["course_daylight"] = False
            result = cb.build_corridor(project)
            q = cq.quantities(result)
            self.assertAlmostEqual(q["items"]["shape/Pave|Surface"]["quantity"], 7.0 * 0.05 * 200, places=6)
            self.assertAlmostEqual(q["items"]["shape/Pave|Base"]["quantity"], 7.0 * 0.25 * 200, places=6)
            self.assertAlmostEqual(q["items"]["area/Pave|Surface"]["quantity"], 7.0 * 200, places=6)
            # fill to the datum (0.3 below the top): 0.7 m over 7 m plus the batters from the top edge
            fill = q["rows"][0]["fill"]
            expected = 0.7 * 7.0 + 2 * (0.5 * 1.0 * 2.0)                  # box edge, then 1 m high 1:2 batters
            self.assertAlmostEqual(fill, expected, places=6)

    def test_benches_on_a_deep_cut(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = straight_road(os.path.join(tmp, "road.xml"), 120.0, 100.0)
            right = [asm.element("lane", width=3.5, crossfall=0.0, buildup=""),
                     asm.element("daylight", cut_bands=[[None, 1.0]], bench_height=6.0, bench_width=3.0,
                                 bench_fall=0.0, max_width=100.0)]
            sec = cb.build_corridor(simple_project(path, right)).sections[0]
            benches = [p for p in sec["top"] if p.code == "BENCH_E" and p.d > 0]
            self.assertEqual(len(benches), 3)                             # 6, 12, 18 m; daylight at 20 m
            self.assertAlmostEqual(sec["top"][-1].d, 3.5 + 20.0 + 3 * 3.0)

    def test_retaining_wall_then_slope(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = straight_road(os.path.join(tmp, "road.xml"), 110.0, 100.0)
            right = [asm.element("lane", width=3.5, crossfall=0.0, buildup=""),
                     asm.element("daylight", cut_bands=[[None, 2.0]], wall_cut=3.0, wall_batter=0.0)]
            sec = cb.build_corridor(simple_project(path, right)).sections[0]
            wall = next(p for p in sec["top"] if p.code == "WALL_T" and p.d > 0)
            self.assertAlmostEqual(wall.z, 103.0)
            self.assertAlmostEqual(sec["top"][-1].d, 3.5 + 7.0 * 2.0)       # 1:2 from the wall top to the ground
            self.assertTrue(any(s["name"] == "Retaining wall" and abs(s["height"] - 3.0) < 1e-9 for s in sec["shapes"]))

    def test_kerb_footway_and_raised_median(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = straight_road(os.path.join(tmp, "road.xml"), 100.0, 100.0)
            right = [asm.element("lane", width=3.5, crossfall=2.5, buildup=""),
                     asm.element("kerb", kind="barrier", height=0.15, face=0.0, top=0.15, channel=0.0),
                     asm.element("sidewalk", width=2.0, crossfall=2.0, buildup=""), asm.element("daylight")]
            median = asm.element("median", kind="raised", width=2.0, kerb_height=0.15, top_fall=0.0)
            sec = cb.build_corridor(simple_project(path, right, median=median)).sections[0]
            kt = next(p for p in sec["top"] if p.code == "KERB_T" and p.d > 0)
            self.assertAlmostEqual(kt.z, 100.0 - 3.5 * 0.025 + 0.15, places=6)
            sw = next(p for p in sec["top"] if p.code == "SW_B" and p.d > 0)
            self.assertAlmostEqual(sw.z, kt.z + 2.0 * 0.02, places=6)
            self.assertTrue(any(s["name"] == "Median fill" for s in sec["shapes"]))
            self.assertTrue(any(p.code == "MED_KT" for p in sec["top"]))

    def test_why_this_point(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = straight_road(os.path.join(tmp, "road.xml"), 100.0, 102.0)
            right = [asm.element("lane", width=3.5), asm.element("daylight", fill_bands=[[None, 2.0]])]
            sec = cb.build_corridor(simple_project(path, right)).sections[0]
            etw = next(p for p in sec["top"] if p.code == "ETW")
            self.assertIn("width 3.5", etw.why)
            self.assertIn("crossfall 2.5 %", etw.why)
            self.assertIn("fill slope 1:2", sec["top"][-1].why)


class CorridorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = curved_road(os.path.join(cls.tmp.name, "curved.xml"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def project(self, standard="kenya_rdm13_2025_sections", template="R5 single carriageway"):
        project = cb.new_project(self.path, "Curved", "FRL", standard, template, True, 80,
                                 {"type": "tin", "path": self.path, "surface": "EG"})
        project["superelevation"]["source"] = "standard"
        return project

    def test_superelevation_and_widening_from_the_standard(self):
        result = cb.build_corridor(self.project())
        sch = result.context["schedules"]
        self.assertEqual(len(sch.records), 2)
        right_curve, left_curve = sch.records
        self.assertGreater(right_curve["rate"], 0)
        self.assertLess(left_curve["rate"], 0)
        self.assertAlmostEqual(right_curve["begin_runoff"], 150.0)        # runoff over the spiral (RDM S5.9.4)
        mid = (right_curve["full_start"] + right_curve["full_end"]) / 2
        states = sch.states(mid)
        self.assertAlmostEqual(states["right"].lane_fall, abs(right_curve["rate"]), places=6)
        self.assertAlmostEqual(states["left"].lane_fall, -abs(right_curve["rate"]), places=6)
        self.assertEqual(len(sch.widen), 1)                                # R 100: 0.9 m (Table 4.15); R 250: none
        self.assertAlmostEqual(sch.widen[0]["width"], 0.9)
        s = sum(sch.widen[0]["stations"][1:3]) / 2
        st = sch.states(s)
        self.assertAlmostEqual(st["left"].widening, 0.9)                   # left curve: inside is left
        self.assertAlmostEqual(st["right"].widening, 0.0)

    def test_written_sections_match_corridor_section_quantities(self):
        project = self.project()
        result = cb.build_corridor(project)
        q = cq.quantities(result)
        with tempfile.TemporaryDirectory() as tmp:
            out = cb.write_corridor_landxml(result, os.path.join(tmp, "corridor.xml"))
            root = load_document(out).root
            sections, warnings, surfaces, shapes = read_corridor_sections(root)
            self.assertEqual(len(sections), len(result.sections))
            self.assertIn("EG", surfaces)
            res = corridor_quantities(sections, "EG", "Corridor Top", grade_at=result.context["profile"].elevation,
                                      use_datum=True)
            self.assertAlmostEqual(res["totals"]["cut_volume"], q["items"]["earthworks/cut"]["quantity"], delta=1e-3)
            self.assertAlmostEqual(res["totals"]["fill_volume"], q["items"]["earthworks/fill"]["quantity"], delta=1e-3)
            totals = cq.course_totals(q)
            for name, volume in res["materials"].items():
                engine = sum(v[0] for k, v in totals.items() if k.endswith("|" + name))
                self.assertAlmostEqual(volume, engine, delta=1e-3)
            # feature lines: re-read levels equal the section levels
            features, fw = read_line_features(root)
            self.assertFalse(fw)
            strings = cb.feature_strings(result)
            key = next(k for k in strings if k[0] == "R" and k[2] == "ETW")
            st = np.array([p[0] for p in strings[key]])
            off = np.array([p[1] for p in strings[key]])
            lev = np.array([p[2] for p in strings[key]])
            xy, _t, normal = result.context["frame"].at(st)
            plan = xy + normal * off[:, None]
            parts = [f for f in features if f.name.startswith("ETW R")]  # long lines come in parts
            pts = np.vstack([np.array(f.points)[:, [1, 0, 2]] for f in parts])   # stored northing first
            for p, z in zip(plan, lev):
                k = int(np.argmin(np.hypot(*(pts[:, :2] - p).T)))
                self.assertLess(np.hypot(*(pts[k, :2] - p)), 0.005)
                self.assertLess(abs(pts[k, 2] - z), 0.005)
            # surfaces
            names = [s.attrib.get("name") for s in root.iter() if s.tag.endswith("Surface") and s.attrib.get("name")]
            self.assertIn("Corridor Top", names)
            self.assertIn("Corridor Datum", names)

    def test_axis_order_is_explicit(self):
        result = cb.build_corridor(self.project())
        with tempfile.TemporaryDirectory() as tmp:
            east_first = os.path.join(tmp, "east.xml")
            road = HorizontalAlignment("Curved", start=(500000.0, 9900000.0), bearing=45.0, elements=[Line(300)])
            write_landxml(east_first, [(road, [VerticalProfile("FRL", [(0, 1500, 0), (300, 1503, 0)])])],
                          northing_first=False)
            p = cb.new_project(east_first, "Curved", "FRL", "generic_sections", None, False, 60, {"type": "none"})
            r = cb.build_corridor(p)
            xy = r.sections[0]["xy"]
            self.assertAlmostEqual(xy[0], 500000.0, places=3)
            self.assertTrue(result.sections)

    def test_regions_and_transition_blend(self):
        std = load_section_standard("kenya_rdm13_2025_sections")
        project = self.project(template="R5 single carriageway")
        cb.add_template(project, std, "R4 single carriageway")
        project["regions"] = [{"start": None, "end": 300, "assembly": "R5 single carriageway", "transition": 0},
                              {"start": 300, "end": None, "assembly": "R4 single carriageway", "transition": 40}]
        ctx = cb.prepare(project)
        sec = cb.section_at(ctx, 320.0)
        self.assertAlmostEqual(sec["transition"], 0.5)
        lane = sec["sides"]["right"]["points"]
        etw = next(p for p in lane if p.code == "ETW")
        self.assertAlmostEqual(abs(etw.d), (3.5 + 3.65) / 2, places=6)
        self.assertIn("transition 50 %", etw.why)

    def test_boq_and_variants(self):
        std = load_section_standard("kenya_rdm13_2025_sections")
        a = cb.build_corridor(self.project())
        project_b = self.project(template="R4 single carriageway")
        b = cb.build_corridor(project_b)
        qa, qb = cq.quantities(a), cq.quantities(b)
        items = [{"item": "1.1", "description": "Cut", "unit": "m3", "rate": 2.5, "quantity": "earthworks/cut"},
                 {"item": "1.2", "description": "Surfacing", "unit": "m2", "rate": None,
                  "quantity": "area/Carriageway|Asphalt concrete wearing course"}]
        rows = cq.boq_rows(qa, items)
        self.assertAlmostEqual(rows[0][5], qa["items"]["earthworks/cut"]["quantity"] * 2.5)
        self.assertIsNone(rows[1][5])                                      # no rate is assumed
        comparison = cq.compare(qa, qb, ("R5", "R4"), items)
        surf = next(r for r in comparison["rows"] if r["key"] == "area/Carriageway|Asphalt concrete wearing course")
        self.assertGreater(surf["b"], surf["a"])                           # R4 is wider
        with tempfile.TemporaryDirectory() as tmp:
            path = cq.write_boq(os.path.join(tmp, "boq.xlsx"), qa, items, comparison)
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
                self.assertIn("xl/worksheets/sheet4.xml", names)
                self.assertIn("D2*E2", z.read("xl/worksheets/sheet1.xml").decode())
        html = cq.report_html(a, qa, comparison=comparison)
        self.assertIn("Superelevation", html)
        self.assertIn("advisory", html)
        self.assertTrue(std)

    def test_project_round_trip(self):
        project = self.project()
        with tempfile.TemporaryDirectory() as tmp:
            path = cb.save_project(project, os.path.join(tmp, "p.json"))
            again = cb.load_project(path)
            self.assertEqual(again["assemblies"], json.loads(json.dumps(project["assemblies"])))
            self.assertEqual(again["sha256"], cb.file_sha256(self.path))


class PlanDesignTests(unittest.TestCase):
    def test_fit_edit_refuse_undo(self):
        from landxml_plugin.landxml.plan_design import PlanDesign
        from landxml_plugin.landxml.criteria import load_criteria

        d = PlanDesign.from_polyline([(0, 0), (300, 0), (500, 250), (800, 250)], 200)
        self.assertEqual([e["kind"] for e in d.elements()], ["line", "curve", "line", "curve", "line"])
        d.set_curve(1, 150, 40, 40)
        self.assertEqual([e["kind"] for e in d.elements()][:4], ["line", "spiral", "curve", "spiral"])
        with self.assertRaises(ValueError):
            d.set_curve(1, 5000)
        self.assertEqual(d.curves[0]["radius"], 150)
        mark = d.mark()
        for dx in (5, 10, 15):
            d.move(2, 500 + dx, 250)
        d.squash(mark)
        d.undo()
        self.assertEqual(d.pis[2], [500.0, 250.0])
        checks = d.checks(load_criteria("kenya_rdm13_2025"), 80)
        self.assertTrue(any("Table 5.1" in ref for _l, _m, ref in checks))
        k = d.add(800, 600)                               # a new end; the old end's curve shrinks until it fits
        self.assertEqual(k, 4)
        self.assertIsNotNone(d.alignment)

    def test_pis_from_a_landxml_alignment(self):
        from landxml_plugin.landxml.geometry import read_alignments
        from landxml_plugin.landxml.plan_design import PlanDesign

        with tempfile.TemporaryDirectory() as tmp:
            path = curved_road(os.path.join(tmp, "c.xml"))
            record = read_alignments(path, 1.0)[0][0]
            segs = [dict(s, points=[(p[1], p[0]) for p in s["points"]]) for s in record["segments"]]
            d = PlanDesign.from_segments(segs)
            self.assertAlmostEqual(d.alignment.length, record["vertex_distances"][-1], places=3)
            self.assertEqual(d.curves[0], {"radius": 250.0, "spiral_in": 60.0, "spiral_out": 60.0})


class FeatureTests(unittest.TestCase):
    def test_features_insert_themselves(self):
        from landxml_plugin.landxml import corridor_features as cf

        self.assertAlmostEqual(cf.bed_length(130, 10, 10), 130 * 130 / (254 * 0.2))   # RDM 1.3 Equation 12.1
        with tempfile.TemporaryDirectory() as tmp:
            path = curved_road(os.path.join(tmp, "c.xml"))
            project = cb.new_project(path, "Curved", "FRL", "kenya_rdm13_2025_sections", "R5 single carriageway", True,
                                     80, {"type": "tin", "path": path, "surface": "EG"})
            project["features"] = [cf.new_feature("bus_bay", 100, "left"), cf.new_feature("climbing_lane", 300, "uphill"),
                                   cf.new_feature("junction_mouth", 700, "right"), cf.new_feature("escape_ramp", 650, "downhill"),
                                   cf.new_feature("ghost_island", 600)]
            ctx = cb.prepare(project)
            sides = {f["type"]: f["side"] for f in ctx["features"]}
            self.assertEqual(sides["climbing_lane"], "right")        # the profile falls at 300: uphill traffic goes back
            sec = cb.section_at(ctx, 110.0)
            bay = [p for p in sec["top"] if p.eid == "feat_bus_bay_1"]
            self.assertAlmostEqual(abs(bay[-1].d) - abs(bay[0].d) if len(bay) > 1 else 0.0, 0.0, places=6)
            spans = asm.element_spans(sec)
            inner, outer, _label = spans[("left", "feat_bus_bay_1")]
            self.assertAlmostEqual(abs(outer.d - inner.d), 4.0, places=6)      # full depth along the bay
            half = cb.section_at(ctx, 85.0)                                       # half way along the 30 m entry taper
            inner, outer, _label = asm.element_spans(half)[("left", "feat_bus_bay_1")]
            self.assertAlmostEqual(abs(outer.d - inner.d), 2.0, places=6)
            mouth = cb.section_at(ctx, 700.0)
            self.assertFalse(any(p.code.startswith("DITCH") and p.d > 0 for p in mouth["top"]))
            result = cb.build_corridor(project, context=ctx)
            q = cq.quantities(result)
            self.assertIn("feature/culvert/Junction mouth (side road) junction_mouth_1", q["items"])
            ramp = cb.escape_ramps(result)[0]
            self.assertAlmostEqual(ramp["bed"], cf.bed_length(130, 10, 10))
            out = cb.write_corridor_landxml(result, os.path.join(tmp, "f.xml"))
            names = [a.attrib["name"] for a in load_document(out).root.iter() if a.tag.endswith("Alignment")]
            self.assertIn("Truck escape ramp escape_ramp_1", names)


class InteropTests(unittest.TestCase):
    """Export by stage for Civil 3D and OpenRoads; start from OpenRoads files and from feature lines."""

    def test_stages_and_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = curved_road(os.path.join(tmp, "c.xml"))
            project = cb.new_project(path, "Curved", "FRL", "kenya_rdm13_2025_sections", "R5 single carriageway", True,
                                     80, {"type": "tin", "path": path, "surface": "EG"})
            result = cb.build_corridor(project)
            sizes = {}
            for stage in cb.STAGES:
                for target in cb.TARGETS:
                    out = cb.write_corridor_landxml(result, os.path.join(tmp, f"{stage}_{target}.xml"), True, target, stage)
                    root = load_document(out).root
                    tags = {e.tag.split("}")[-1] for e in root.iter()}
                    sizes[(stage, target)] = os.path.getsize(out)
                    self.assertIn("Alignment", tags)
                    self.assertEqual("ProfAlign" in tags, stage != "alignment")
                    self.assertEqual("CrossSect" in tags, stage == "corridor")
                    self.assertEqual("PlanFeature" in tags, stage in ("surfaces", "corridor"))
                    if stage in ("surfaces", "corridor"):
                        self.assertEqual("Breakline" in tags, target == "openroads")
                        features, warnings = read_line_features(root)
                        self.assertFalse(warnings)
                        etw = next(f for f in features if (f.name or "").startswith("ETW R"))
                        pts = np.array(etw.points)
                        self.assertGreater(len(pts), 10)
                        if target == "civil3d":                   # imported as feature lines, not 3D polylines
                            text = open(out, encoding="utf-8").read()
                            self.assertIn('<Feature code="FeatureLine" source="Autodesk Civil 3D">', text)
                            self.assertIn('<PlanFeatures name="Siteless FeatureLines">', text)
                            self.assertNotIn('label="site"', text)
                            self.assertIn('<Feature code="FeatureLines" source="Autodesk Civil 3D">', text)
                    names = [e.attrib.get("name") for e in root.iter() if e.tag.endswith("Surface")]
                    self.assertIn("EG", names)                        # the ground over the footprint
            self.assertLess(sizes[("alignment", "civil3d")], sizes[("corridor", "civil3d")])

    def test_openroads_file_and_feature_line_start(self):
        from landxml_plugin.landxml.plan_design import PlanDesign, profile_from_points, simplify

        ord_file = ROOT / "tests" / "fixtures" / "openroads" / "station_equations.xml"
        project = cb.new_project(str(ord_file), "Equation road", "Design", "aashto_2018_us_customary_sections",
                                 None, False, 50, {"type": "none"})
        result = cb.build_corridor(project)
        self.assertTrue(any("station equations" in w for w in result.warnings))
        self.assertGreater(len(result.sections), 10)
        line = [(0, 0, 100), (120, 5, 102), (260, 60, 101), (400, 70, 104)]
        d = PlanDesign.from_polyline([p[:2] for p in simplify(line, 0.1)], 150)
        profile = profile_from_points(d.alignment, line, 40.0)
        self.assertAlmostEqual(profile.elevation(0.0), 100.0)
        self.assertAlmostEqual(profile.elevation(d.alignment.length), 104.0)


if __name__ == "__main__":
    unittest.main()


class DemCrsTests(unittest.TestCase):
    """A DEM in another CRS (degrees) under a projected design: resampled into the design CRS, never assumed."""

    def test_geographic_dem_under_a_utm_design(self):
        from osgeo import gdal, osr

        with tempfile.TemporaryDirectory() as directory:
            path = curved_road(os.path.join(directory, "road.xml"))
            utm, wgs = osr.SpatialReference(), osr.SpatialReference()
            utm.ImportFromEPSG(32737)
            wgs.ImportFromEPSG(4326)
            for ref in (utm, wgs):
                ref.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            to_utm = osr.CoordinateTransformation(wgs, utm)
            lon0, lat0 = 38.995, -0.88
            cell = 0.0001                                           # about 11 m
            nx, ny = 300, 300
            cols, rows = np.meshgrid(np.arange(nx), np.arange(ny))
            lonlat = np.column_stack([(lon0 + (cols + 0.5) * cell).ravel(), (lat0 - (rows + 0.5) * cell).ravel()])
            xy = np.array(to_utm.TransformPoints(lonlat.tolist()))[:, :2]
            data = (1501.0 + 0.04 * (xy[:, 0] - 500100) + 3 * np.sin((xy[:, 1] - 9900000) / 60.0)).reshape(ny, nx)
            dem = os.path.join(directory, "dem4326.tif")
            ds = gdal.GetDriverByName("GTiff").Create(dem, nx, ny, 1, gdal.GDT_Float32)
            ds.SetGeoTransform((lon0, cell, 0, lat0, 0, -cell))
            ds.SetProjection(wgs.ExportToWkt())
            ds.GetRasterBand(1).WriteArray(data)
            ds = None
            project = cb.new_project(path, "Curved", "FRL", "generic_sections", None, True, 60,
                                     {"type": "dem", "path": dem, "crs": "EPSG:4326"})
            project["crs"] = "EPSG:32737"
            ctx = cb.prepare(project)
            xy = ctx["frame"].xy[::20]
            z = ctx["ground"].sample(xy)
            expected = 1501.0 + 0.04 * (xy[:, 0] - 500100) + 3 * np.sin((xy[:, 1] - 9900000) / 60.0)
            ok = np.isfinite(z)
            self.assertGreater(ok.mean(), 0.9)
            self.assertLess(np.abs(z[ok] - expected[ok]).max(), 0.6)
            # without the design CRS the DEM is taken as given (degrees): it does not cover the road
            project.pop("crs")
            with self.assertRaises(ValueError):
                cb.prepare(project)


class ElementProfileTests(unittest.TestCase):
    """A side drain whose invert follows its own profile on the alignment."""

    def test_drain_invert_on_a_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "road.xml")
            road = HorizontalAlignment("Straight", start=(1000.0, 5000.0), bearing=0.0, elements=[Line(200.0)])
            frl = VerticalProfile("FRL", [(0, 102.0, 0), (200, 102.0, 0)])
            ditch = VerticalProfile("Ditch", [(0, 100.8, 0), (200, 100.2, 0)])
            eg = grid_surface("EG", 900, 4900, 1100, 5300, lambda X, Y: np.full_like(X, 104.0))
            write_landxml(path, [(road, [frl, ditch])], northing_first=True, surfaces=[eg])
            right = [asm.element("lane", width=3.5, crossfall=2.5),
                     asm.element("side_drain", depth=0.5, invert_profile="Ditch", where="always"),
                     asm.element("daylight")]
            project = simple_project(path, right)
            project["profile"] = "FRL"
            ctx = cb.prepare(project)
            for station, expected in ((0.0, 100.8), (100.0, 100.5), (200.0, 100.2)):
                sec = cb.section_at(ctx, station)
                inv = [p for p in sec["top"] if p.code == "DITCH_I"]
                self.assertEqual(len(inv), 2)
                for p in inv:
                    self.assertAlmostEqual(p.z, expected, places=6)
                    self.assertIn("profile 'Ditch'", p.why)
            # without the profile: depth below the edge
            ctx["project"]["assemblies"]["Test"]["right"][1]["invert_profile"] = ""
            cb.refresh_context(ctx)
            sec = cb.section_at(ctx, 100.0)
            inv = [p for p in sec["top"] if p.code == "DITCH_I"][0]
            self.assertAlmostEqual(inv.z, 102.0 - 3.5 * 0.025 - 0.5, places=6)
