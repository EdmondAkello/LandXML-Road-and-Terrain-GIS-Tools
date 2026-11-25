"""Graded objects edited on the map (Grading Editor): pads and lines with a level at every vertex.

An object is a dict::

    {"kind": "pad" | "line", "name": str, "vertices": [[x, y, z], ...],
     "cut_slope": 1.5, "fill_slope": 2.0, "bench_height": 0.0, "bench_width": 0.0, "benches_in": "both",
     "max_width": 0.0, "width": 0.0, "crossfall": 0.0, "mode": "both"}

Coordinates are in the design CRS (projected, metres or feet); levels in the same unit. Objects are applied in
order, each on the result of those before it. This module is pure numpy/GDAL (no QGIS), so it is tested on its own
and shared by the editor's live preview and its Build.
"""

from __future__ import annotations

import html
import json

import numpy as np

from . import design_shapes as ds
from .plan_features import footprint_lines
from .writer import FeatureLine, Surface, write_landxml

DEFAULTS = {"cut_slope": 1.5, "fill_slope": 2.0, "bench_height": 0.0, "bench_width": 0.0, "benches_in": "both",
            "max_width": 0.0, "width": 0.0, "crossfall": 0.0, "mode": "both"}
KINDS = {"pad": "Pad / platform", "line": "Graded line (ramp, road, bund, swale)"}


def new_object(kind, name, vertices=None, **values):
    obj = {"kind": kind, "name": name, "vertices": [list(map(float, v)) for v in (vertices or [])]}
    obj.update(DEFAULTS)
    if kind == "line":
        obj["width"] = 6.0
    obj.update(values)
    return obj


def valid(obj):
    n = len(obj.get("vertices") or [])
    return n >= (3 if obj["kind"] == "pad" else 2)


def bounds(objects):
    pts = np.array([v[:2] for o in objects for v in o["vertices"]], float)
    if not len(pts):
        return None
    return pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max()


def reach(obj, ground_relief=0.0):
    """How far an object's batters may run (for the grid window): max width or relief × the flatter slope."""
    if obj.get("max_width"):
        return float(obj["max_width"]) + float(obj.get("width") or 0) / 2
    slope = max(float(obj["cut_slope"]), float(obj["fill_slope"]))
    bench = float(obj.get("bench_width") or 0) / max(float(obj.get("bench_height") or 0) or 1e9, 1e-9)
    z = [v[2] for v in obj["vertices"]]
    span = (max(z) - min(z)) if z else 0.0
    return (ground_relief + span + 2.0) * (slope + bench) + float(obj.get("width") or 0) / 2


def window(objects, ground_relief=10.0, margin=10.0):
    box = bounds(objects)
    if box is None:
        return None
    r = max([reach(o, ground_relief) for o in objects] + [margin])
    r = min(r, 2000.0)
    return box[0] - r, box[1] - r, box[2] + r, box[3] + r


def ground_grid(spec, box, cell):
    """The ground as a Grid over ``box`` (design CRS): spec {"type": "dem", "path", "crs", "design_crs", "band"} or
    {"type": "flat", "level"}; a DEM in another CRS is resampled into the design CRS (bilinear)."""
    x0, y0, x1, y1 = box
    cols, rows = max(int(np.ceil((x1 - x0) / cell)), 2), max(int(np.ceil((y1 - y0) / cell)), 2)
    grid = ds.Grid(np.zeros((rows, cols)), x0, y0 + rows * cell, cell)
    if spec.get("type") == "flat":
        grid.z[:] = float(spec.get("level", 0.0))
        return grid
    from osgeo import gdal

    from .corridor_build import _dem_in_design_crs

    source = gdal.Open(spec["path"])
    if source is None:
        raise ValueError(f"Cannot open the DEM {spec['path']}.")
    dataset = _dem_in_design_crs(source, spec, (x0, y0, x1, y1))
    gt = dataset.GetGeoTransform()
    band = dataset.GetRasterBand(int(spec.get("band", 1)))
    nodata = band.GetNoDataValue()
    data = band.ReadAsArray().astype(float)
    if nodata is not None:
        data[data == nodata] = np.nan
    data[data < -9000] = np.nan
    src = ds.Grid(data, gt[0], gt[3], abs(gt[1]))
    if abs(abs(gt[5]) - abs(gt[1])) > 1e-9 * abs(gt[1]):
        # non-square cells: sample by the true geotransform
        xy = grid.xy
        col = (xy[:, 0] - gt[0]) / gt[1] - 0.5
        row = (xy[:, 1] - gt[3]) / gt[5] - 0.5
        c0, r0 = np.clip(np.floor(col).astype(int), 0, data.shape[1] - 2), np.clip(np.floor(row).astype(int), 0, data.shape[0] - 2)
        fc, fr = np.clip(col - c0, 0, 1), np.clip(row - r0, 0, 1)
        z = (data[r0, c0] * (1 - fc) * (1 - fr) + data[r0, c0 + 1] * fc * (1 - fr) + data[r0 + 1, c0] * (1 - fc) * fr
             + data[r0 + 1, c0 + 1] * fc * fr)
        grid.z = z.reshape(rows, cols)
    else:
        grid.z = src.sample(grid.xy).reshape(rows, cols)
    return grid


