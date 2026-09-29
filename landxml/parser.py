"""Shared LandXML document index and engineering metadata inspection."""

from __future__ import annotations

from dataclasses import dataclass
import math
import os

from .. import safe_xml
from .common import child, descendants, first_descendant, local_name
from .detection import detect_vendor


def _units(root):
    units = child(root, "Units")
    if units is None:
        return None, None, ["LandXML contains no Units declaration."]
    unit_nodes = [
        item for item in units if local_name(item.tag) in {"Imperial", "Metric"}
    ]
    if not unit_nodes:
        return None, None, ["LandXML Units has no Imperial or Metric declaration."]
    found = set()
    for item in unit_nodes:
        found.add(
            (item.attrib.get("linearUnit"), item.attrib.get("verticalLinearUnit"))
        )
    if len(found) > 1:
        return None, None, ["LandXML has conflicting unit declarations."]
    horizontal, vertical = found.pop()
    warnings = []
    if not horizontal:
        warnings.append("Horizontal unit is not declared.")
    if not vertical:
        warnings.append(
            "Vertical unit is not declared; it has not been inferred from the horizontal unit."
        )
    return horizontal, vertical, warnings


def _crs(root):
    element = child(root, "CoordinateSystem")
    if element is None:
        return None
    for key in (
        "epsgCode",
        "name",
        "desc",
        "horizontalDatum",
        "horizontalCoordinateSystemName",
    ):
        if element.attrib.get(key):
            return f"{key}={element.attrib[key]}"
    return "CoordinateSystem present without a recognized identifier"


def _bounds(root):
    xs, ys, zs = [], [], []
    for item in descendants(root, "P"):
        parts = (item.text or "").split()
        if len(parts) >= 3:
            try:
                x, y, z = map(float, parts[:3])
            except ValueError:
                continue
            if all(math.isfinite(v) for v in (x, y, z)):
                xs.append(x)
                ys.append(y)
                zs.append(z)
    for item in descendants(root, "PntList3D"):
        values = (item.text or "").split()
        if len(values) % 3:
            continue
        for index in range(0, len(values), 3):
            try:
                x, y, z = map(float, values[index : index + 3])
            except ValueError:
                continue
            if all(math.isfinite(value) for value in (x, y, z)):
                xs.append(x)
                ys.append(y)
                zs.append(z)
    for tag in ("Start", "End", "PI", "Center"):
        for item in descendants(root, tag):
            parts = (item.text or "").split()
            if len(parts) >= 2:
                try:
                    x, y = map(float, parts[:2])
                except ValueError:
                    continue
                if math.isfinite(x) and math.isfinite(y):
                    xs.append(x)
                    ys.append(y)
    return (
        (min(xs), min(ys), max(xs), max(ys)) if xs else None,
        (min(zs), max(zs)) if zs else None,
    )


@dataclass
class LandXMLDocument:
    """Indexed LandXML source; coordinate values remain exactly as stored."""

    path: str
    root: object
    vendor: str
    vendor_evidence: str | None
    version: str | None
    horizontal_unit: str | None
    vertical_unit: str | None
    declared_crs: str | None
    warnings: list[str]

    def surfaces(self):
        return list(descendants(self.root, "Surface"))

    def alignments(self):
        return list(descendants(self.root, "Alignment"))

    def profiles(self):
        """Yield (alignment name, profile) using nesting, then explicit references."""
        seen = set()
        for alignment in self.alignments():
            for profile in descendants(alignment, "ProfAlign"):
                seen.add(id(profile))
                yield alignment.attrib.get("name"), profile
        for profile in descendants(self.root, "ProfAlign"):
            if id(profile) not in seen:
                yield profile.attrib.get("alignment"), profile

    def inspect(self) -> dict:
        """Report source entities, units and raw bounds without changing coordinates."""
        bounds, elevations = _bounds(self.root)
        surfaces = []
        for surface in self.surfaces():
            points = first_descendant(surface, "Pnts")
            faces = first_descendant(surface, "Faces")
            surfaces.append(
                {
                    "name": surface.attrib.get("name"),
                    "description": surface.attrib.get("desc"),
                    "points": len(list(descendants(points, "P")))
                    if points is not None
                    else 0,
                    "triangles": len(list(descendants(faces, "F")))
                    if faces is not None
                    else 0,
                    "breaklines": len(list(descendants(surface, "Breakline"))),
                    "feature_lines": len(list(descendants(surface, "FeatureLine"))),
                    "explicit_boundaries": len(list(descendants(surface, "Boundary"))),
                }
            )
        alignments = []
        for alignment in self.alignments():
            alignments.append(
                {
                    "name": alignment.attrib.get("name"),
                    "description": alignment.attrib.get("desc"),
                    "station_start": alignment.attrib.get("staStart"),
                    "profiles": [
                        item.attrib.get("name")
                        for item in descendants(alignment, "ProfAlign")
                    ],
                    "cross_sections": len(list(descendants(alignment, "CrossSect"))),
                }
            )
        return {
            "source_file": os.path.basename(self.path),
            "vendor": self.vendor,
            "vendor_evidence": self.vendor_evidence,
            "version": self.version,
            "horizontal_unit": self.horizontal_unit,
            "vertical_unit": self.vertical_unit,
            "declared_crs": self.declared_crs,
            "axis_order": "undetermined; coordinates retained in stored order",
            "surfaces": surfaces,
            "alignments": alignments,
            "profiles": len(list(descendants(self.root, "ProfAlign"))),
            "cross_sections": len(list(descendants(self.root, "CrossSect"))),
            "breaklines": len(list(descendants(self.root, "Breakline"))),
            "feature_lines": len(list(descendants(self.root, "FeatureLine"))),
            "bounds": bounds,
            "elevation_range": elevations,
            "warnings": self.warnings,
        }


def load_document(path: str) -> LandXMLDocument:
    """Parse a LandXML file safely and expose only source-backed metadata."""
    try:
        root = safe_xml.parse_root(path)
    except (OSError, safe_xml.ParseError, safe_xml.UnsafeXmlError) as exc:
        raise ValueError(f"Cannot parse LandXML file '{path}': {exc}") from exc
    if local_name(root.tag) != "LandXML":
        raise ValueError(
            f"Expected a LandXML root element in '{path}', found {local_name(root.tag)}"
        )
    vendor, evidence = detect_vendor(root)
    horizontal, vertical, warnings = _units(root)
    if vendor == "generic":
        warnings.append(
            "Source application is unidentified; generic LandXML rules will be used."
        )
    if _crs(root) is None:
        warnings.append(
            "No source CRS is declared. Select and verify the CRS before georeferenced output."
        )
    return LandXMLDocument(
        path,
        root,
        vendor,
        evidence,
        root.attrib.get("version"),
        horizontal,
        vertical,
        _crs(root),
        warnings,
    )
