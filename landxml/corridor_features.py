"""Parametric corridor features: bus bays, lay-bys, climbing and descending lanes, auxiliary lanes, widenings and
truck escape ramps placed by chainage and side, that build themselves into the corridor (pure Python).

A feature is ``{"id", "type", "at", "side", <parameters>}``: ``at`` is the chainage where the full-width part starts
for the traffic using it, ``side`` is left, right, uphill or downhill (resolved from the design profile and the
side of the road the traffic keeps to). Defaults come from ``criteria/features`` (ported from the Road Furniture
Designer's definitions; RDM 1.3 drawings and clauses); every parameter is editable. At each station the active
features insert their elements (a bay or a lane with its tapers, a kerb along a bus bay, a through lane narrowed
beside a climbing lane), so the sections, surfaces, feature lines and quantities follow without regions to edit.
An escape ramp widens the road edge over its taper and adds its own alignment, profile and arrester bed.
"""

from __future__ import annotations

import copy
import json
import math
import os

from . import assembly as asm

FEATURES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "criteria", "features")


def load_feature_types(path=None):
    path = path or os.path.join(FEATURES_DIR, "rdm13_features.json")
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


def new_feature(kind, at, side=None, types=None, **params):
    types = types or load_feature_types()
    spec = types["types"][kind]
    out = {"type": kind, "at": float(at)}
    out.update(copy.deepcopy(spec["params"]))
    out["side"] = side or spec["params"].get("side") or "left"
    out.update(params)
    return out


def assign_ids(features):
    taken = {f.get("id") for f in features if f.get("id")}
    for f in features:
        if not f.get("id"):
            k = 1
            while f"{f['type']}_{k}" in taken:
                k += 1
            f["id"] = f"{f['type']}_{k}"
            taken.add(f["id"])
    return features


def _travel_with_chainage(side, keep):
    """Traffic on ``side`` travels with the chainage when it keeps to that side (left in Kenya)."""
    return side == keep


def resolve(features, frame, profile, keep="left", types=None):
    """Features with their side resolved and their chainage spans: [{..., "side", "with", "spans"}]."""
    types = types or load_feature_types()
    out = []
    for f in assign_ids(copy.deepcopy(features or [])):
        spec = types["types"].get(f["type"])
        if spec is None:
            continue
        full = dict(copy.deepcopy(spec["params"]))
        full.update(f)
        at = float(full["at"])
        side = full.get("side") or "left"
        if spec["insert"] == "centre":
            side = "both"
        if side in ("uphill", "downhill"):
            length = float(full.get("bay") or 100.0)
            rise = profile.elevation(min(at + length, frame.end)) - profile.elevation(at)
            with_side = keep                                        # traffic keeping to its side, with the chainage
            climbs_with = rise > 0
            if side == "uphill":
                side = with_side if climbs_with else _other(keep)
            else:
                side = with_side if not climbs_with else _other(keep)
        with_ = _travel_with_chainage(side, keep) if side != "both" else True
        if spec["insert"] == "gap":
            half = float(full.get("bay") or 10.0) / 2
            spans = (at - half, at - half, at + half, at + half)
        elif spec["insert"] == "ramp":
            taper = float(full.get("taper") or 50.0)
            spans = (at - taper, at, at, at) if with_ else (at, at, at, at + taper)
        else:
            entry, bay, exit_ = float(full.get("entry") or 0), float(full.get("bay") or 0), float(full.get("exit") or 0)
            spans = (at - entry, at, at + bay, at + bay + exit_) if with_ else (at - bay - exit_, at - bay, at, at + entry)
        full.update(side=side, with_chainage=with_, spans=spans, label=full.get("name") or f"{spec['label']} {full['id']}",
                    insert=spec["insert"], ref=spec.get("ref", ""))
        out.append(full)
    return out


def _other(side):
    return "right" if side == "left" else "left"


