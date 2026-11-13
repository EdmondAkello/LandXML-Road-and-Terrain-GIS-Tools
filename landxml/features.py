"""Source-backed line features shared by all QGIS outputs."""

from __future__ import annotations

from dataclasses import dataclass
import math

from .common import child, descendants, first_descendant, local_name
from .geometry import _curve_points, _spiral_points


@dataclass(frozen=True)
class LineFeature:
    """A LandXML polyline without GIS CRS assignment or unit conversion."""

    kind: str
    name: str | None
    description: str | None
    feature_code: str | None
    breakline_type: str | None
    surface_name: str | None
    properties: dict[str, str]
    points: tuple[tuple[float, ...], ...]


def _points(node, label):
    point_list = first_descendant(node, "PntList3D")
    dimension = 3
    if point_list is None:
        point_list = first_descendant(node, "PntList2D")
        dimension = 2
    if point_list is None:
        raise ValueError(f"{label} has no PntList3D or PntList2D")
    tokens = (point_list.text or "").split()
    if len(tokens) % dimension:
        raise ValueError(
            f"{label} has {len(tokens)} coordinate values, not a multiple of {dimension}"
        )
    if len(tokens) < 2 * dimension:
        raise ValueError(f"{label} requires at least two points")
    try:
        values = [float(token) for token in tokens]
    except ValueError as exc:
        raise ValueError(f"{label} contains a nonnumeric coordinate") from exc
    if not all(math.isfinite(value) for value in values):
        raise ValueError(f"{label} contains a nonfinite coordinate")
    return tuple(
        tuple(values[i : i + dimension]) for i in range(0, len(values), dimension)
    )


def _coords(node, label):
    """All numbers of a Start / End / Center / PI node, as stored (LandXML: northing easting [elevation])."""
    if node is None or not (node.text or "").strip():
        raise ValueError(f"{label} has a missing coordinate")
    try:
        values = tuple(float(v) for v in node.text.split())
    except ValueError as exc:
        raise ValueError(f"{label} contains a nonnumeric coordinate") from exc
    if len(values) not in (2, 3) or not all(math.isfinite(v) for v in values):
        raise ValueError(f"{label} has {len(values)} coordinate values, not 2 or 3")
    return values


def _with_z(points2d, z0, z1):
    """Elevations interpolated by length along a sampled curve or spiral (None: 2D)."""
    if z0 is None or z1 is None:
        return [tuple(p) for p in points2d]
    lengths = [0.0]
    for a, b in zip(points2d, points2d[1:]):
        lengths.append(lengths[-1] + math.dist(a, b))
    total = lengths[-1] or 1.0
    return [(p[0], p[1], z0 + (z1 - z0) * d / total) for p, d in zip(points2d, lengths)]


