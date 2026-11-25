"""Build the test scenarios in scenarios.json on a DEM: ground clips, shape polygons, road LandXML files and design
surfaces, for testing the tools on realistic ground (run_tools.py then runs the tools on them).

Run with the Python that QGIS uses (the OSGeo4W shell on Windows, or a conda environment with QGIS):

    python tests/scenarios/make_scenarios.py --dem DEM.tif --vertical-unit metre
    python tests/scenarios/make_scenarios.py --dem DEM.tif --vertical-unit metre --only golf_hole --centre golf_hole=E,N
    python tests/scenarios/make_scenarios.py --dem GEM.tif --compare FABDEM.tif --compare ALOS.tif \\
        --vertical-unit metre --warp-crs EPSG:32737

The DEM must be in a projected CRS in metres or feet; the elevation unit is never guessed (give --vertical-unit when
the DEM does not declare it). Comparison DEMs in another CRS are warped only when --warp-crs is given. Everything is
written under --out (default tests/scenarios/out, which git ignores): one folder a scenario with its inputs, outputs
and a scenario.json of what was built.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
APP = None
UNIT_NAMES = {"metre": "m", "meter": "m", "m": "m", "foot": "ft", "feet": "ft", "ft": "ft",
              "us-foot": "US survey foot", "ussurveyfoot": "US survey foot"}


def plugin():
    if "landxml_plugin" not in sys.modules:
        spec = importlib.util.spec_from_file_location("landxml_plugin", REPO / "__init__.py",
                                                      submodule_search_locations=[str(REPO)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return sys.modules["landxml_plugin"]


def start_qgis():
    from qgis.core import QgsApplication
    app = QgsApplication([], False)
    app.initQgis()
    return app


# ------------------------------------------------------------------ the DEM
class Dem:
    def __init__(self, path, unit=None):
        from osgeo import gdal, osr
        gdal.UseExceptions()
        self.path = str(path)
        self.ds = gdal.Open(self.path)
        self.band = self.ds.GetRasterBand(1)
        x0, cx, r1, y0, r2, cy = self.ds.GetGeoTransform()
        if r1 or r2:
            raise SystemExit(f"{path}: the DEM is rotated; warp it to a north-up grid first.")
        self.x0, self.y0, self.cx, self.cy = x0, y0, cx, cy
        self.srs = osr.SpatialReference(wkt=self.ds.GetProjection())
        self.unit = UNIT_NAMES.get((self.band.GetUnitType() or "").strip().lower()) or (UNIT_NAMES[unit] if unit else None)
        self.nodata = self.band.GetNoDataValue()

    @property
    def projected(self):
        return bool(self.srs.IsProjected())

    @property
    def epsg(self):
        code = self.srs.GetAuthorityCode(None)
        return int(code) if code and code.isdigit() else None

    @property
    def bounds(self):
        return (self.x0, self.y0 + self.cy * self.ds.RasterYSize, self.x0 + self.cx * self.ds.RasterXSize, self.y0)

    def overview(self, cell):
        """(z, x0, y0, cell) of the whole DEM resampled to about ``cell`` (GDAL picks an overview when it has one)."""
        factor = max(cell / abs(self.cx), 1.0)
        width, height = max(int(self.ds.RasterXSize / factor), 2), max(int(self.ds.RasterYSize / factor), 2)
        z = self.band.ReadAsArray(buf_xsize=width, buf_ysize=height).astype(float)
        if self.nodata is not None:
            z[z == self.nodata] = np.nan
        return z, self.x0, self.y0, self.cx * self.ds.RasterXSize / width

    def clip(self, box, path):
        """Write the DEM inside ``box`` (xmin, ymin, xmax, ymax) to a GeoTIFF with the elevation unit set."""
        from osgeo import gdal
        gdal.Translate(str(path), self.ds, projWin=[box[0], box[3], box[2], box[1]], creationOptions=["COMPRESS=DEFLATE"])
        out = gdal.Open(str(path), gdal.GA_Update)
        out.GetRasterBand(1).SetUnitType(self.unit)
        out = None
        return str(path)


def warp_clip(path, box, crs, cell, unit, out):
    from osgeo import gdal
    gdal.UseExceptions()
    gdal.Warp(str(out), str(path), dstSRS=crs, outputBounds=box, xRes=cell, yRes=cell, resampleAlg="bilinear",
              creationOptions=["COMPRESS=DEFLATE"])
    ds = gdal.Open(str(out), gdal.GA_Update)
    ds.GetRasterBand(1).SetUnitType(unit)
    ds = None
    return str(out)


def read_grid(path):
    from osgeo import gdal
    ds = gdal.Open(str(path))
    band = ds.GetRasterBand(1)
    z = band.ReadAsArray().astype(float)
    if band.GetNoDataValue() is not None:
        z[z == band.GetNoDataValue()] = np.nan
    x0, cx, _r1, y0, _r2, cy = ds.GetGeoTransform()
    return z, x0, y0, cx, cy


# ------------------------------------------------------------------ finding sites
def find_site(dem, window, terrain, taken, search=None):
    """The centre of a square of side ``window`` whose mean slope is nearest the middle of ``terrain``, all with data,
    clear of the ``taken`` boxes (deterministic)."""
    z, x0, y0, step = dem.overview(max(window / 30.0, 120.0))
    gy, gx = np.gradient(z, step)
    slope = np.hypot(gx, gy)
    k = max(int(round(window / step)), 3)
    bad = ~np.isfinite(slope)
    table = np.pad(np.where(bad, 0.0, slope).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    holes = np.pad(bad.cumsum(0).cumsum(1), ((1, 0), (1, 0)))

    def box_sum(t):
        return t[k:, k:] - t[:-k, k:] - t[k:, :-k] + t[:-k, :-k]

    mean = box_sum(table) / (k * k)
    ok = box_sum(holes) == 0
    lo, hi = terrain
    ok &= (mean >= lo) & (mean <= hi)
    rr, cc = np.mgrid[0:mean.shape[0], 0:mean.shape[1]]
    e = x0 + (cc + k / 2) * step
    n = y0 - (rr + k / 2) * step
    if search:
        ok &= (e >= search[0]) & (e <= search[2]) & (n >= search[1]) & (n <= search[3])
    for t in taken:
        ok &= ~((e - window / 2 < t[2]) & (t[0] < e + window / 2) & (n - window / 2 < t[3]) & (t[1] < n + window / 2))
    if not ok.any():
        raise ValueError(f"No {window:g} m site with a mean slope of {lo:.0%}-{hi:.0%} and full data on this DEM; "
                         "give --centre or widen 'terrain'.")
    score = np.where(ok, np.abs(mean - (lo + hi) / 2), np.inf)
    r, c = np.unravel_index(int(np.argmin(score)), score.shape)
    return float(e[r, c]), float(n[r, c]), float(mean[r, c])


# ------------------------------------------------------------------ shapes
def ring(spec, centre):
    e, n = centre
    if "rect" in spec:
        cx, cy, w, h, rot = spec["rect"]
        local = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
    elif "ellipse" in spec:
        cx, cy, a, b, rot = spec["ellipse"]
        t = np.linspace(0, 2 * math.pi, 49)[:-1]
        local = list(zip(a * np.cos(t), b * np.sin(t)))
    else:
        cx, cy, rot = 0.0, 0.0, 0.0
        local = [tuple(p) for p in spec["polygon"]]
    angle = math.radians(rot)
    ca, sa = math.cos(angle), math.sin(angle)
    pts = [(e + cx + x * ca - y * sa, n + cy + x * sa + y * ca) for x, y in local]
    return np.array(pts + [pts[0]], float)


def ground_stat(expr, polygon, grid):
    """A level from a number or 'median-60' style expression on the ground cells inside ``polygon``."""
    if isinstance(expr, (int, float)):
        return float(expr)
    from landxml_plugin.landxml.grading import _inside_many
    z, x0, y0, cx, cy = grid
    rr, cc = np.mgrid[0:z.shape[0], 0:z.shape[1]]
    pts = np.column_stack([x0 + (cc.ravel() + 0.5) * cx, y0 + (rr.ravel() + 0.5) * cy])
    inside = _inside_many(pts, polygon) & np.isfinite(z.ravel())
    if not inside.any():
        raise ValueError("The shape has no ground under it.")
    values = z.ravel()[inside]
    text = str(expr).replace(" ", "")
    for name, fn in (("median", np.median), ("mean", np.mean), ("min", np.min), ("max", np.max)):
        if text.startswith(name):
            rest = text[len(name):]
            return round(float(fn(values)) + (float(rest) if rest else 0.0), 2)
    return float(text)


LEVEL_KEYS = ("level", "floor", "top", "rim")
GEOMETRY_KEYS = ("rect", "ellipse", "polygon")


def write_shapes(path, shapes, centre, grid, epsg):
    from osgeo import ogr, osr
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(epsg)
    driver = ogr.GetDriverByName("GPKG")
    if Path(path).exists():
        driver.DeleteDataSource(str(path))
    ds = driver.CreateDataSource(str(path))
    layer = ds.CreateLayer("shapes", srs, ogr.wkbPolygon)
    keys = sorted({k for s in shapes for k in s if k not in GEOMETRY_KEYS})
    for key in keys:
        kind = ogr.OFTString if key in ("name", "shape") else ogr.OFTInteger if key == "order" else ogr.OFTReal
        layer.CreateField(ogr.FieldDefn(key, kind))
    built = []
    for spec in shapes:
        polygon = ring(spec, centre)
        feature = ogr.Feature(layer.GetLayerDefn())
        values = {}
        for key in keys:
            if key not in spec:
                continue
            value = ground_stat(spec[key], polygon, grid) if key in LEVEL_KEYS else spec[key]
            feature.SetField(key, value)
            values[key] = value
        geometry = ogr.Geometry(ogr.wkbPolygon)
        line = ogr.Geometry(ogr.wkbLinearRing)
        for x, y in polygon:
            line.AddPoint_2D(float(x), float(y))
        geometry.AddGeometry(line)
        feature.SetGeometry(geometry)
        layer.CreateFeature(feature)
        built.append(values)
    ds = None
    return built


# ------------------------------------------------------------------ roads
def tin_from_grid(grid, tolerance):
    from landxml_plugin.landxml.dem_tin import adaptive_tin
    z, x0, y0, cx, cy = grid
    nodes, faces = adaptive_tin(z, tolerance)
    xyz = np.column_stack([x0 + (nodes[:, 1] + 0.5) * cx, y0 + (nodes[:, 0] + 0.5) * cy, z[nodes[:, 0], nodes[:, 1]]])
    return xyz, faces


def contour_pis(clip, centre, half, simplify):
    """PIs of the contour through the site centre: GDAL traces the contour at the centre's ground level, the longest
    piece within ``half`` of the centre is kept and simplified (Douglas-Peucker, ``simplify`` metres)."""
    from osgeo import gdal, ogr
    ds = gdal.Open(clip)
    band = ds.GetRasterBand(1)
    x0, cx, _r, y0, _r2, cy = ds.GetGeoTransform()
    z = band.ReadAsArray()
    row, col = int((centre[1] - y0) / cy), int((centre[0] - x0) / cx)
    level = float(z[row, col])
    memory = ogr.GetDriverByName("Memory").CreateDataSource("contours")
    layer = memory.CreateLayer("c", geom_type=ogr.wkbLineString)
    layer.CreateField(ogr.FieldDefn("z", ogr.OFTReal))
    gdal.ContourGenerate(band, 0, 0, [level], 0, 0, layer, -1, 0)
    box = ogr.CreateGeometryFromWkt(f"POLYGON(({centre[0] - half} {centre[1] - half},{centre[0] + half} {centre[1] - half},"
                                    f"{centre[0] + half} {centre[1] + half},{centre[0] - half} {centre[1] + half},"
                                    f"{centre[0] - half} {centre[1] - half}))")
    best = None
    for feature in layer:
        piece = feature.GetGeometryRef().Intersection(box)
        for k in range(max(piece.GetGeometryCount(), 1)):
            line = piece.GetGeometryRef(k) if piece.GetGeometryCount() else piece
            if line is not None and line.GetGeometryType() in (ogr.wkbLineString, ogr.wkbLineString25D):
                if best is None or line.Length() > best.Length():
                    best = line.Clone()
    if best is None or best.Length() < half:
        raise ValueError("No contour long enough through the site centre.")
    simple = best.SimplifyPreserveTopology(simplify)
    return [(simple.GetX(i), simple.GetY(i)) for i in range(simple.GetPointCount())], level


def build_contour_road(spec, centre, grid, folder, epsg, unit):
    from landxml_plugin.landxml.writer import PICurve, alignment_from_pis
    half = float(spec["window"]) / 2
    for simplify in (spec.get("simplify", 40.0), 60.0, 90.0, 130.0):
        pis, level = contour_pis(str(folder / "ground.tif"), centre, half * 0.95, simplify)
        for radius in (spec["radius"], spec["radius"] * 0.7, spec["radius"] * 0.5):
            spiral = min(spec.get("spiral", 0.0), radius / 3)
            try:
                alignment_from_pis("check", pis, [PICurve(radius, spiral, spiral)] * (len(pis) - 2))
            except ValueError:
                continue
            fitted = dict(spec, pis=[(x - centre[0], y - centre[1]) for x, y in pis], radius=radius, spiral=spiral,
                          route="pis")
            out = build_road(fitted, centre, grid, folder, epsg, unit)
            out.update(route=f"contour at {level:.1f}, simplified {simplify:g} m, radius {radius:g}")
            return out
    raise ValueError("The contour's PIs leave no room for the curves; raise 'simplify' or lower 'radius'.")


def build_road(spec, centre, grid, folder, epsg, unit):
    from landxml_plugin.landxml.synthetic_corridor import synthetic_corridor
    from landxml_plugin.landxml.tin_overlay import TinIndex
    from landxml_plugin.landxml.writer import (GroundProfile, PICurve, Surface, VerticalProfile, alignment_from_pis,
                                               write_landxml)

    e, n = centre
    if spec.get("route") == "contour":
        return build_contour_road(spec, centre, grid, folder, epsg, unit)
    local = np.asarray(spec["pis"], float)
    if spec.get("orient") == "contour":
        # turn the PI chain so its overall direction (first to last PI) runs along the contours: across the site's
        # main slope, as a hill road is laid out
        z, _x0, _y0, cx, cy = grid
        gy, gx = np.gradient(np.where(np.isfinite(z), z, np.nanmean(z)), cy, cx)
        downhill = math.atan2(-float(np.nanmean(gy)), -float(np.nanmean(gx)))
        chain = local[-1] - local[0]
        turn = downhill + math.pi / 2 - math.atan2(chain[1], chain[0])
        rot = np.array([[math.cos(turn), -math.sin(turn)], [math.sin(turn), math.cos(turn)]])
        local = local @ rot.T
    pis = [(e + x, n + y) for x, y in local]
    curve = PICurve(spec["radius"], spec.get("spiral", 0.0), spec.get("spiral", 0.0))
    alignment = alignment_from_pis(spec["name"].replace("_", " ").title(), pis, [curve] * (len(pis) - 2))
    ground = tin_from_grid(grid, 0.1)
    index = TinIndex(*ground)
    pts = np.array(alignment.points(10.0))
    levels = index.sample(pts[:, :2])
    if not np.isfinite(levels).all():
        raise ValueError("The alignment leaves the ground clip.")
    stations = pts[:, 2]
    eg = GroundProfile("Existing ground", [(s, round(z, 3)) for s, z in zip(stations, levels)], "From the DEM")
    # the design: PVIs every profile_spacing on the averaged ground, then held within max_grade (a pass each way),
    # with vertical curves of K × the grade change (as much as fits between the PVIs)
    spacing = float(spec.get("profile_spacing", 200.0))
    where = [stations[0]]
    while where[-1] + spacing < stations[-1] - spacing / 2:
        where.append(where[-1] + spacing)
    where.append(stations[-1])
    z = [float(levels[np.abs(stations - s) <= spacing / 2].mean()) for s in where]
    z[0], z[-1] = float(levels[0]), float(levels[-1])
    limit = float(spec.get("max_grade", 0.08))
    for k in range(1, len(z)):                       # from the start, follow the ground where the grade allows
        run = where[k] - where[k - 1]
        z[k] = min(max(z[k], z[k - 1] - limit * run), z[k - 1] + limit * run)
    kvalue = float(spec.get("k_value", 20.0))
    pvis = []
    for k, (s, level) in enumerate(zip(where, z)):
        if k in (0, len(z) - 1):
            pvis.append((s, round(level, 3), 0.0))
            continue
        change = abs((z[k + 1] - level) / (where[k + 1] - s) - (level - z[k - 1]) / (s - where[k - 1])) * 100
        room = min(s - where[k - 1] - pvis[-1][2] / 2, (where[k + 1] - s) / 2) * 2 * 0.95
        pvis.append((s, round(level, 3), round(max(min(kvalue * change, room), 0.0), 1)))
    frl = VerticalProfile("Design FRL", pvis)
    centre_pts = np.array(alignment.points(5.0))
    finished = np.array([frl.elevation(s) for s in centre_pts[:, 2]])
    section = spec.get("section", {})
    xyz, faces, _station, _offset, daylight = synthetic_corridor(
        np.column_stack([centre_pts[:, :2], finished]), centre_pts[:, 2], index, **section)
    out = folder / "road.xml"
    units = {"m": "meter", "ft": "foot", "US survey foot": "USSurveyFoot"}[unit]
    write_landxml(str(out), [(alignment, [eg, frl])], linear_unit=units, vertical_unit=units,
                  epsg=epsg, surfaces=[Surface("Existing ground", ground[0].tolist(), ground[1].tolist(), "From the DEM"),
                                       Surface("Corridor", xyz.tolist(), faces.tolist(), "Synthetic corridor")])
    grades = [abs((b[1] - a[1]) / (b[0] - a[0])) for a, b in zip(frl.pvis, frl.pvis[1:])]
    rise = float(levels[-1] - levels[0])
    return {"landxml": str(out), "alignment": alignment.name, "ground_rise": round(rise, 1),
            "design_end_miss": round(float(z[-1] - levels[-1]), 1), "length": round(alignment.length, 1),
            "pis": [[round(x, 3), round(y, 3)] for x, y in pis], "profiles": ["Design FRL"], "ground_profiles": ["Existing ground"],
            "surfaces": ["Existing ground", "Corridor"], "max_grade_pct": round(100 * max(grades), 2),
            "corridor_triangles": len(faces), "widest_daylight": round(float(daylight.max()), 1)}


def build_shapes(spec, centre, grid, folder, epsg, clip):
    from qgis.core import QgsProcessingContext, QgsProcessingFeedback, QgsRasterLayer, QgsVectorLayer
    from landxml_plugin.design_shapes_algorithm import DesignShapesAlgorithm

    gpkg = folder / "shapes.gpkg"
    built = write_shapes(gpkg, spec["shapes"], centre, grid, epsg)
    algorithm = DesignShapesAlgorithm()
    algorithm.initAlgorithm()
    feedback = QgsProcessingFeedback()
    result = algorithm.processAlgorithm(dict(
        GROUND=QgsRasterLayer(clip, "Ground"), SHAPES=QgsVectorLayer(f"{gpkg}|layername=shapes", "Shapes", "ogr"),
        MARGIN=float(spec.get("margin", 30)), CELL=float(spec.get("cell", 0)), VERTICAL_UNIT=0,
        OUTPUT_XML=str(folder / "design.xml"), OUTPUT_DEM=str(folder / "design.tif"),
        REPORT=str(folder / "design_volumes.html")), QgsProcessingContext(), feedback)
    return {"landxml": str(folder / "design.xml"), "shapes": built, "surfaces": ["Existing ground", "Design"],
            "cut": round(result["CUT"], 1), "fill": round(result["FILL"], 1), "design_tif": str(folder / "design.tif"),
            "volumes_report": str(folder / "design_volumes.html")}


def build_dem_tin(spec, box, folder, args, clips):
    from qgis.core import QgsProcessingContext, QgsProcessingFeedback, QgsRasterLayer
    from landxml_plugin.dem_tin_algorithm import DemToTinAlgorithm

    algorithm = DemToTinAlgorithm()
    algorithm.initAlgorithm()
    rows = []
    for label, clip in clips.items():
        for tolerance in spec.get("tolerances", [1.0]):
            started = time.time()
            out = folder / f"{label}_tol{tolerance:g}.xml"
            result = algorithm.processAlgorithm(dict(DEM=QgsRasterLayer(clip, label), TRIANGULATION=0, TOLERANCE=tolerance,
                                                     MAX_TRIANGLES=0, VERTICAL_UNIT=0, OUTPUT=str(out),
                                                     SURFACE_NAME=f"{label} {tolerance:g}"),
                                                QgsProcessingContext(), QgsProcessingFeedback())
            rows.append({"dem": label, "tolerance": tolerance, "points": result["POINTS"], "triangles": result["TRIANGLES"],
                         "max_miss": round(result["MAX_MISS"], 3), "seconds": round(time.time() - started, 1),
                         "mb": round(out.stat().st_size / 1e6, 1), "landxml": str(out)})
    return {"runs": rows}


# ------------------------------------------------------------------ main
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dem", required=True, help="the DEM the scenarios are built on (projected, metres or feet)")
    parser.add_argument("--compare", action="append", default=[], help="another DEM for the DEM comparison scenario")
    parser.add_argument("--vertical-unit", choices=sorted(UNIT_NAMES), help="elevation unit, when the DEM does not declare it")
    parser.add_argument("--warp-crs", help="CRS to warp comparison DEMs to when theirs differs (e.g. EPSG:32737)")
    parser.add_argument("--scenarios", default=str(HERE / "scenarios.json"))
    parser.add_argument("--out", default=str(HERE / "out"))
    parser.add_argument("--only", nargs="*", help="build only these scenarios")
    parser.add_argument("--centre", action="append", default=[], help="name=E,N: the site centre of a scenario")
    parser.add_argument("--search", help="xmin,ymin,xmax,ymax: look for sites only inside this box")
    args = parser.parse_args(argv)

    plugin()
    global APP
    APP = start_qgis()                                   # kept alive for the run
    dem = Dem(args.dem, args.vertical_unit)
    if not dem.projected or dem.epsg is None:
        raise SystemExit("The DEM must be in a projected CRS with an EPSG code (metres or feet); warp it first.")
    if dem.unit is None:
        raise SystemExit("The DEM does not declare its elevation unit: give --vertical-unit.")
    centres = {k: tuple(map(float, v.split(","))) for k, v in (c.split("=", 1) for c in args.centre)}
    search = tuple(map(float, args.search.split(","))) if args.search else None
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    spec_all = json.loads(Path(args.scenarios).read_text(encoding="utf-8"))["scenarios"]
    taken, summary = [], []
    for spec in spec_all:
        name = spec["name"]
        if args.only and name not in args.only:
            continue
        folder = out / name
        folder.mkdir(exist_ok=True)
        started = time.time()
        record = {"name": name, "kind": spec["kind"], "dem": args.dem, "epsg": dem.epsg, "unit": dem.unit}
        try:
            window = float(spec["window"])
            if name in centres:
                e, n = centres[name]
                record["site"] = "given"
            else:
                e, n, slope = find_site(dem, window, spec["terrain"], taken, search)
                record["site"] = f"found (mean slope {slope:.1%})"
            half = window / 2 + float(spec.get("margin", 0)) + window * 0.1
            box = (e - half, n - half, e + half, n + half)
            taken.append(box)
            record.update(centre=[round(e, 1), round(n, 1)], window=window, box=[round(v, 1) for v in box])
            clip = dem.clip(box, folder / "ground.tif")
            grid = read_grid(clip)
            record["ground"] = clip
            print(f"{name}: site {record['site']} at {e:.0f}, {n:.0f}")
            if spec["kind"] == "road":
                record.update(build_road(spec, (e, n), grid, folder, dem.epsg, dem.unit))
            elif spec["kind"] == "shapes":
                record.update(build_shapes(spec, (e, n), grid, folder, dem.epsg, clip))
            elif spec["kind"] == "dem_tin":
                clips = {"primary": clip}
                for k, other in enumerate(args.compare, 1):
                    label = Path(other).stem.split("_")[0].split(" ")[0].lower() or f"dem{k}"
                    odem = Dem(other, args.vertical_unit)
                    if odem.projected and odem.epsg == dem.epsg:
                        clips[label] = odem.clip(box, folder / f"{label}.tif")
                    elif args.warp_crs:
                        clips[label] = warp_clip(other, box, args.warp_crs, 30.0, odem.unit or dem.unit,
                                                 folder / f"{label}.tif")
                    else:
                        print(f"  {other}: another CRS; give --warp-crs to compare it")
                record.update(build_dem_tin(spec, box, folder, args, clips))
            record["status"] = "built"
        except Exception as error:                                   # a scenario that cannot be built is reported
            import traceback
            record["status"] = f"failed: {error}"
            record["traceback"] = traceback.format_exc()
            print(f"{name}: {record['status']}\n{record['traceback']}")
        record["seconds"] = round(time.time() - started, 1)
        (folder / "scenario.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        summary.append(record)
    (out / "scenarios_built.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    for record in summary:
        print(f"{record['name']:28s} {record['status']:10.60s} {record['seconds']:7.1f} s")


if __name__ == "__main__":
    main()