def factor(feature, station):
    """0 .. 1: how far the feature is developed at ``station`` (tapers linear)."""
    a, b, c, d = feature["spans"]
    if feature["insert"] == "gap":
        return 1.0 if b - 1e-9 <= station <= c + 1e-9 else 0.0
    if station < a - 1e-9 or station > d + 1e-9:
        return 0.0
    if feature["insert"] == "ramp":
        if feature["with_chainage"]:
            return (station - a) / max(b - a, 1e-9) if station <= b else 0.0
        return (d - station) / max(d - c, 1e-9) if station >= c else 0.0
    if station < b:
        return (station - a) / max(b - a, 1e-9)
    if station > c:
        return (d - station) / max(d - c, 1e-9)
    return 1.0


def key_stations(features):
    return [s for f in features for s in f["spans"]]


def apply(assembly, station, features):
    """The assembly at ``station`` with the active features inserted; returns (assembly, [active features])."""
    active = [(f, factor(f, station)) for f in features]
    active = [(f, t) for f, t in active if t > 1e-9 or f["spans"][1] - 1e-9 <= station <= f["spans"][2] + 1e-9]
    if not active:
        return assembly, []
    out = copy.deepcopy(assembly)
    for f, t in active:
        if f["insert"] == "centre":
            for side in asm.SIDES:
                strip = asm.element("lane", id=f"feat_{f['id']}", name=f["label"], kind="turning",
                                    width=float(f.get("width") or 3.0) / 2 * t, widen=False)
                lanes = [e for e in out.get(side, []) if e["type"] == "lane"]
                if lanes:
                    strip["crossfall"] = float(lanes[0]["crossfall"])
                    strip["buildup"] = lanes[0].get("buildup", "Carriageway")
                out[side] = [strip] + list(out.get(side) or [])
            continue
        items = out.get(f["side"]) or []
        if f["insert"] == "gap":
            if t > 0 or f["spans"][1] - 1e-9 <= station <= f["spans"][2] + 1e-9:
                out[f["side"]] = [e for e in items if e["type"] != "side_drain"]
            continue
        lanes = [k for k, e in enumerate(items) if e["type"] == "lane"]
        position = (lanes[-1] + 1) if lanes else 0
        through = items[lanes[-1]] if lanes else None
        why = f"{f['label']} ({f['ref']}), {t * 100:.0f} % developed"
        if f["insert"] == "bay":
            bay = asm.element("lane", id=f"feat_{f['id']}", name=f["label"], width=float(f.get("depth") or 3.5) * t,
                              kind="bus" if f["type"] == "bus_bay" else "parking",
                              crossfall=float(through["crossfall"]) if through else 2.5,
                              buildup=through.get("buildup", "Carriageway") if through else "Carriageway", widen=False)
            bay["_why"] = why
            items.insert(position, bay)
            if float(f.get("kerb_height") or 0) > 0:
                a, b, c, d = f["spans"]
                ramp = float(f.get("kerb_ramp") or 2.0)
                if b <= station <= c:
                    g = min(1.0, (station - b) / ramp, (c - station) / ramp) if ramp > 0 else 1.0
                else:
                    g = 0.0
                kerb = asm.element("kerb", id=f"feat_{f['id']}_kerb", name=f"{f['label']} kerb", kind="barrier",
                                   height=float(f["kerb_height"]) * max(g, 0.0), top=0.15, face=0.02)
                items.insert(position + 1, kerb)
                if float(f.get("platform") or 0) > 0 and g > 0:
                    items.insert(position + 2, asm.element("sidewalk", id=f"feat_{f['id']}_platform",
                                                           name=f"{f['label']} platform", width=float(f["platform"]) * g,
                                                           crossfall=2.0))
        elif f["insert"] == "lane":
            width = float(f.get("width") or 0.0)
            reduce = float(f.get("reduce_through") or 0.0)
            if through is not None:
                if not width:
                    width = float(through["width"]) - (reduce if float(through["width"]) >= 3.25 else 0.0)
                if reduce and float(through["width"]) >= 3.25:
                    through["width"] = float(through["width"]) - reduce * t
            lane = asm.element("lane", id=f"feat_{f['id']}", name=f["label"],
                               kind={"climbing_lane": "climbing", "descending_lane": "climbing"}.get(f["type"], "auxiliary"),
                               width=width * t, crossfall=float(through["crossfall"]) if through else 2.5,
                               buildup=through.get("buildup", "Carriageway") if through else "Carriageway", widen=False)
            items.insert(position, lane)
        elif f["insert"] == "ramp":
            lane = asm.element("lane", id=f"feat_{f['id']}", name=f"{f['label']} entry", kind="auxiliary",
                               width=float(f.get("width") or 8.0) * t,
                               crossfall=float(through["crossfall"]) if through else 2.5,
                               buildup=through.get("buildup", "Carriageway") if through else "Carriageway", widen=False)
            items.insert(position, lane)
        out[f["side"]] = items
    return out, [f for f, _t in active]


