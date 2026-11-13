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


def _coord_geom_points(node, label, segment_length=5.0):
    """A PlanFeature's CoordGeom (Line, Curve, Spiral, IrregularLine) as one polyline in stored order.
    Curves and spirals are sampled as the alignment reader samples them; elevations on them are
    interpolated along their length between the Start and End elevations."""
    geom = child(node, "CoordGeom")
    if geom is None:
        raise ValueError(f"{label} has no CoordGeom")
    pts = []
    for item in geom:
        kind = local_name(item.tag)
        if kind == "Line":
            seg = [_coords(child(item, "Start"), label), _coords(child(item, "End"), label)]
        elif kind == "IrregularLine":
            seg = list(_points(item, label))
        elif kind in ("Curve", "Spiral"):
            start = _coords(child(item, "Start"), label)
            end = _coords(child(item, "End"), label)
            plan = (_curve_points(item, segment_length) if kind == "Curve"
                    else _spiral_points(item, segment_length))
            seg = _with_z(plan, start[2] if len(start) == 3 else None, end[2] if len(end) == 3 else None)
        else:
            continue
        if pts and seg and math.dist(pts[-1][:2], seg[0][:2]) < 1e-6:
            seg = seg[1:]
        pts.extend(seg)
    if len(pts) < 2:
        raise ValueError(f"{label} requires at least two points")
    dimension = min(len(p) for p in pts)
    return tuple(tuple(p[:dimension]) for p in pts)


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