def apply(grid, objects):
    """(design Grid, [per-object dict with cut, fill]) applying the valid objects in order."""
    design, rows = grid, []
    for obj in objects:
        if not valid(obj):
            continue
        v = np.asarray(obj["vertices"], float)
        common = dict(cut_slope=float(obj["cut_slope"]), fill_slope=float(obj["fill_slope"]),
                      bench_height=float(obj.get("bench_height") or 0), bench_width=float(obj.get("bench_width") or 0),
                      benches_in=obj.get("benches_in", "both"), max_width=float(obj.get("max_width") or 0) or None)
        if obj["kind"] == "pad":
            after = ds.graded_pad(design, v, **common)
        else:
            after = ds.graded_line(design, v, width=float(obj.get("width") or 0), mode=obj.get("mode", "both"),
                                   crossfall=float(obj.get("crossfall") or 0), **common)
        cut, fill = ds.volumes(design, after)
        rows.append({"name": obj["name"], "kind": obj["kind"], "cut": cut, "fill": fill})
        design = after
    return design, rows


def preview(objects, ground_spec, budget=60_000, cell=None):
    """(ground, design, rows, cell) on a grid sized to ``budget`` cells over the objects and their batters."""
    objs = [o for o in objects if valid(o)]
    if not objs:
        return None
    box = window(objs)
    if cell is None:
        area = (box[2] - box[0]) * (box[3] - box[1])
        cell = max(float(np.sqrt(area / budget)), 0.25)
    ground = ground_grid(ground_spec, box, cell)
    if not np.isfinite(ground.z).any():
        raise ValueError("The ground does not cover the objects (check the DEM and the design CRS).")
    design, rows = apply(ground, objs)
    return ground, design, rows, cell


def daylight_lines(ground, design, min_length=None):
    """Outlines of the earthworks (where design and ground differ), as (n, 3) arrays at design levels."""
    depth = (design.z - ground.z).ravel()
    pts, faces = design.tin()
    keep = np.flatnonzero(np.isfinite(design.z.ravel()))
    if not (np.abs(depth[keep]) > 1e-6).any():
        return []
    return [np.asarray(line)[:, :3] for line in
            footprint_lines(pts, faces, depth[keep], tolerance=0.01, min_length=min_length or ground.cell * 2)]


def build(objects, ground_spec, out_xml, unit="meter", epsg=None, wkt=None, ground_name="ground", dem_path=None,
          report_path=None, json_path=None, cell=None, budget=250_000, dxf_path=None, tin_tolerance=0.02):
    """Write the LandXML (existing ground and design TINs; outlines with their vertex levels and daylight lines as
    feature lines; northing first, as Civil 3D writes), and optionally the design GeoTIFF, an HTML report, the objects
    as JSON and a DXF of the same. Returns totals."""
    result = preview(objects, ground_spec, budget=budget, cell=cell)
    if result is None:
        raise ValueError("Nothing to build: draw a pad (3 or more vertices) or a line (2 or more).")
    ground, design, rows, cell = result
    (eg_pts, eg_faces), (fg_pts, fg_faces) = _tins(ground, design, tin_tolerance)
    lines = []
    for obj in objects:
        if not valid(obj):
            continue
        v = [tuple(map(float, p[:3])) for p in obj["vertices"]]
        if obj["kind"] == "pad" and v[0][:2] != v[-1][:2]:
            v.append(v[0])
        lines.append(FeatureLine(f"{obj['name']} {'outline' if obj['kind'] == 'pad' else 'line'}", v))
    for k, line in enumerate(daylight_lines(ground, design)):
        lines.append(FeatureLine(f"Daylight {k + 1}", [tuple(map(float, p)) for p in line]))
    write_landxml(out_xml, [], linear_unit=unit, vertical_unit=unit, epsg=epsg,
                  surfaces=[Surface("Existing ground", eg_pts.tolist(), eg_faces.tolist(), f"Resampled from {ground_name}"),
                            Surface("Design", fg_pts.tolist(), fg_faces.tolist(), "Grading Editor")],
                  feature_lines=lines)
    cut, fill = ds.volumes(ground, design)
    if dem_path:
        from ..core import write_geotiff
        write_geotiff(dem_path, np.where(np.isfinite(design.z), design.z, -9999.0), design.x0, design.y0, cell, wkt=wkt)
    if report_path:
        with open(report_path, "w", encoding="utf-8") as stream:
            stream.write(report_html(rows, cut, fill, unit, cell, ground_name))
    if json_path:
        save(json_path, objects, ground_spec, epsg)
    if dxf_path:
        from .dxf_export import export_dxf
        export_dxf(out_xml, dxf_path, northing_first=True, station_interval=0.0)
    return {"cut": cut, "fill": fill, "rows": rows, "cell": cell, "xml": out_xml}


