"""Shared LandXML document index and engineering metadata inspection."""

from __future__ import annotations

from dataclasses import dataclass
import math
import os

from .. import safe_xml
from .common import child, descendants, first_descendant, local_name
from .detection import detect_vendor
from . import tin_stream


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
        # Some writers declare the vertical unit as elevationUnit; verticalLinearUnit wins when both are present.
        found.add(
            (item.attrib.get("linearUnit"),
             item.attrib.get("verticalLinearUnit") or item.attrib.get("elevationUnit"))
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


def _bounds(root, path=None):
    xs, ys, zs = [], [], []
    if path:                                   # TIN points kept out of the light tree
        for pnts in descendants(root, "Pnts"):
            span = tin_stream.span_of(pnts)
            extent = tin_stream.points_extent(path, span) if span else None
            if extent:
                xs += [extent[0], extent[3]]
                ys += [extent[1], extent[4]]
                zs += [extent[2], extent[5]]
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
    skeleton: bool = False       # TIN point/face contents are read from disk on demand

    def surfaces(self):
        return list(descendants(self.root, "Surface"))

    def tin_spans(self, surface):
        """Byte spans (points, faces) of a surface read from disk, or None when held in the tree."""
        pnts = first_descendant(surface, "Pnts")
        faces = first_descendant(surface, "Faces")
        a, b = tin_stream.span_of(pnts), tin_stream.span_of(faces)
        return (a, b) if a and b else None

    def _count(self, element, tag):
        if element is None:
            return 0
        span = tin_stream.span_of(element)
        if span:
            return tin_stream.count_elements(self.path, span)
        return len(list(descendants(element, tag)))

    def alignments(self):
        return list(descendants(self.root, "Alignment"))

    def station_maps(self):
        """Validate equations against sampled geometry and retain internal source stations."""
        if not hasattr(self, "_station_maps"):
            maps = {}
            if any(True for _ in descendants(self.root, "StaEquation")):
                from .geometry import read_alignments

                records, warnings = read_alignments(self.path)
                maps = {r["name"]: r["station_map"] for r in records if r.get("station_map")}
                expected = [a for a in self.alignments() if any(True for _ in descendants(a, "StaEquation"))]
                names = [a.attrib.get("name") for a in expected]
                all_names = [a.attrib.get("name") for a in self.alignments()]
                if any(all_names.count(name) != 1 or name not in maps for name in names):
                    raise ValueError("Station equations require unique named, supported connected alignments. " + "; ".join(warnings))
            self._station_maps = maps
        return self._station_maps

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
        equation_error = None
        try:
            maps = self.station_maps()
        except ValueError as exc:
            maps = {}
            equation_error = str(exc)
        bounds, elevations = _bounds(self.root, self.path if self.skeleton else None)
        surfaces = []
        for surface in self.surfaces():
            points = first_descendant(surface, "Pnts")
            faces = first_descendant(surface, "Faces")
            surfaces.append(
                {
                    "name": surface.attrib.get("name"),
                    "description": surface.attrib.get("desc"),
                    "points": self._count(points, "P"),
                    "triangles": self._count(faces, "F"),
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
                    "station_equations": maps[alignment.attrib.get("name")].equations if alignment.attrib.get("name") in maps else [],
                    "profiles": [
                        item.attrib.get("name")
                        for item in descendants(alignment, "ProfAlign")
                    ],
                    "surface_profiles": [
                        item.attrib.get("name")
                        for item in descendants(alignment, "ProfSurf")
                    ],
                    "cross_sections": len(list(descendants(alignment, "CrossSect"))),
                }
            )
        surface_volumes = []
        volume_units = {declaration.get("volumeUnit") for units in descendants(self.root, "Units") for declaration in units}
        for item in descendants(self.root, "SurfVolume"):
            record = {"name": item.attrib.get("name"),
                      "volume_unit": next(iter(volume_units)) if len(volume_units) == 1 else None}
            for key, label in (
                ("surfBase", "base_surface"),
                ("surfCompare", "compare_surface"),
            ):
                record[label] = item.attrib.get(key)
            for key, label in (
                ("volCut", "cut_volume"),
                ("volFill", "fill_volume"),
                ("volTotal", "net_volume"),
            ):
                try:
                    record[label] = float(item.attrib[key])
                except (KeyError, ValueError):
                    record[label] = None
            surface_volumes.append(record)
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
            "surface_profiles": len(list(descendants(self.root, "ProfSurf"))),
            "surface_volumes": surface_volumes,
            "breaklines": len(list(descendants(self.root, "Breakline"))),
            "feature_lines": len(list(descendants(self.root, "FeatureLine")))
            + len(list(descendants(self.root, "PlanFeature"))),
            "bounds": bounds,
            "elevation_range": elevations,
            "station_equation_error": equation_error,
            "warnings": self.warnings + ([f"Invalid station equations: {equation_error} "
                                          "Station-dependent outputs are unavailable for this file."]
                                         if equation_error else []),
        }


def _parse_light(path):
    """Parse everything except the contents of TIN <Pnts>/<Faces> blocks (None if not possible)."""
    reader = tin_stream.open_skeleton(path)
    if reader is None:
        return None
    try:
        return safe_xml.parse(reader).getroot()
    except safe_xml.ParseError:
        return None                                  # the full parse will report the real error
    finally:
        reader.close()


def load_document(path: str, full: bool = False) -> LandXMLDocument:
    """Parse a LandXML file safely and expose only source-backed metadata.

    By default the contents of TIN point and face blocks are not loaded into the tree
    (they can be gigabytes); ``read_tin`` streams them from disk. Pass ``full=True``
    when every ``<P>`` and ``<F>`` node is needed in the tree.
    """
    try:
        root = None if full else _parse_light(path)
        skeleton = root is not None
        if root is None:
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
        skeleton,
    )
