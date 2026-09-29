"""Source-backed line features shared by all QGIS outputs."""

from __future__ import annotations

from dataclasses import dataclass
import math

from .common import child, descendants, first_descendant, local_name


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


def read_line_features(root) -> tuple[list[LineFeature], list[str]]:
    """Read standard Breakline/FeatureLine records, retaining source metadata."""
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
            if kind not in {"Breakline", "FeatureLine"} or id(node) in seen:
                continue
            seen.add(id(node))
            label = f"{kind} in surface '{surface_name or 'unassigned'}'"
            try:
                points = _points(node, label)
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
            records.append(
                LineFeature(
                    kind=kind,
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