def _tins(ground, design, tolerance):
    """Ground and design as TINs on one adaptive mesh within ``tolerance`` of both (flat pads and batters need few
    triangles; where the design equals the ground the two match exactly, so no false cut or fill appears)."""
    if not tolerance:
        return ground.tin(), design.tin()
    from .dem_tin import adaptive_tin

    nodes, faces = adaptive_tin(design.z, tolerance, also=[ground.z])
    nodes = np.asarray(nodes)
    if not len(faces):
        return ground.tin(), design.tin()
    rows, cols = nodes[:, 0], nodes[:, 1]
    xy = np.column_stack([ground.x0 + (cols + 0.5) * ground.cell, ground.y0 - (rows + 0.5) * ground.cell])
    faces = np.asarray(faces)
    return ((np.column_stack([xy, ground.z[rows, cols]]), faces), (np.column_stack([xy, design.z[rows, cols]]), faces))


def report_html(rows, cut, fill, unit, cell, ground_name):
    u = "m" if unit == "meter" else "ft"
    body = "".join(f"<tr><td>{html.escape(r['name'])}</td><td>{html.escape(KINDS.get(r['kind'], r['kind']))}</td>"
                   f"<td>{r['cut']:,.1f}</td><td>{r['fill']:,.1f}</td><td>{r['fill'] - r['cut']:,.1f}</td></tr>"
                   for r in rows)
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'><title>Grading quantities</title><style>"
            "body{font-family:sans-serif;margin:24px}table{border-collapse:collapse}td,th{border:1px solid #bbb;"
            "padding:4px 8px;text-align:right}td:first-child,td:nth-child(2),th{text-align:left}</style></head><body>"
            "<h1>Grading quantities</h1>"
            f"<p>Cut <b>{cut:,.1f}</b> · Fill <b>{fill:,.1f}</b> · Net (fill − cut) <b>{fill - cut:,.1f}</b> "
            f"cubic {u} (bank volumes, no shrinkage or bulking), on a {cell:.2f} {u} grid from {html.escape(ground_name)}."
            "</p><table><tr><th>Object</th><th>Type</th><th>Cut</th><th>Fill</th><th>Net</th></tr>"
            f"{body}</table><p>Each object's volumes are measured on the result of the objects before it.</p>"
            "</body></html>")


def save(path, objects, ground_spec=None, epsg=None):
    with open(path, "w", encoding="utf-8") as stream:
        json.dump({"version": 1, "epsg": epsg, "ground": ground_spec, "objects": objects}, stream, indent=1)


def load(path):
    with open(path, encoding="utf-8") as stream:
        data = json.load(stream)
    objects = []
    for o in data.get("objects", []):
        obj = new_object(o.get("kind", "pad"), o.get("name", "Object"))
        obj.update(o)
        objects.append(obj)
    return objects, data.get("ground"), data.get("epsg")


def set_levels(obj, how, value, ground=None, index=0):
    """Change vertex levels: "level" (all at value), "raise" (by value), "grade" (value % from vertex ``index`` along
    the vertices, both ways), "ground" (ground + value; ``ground`` is a function of (n, 2) points)."""
    v = np.asarray(obj["vertices"], float)
    if how == "level":
        v[:, 2] = value
    elif how == "raise":
        v[:, 2] += value
    elif how == "ground":
        if ground is None:
            raise ValueError("No ground chosen.")
        z = np.asarray(ground(v[:, :2]), float)
        if not np.isfinite(z).all():
            raise ValueError("The ground does not cover every vertex.")
        v[:, 2] = z + value
    elif how == "grade":
        seg = np.hypot(*np.diff(v[:, :2], axis=0).T)
        chain = np.concatenate([[0.0], np.cumsum(seg)])
        v[:, 2] = v[index, 2] + (chain - chain[index]) * value / 100.0
    else:
        raise ValueError(f"Unknown level change {how}.")
    obj["vertices"] = v.tolist()
    return obj


__all__ = ["new_object", "valid", "window", "ground_grid", "apply", "preview", "build", "save", "load", "set_levels",
           "daylight_lines", "KINDS", "DEFAULTS"]
