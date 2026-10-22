"""Descriptive, filesystem-safe names for automatically exported terrains."""

import os
import re

from .catalog import DATUM_HINTS, _tokens
from .common import local_name


def _filename_part(value):
    return re.sub(r"[^\w.-]+", "_", value.strip()).strip("._")[:80].rstrip("._")


def _prepared_metadata(surface):
    for feature in surface:
        if local_name(feature.tag) == "Feature" and feature.get("name") == "LandXMLTools.Prepare":
            return {item.get("label"): item.get("value", "") for item in feature
                    if local_name(item.tag) == "Property"}
    return {}


def _surface_role(surface, name, metadata):
    source = os.path.splitext(os.path.basename(metadata.get("source", "")))[0]
    tokens = _tokens(" ".join((name, surface.get("desc", ""), source)))
    existing = bool(tokens & {"existing", "eg", "egl", "ogl", "natural", "survey"})
    finished = bool(tokens & {"finished", "fg", "frl"})
    datum = bool(tokens & set(DATUM_HINTS))
    proposed = bool(tokens & {"proposed", "design", "corridor"})
    state = metadata.get("role", surface.get("state", "")).casefold()
    existing |= state == "existing"
    proposed |= state == "proposed"
    if existing and (finished or datum or proposed):
        return ""
    if datum:
        return "Datum"
    if finished:
        return "Finished_Grade"
    if existing:
        return "Existing_Ground"
    if proposed:
        return "Proposed" if state == "proposed" or "proposed" in tokens else "Design"
    return ""


def surface_output_names(path, surfaces):
    """Return unique stems in source order, before any surface selection/filtering."""
    base = _filename_part(os.path.splitext(os.path.basename(path))[0]) or "LandXML"
    names, used = [], set()
    for index, surface in enumerate(surfaces, 1):
        metadata = _prepared_metadata(surface)
        original_name = metadata.get("surface", "").strip() or surface.get("name", "")
        name = _filename_part(original_name) or f"surface_{index:02d}"
        role = _surface_role(surface, original_name, metadata)
        if set(role.casefold().split("_")).issubset(_tokens(name)):
            role = ""
        stem = "_".join(part for part in (base, role, name) if part)
        unique, count = stem, 2
        while unique.casefold() in used:
            unique = f"{stem}_{count}"
            count += 1
        used.add(unique.casefold())
        names.append(unique)
    return names