def _three_point_arc(start, mid, end, segment_length, label):
    """Civil 3D's feature-line Curve: Start, Center and End are points ON the arc (Center its middle point,
    with its own elevation) and no rot is given. Sampled through the three points in stored order, with
    chords within 5 mm of the arc; elevations interpolated start-middle and middle-end by length."""
    (x0, y0), (x1, y1), (x2, y2) = start[:2], mid[:2], end[:2]
    d = 2 * (x0 * (y1 - y2) + x1 * (y2 - y0) + x2 * (y0 - y1))
    if abs(d) < 1e-12:                                  # collinear: a straight segment through the middle
        return [tuple(start), tuple(mid), tuple(end)]
    s0, s1, s2 = x0 * x0 + y0 * y0, x1 * x1 + y1 * y1, x2 * x2 + y2 * y2
    cx = (s0 * (y1 - y2) + s1 * (y2 - y0) + s2 * (y0 - y1)) / d
    cy = (s0 * (x2 - x1) + s1 * (x0 - x2) + s2 * (x1 - x0)) / d
    r = math.hypot(x0 - cx, y0 - cy)
    a0, a1, a2 = (math.atan2(y - cy, x - cx) for x, y in ((x0, y0), (x1, y1), (x2, y2)))
    ccw_to_mid, ccw_to_end = (a1 - a0) % (2 * math.pi), (a2 - a0) % (2 * math.pi)
    sweep = ccw_to_end if ccw_to_mid < ccw_to_end else -((a0 - a2) % (2 * math.pi))
    step = min(segment_length, math.sqrt(8.0 * r * 0.005))    # chords within 5 mm of a tight arc
    n = max(2, int(math.ceil(abs(sweep) * r / max(step, 0.001))) + 1)
    plan = [(cx + r * math.cos(a0 + sweep * i / (n - 1)), cy + r * math.sin(a0 + sweep * i / (n - 1)))
            for i in range(n)]
    plan[0], plan[-1] = (x0, y0), (x2, y2)
    if len(start) < 3 or len(end) < 3:
        return plan
    zm = mid[2] if len(mid) == 3 else (start[2] + end[2]) / 2
    half = abs(sweep) / 2
    out = []
    for i, (x, y) in enumerate(plan):
        t = abs(sweep) * i / (n - 1)
        z = (start[2] + (zm - start[2]) * t / half) if t <= half else (zm + (end[2] - zm) * (t - half) / half)
        out.append((x, y, z))
    return out


def _with_elevation_points(pts, node, label, anchor=None, tolerance=0.05):
    """Civil 3D feature lines keep their levels between PIs as elevation points:
    ``<Location desc="FeatureLine Elevation Point">`` children of the PlanFeature. Each is inserted into
    the polyline on the segment it lies on (within ``tolerance`` in plan), so the line keeps those
    levels instead of a straight grade between PIs. Points off the line are skipped and counted.
    With elevation points present, the levels of the points sampled along curves and spirals (``anchor``
    False) are graded along the line between their neighbouring PIs and elevation points, as Civil 3D
    grades them, instead of from the curve's own end levels."""
    locs = []
    for loc in node:
        if local_name(loc.tag) != "Location":
            continue
        if "elevation" not in (loc.attrib.get("desc") or "").lower():
            continue
        try:
            p = _coords(loc, label)
        except ValueError:
            continue
        if len(p) == 3:
            locs.append(p)
    if not locs or len(pts[0]) < 3:
        return pts, 0
    inserts, skipped = {}, 0
    for p in locs:
        best = None
        for i, (a, b) in enumerate(zip(pts, pts[1:])):
            dx, dy = b[0] - a[0], b[1] - a[1]
            l2 = dx * dx + dy * dy
            t = 0.0 if l2 < 1e-12 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
            d = math.hypot(a[0] + dx * t - p[0], a[1] + dy * t - p[1])
            if best is None or d < best[0]:
                best = (d, i, t)
        if best[0] > tolerance or best[2] <= 1e-9 or best[2] >= 1 - 1e-9:
            skipped += best[0] > tolerance
            continue
        inserts.setdefault(best[1], []).append((best[2], p))
    out, keep = [], []
    anchor = anchor or [True] * len(pts)
    for i, a in enumerate(pts):
        out.append(a); keep.append(anchor[i])
        for _, p in sorted(inserts.get(i, ())):
            out.append(tuple(p)); keep.append(True)
    if inserts and not all(keep):
        d = [0.0]
        for a, b in zip(out, out[1:]):
            d.append(d[-1] + math.dist(a[:2], b[:2]))
        ids = [i for i, k in enumerate(keep) if k]
        for a, b in zip(ids, ids[1:]):
            for i in range(a + 1, b):
                t = 0.0 if d[b] - d[a] < 1e-9 else (d[i] - d[a]) / (d[b] - d[a])
                out[i] = (out[i][0], out[i][1], out[a][2] + (out[b][2] - out[a][2]) * t)
    return out, skipped


