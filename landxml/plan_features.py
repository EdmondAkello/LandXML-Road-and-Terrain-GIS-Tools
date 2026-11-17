"""Grading lines written back as LandXML PlanFeatures (Civil 3D feature lines).

Lines are kept in the file's stored coordinate order (northing easting elevation for
Civil 3D), so a written file imports back into the drawing it came from whatever
coordinate interpretation was used for the GIS outputs. Each line is a chain of
``Line`` elements with ``Feature code="FeatureLine"``, the form Civil 3D exports and
imports; ``landxml/features.py`` reads the same form.
"""

from __future__ import annotations

from datetime import datetime
import math

import numpy as np

import xml.etree.ElementTree as ET  # nosec B405 - builds and writes nodes only; nothing is parsed here

NAMESPACE = "http://www.landxml.org/schema/LandXML-1.2"
METRIC = {"meter", "metre", "millimeter", "centimeter", "kilometer"}


def level_lines(xyz, faces, level=0.0, values=None, decimals=6):
    """Polylines where ``values`` (default: the elevations) equal ``level`` on a TIN.

    Each triangle whose vertices straddle the level gives one segment; segments are
    chained through shared end points. Elevations along the line are interpolated
    from ``xyz[:, 2]`` on the same edges, so a zero-depth line of a depth surface can
    carry design levels. A vertex exactly at the level counts as above it. Returns a
    list of (n, 3) arrays; a closed line repeats its first point at the end.
    """
    xyz = np.asarray(xyz, float)
    faces = np.asarray(faces, np.int64)
    values = xyz[:, 2] if values is None else np.asarray(values, float)
    if not len(faces):
        return []
    v = values[faces] - level
    above = v >= 0
    count = above.sum(axis=1)
    mixed = (count == 1) | (count == 2)
    if not mixed.any():
        return []
    faces, v, above = faces[mixed], v[mixed], above[mixed]
    ends = []
    for a, b in ((0, 1), (1, 2), (2, 0)):
        cross = above[:, a] != above[:, b]
        t = np.where(cross, v[:, a] / np.where(cross, v[:, a] - v[:, b], 1.0), np.nan)
        pa, pb = xyz[faces[:, a]], xyz[faces[:, b]]
        ends.append((cross, pa + (pb - pa) * t[:, None]))
    segments = []
    for k in range(len(faces)):
        points = [point[k] for cross, point in ends if cross[k]]
        if len(points) == 2 and math.dist(points[0][:2], points[1][:2]) > 10.0 ** -decimals:
            segments.append(points)
    return _chain(segments, decimals)


def _chain(segments, decimals):
    def key(point):
        return (round(float(point[0]), decimals), round(float(point[1]), decimals))

    touching = {}
    for index, (start, end) in enumerate(segments):
        touching.setdefault(key(start), []).append(index)
        touching.setdefault(key(end), []).append(index)
    used = np.zeros(len(segments), bool)

    def walk(point, line):
        while True:
            options = [i for i in touching.get(key(point), ()) if not used[i]]
            if not options:
                return
            index = options[0]
            used[index] = True
            start, end = segments[index]
            point = end if key(start) == key(point) else start
            line.append(point)

    lines = []
    # Open lines first, from an end point, so they are not split in the middle.
    order = sorted(range(len(segments)), key=lambda i: min(len(touching[key(segments[i][0])]),
                                                           len(touching[key(segments[i][1])])))
    for index in order:
        if used[index]:
            continue
        used[index] = True
        start, end = segments[index]
        line = [start, end]
        walk(end, line)
        line.reverse()
        walk(start, line)
        lines.append(np.array(line, float))
    return lines


def weld(xyz, faces, decimals=6):
    """Merge vertices at the same plan position (to ``decimals``) and drop zero-area faces.

    Returns (vertices, faces, source index of each vertex)."""
    xyz = np.asarray(xyz, float)
    faces = np.asarray(faces, np.int64)
    used = np.unique(faces)
    keys = np.round(xyz[used, :2], decimals)
    _unique, first, inverse = np.unique(keys, axis=0, return_index=True, return_inverse=True)
    welded = xyz[used[first]]
    remap = np.empty(int(used.max()) + 1, np.int64)
    remap[used] = inverse.reshape(-1)
    welded_faces = remap[faces]
    area2 = np.cross(welded[welded_faces[:, 1], :2] - welded[welded_faces[:, 0], :2],
                     welded[welded_faces[:, 2], :2] - welded[welded_faces[:, 0], :2])
    return welded, welded_faces[np.abs(area2) > 0], used[first]


