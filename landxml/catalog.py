"""Fast name catalog of a LandXML file, and forgiving name resolution.

The catalog scans element start tags only, so even large exports list their
surfaces, alignments, profiles and section surfaces in well under a second. It feeds the Processing
dropdowns and the automatic surface choices.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import html
import mmap
import os
import re

EXISTING_HINTS = ("ogl", "o.g.l", "egl", "eg", "existing", "ground", "natural", "survey", "topo", "terrain", "dtm")
DATUM_HINTS = ("datum", "bottom", "formation", "subgrade")
DESIGN_HINTS = ("top", "frl", "finished", "design", "corridor", "proposed", "fg")


@dataclass
class Catalog:
    surfaces: list = field(default_factory=list)
    alignments: list = field(default_factory=list)
    design_profiles: dict = field(default_factory=dict)  # alignment -> [names]
    surface_profiles: dict = field(default_factory=dict)  # alignment -> [(name, state)]
    section_surfaces: dict = field(default_factory=dict)  # alignment -> [names]
    cross_sections: dict = field(default_factory=dict)  # alignment -> count
    roadway_surfaces: list = field(default_factory=list)
    surface_volumes: list = field(default_factory=list)  # (name, base, compare)

    def names(self, kind, alignment=None):
        def pick(mapping):
            if alignment:
                values = mapping.get(alignment, [])
            else:
                values = [item for items in mapping.values() for item in items]
            result = []
            for item in values:
                item = item[0] if isinstance(item, tuple) else item
                if item not in result:
                    result.append(item)
            return result

        if kind == "surfaces":
            return list(self.surfaces)
        if kind == "alignments":
            return list(self.alignments)
        if kind == "design_profiles":
            return pick(self.design_profiles)
        if kind == "surface_profiles":
            return pick(self.surface_profiles)
        if kind == "profiles":
            return pick(self.design_profiles) + [n for n in pick(self.surface_profiles) if n not in pick(self.design_profiles)]
        if kind == "section_surfaces":
            return pick(self.section_surfaces)
        raise ValueError(f"Unknown name kind '{kind}'")


_CACHE = {}
_TAG = re.compile(
    rb"<(/?)(?:[A-Za-z_][\w.-]*:)?(Surfaces|Surface|Alignment|ProfAlign|ProfSurf|CrossSectSurf|CrossSect|Roadway|SurfVolume)\b([^>]*)>"
)
_ATTRIBUTE = re.compile(rb"""([\w:.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")


def _attributes(raw):
    result = {}
    for match in _ATTRIBUTE.finditer(raw):
        value = match.group(2) if match.group(2) is not None else match.group(3)
        key = match.group(1).decode("utf-8", "replace").rsplit(":", 1)[-1]
        result[key] = html.unescape(value.decode("utf-8", "replace"))
    return result


def read_catalog(path):
    """Return a Catalog for ``path`` (cached by size and modification time).

    Only start/end tags of a few elements are scanned (memory-mapped, no
    entity expansion, no tree), so this is fast and safe for dropdown lists.
    Processing itself always reads the file through the hardened parser.
    """
    stat = os.stat(path)
    key = (os.path.abspath(path), stat.st_size, stat.st_mtime)
    if key in _CACHE:
        return _CACHE[key]
    catalog = Catalog()
    alignment = None
    in_surfaces = False
    with open(path, "rb") as stream:
        if stat.st_size == 0:
            raise ValueError(f"'{path}' is empty")
        with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
            if data.find(b"<LandXML", 0, 65536) < 0 and data.find(b":LandXML", 0, 65536) < 0:
                raise ValueError(f"'{path}' does not look like a LandXML file")
            for match in _TAG.finditer(data):
                closing, name, raw = match.group(1), match.group(2).decode(), match.group(3)
                if closing:
                    if name == "Alignment":
                        alignment = None
                    elif name == "Surfaces":
                        in_surfaces = False
                    continue
                attrib = _attributes(raw)
                self_closing = raw.rstrip().endswith(b"/")
                if name == "Surfaces":
                    in_surfaces = not self_closing
                elif name == "Surface" and in_surfaces:
                    catalog.surfaces.append(attrib.get("name") or f"Surface {len(catalog.surfaces) + 1}")
                elif name == "Alignment":
                    alignment = attrib.get("name") or f"Alignment {len(catalog.alignments) + 1}"
                    if alignment not in catalog.alignments:
                        catalog.alignments.append(alignment)
                    if self_closing:
                        alignment = None
                elif name == "ProfAlign":
                    catalog.design_profiles.setdefault(alignment, []).append(attrib.get("name") or "Design profile")
                elif name == "ProfSurf":
                    catalog.surface_profiles.setdefault(alignment, []).append(
                        (attrib.get("name") or "Surface profile", attrib.get("state"))
                    )
                elif name == "CrossSect":
                    catalog.cross_sections[alignment] = catalog.cross_sections.get(alignment, 0) + 1
                elif name == "CrossSectSurf":
                    names = catalog.section_surfaces.setdefault(alignment, [])
                    surface = attrib.get("name") or "Unnamed surface"
                    if surface not in names:
                        names.append(surface)
                elif name == "Roadway":
                    catalog.roadway_surfaces.extend((attrib.get("surfaceRefs") or "").split())
                elif name == "SurfVolume":
                    catalog.surface_volumes.append(
                        (attrib.get("name"), attrib.get("surfBase"), attrib.get("surfCompare"))
                    )
    if len(_CACHE) > 16:
        _CACHE.clear()
    _CACHE[key] = catalog
    return catalog


def match_name(candidates, requested, label="name"):
    """Resolve a typed name: exact, then case-insensitive, then a unique partial match."""
    requested = (requested or "").strip()
    if not requested:
        return None
    if requested in candidates:
        return requested
    folded = requested.casefold()
    exact = [name for name in candidates if name.casefold() == folded]
    if len(exact) == 1:
        return exact[0]
    partial = [name for name in candidates if folded in name.casefold()]
    if len(partial) == 1:
        return partial[0]
    available = ", ".join(f"'{name}'" for name in candidates) or "none"
    if len(partial) > 1:
        raise ValueError(
            f"{label.capitalize()} '{requested}' matches several names ({', '.join(repr(n) for n in partial)}); "
            f"enter more of the name. Available: {available}."
        )
    raise ValueError(f"{label.capitalize()} '{requested}' was not found. Available: {available}.")


def _tokens(name):
    text = name.casefold()
    for separator in "-_()[]/.,;:":
        text = text.replace(separator, " ")
    return set(text.split()) | {name.casefold()}


def _has_hint(name, hints):
    tokens = _tokens(name)
    return any(hint in tokens or (len(hint) > 3 and hint in name.casefold()) for hint in hints)


def suggest_terrain_pair(catalog):
    """Suggest (base, compare, reason) TIN surfaces from source evidence.

    Order of evidence: a Civil 3D SurfVolume pair; the surface named in an
    existing-ground ProfSurf (base) and Roadway corridor surfaces (compare,
    preferring the datum/bottom surface for earthworks); then name hints.
    """
    surfaces = catalog.surfaces
    for _name, base, compare in catalog.surface_volumes:
        if base in surfaces and compare in surfaces:
            return base, compare, f"Civil 3D volume surface '{_name}' pairs these surfaces"
    profile_names = [
        name
        for items in catalog.surface_profiles.values()
        for name, state in items
        if (state or "existing").lower() == "existing"
    ]
    base = next((s for s in surfaces if any(s and s in p for p in profile_names)), None)
    reason = []
    if base:
        reason.append(f"'{base}' is the existing-ground profile surface")
    else:
        base = next((s for s in surfaces if _has_hint(s, EXISTING_HINTS)), None)
        if base:
            reason.append(f"'{base}' is named like existing ground")
    refs = [s for s in surfaces if s != base and any(s == r or s.casefold() == r.casefold() for r in catalog.roadway_surfaces)]
    candidates = refs or [s for s in surfaces if s != base and _has_hint(s, DATUM_HINTS + DESIGN_HINTS)]
    compare = next((s for s in candidates if _has_hint(s, DATUM_HINTS)), None) or (candidates[0] if candidates else None)
    if compare is None and base is not None:
        others = [s for s in surfaces if s != base]
        compare = others[0] if len(others) == 1 else None
    if compare:
        source = "a Roadway corridor surface" if refs else "named like a design surface"
        kind = " (datum/bottom preferred for earthworks)" if _has_hint(compare, DATUM_HINTS) else ""
        reason.append(f"'{compare}' is {source}{kind}")
    return base, compare, "; ".join(reason)
