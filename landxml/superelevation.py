"""LandXML superelevation transitions as lane cross-slope diagrams.

Each ``Superelevation`` element lists, for one curve, the stations where the
outside lane leaves normal crown (BeginRunoutSta), reaches level
(BeginRunoffSta) and full superelevation (FullSuperSta), and the mirrored
exit stations. ``FullSuperelev`` is the signed full rate in percent. Its sign follows
the curve direction: in the examined Civil 3D exports every negative rate
belongs to a counter-clockwise (left) curve, which banks down to the left. The normal crown rate is not stored in
LandXML, so it is an explicit input.

Slopes returned here are outward cross-slopes in percent for each lane:
negative falls away from the centreline (normal crown), positive rises.
"""

from __future__ import annotations

from .common import local_name


def _float(node):
    try:
        return float((node.text or "").strip())
    except (AttributeError, ValueError):
        return None


def read_superelevation(alignment):
    """Return transition records with complete station sets, sorted by station."""
    records = []
    for node in alignment:
        if local_name(node.tag) != "Superelevation":
            continue
        values = {local_name(child.tag): _float(child) for child in node}
        keys = ("BeginRunoutSta", "BeginRunoffSta", "FullSuperSta", "FullSuperelev", "RunoffSta", "StartofRunoutSta", "EndofRunoutSta")
        if any(values.get(key) is None for key in keys):
            continue
        record = {
            "begin_runout": values["BeginRunoutSta"],
            "begin_runoff": values["BeginRunoffSta"],
            "full_start": values["FullSuperSta"],
            "rate": values["FullSuperelev"],
            "full_end": values["RunoffSta"],
            "end_runoff": values["StartofRunoutSta"],
            "end_runout": values["EndofRunoutSta"],
            "curve_start": float(node.attrib.get("staStart") or values["FullSuperSta"]),
            "curve_end": float(node.attrib.get("staEnd") or values["RunoffSta"]),
        }
        stations = [record[k] for k in ("begin_runout", "begin_runoff", "full_start", "full_end", "end_runoff", "end_runout")]
        if any(b < a - 1e-6 for a, b in zip(stations, stations[1:])):
            continue
        records.append(record)
    records.sort(key=lambda item: item["begin_runout"])
    return records


def lane_slopes(records, normal_crown=2.5):
    """Return (left, right) lists of (station, outward slope %) breakpoints."""
    nc = abs(normal_crown)
    left, right = [], []
    for record in records:
        rate = abs(record["rate"])
        # A negative (left-curve) rate lifts the right, outside lane.
        outside, inside = (right, left) if record["rate"] < 0 else (left, right)
        rc_in = record["begin_runoff"] + (record["full_start"] - record["begin_runoff"]) * min(nc / rate, 1.0) if rate else record["full_start"]
        rc_out = record["end_runoff"] - (record["end_runoff"] - record["full_end"]) * min(nc / rate, 1.0) if rate else record["full_end"]
        inside_full = -max(rate, nc) if rate >= nc else -nc
        outside.extend(
            [
                (record["begin_runout"], -nc),
                (record["begin_runoff"], 0.0),
                (record["full_start"], rate),
                (record["full_end"], rate),
                (record["end_runoff"], 0.0),
                (record["end_runout"], -nc),
            ]
        )
        inside.extend(
            [
                (record["begin_runout"], -nc),
                (rc_in, -nc),
                (record["full_start"], inside_full),
                (record["full_end"], inside_full),
                (rc_out, -nc),
                (record["end_runout"], -nc),
            ]
        )
    return sorted(left), sorted(right)