def footprint_lines(xyz, faces, depth, tolerance=0.01, min_length=1.0, decimals=6):
    """Outline of the earthworks footprint of a depth surface: closed lines around the
    triangles where |depth| exceeds ``tolerance``, with elevations from ``xyz[:, 2]``
    (pass design levels for daylight lines). Where design and existing ground coincide
    (tie-ins, untouched islands) there is no footprint, so the outer line is the daylight
    line and islands become holes. Rings shorter than ``min_length`` in plan are dropped.
    """
    from .grading import outline_polygons

    xyz = np.asarray(xyz, float)
    faces = np.asarray(faces, np.int64)
    depth = np.asarray(depth, float)
    worked = faces[np.abs(depth[faces]).max(axis=1) > tolerance]
    if not len(worked):
        return []
    welded, welded_faces, _first = weld(xyz, worked, decimals)
    lines = []
    for line in ring_lines(outline_polygons(welded, welded_faces, dims=3)):
        if np.hypot(*np.diff(line[:, :2], axis=0).T).sum() >= min_length:
            lines.append(line)
    return lines


def ring_lines(polygons):
    """Closed lines (first point repeated) from ``[(outer, [holes]), ...]`` rings with elevations."""
    lines = []
    for outer, holes in polygons:
        for ring in [outer, *holes]:
            ring = np.asarray(ring, float)
            if len(ring) >= 3:
                lines.append(np.vstack([ring, ring[:1]]))
    return lines


def _number(value):
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _units(parent, horizontal, vertical):
    horizontal = horizontal or "meter"
    metric = horizontal.lower() in METRIC
    attributes = {"linearUnit": horizontal}
    if metric:
        attributes.update(areaUnit="squareMeter", volumeUnit="cubicMeter")
    else:
        attributes.update(areaUnit="squareFoot", volumeUnit="cubicYard")
    attributes.update(angularUnit="decimal degrees", directionUnit="decimal degrees")
    if vertical:
        attributes["verticalLinearUnit"] = vertical
    ET.SubElement(ET.SubElement(parent, "Units"), "Metric" if metric else "Imperial", attributes)


def write_plan_features(path, features, horizontal_unit=None, vertical_unit=None, epsg=None,
                        group="Grading feature lines", application_version=""):
    """Write ``features`` as one PlanFeatures group; returns the number written.

    Each feature is a dict with ``name``, ``points`` ((n, 3), stored coordinate
    order), optional ``description`` and ``properties`` (label → value, written as
    Feature properties). Lines with fewer than two distinct points are skipped.
    """
    ET.register_namespace("", NAMESPACE)
    now = datetime.now()
    root = ET.Element("LandXML", {"xmlns": NAMESPACE, "version": "1.2",
                                  "date": now.strftime("%Y-%m-%d"), "time": now.strftime("%H:%M:%S")})
    _units(root, horizontal_unit, vertical_unit)
    if epsg:
        ET.SubElement(root, "CoordinateSystem", {"epsgCode": str(epsg)})
    ET.SubElement(root, "Application", {"name": "LandXML Road & Terrain GIS Tools",
                                        "manufacturer": "LandXML Road & Terrain GIS Tools",
                                        "version": str(application_version)})
    container = ET.SubElement(root, "PlanFeatures", {"name": group})
    names, written = {}, 0
    for feature in features:
        points = np.asarray(feature["points"], float)
        if points.ndim != 2 or points.shape[1] < 3 or not np.isfinite(points[:, :3]).all():
            continue
        keep = np.ones(len(points), bool)
        keep[1:] = np.any(np.abs(np.diff(points[:, :3], axis=0)) > 1e-9, axis=1)
        points = points[keep]
        if len(points) < 2:
            continue
        name = str(feature.get("name") or f"Line {written + 1}")
        if name in names:  # Civil 3D needs unique names within a site
            names[name] += 1
            name = f"{name} ({names[name]})"
        else:
            names[name] = 1
        attributes = {"name": name}
        if feature.get("description"):
            attributes["desc"] = str(feature["description"])
        node = ET.SubElement(container, "PlanFeature", attributes)
        geometry = ET.SubElement(node, "CoordGeom")
        for start, end in zip(points[:-1], points[1:]):
            line = ET.SubElement(geometry, "Line")
            ET.SubElement(line, "Start").text = " ".join(_number(value) for value in start[:3])
            ET.SubElement(line, "End").text = " ".join(_number(value) for value in end[:3])
        code = ET.SubElement(node, "Feature", {"code": "FeatureLine"})
        for label, value in (feature.get("properties") or {}).items():
            ET.SubElement(code, "Property", {"label": str(label), "value": str(value)})
        written += 1
    tree = ET.ElementTree(root)
    ET.indent(tree, space=" ")
    with open(path, "wb") as stream:
        tree.write(stream, encoding="utf-8", xml_declaration=True)
    return written