def bed_length(speed_kmh, grade, rolling):
    """Arrester bed length to stop from ``speed_kmh`` (RDM 1.3 Equation 12.1): V^2 / (254 (R + G) / 100)."""
    r = (rolling + grade) / 100.0
    if r <= 0:
        raise ValueError("The bed's rolling resistance plus its grade must be positive.")
    return speed_kmh * speed_kmh / (254.0 * r)


def escape_ramps(features, frame, profile, edge_offset, types=None):
    """For each escape ramp: the ramp centreline (map points), its profile (station, level) and bed figures.

    ``edge_offset(station, side)`` gives the signed offset of the road edge where the ramp departs."""
    types = types or load_feature_types()
    rolling = types.get("materials", {})
    out = []
    for f in features:
        if f["insert"] != "ramp":
            continue
        at = float(f["at"])
        xy, tangent, normal = frame.at([at])
        sign = -1.0 if f["side"] == "left" else 1.0
        direction = tangent[0] * (1 if f["with_chainage"] else -1)
        angle = math.radians(float(f.get("angle") or 4.0)) * sign * (1 if f["with_chainage"] else -1)
        c, s = math.cos(-angle), math.sin(-angle)
        heading = (direction[0] * c - direction[1] * s, direction[0] * s + direction[1] * c)
        start = xy[0] + normal[0] * edge_offset(at, f["side"])
        material = f.get("material") or "Gravel, loose"
        r = float(rolling.get(material, 10.0))
        grade = float(f.get("grade") or 0.0)
        entry = float(f.get("entry") or 30.0)
        bed = bed_length(float(f.get("speed") or 130.0), grade, r)
        total = entry + bed
        z0 = profile.elevation(at)
        road_grade = (profile.elevation(at + 1.0) - profile.elevation(at - 1.0)) / 2.0 * (1 if f["with_chainage"] else -1)
        pts, levels = [], []
        for k in range(0, int(math.ceil(total)) + 1):
            d = min(float(k), total)
            pts.append((start[0] + heading[0] * d, start[1] + heading[1] * d))
            g = road_grade if d <= entry else grade / 100.0
            levels.append(z0 + (road_grade * min(d, entry)) + (grade / 100.0) * max(d - entry, 0.0) if d > entry
                          else z0 + road_grade * d)
            _ = g
        width = float(f.get("width") or 8.0)
        depth = float(f.get("bed_depth") or 1.0)
        out.append({"feature": f, "points": pts, "levels": levels, "entry": entry, "bed": bed, "material": material,
                    "rolling": r, "width": width, "volume": bed * width * depth, "start": tuple(start),
                    "heading": heading, "why": f"{f['label']}: bed {bed:.0f} long for {float(f.get('speed') or 130):g} km/h "
                    f"on {material} (R = {r:g} %, Table 12.7) at {grade:+g} % (Equation 12.1)"})
    return out


__all__ = ["load_feature_types", "new_feature", "resolve", "apply", "factor", "key_stations", "escape_ramps",
           "bed_length", "assign_ids"]