def _coord_geom_points(node, label, segment_length=5.0):
    """A PlanFeature's CoordGeom (Line, Curve, Spiral, IrregularLine) as one polyline in stored order.
    Curves with ``rot`` and spirals are sampled as the alignment reader samples them, elevations
    interpolated along their length between the Start and End elevations. A Curve without ``rot`` is
    Civil 3D's feature-line form, three points on the arc (see ``_three_point_arc``)."""
    geom = child(node, "CoordGeom")
    if geom is None:
        raise ValueError(f"{label} has no CoordGeom")
    pts, anchor = [], []
    for item in geom:
        kind = local_name(item.tag)
        sampled = kind in ("Curve", "Spiral")
        if kind == "Line":
            seg = [_coords(child(item, "Start"), label), _coords(child(item, "End"), label)]
        elif kind == "IrregularLine":
            seg = list(_points(item, label))
        elif kind == "Curve" and not item.attrib.get("rot"):
            seg = _three_point_arc(_coords(child(item, "Start"), label), _coords(child(item, "Center"), label),
                                   _coords(child(item, "End"), label), segment_length, label)
        elif kind in ("Curve", "Spiral"):
            start = _coords(child(item, "Start"), label)
            end = _coords(child(item, "End"), label)
            plan = (_curve_points(item, segment_length) if kind == "Curve"
                    else _spiral_points(item, segment_length))
            seg = _with_z(plan, start[2] if len(start) == 3 else None, end[2] if len(end) == 3 else None)
        else:
            continue
        flags = [not sampled or k in (0, len(seg) - 1) for k in range(len(seg))]
        if pts and seg and math.dist(pts[-1][:2], seg[0][:2]) < 1e-6:
            seg, flags = seg[1:], flags[1:]
        pts.extend(seg)
        anchor.extend(flags)
    if len(pts) < 2:
        raise ValueError(f"{label} requires at least two points")
    dimension = min(len(p) for p in pts)
    pts = [tuple(p[:dimension]) for p in pts]
    pts, _ = _with_elevation_points(pts, node, label, anchor)
    return tuple(pts)


def read_line_features(root) -> tuple[list[LineFeature], list[str]]:
    """Read standard Breakline/FeatureLine records, retaining source metadata.

    PlanFeatures (how Civil 3D exports feature lines, and imports them back when they carry
    ``Feature code="FeatureLine"``; 3D polylines otherwise) are read as FeatureLine records from
    their CoordGeom, with ``landxml_element=PlanFeature`` and their Feature properties (site ...)."""
    records = []
    warnings = []
    scopes = [
        (surface.attrib.get("name"), surface)
        for surface in descendants(root, "Surface")
    ]
    scopes.append((None, root))
    seen = set()
    for surface_name, scope in scopes:
        for node in scope.iter():
            kind = local_name(node.tag)
            if kind not in {"Breakline", "FeatureLine", "PlanFeature"} or id(node) in seen:
                continue
            seen.add(id(node))
            label = f"{kind} in surface '{surface_name or 'unassigned'}'"
            if kind == "PlanFeature":
                label = f"PlanFeature '{node.attrib.get('name') or 'unnamed'}'"
            try:
                points = (_coord_geom_points(node, label) if kind == "PlanFeature"
                          else _points(node, label))
            except ValueError as exc:
                warnings.append(str(exc))
                continue
            feature = child(node, "Feature")
            properties = {}
            if feature is not None:
                for prop in descendants(feature, "Property"):
                    key = prop.attrib.get("label")
                    if (
                        key
                        and key not in properties
                        and prop.attrib.get("value") is not None
                    ):
                        properties[key] = prop.attrib["value"]
            if kind == "PlanFeature":
                properties.setdefault("landxml_element", "PlanFeature")
            records.append(
                LineFeature(
                    kind="FeatureLine" if kind == "PlanFeature" else kind,
                    name=node.attrib.get("name"),
                    description=node.attrib.get("desc"),
                    feature_code=feature.attrib.get("code")
                    if feature is not None
                    else None,
                    breakline_type=node.attrib.get("brkType"),
                    surface_name=surface_name,
                    properties=properties,
                    points=points,
                )
            )
    return records, warnings
