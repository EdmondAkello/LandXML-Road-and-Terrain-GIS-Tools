"""A plain-language summary of a 3D scene setup, shown in the dialog before Run (pure text, no QGIS)."""

from __future__ import annotations


def _num(values, name, default=None):
    try:
        return float(values.get(name))
    except (TypeError, ValueError):
        return default


def _names(text):
    return [part.strip() for part in str(text or "").split(";") if part.strip()]


def setup_summary(values, unit="m", catalog=None):
    """Lines describing what a run with ``values`` will build. ``catalog`` (a LandXML catalogue, optional) lets it say
    whether each chosen alignment has a design profile; ``unit`` is the output length unit. Never raises on odd values."""
    lines = []
    roads = _names(values.get("SYNTHETIC")) if values.get("SYN_GENERATE", True) else []
    if not roads:
        lines.append("Terrain scene only: no road is generated.")
    if roads:
        width, shoulder, lanes = _num(values, "SYN_CARRIAGEWAY"), _num(values, "SYN_SHOULDER"), _num(values, "SYN_LANES")
        cut, fill = _num(values, "SYN_CUT_SLOPE"), _num(values, "SYN_FILL_SLOPE")
        section = []
        if width is not None:
            lane = f", {width / lanes:.2f} {unit} a lane" if lanes else ""
            section.append(f"carriageway {width:g} {unit}{lane}")
        if shoulder:
            section.append(f"shoulders {shoulder:g} {unit} each side")
        lines.append("Road on " + ", ".join(f"'{r}'" for r in roads) + (": " + ", ".join(section) if section else "") + ".")
        if cut is not None and fill is not None:
            lines.append(f"Side slopes: cut {cut:g}H:1V, fill {fill:g}H:1V.")
        if catalog is not None:
            for road in roads:
                if road not in catalog.alignments:
                    lines.append(f"'{road}' is not an alignment in this file.")
                elif not catalog.design_profiles.get(road) and not values.get("SYN_AT_GRADE"):
                    lines.append(f"'{road}' has no design profile: tick 'at grade' to follow the ground, or the road cannot be built.")
        ground = str(values.get("SYN_GROUND") or "").strip()
        lines.append(f"Ground for tie-ins: {ground.split(',')[0].strip()}." if ground else "Ground for tie-ins: the first surface.")
    width = _num(values, "CORRIDOR_WIDTH")
    if width is not None:
        lines.append("Scene width: Auto (sized from the road section and batters)." if width == 0
                     else f"Scene width: {width:g} {unit} either side of the alignment.")
    extras = []
    if values.get("VEGETATION"):
        extras.append("vegetation")
    if values.get("WATERWAYS"):
        extras.append("a waterway ribbon")
    if str(values.get("WATER") or "").strip():
        extras.append("water levels")
    if extras:
        lines.append("Also: " + ", ".join(extras) + ".")
    return [line for line in lines if line]
