"""Conservative source identification from explicit LandXML creator metadata."""

from __future__ import annotations

from .common import descendants, local_name


def detect_vendor(root) -> tuple[str, str | None]:
    """Return (vendor, evidence); generic means no trustworthy vendor signal."""
    values = []
    for key, value in root.attrib.items():
        if local_name(key).lower() in {
            "application",
            "creator",
            "createdby",
            "manufacturer",
        }:
            values.append((f"LandXML {local_name(key)}", value))
    for item in descendants(root, "Application"):
        for key in ("name", "manufacturer", "desc"):
            if item.attrib.get(key):
                values.append((f"Application {key}", item.attrib[key]))
    for label, value in values:
        lower = value.lower()
        if any(token in lower for token in ("bentley", "openroads", "opensite")):
            return "OpenRoads Designer", f"{label}: {value}"
        if any(token in lower for token in ("autodesk", "civil 3d", "civ3d")):
            return "Civil 3D", f"{label}: {value}"
    return "generic", None
