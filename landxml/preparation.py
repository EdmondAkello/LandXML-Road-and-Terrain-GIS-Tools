"""Combine matching LandXML exports and fill missing terrain profiles/sections."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import math
import os
import tempfile
import xml.etree.ElementTree as ET  # nosec B405 - builds nodes; parsing uses hardened safe_xml

from qgis.core import QgsCoordinateReferenceSystem

from .. import safe_xml
from ..core import read_tin
from .catalog import match_name
from .common import child, descendants, local_name
from .corridor import guess_surfaces, read_corridor_sections, roadway_surface_refs
from .geometry import read_alignments
from .longsection import read_alignment_profiles
from .parser import load_document
from .stationing import StationedPolyline
from .tin_sampling import TinSampler, regular_stations, sample_profile

MARKER = "LandXMLTools.Prepare"
UNITS = {"meter": "meter", "metre": "meter", "foot": "foot", "internationalfoot": "foot", "ussurveyfoot": "usSurveyFoot"}


def read_tree(path):
    return safe_xml.parse(path, builder=ET.TreeBuilder(insert_comments=True, insert_pis=True)).getroot()


def metadata(node):
    for feature in node:
        if local_name(feature.tag) == "Feature" and feature.get("name") == MARKER:
            return {item.get("label"): item.get("value") for item in feature if local_name(item.tag) == "Property"}
    return {}


def add_node(parent, tag, attributes=None, text=None):
    namespace = parent.tag.split("}")[0] + "}" if parent.tag.startswith("{") else ""
    node = ET.SubElement(parent, namespace + tag, attributes or {})
    node.text = text
    if tag != "Feature":
        first_feature = next((i for i, item in enumerate(parent) if local_name(item.tag) == "Feature"), None)
        if first_feature is not None:
            parent.remove(node)
            parent.insert(first_feature, node)
    return node


def mark(node, **values):
    feature = add_node(node, "Feature", {"name": MARKER})
    for key, value in values.items():
        add_node(feature, "Property", {"label": key, "value": str(value)})


def _coordinate_metadata(root):
    cs = child(root, "CoordinateSystem")
    if cs is None:
        return None, None, None
    code = cs.get("epsgCode")
    name = cs.get("name")
    crs = QgsCoordinateReferenceSystem(f"EPSG:{code}" if code else (name or ""))
    identifier = crs.authid() if crs.isValid() and crs.authid() else name or cs.get("desc")
    datum = cs.get("horizontalDatum")
    vertical = cs.get("verticalDatum") or cs.get("verticalDatumName")
    return identifier, datum, vertical


def validate_metadata(documents, horizontal, vertical, confirmed):
    if any("conflicting unit declarations" in warning.lower() for doc in documents for warning in doc.warnings):
        raise ValueError("An input has conflicting unit declarations; correct the source export first.")
    def resolve(attribute, explicit):
        declared = [getattr(doc, attribute) for doc in documents]
        normalized = [UNITS.get(value.lower()) if value else None for value in declared]
        if any(value and norm is None for value, norm in zip(declared, normalized)):
            raise ValueError(f"Unsupported {attribute.replace('_', ' ')}; use metres or declared feet.")
        chosen = explicit or next((value for value in normalized if value), None)
        if chosen is None or (not explicit and any(value is None for value in normalized)):
            raise ValueError(f"Select explicit {attribute.replace('_', ' ')} because an input omits it.")
        if any(value is not None and value != chosen for value in normalized):
            raise ValueError(f"Inputs have conflicting {attribute.replace('_', ' ')} declarations; no conversion is performed.")
        return chosen, any(value is None for value in normalized)

    horizontal, missing_h = resolve("horizontal_unit", horizontal)
    vertical, missing_v = resolve("vertical_unit", vertical)
    if horizontal != vertical:
        raise ValueError("Horizontal and vertical units must match for terrain sampling.")
    coordinate_values = [_coordinate_metadata(doc.root) for doc in documents]
    for index, label in enumerate(("coordinate system", "horizontal datum", "vertical datum")):
        known = {values[index].strip().casefold() for values in coordinate_values if values[index]}
        if len(known) > 1:
            raise ValueError(f"Inputs declare conflicting {label}s; align the exports before preparing them.")
    missing_crs = any(not values[0] or not values[2] for values in coordinate_values)
    if (missing_h or missing_v or missing_crs) and not confirmed:
        raise ValueError("Metadata is incomplete. Confirm that all inputs share coordinates, units and vertical datum.")
    return horizontal, vertical


def _write_units(root, horizontal, vertical):
    units = child(root, "Units")
    if units is None:
        units = add_node(root, "Units")
        root.remove(units)
        root.insert(0, units)
    declaration = next((node for node in units if local_name(node.tag) in ("Metric", "Imperial")), None)
    if declaration is None:
        metric = horizontal == "meter"
        declaration = add_node(units, "Metric" if metric else "Imperial", {
            "areaUnit": "squareMeter" if metric else "squareFoot",
            "volumeUnit": "cubicMeter" if metric else "cubicFeet",
        })
    if not declaration.get("linearUnit"):
        declaration.set("linearUnit", horizontal)
    if not declaration.get("verticalLinearUnit"):
        declaration.set("verticalLinearUnit", vertical)


def _pairs(points):
    return " ".join(f"{distance:.17g} {elevation:.17g}" for distance, elevation in points)


def _profile_for_role(profiles, role, surface_name):
    candidates = [p for p in profiles if p.kind == "surface"]
    exact = [p for p in candidates if surface_name in p.name or metadata(p.element).get("role") == role]
    if exact:
        return exact
    state = "existing" if role == "existing" else "proposed"
    return [p for p in candidates if (p.state or "").lower() == state]


def prepare_landxml(path, output, terrains, alignment, profile_interval, section_interval,
                    left, right, horizontal, vertical, confirmed, feedback):
    """``terrains`` maps role to (file, surface); blank file uses the primary file."""
    if not output:
        raise ValueError("Choose a prepared LandXML output file.")
    for value in (profile_interval, section_interval, left, right):
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Sampling intervals and section widths must be finite and greater than zero.")
    input_paths = [path] + [source for source, _name in terrains.values() if source]
    for source in input_paths:
        if os.path.normcase(os.path.realpath(source)) == os.path.normcase(os.path.realpath(output)) or (
            os.path.exists(output) and os.path.samefile(source, output)
        ):
            raise ValueError("The output must be a new path, different from every input file.")
    docs = {os.path.realpath(source): load_document(source) for source in input_paths}
    doc = docs[os.path.realpath(path)]
    horizontal, vertical = validate_metadata(list(docs.values()), horizontal, vertical, confirmed)
    root = read_tree(path)
    _write_units(root, horizontal, vertical)
    records, unsupported = read_alignments(path, min(profile_interval, 2.0), cancel=feedback.isCanceled)
    for message in unsupported:
        feedback.pushWarning(f"Unsupported alignment skipped: {message}")
    names = [record["name"] for record in records]
    if len(names) != len(set(names)):
        raise ValueError("Alignment names are duplicated; give alignments unique names before preparing them.")
    selected = match_name(names, alignment, "alignment") if alignment else None
    records = [record for record in records if not selected or record["name"] == selected]
    if not records:
        raise ValueError("No supported alignments were found.")
    stats = {"ALIGNMENT_COUNT": len(records), "GENERATED_PROFILES": 0, "GENERATED_SECTIONS": 0,
             "PRESERVED_PROFILES": sum(1 for n in root.iter() if local_name(n.tag) in ("ProfAlign", "ProfSurf")),
             "PRESERVED_SECTIONS": sum(1 for n in root.iter() if local_name(n.tag) in ("CrossSectSurf", "DesignCrossSectSurf")),
             "COVERAGE_GAPS": 0}
    sources = []
    primary_surfaces = list(descendants(root, "Surface"))
    for role, (source, requested) in terrains.items():
        if not source and not requested:
            continue
        source = source or path
        terrain_doc = docs[os.path.realpath(source)]
        surfaces = terrain_doc.surfaces()
        candidates = [node.get("name", f"Surface {i + 1}") for i, node in enumerate(surfaces)]
        if len(candidates) != len(set(candidates)):
            raise ValueError(f"{role.capitalize()} terrain contains duplicate surface names.")
        if not requested and len(candidates) != 1:
            raise ValueError(f"Select the {role} surface; available surfaces: {', '.join(candidates) or 'none'}.")
        name = match_name(candidates, requested, f"{role} surface") if requested else candidates[0]
        vertices, faces, info = read_tin(source, name, cancel=feedback.isCanceled)
        digestor = hashlib.sha256()
        digestor.update(vertices)
        digestor.update(faces)
        digest = digestor.hexdigest()
        same_file = os.path.samefile(source, path)
        target_name = name if same_file else ("Prepared Existing Ground" if role == "existing" else "Prepared Proposed Terrain")
        collisions = [node for node in primary_surfaces if node.get("name") == target_name]
        if len(collisions) > 1:
            raise ValueError(f"Surface name '{target_name}' is ambiguous.")
        if not same_file:
            if collisions:
                if metadata(collisions[0]).get("digest") != digest or metadata(collisions[0]).get("role") != role:
                    raise ValueError(f"Surface '{target_name}' already exists with different data; choose an unprepared primary file.")
            else:
                external_root = read_tree(source)
                copied = deepcopy(list(descendants(external_root, "Surface"))[candidates.index(name)])
                source_ns = copied.tag.rsplit("}", 1)[0] + "}" if copied.tag.startswith("{") else ""
                target_ns = root.tag.rsplit("}", 1)[0] + "}" if root.tag.startswith("{") else ""
                for node in copied.iter():
                    if not isinstance(node.tag, str):
                        continue
                    if (source_ns and node.tag.startswith(source_ns)) or (not source_ns and not node.tag.startswith("{")):
                        node.tag = target_ns + local_name(node.tag)
                    for key, value in list(node.attrib.items()):
                        if local_name(key) in ("surfaceRef", "surfaceRefs", "surfBase", "surfCompare") and value == name:
                            node.set(key, target_name)
                copied.set("name", target_name)
                mark(copied, role=role, digest=digest, source=os.path.basename(source), surface=name)
                container = child(root, "Surfaces")
                if container is None:
                    container = add_node(root, "Surfaces")
                container.append(copied)
                primary_surfaces.append(copied)
        if info["invisible_face_count"]:
            feedback.pushInfo(f"{name}: excluded {info['invisible_face_count']:,} hidden triangles.")
        sources.append((role, target_name, name, digest, TinSampler(vertices, faces, feedback)))
    if len(sources) == 2 and sources[0][1] == sources[1][1]:
        raise ValueError("Existing and proposed terrain must be different surfaces.")
    usable = 0
    attempted = 0
    reused = 0
    elements = {node.get("name"): node for node in descendants(root, "Alignment")}
    for index, record in enumerate(records):
        if feedback.isCanceled():
            raise RuntimeError("LandXML preparation cancelled.")
        element = elements[record["name"]]
        profiles = read_alignment_profiles(element, profile_interval)
        existing_sections = list(descendants(element, "CrossSect"))
        if not sources:
            if not (profiles and existing_sections):
                raise ValueError(f"{record['name']}: select an existing or proposed terrain to generate missing data.")
            usable += 1
            continue
        polyline = StationedPolyline.from_alignment(record)
        if polyline is None:
            feedback.pushWarning(f"{record['name']}: no start station; sampling skipped.")
            continue
        if not all(math.isfinite(s) for s in polyline.stations):
            raise ValueError(f"{record['name']}: alignment stations must be finite.")
        section_index = {}
        for section in existing_sections:
            try:
                station = float(section.get("sta", "nan"))
            except ValueError:
                feedback.pushWarning(f"{record['name']}: a section has an invalid station; left unchanged.")
                continue
            if math.isfinite(station):
                section_index.setdefault(round(station, 7), []).append(section)
        wrapper = ET.Element("Alignments")
        wrapper.append(element)
        known_sections, *_ = read_corridor_sections(wrapper)
        existing_name, proposed_name = guess_surfaces(known_sections, [p for p in profiles if p.kind == "surface"], roadway_surface_refs(root))
        usable += bool(profiles or existing_sections)
        for role, name, original_name, digest, sampler in sources:
            feedback.setProgressText(f"Sampling {record['name']}: {name}")
            role_profiles = _profile_for_role(profiles, role, name)
            for profile in role_profiles:
                previous = metadata(profile.element)
                if previous.get("role") == role and previous.get("digest") != digest:
                    raise ValueError(f"Generated profile '{profile.name}' belongs to different terrain data.")
                reused += bool(previous.get("profileGroup"))
            if not role_profiles:
                attempted += 1
                runs = sample_profile(sampler, polyline, profile_interval)
                if runs:
                    profile = child(element, "Profile")
                    if profile is None:
                        profile = add_node(element, "Profile", {"name": "Prepared terrain profiles"})
                    for run in runs:
                        node = add_node(profile, "ProfSurf", {"name": name, "state": "existing" if role == "existing" else "proposed",
                                                               "desc": "Sampled from original TIN triangles by LandXML Tools"})
                        add_node(node, "PntList2D", text=_pairs(run))
                        mark(node, role=role, profileGroup=f"{record['name']}:{role}:{name}", digest=digest)
                    stats["GENERATED_PROFILES"] += 1
                    usable += 1
                    covered = sum(run[-1][0] - run[0][0] for run in runs)
                    if covered < polyline.station_end - polyline.station_start - 1e-7:
                        stats["COVERAGE_GAPS"] += 1
                        feedback.pushWarning(f"{record['name']}, {name}: profile has incomplete terrain coverage; gaps remain disconnected.")
                else:
                    stats["COVERAGE_GAPS"] += 1
                    feedback.pushWarning(f"{record['name']}, {name}: terrain does not cover the alignment profile.")
            aliases = {name, original_name} | {p.name for p in role_profiles}
            inferred_name = existing_name if role == "existing" else proposed_name
            if inferred_name:
                aliases.add(inferred_name)
            for station in regular_stations(polyline.station_start, polyline.station_end, section_interval):
                sampler.check_cancel()
                matches = section_index.get(round(station, 7), [])
                preserved = [node for section in matches for node in section
                             if local_name(node.tag) == "CrossSectSurf" and node.get("name") in aliases]
                if preserved:
                    for node in preserved:
                        previous = metadata(node)
                        if previous.get("role") == role and previous.get("digest") != digest:
                            raise ValueError(f"Generated section '{name}' belongs to different terrain data.")
                        reused += bool(previous.get("digest"))
                    continue
                attempted += 1
                runs = sampler.segment(polyline.point_at(station, -left), polyline.point_at(station, right), -left, right)
                section = matches[0] if matches else None
                if section is None:
                    container = child(element, "CrossSects")
                    if container is None:
                        container = add_node(element, "CrossSects")
                    section = add_node(container, "CrossSect", {"sta": f"{station:.17g}", "name": f"Prepared {station:g}"})
                    section_index[round(station, 7)] = [section]
                if len(runs) != 1:
                    stats["COVERAGE_GAPS"] += 1
                    feedback.pushWarning(f"{record['name']}, {name}, station {station:g}: section omitted (missing or disconnected coverage).")
                    if not metadata(section):
                        mark(section, coverageGap="true")
                    continue
                if runs[0][0][0] > -left + 1e-7 or runs[0][-1][0] < right - 1e-7:
                    stats["COVERAGE_GAPS"] += 1
                    feedback.pushWarning(f"{record['name']}, {name}, station {station:g}: section clipped to terrain coverage.")
                node = add_node(section, "CrossSectSurf", {"name": name})
                add_node(node, "PntList2D", text=_pairs(runs[0]))
                mark(node, role=role, digest=digest)
                stats["GENERATED_SECTIONS"] += 1
                usable += 1
        feedback.setProgress(90 * (index + 1) / len(records))
    if not usable or (attempted and not reused and not stats["GENERATED_PROFILES"] and not stats["GENERATED_SECTIONS"]):
        raise ValueError("Requested sampling produced no usable data; check terrain coverage and input coordinates.")
    temporary = None
    try:
        if root.tag.startswith("{"):
            ET.register_namespace("", root.tag[1:].split("}")[0])
        with tempfile.NamedTemporaryFile(mode="wb", dir=os.path.dirname(os.path.abspath(output)), suffix=".xml", delete=False) as stream:
            temporary = stream.name
            ET.ElementTree(root).write(stream, encoding="utf-8", xml_declaration=True)
        load_document(temporary)
        if feedback.isCanceled():
            raise RuntimeError("LandXML preparation cancelled.")
        os.replace(temporary, output)
        temporary = None
    finally:
        if temporary is not None:
            os.unlink(temporary)
    for key, value in stats.items():
        feedback.pushInfo(f"{key.replace('_', ' ').lower()}: {value}")
    feedback.setProgress(100)
    return {"OUTPUT": output, **stats}
