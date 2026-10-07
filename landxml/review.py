"""Geometric design review: phasing, vertical and horizontal geometry, sight
distance, superelevation and roadside barrier warrants, with recommendations
the designer can apply back in the design software.

Only design elements under the designer's control are checked (alignment,
profile, superelevation and the corridor cross-section). Findings cite the
criteria table they use; recommendations are computed values (PVI stations,
elevations, curve lengths, radii, runoff lengths, barrier ranges), never
automatic edits of the source design.
"""

from __future__ import annotations

from bisect import bisect_right
import math

SEVERITY_ORDER = {"High": 0, "Medium": 1, "Low": 2, "Info": 3}


def finding(category, severity, start, end, title, detail, reference="", recommendation="", side="", value=None, required=None, code=""):
    return {
        "category": category,
        "severity": severity,
        "start": start,
        "end": end if end is not None else start,
        "side": side,
        "title": title,
        "detail": detail,
        "reference": reference,
        "recommendation": recommendation,
        "value": value,
        "required": required,
        "code": code,
    }


# --------------------------------------------------------------- geometry


def curve_groups(elements):
    """Merge spiral-curve-spiral runs into horizontal curve groups."""
    groups, current = [], None
    for element in elements:
        if element["type"] == "line":
            if current:
                groups.append(current)
                current = None
            continue
        if current is None or (element["type"] == "spiral" and current.get("closed")):
            if current:
                groups.append(current)
            current = {"start": element["start"], "end": element["end"], "radius": None, "turn": element["turn"],
                       "arc_start": None, "arc_end": None, "spiral_in": 0.0, "spiral_out": 0.0, "elements": []}
        current["elements"].append(element)
        current["end"] = element["end"]
        current["turn"] = current["turn"] or element["turn"]
        if element["type"] == "curve":
            if current["radius"] is not None:
                # A second circular arc starts a new group (compound or reverse curve).
                previous = current
                previous["elements"].pop()
                previous["end"] = element["start"]
                groups.append(previous)
                current = {"start": element["start"], "end": element["end"], "radius": element["radius"], "turn": element["turn"],
                           "arc_start": element["start"], "arc_end": element["end"], "spiral_in": 0.0, "spiral_out": 0.0,
                           "elements": [element]}
                continue
            current["radius"] = element["radius"]
            current["arc_start"], current["arc_end"] = element["start"], element["end"]
        elif element["type"] == "spiral":
            if current["radius"] is None:
                current["spiral_in"] += element["length"]
            else:
                current["spiral_out"] += element["length"]
                current["closed"] = True
    if current:
        groups.append(current)
    result = []
    for group in groups:
        if group["radius"] is None:
            # Spiral-only group: use its sharpest end radius.
            radii = [r for e in group["elements"] for r in (e.get("radius_start"), e.get("radius_end")) if r]
            group["radius"] = min(radii) if radii else None
            group["arc_start"], group["arc_end"] = group["start"], group["end"]
        if group["radius"] is None:
            continue
        group["mid"] = (group["arc_start"] + group["arc_end"]) / 2.0
        group["length"] = group["end"] - group["start"]
        arc = group["arc_end"] - group["arc_start"]
        group["deflection_deg"] = math.degrees((arc + (group["spiral_in"] + group["spiral_out"]) / 2.0) / group["radius"])
        result.append(group)
    return result


class ProfileModel:
    """Vertical alignment as PVIs with symmetric parabolic curves."""

    def __init__(self, pvis):
        self.pvis = sorted(pvis, key=lambda p: p["station"])

    @classmethod
    def from_controls(cls, controls):
        return cls(
            [
                {"station": c["station"], "elevation": c["elevation"], "length": c.get("curve_length") or 0.0}
                for c in controls
            ]
        )

    def copy(self):
        return ProfileModel([dict(p) for p in self.pvis])

    def grade(self, i, j):
        a, b = self.pvis[i], self.pvis[j]
        return (b["elevation"] - a["elevation"]) / (b["station"] - a["station"])

    def curves(self):
        """Vertical curve records for interior PVIs with a curve length."""
        result = []
        for i in range(1, len(self.pvis) - 1):
            p = self.pvis[i]
            g1, g2 = self.grade(i - 1, i), self.grade(i, i + 1)
            a = (g2 - g1) * 100.0
            result.append(
                {
                    "index": i,
                    "station": p["station"],
                    "elevation": p["elevation"],
                    "length": p["length"],
                    "start": p["station"] - p["length"] / 2.0,
                    "end": p["station"] + p["length"] / 2.0,
                    "g1": g1 * 100.0,
                    "g2": g2 * 100.0,
                    "A": abs(a),
                    "kind": "crest" if a < 0 else "sag" if a > 0 else "flat",
                    "K": p["length"] / abs(a) if a and p["length"] else None,
                }
            )
        return result

    def elevation(self, station):
        pvis = self.pvis
        if station <= pvis[0]["station"]:
            return pvis[0]["elevation"]
        if station >= pvis[-1]["station"]:
            return pvis[-1]["elevation"]
        stations = [p["station"] for p in pvis]
        i = bisect_right(stations, station)
        a, b = pvis[i - 1], pvis[i]
        z = a["elevation"] + (b["elevation"] - a["elevation"]) * (station - a["station"]) / (b["station"] - a["station"])
        for k in (i - 1, i):
            if 0 < k < len(pvis) - 1 and pvis[k]["length"]:
                half = pvis[k]["length"] / 2.0
                if pvis[k]["station"] - half <= station <= pvis[k]["station"] + half:
                    g1, g2 = self.grade(k - 1, k), self.grade(k, k + 1)
                    x = station - (pvis[k]["station"] - half)
                    z_tangent = pvis[k]["elevation"] - g1 * half + g1 * x
                    return z_tangent + (g2 - g1) / (2 * pvis[k]["length"]) * x * x
        return z

    def samples(self, step=2.0):
        start, end = self.pvis[0]["station"], self.pvis[-1]["station"]
        count = max(2, int(math.ceil((end - start) / step)) + 1)
        return [(start + (end - start) * i / (count - 1), self.elevation(start + (end - start) * i / (count - 1))) for i in range(count)]

    def fits(self, i):
        """True when curve i does not overlap its neighbours' curves."""
        p = self.pvis[i]
        if i > 0:
            q = self.pvis[i - 1]
            if p["station"] - p["length"] / 2 < q["station"] + (q["length"] / 2 if i - 1 > 0 else 0) - 1e-6:
                return False
        if i < len(self.pvis) - 1:
            q = self.pvis[i + 1]
            if p["station"] + p["length"] / 2 > q["station"] - (q["length"] / 2 if i + 1 < len(self.pvis) - 1 else 0) + 1e-6:
                return False
        return True

    def room(self, i):
        """Maximum curve length at PVI i without overlapping neighbours."""
        p = self.pvis[i]
        left = p["station"] - (self.pvis[i - 1]["station"] + (self.pvis[i - 1]["length"] / 2 if i - 1 > 0 else 0))
        right = (self.pvis[i + 1]["station"] - (self.pvis[i + 1]["length"] / 2 if i + 1 < len(self.pvis) - 1 else 0)) - p["station"]
        return 2.0 * max(0.0, min(left, right))


def sight_distance(samples, index, direction, eye, target, limit):
    """Available sight distance along a sampled profile (vertical only)."""
    s0, z0 = samples[index]
    eye_z = z0 + eye
    best = -math.inf
    step = 1 if direction > 0 else -1
    i = index + step
    while 0 <= i < len(samples):
        s, z = samples[i]
        distance = abs(s - s0)
        if distance > limit:
            return limit
        visible = (z + target - eye_z) / distance >= best - 1e-12
        if not visible:
            return distance
        best = max(best, (z - eye_z) / distance)
        i += step
    return None  # sight runs off the profile end; not assessable


def round_up(value, step=5.0):
    return math.ceil(value / step - 1e-9) * step


# ---------------------------------------------------------------- review


class Review:
    def __init__(self, criteria, speed_at, emax=6.0, road_type="rural", terrain="rolling", max_grade=None, lane_width=None):
        self.c = criteria
        self.speed_at = speed_at
        self.emax = emax
        self.road_type = road_type
        self.terrain = terrain
        self.max_grade_override = max_grade
        self.lane_width = lane_width or float(criteria.get("lane_width", 3.5))
        self.findings = []
        self.changes = []

    def add(self, *args, **kwargs):
        self.findings.append(finding(*args, **kwargs))

    def speed(self, start, end=None):
        """Design speed at a station, or the governing (highest) speed over a range."""
        over = getattr(self.speed_at, "over", None)
        if end is None or over is None:
            return self.speed_at(start)
        return over(start, end)

    # ---- vertical -------------------------------------------------------
    def required_length(self, curve, speed):
        k = self.c.k_crest(speed) if curve["kind"] == "crest" else self.c.k_sag(speed)
        lengths = [self.c.min_vc_length(speed) or 0.0]
        if k:
            lengths.append(k * curve["A"])
        small = self.c.get("small_grade_change")
        if small and curve["A"] < small["threshold_percent"]:
            lengths.append(small["min_length_factor"] * speed)
        return max(lengths), k

    def check_vertical(self, model):
        c = self.c
        curves = model.curves()
        for curve in curves:
            if curve["kind"] == "flat":
                continue
            speed = self.speed(curve["start"], curve["end"])
            label = f"{curve['kind'].capitalize()} curve at PVI {curve['station']:.2f}"
            needed, kmin = self.required_length(curve, speed)
            ref_k = c.ref("k_crest" if curve["kind"] == "crest" else "k_sag")
            if curve["length"] <= 0:
                self.add("Vertical", "High" if curve["A"] > 0.5 else "Low", curve["station"], curve["station"],
                         f"{label}: no vertical curve", f"Grade change {curve['A']:.2f}% without a curve.", ref_k,
                         f"Insert a parabolic curve of at least {round_up(needed):.0f} m.", value=0.0, required=needed, code="V0")
            elif kmin and curve["K"] is not None and curve["K"] < kmin - 1e-6:
                self.add("Vertical", "High", curve["start"], curve["end"], f"{label}: K below minimum",
                         f"K = {curve['K']:.1f} (L {curve['length']:.1f} m, A {curve['A']:.2f}%) at {speed:g} km/h; minimum K {kmin:g}.",
                         ref_k, f"Lengthen to L ≥ {round_up(needed):.0f} m (K {round_up(needed) / curve['A']:.1f}).",
                         value=curve["K"], required=kmin, code="V1")
            elif curve["length"] < needed - 1e-6:
                reason = c.ref("small_grade_change") if c.get("small_grade_change") and curve["A"] < c.get("small_grade_change")["threshold_percent"] else c.ref("min_vc_length")
                self.add("Vertical", "Medium" if curve["A"] >= 0.5 else "Low", curve["start"], curve["end"],
                         f"{label}: shorter than the minimum length", f"L = {curve['length']:.1f} m; minimum {needed:.0f} m at {speed:g} km/h.",
                         reason, f"Lengthen to L ≥ {round_up(needed):.0f} m.", value=curve["length"], required=needed, code="V2")
        gap = self.c.get("crest_sag_gap")
        if gap and gap.get("min"):
            for a, b in zip(curves, curves[1:]):
                if {a["kind"], b["kind"]} == {"crest", "sag"} and b["start"] - a["end"] < gap["min"] - 1e-6:
                    self.add("Phasing", "Low", a["end"], b["start"], "Crest and sag share an end (dropped road / hump effect)",
                             f"Straight grade between the curves is {max(0.0, b['start'] - a['end']):.1f} m.", self.c.ref("crest_sag_gap"),
                             f"Insert {gap['min']:g}–{gap['max']:g} m of constant grade by shortening or moving the curves.", code="P7")
        return curves

    def check_grades(self, model, ground=None):
        limits = self.c.max_grade(self.road_type, self.terrain)
        if self.max_grade_override:
            limits = (self.max_grade_override, self.max_grade_override)
        min_grade = self.c.get("min_grade_percent")
        for i in range(len(model.pvis) - 1):
            a, b = model.pvis[i], model.pvis[i + 1]
            grade = model.grade(i, i + 1) * 100.0
            start = a["station"] + (a["length"] / 2 if i > 0 else 0)
            end = b["station"] - (b["length"] / 2 if i + 1 < len(model.pvis) - 1 else 0)
            if end <= start:
                continue
            if limits:
                desirable, absolute = limits
                if abs(grade) > absolute + 1e-9:
                    self.add("Grades", "High", start, end, f"Grade {grade:+.2f}% exceeds the absolute maximum",
                             f"Absolute maximum {absolute:g}% ({self.road_type}, {self.terrain}).", self.c.ref("max_grade"),
                             "Flatten the grade, lengthen the development or record a Departure from Standards.", value=grade, required=absolute, code="G1")
                elif abs(grade) > desirable + 1e-9:
                    self.add("Grades", "Medium", start, end, f"Grade {grade:+.2f}% exceeds the desirable maximum",
                             f"Desirable maximum {desirable:g}% ({self.road_type}, {self.terrain}).", self.c.ref("max_grade"),
                             "Flatten where economic; check truck speed reduction.", value=grade, required=desirable, code="G2")
            limit = self.c.max_grade_length(abs(grade))
            if limit and end - start > limit + 1e-6:
                self.add("Grades", "Medium", start, end, f"{abs(grade):.2f}% grade longer than {limit:.0f} m",
                         f"Length {end - start:.0f} m.", self.c.ref("max_grade_length"),
                         "Break the grade or consider a climbing lane.", value=end - start, required=limit, code="G3")
            if min_grade and abs(grade) < min_grade - 1e-9 and end - start > 20:
                in_cut = None
                if ground is not None:
                    mid = (start + end) / 2.0
                    z = ground.elevation_at(mid)
                    in_cut = z is not None and model.elevation(mid) < z
                severity = "Medium" if in_cut else "Low"
                where = " in cut" if in_cut else ""
                self.add("Grades", severity, start, end, f"Flat grade {grade:+.2f}%{where}",
                         f"Below the {min_grade:g}% minimum for longitudinal drainage over {end - start:.0f} m.", self.c.ref("min_grade"),
                         f"Use at least {min_grade:g}% or provide side drains with independent falls.", value=grade, required=min_grade, code="G4")

    def check_sight(self, model, step=5.0):
        samples = model.samples(2.0)
        stride = max(1, int(step / 2.0))
        eye, target = self.c.eye_height, self.c.object_height
        bad = []
        for index in range(0, len(samples), stride):
            station = samples[index][0]
            speed = self.speed_at(station)
            for direction in (1, -1):
                ahead = samples[min(len(samples) - 1, index + direction * 5)] if 0 <= index + direction * 5 < len(samples) else None
                grade = 0.0
                if ahead:
                    grade = (ahead[1] - samples[index][1]) / (ahead[0] - samples[index][0]) * 100.0
                    grade = grade if direction > 0 else -grade
                need = self.c.ssd(speed, grade)
                if not need:
                    continue
                available = sight_distance(samples, index, direction, eye, target, need)
                if available is not None and available < need - 1e-6:
                    bad.append((station, direction, available, need))
        # Merge consecutive deficient stations per direction.
        for direction in (1, -1):
            run = [b for b in bad if b[1] == direction]
            groups, current = [], []
            for item in run:
                if current and item[0] - current[-1][0] > step * 1.5:
                    groups.append(current)
                    current = []
                current.append(item)
            if current:
                groups.append(current)
            for group in groups:
                worst = min(group, key=lambda g: g[2] / g[3])
                label = "increasing" if direction > 0 else "decreasing"
                self.add("Sight distance", "High", group[0][0], group[-1][0], f"Stopping sight distance not met (travel in {label} chainage)",
                         f"Lowest available {worst[2]:.0f} m against {worst[3]:.0f} m required at {worst[0]:.0f}.",
                         self.c.ref("ssd"), "Lengthen the crest curve (higher K) or flatten the grades at this crest.",
                         value=worst[2], required=worst[3], code="S1")

    # ---- design speed changes ---------------------------------------------
    def check_speed_changes(self, start, end, groups=()):
        """Design speed steps, transition section lengths and sharp curves just past a reduction."""
        rule = self.c.get("speed_change")
        sections = getattr(self.speed_at, "sections", None)
        if not rule or sections is None:
            return []
        c = self.c
        parts = sections(start, end)
        preferred = rule.get("preferred_step") or 10
        maximum = rule.get("max_step") or 20
        transition = rule.get("transition_min_length")
        for (a0, a1, va), (b0, b1, vb) in zip(parts, parts[1:]):
            step = abs(vb - va)
            high, low = max(va, vb), min(va, vb)
            # Intermediate sections go on the faster side, so the slower section's geometry stays valid.
            faster_before = va > vb
            if step > maximum + 1e-6:
                needed = math.ceil(step / maximum) - 1
                desirable = math.ceil(step / preferred) - 1
                length = transition or 0.0
                speeds = [high - preferred * (k + 1) for k in range(desirable)]
                if length and faster_before:
                    spans = [(a1 - length * (desirable - k), a1 - length * (desirable - k - 1)) for k in range(desirable)]
                elif length:
                    speeds = speeds[::-1]
                    spans = [(b0 + length * k, b0 + length * (k + 1)) for k in range(desirable)]
                if length:
                    plan = "; ".join(f"{v:g} km/h over {x0:,.0f}–{x1:,.0f}" for v, (x0, x1) in zip(speeds, spans))
                else:
                    plan = ", ".join(f"{v:g}" for v in speeds) + " km/h"
                self.add("Design speed", "High", min(a1, b0), max(a1, b0), f"Abrupt design speed change {va:g} → {vb:g} km/h",
                         f"A change of {step:g} km/h; steps above {maximum:g} km/h are poor design and at least {needed} "
                         f"intermediate section(s) are needed ({desirable} at the preferred {preferred:g} km/h steps).",
                         c.ref("speed_change"),
                         f"Add design speed sections in Civil 3D on the {high:g} km/h side: {plan}; then re-check the curves in them.",
                         value=step, required=maximum, code="D1")
            elif step > preferred + 1e-6:
                self.add("Design speed", "Low", min(a1, b0), max(a1, b0), f"Design speed change {va:g} → {vb:g} km/h",
                         f"A change of {step:g} km/h is acceptable but steps of {preferred:g} km/h or less are preferred.",
                         c.ref("speed_change"),
                         f"Consider an intermediate {(high + low) / 2:g} km/h section"
                         + (f" of at least {transition:g} m." if transition else "."),
                         value=step, required=preferred, code="D1")
        if transition:
            for (a0, a1, va), (b0, b1, vb), (c0, c1, vc) in zip(parts, parts[1:], parts[2:]):
                if min(va, vc) < vb < max(va, vc) and b1 - b0 < transition - 1e-6:
                    self.add("Design speed", "Medium", b0, b1, f"Short {vb:g} km/h transition section",
                             f"{b1 - b0:.0f} m between {va:g} and {vc:g} km/h sections; at least {transition:g} m lets drivers adjust speed.",
                             c.ref("speed_change"), f"Lengthen the {vb:g} km/h section to ≥ {transition:g} m.",
                             value=b1 - b0, required=transition, code="D2")
                elif va == vc != vb and b1 - b0 < transition - 1e-6:
                    if vb > va:
                        title = f"Short {vb:g} km/h section between {va:g} km/h sections"
                        advice = (f"Drivers cannot settle at {vb:g} km/h within {b1 - b0:.0f} m. Merge it into the {va:g} km/h sections, "
                                  f"or lengthen it to ≥ {transition:g} m.")
                    else:
                        title = f"Short {vb:g} km/h section between {va:g} km/h sections"
                        advice = (f"Drivers arriving at {va:g} km/h will not slow within {b1 - b0:.0f} m: design its elements for {va:g} km/h, "
                                  f"or lengthen it to ≥ {transition:g} m with speed transitions.")
                    self.add("Design speed", "Low", b0, b1, title,
                             f"{b1 - b0:.0f} m long; speed oscillation between short sections is inherently unsafe.",
                             c.ref("speed_change"), advice, value=b1 - b0, required=transition, code="D2")
        reach = transition or 0.0
        nearest = {}
        for (a0, a1, va), (b0, b1, vb) in zip(parts, parts[1:]):
            if va == vb or not reach:
                continue
            change = a1
            high = max(va, vb)
            rmin_high = c.min_radius(high, self.emax)
            if not rmin_high:
                continue
            # Travelling towards the slower section from the faster one.
            if va > vb:
                window = (change, change + reach)
            else:
                window = (change - reach, change)
            for group in groups:
                if group["end"] < window[0] or group["start"] > window[1] or group["radius"] >= rmin_high:
                    continue
                distance = max(group["start"] - change if va > vb else change - group["end"], 0.0)
                key = (group["start"], group["end"])
                if key in nearest and nearest[key] <= distance:
                    continue
                if key in nearest:
                    self.findings = [f for f in self.findings if not (f["code"] == "D3" and (f["start"], f["end"]) == key)]
                nearest[key] = distance
                self.add("Design speed", "Medium", group["start"], group["end"],
                         f"Sharp curve R{group['radius']:,.0f} close to a speed reduction ({high:g} → {min(va, vb):g} km/h)",
                         f"The curve is {distance:.0f} m from the speed change, within the {reach:g} m drivers need to slow; "
                         f"R{group['radius']:,.0f} is below the {high:g} km/h minimum ({rmin_high:.0f} m).",
                         c.ref("speed_change"),
                         f"Move the speed change at least {reach:g} m before the curve, add an intermediate speed section, "
                         f"or increase the radius to ≥ {round_up(rmin_high):.0f} m.",
                         value=group["radius"], required=rmin_high, code="D3")
        return parts

    # ---- horizontal -----------------------------------------------------
    def check_horizontal(self, elements, groups, superelevation=()):
        c = self.c
        floor = c.get("radius_floor")
        for group in groups:
            speed = self.speed(group["start"], group["end"])
            radius = group["radius"]
            rmin = c.min_radius(speed, self.emax)
            label = f"{group['turn'] or ''} curve R{radius:,.0f}".strip()
            if floor and radius < floor:
                self.add("Horizontal", "High", group["start"], group["end"], f"{label}: below the absolute minimum radius",
                         f"Absolute minimum {floor:g} m.", c.ref("radius_floor"), "Increase the radius.", value=radius, required=floor, code="H0")
            elif rmin and radius < rmin - 1e-6:
                self.add("Horizontal", "High", group["start"], group["end"], f"{label}: radius below minimum",
                         f"Minimum {rmin:.0f} m at {speed:g} km/h, e max {self.emax:g}%.", c.ref("min_radius"),
                         f"Increase the radius to ≥ {round_up(rmin):.0f} m or reduce the design speed with signing.",
                         value=radius, required=rmin, code="H1")
            required_a = None
            transition_radius = c.transition_required_radius(speed)
            spiral_lengths = [e["length"] for e in group["elements"] if e["type"] == "spiral"]
            if transition_radius and radius < transition_radius and not spiral_lengths:
                required_a = c.spiral_min_parameter(speed, radius)
                self.add("Horizontal", "Medium", group["start"], group["end"], f"{label}: transition curve required",
                         f"Transitions are required below R{transition_radius:.0f} at {speed:g} km/h.", c.ref("transition"),
                         f"Add clothoids with A ≥ {required_a:.0f} (L ≥ {required_a ** 2 / radius:.0f} m)." if required_a else "Add transition curves.",
                         value=0.0, required=required_a, code="H2")
            elif spiral_lengths:
                required_a = c.spiral_min_parameter(speed, radius)
                for element in group["elements"]:
                    if element["type"] != "spiral" or not element.get("parameter"):
                        continue
                    if required_a and element["parameter"] < required_a - 1e-6:
                        self.add("Horizontal", "Medium", element["start"], element["end"], f"{label}: transition too short",
                                 f"A = {element['parameter']:.0f}; minimum {required_a:.0f}.", c.ref("transition"),
                                 f"Lengthen the transition to L ≥ {required_a ** 2 / radius:.0f} m.",
                                 value=element["parameter"], required=required_a, code="H3")
            factor = c.get("curve_length_factor")
            arc = group["arc_end"] - group["arc_start"] + (group["spiral_in"] + group["spiral_out"]) / 2.0
            if factor and arc < factor * speed - 1e-6 and group["deflection_deg"] >= 5:
                self.add("Horizontal", "Low", group["start"], group["end"], f"{label}: short curve",
                         f"Curve length {arc:.0f} m (arc + half transitions); minimum {factor * speed:.0f} m for major roads.",
                         c.ref("curve_length"), "Lengthen the curve (larger radius) to avoid a kinked appearance.",
                         value=arc, required=factor * speed, code="H4")
            maximum = c.get("curve_length_max")
            if maximum and group["arc_end"] - group["arc_start"] > maximum:
                self.add("Horizontal", "Low", group["start"], group["end"], f"{label}: circular curve longer than {maximum:g} m",
                         "Long curves reduce overtaking opportunities.", c.ref("curve_length"),
                         f"Keep the circular arc ≤ {c.get('curve_length_preferred_max', maximum):g} m where practicable.",
                         value=group["arc_end"] - group["arc_start"], required=maximum, code="H5")
            self.check_superelevation(group, speed, superelevation, label)
        self.check_tangents(groups)

    def check_tangents(self, groups):
        c = self.c
        for index, (a, b) in enumerate(zip(groups, groups[1:])):
            tangent = b["start"] - a["end"]
            speed = self.speed(a["end"], b["start"])
            same = a["turn"] and a["turn"] == b["turn"]
            minimum_same = c.get("same_direction_tangent_min")
            if same and minimum_same and tangent < minimum_same - 1e-6:
                self.add("Phasing", "Medium", a["end"], b["start"], "Broken-back curve",
                         f"Same-direction curves separated by {tangent:.0f} m of straight (minimum {minimum_same:g} m).",
                         c.ref("same_direction_tangent"), "Replace both curves by a single larger-radius curve, or lengthen the straight.",
                         value=tangent, required=minimum_same, code="H6")
            minimum = c.get("tangent_min_factor")
            if minimum and not same and 0 < tangent < minimum * speed - 1e-6:
                self.add("Horizontal", "Low", a["end"], b["start"], "Short straight between reverse curves",
                         f"{tangent:.0f} m; Table guidance {minimum * speed:.0f} m (6V) when a curve is followed by the next curve.",
                         c.ref("tangents"), "Check that superelevation reversal and run-off fit, or join with a reverse spiral.",
                         value=tangent, required=minimum * speed, code="H7")
        maximum = c.get("tangent_max_factor")
        if maximum:
            for a, b in zip(groups, groups[1:]):
                tangent = b["start"] - a["end"]
                speed = self.speed(a["end"], b["start"])
                limit = c.get("tangent_max_cap_high_speed", 4000) if speed >= 100 else maximum * speed
                if tangent > limit:
                    self.add("Horizontal", "Low", a["end"], b["start"], "Long straight",
                             f"{tangent:.0f} m; maximum {limit:.0f} m.", c.ref("tangents"),
                             "Introduce gentle curvature to limit speeding and fatigue.", value=tangent, required=limit, code="H8")
                factor = c.get("isolated_curve_factor")
                rmin = c.min_radius(self.speed(b["start"], b["end"]), self.emax)
                if factor and rmin and tangent > limit and b["radius"] < factor * rmin:
                    self.add("Horizontal", "Medium", b["start"], b["end"], f"Isolated curve R{b['radius']:,.0f} after a long straight",
                             f"Isolated curves need {factor:g} × the minimum radius ({factor * rmin:.0f} m).", c.ref("isolated_curve"),
                             f"Increase the radius to ≥ {round_up(factor * rmin):.0f} m.", value=b["radius"], required=factor * rmin, code="H9")

    def check_superelevation(self, group, speed, records, label):
        c = self.c
        required, text = c.required_superelevation(group["radius"], speed, self.emax)
        record = next((r for r in records if r["begin_runout"] <= group["mid"] <= r["end_runout"]), None)
        provided = abs(record["rate"]) if record else None
        if required is None:
            # Radius below the tabulated range (already flagged): at least e max applies.
            required, text = self.emax, f"e max {self.emax:g}% (radius below the table)"
        if text not in ("NC",) and provided is None and required > 0:
            self.add("Superelevation", "High", group["start"], group["end"], f"{label}: no superelevation transition",
                     f"{text} required at {speed:g} km/h, e max {self.emax:g}%.", c.ref("superelevation"),
                     f"Apply {text} superelevation with run-off ≥ {c.runoff_min(speed, required, self.lane_width):.0f} m.",
                     value=None, required=required, code="E1")
            return
        if provided is None:
            return
        if text != "NC" and provided < required - 0.25:
            self.add("Superelevation", "High", group["start"], group["end"], f"{label}: superelevation below the required rate",
                     f"{provided:g}% provided; {text} required at {speed:g} km/h, e max {self.emax:g}%.", c.ref("superelevation"),
                     f"Increase full superelevation to {required:.1f}%.", value=provided, required=required, code="E2")
        if provided > self.emax + 1e-6:
            self.add("Superelevation", "Medium", group["start"], group["end"], f"{label}: superelevation above e max",
                     f"{provided:g}% > {self.emax:g}%.", c.ref("superelevation"), f"Limit to {self.emax:g}% or adopt a higher e max formally.",
                     value=provided, required=self.emax, code="E3")
        runoff = min(record["full_start"] - record["begin_runoff"], record["end_runoff"] - record["full_end"])
        needed = c.runoff_min(speed, provided, self.lane_width)
        if needed and runoff < needed - 0.5:
            self.add("Superelevation", "Medium", record["begin_runoff"], record["end_runoff"], f"{label}: run-off too short",
                     f"{runoff:.1f} m provided; minimum {needed:.0f} m at {speed:g} km/h.", c.ref("runoff"),
                     f"Lengthen the run-off to ≥ {round_up(needed):.0f} m.", value=runoff, required=needed, code="E4")

    def check_combined_grade(self, model, records):
        limit = self.c.get("max_combined_grade_percent")
        if not limit:
            return
        for record in records:
            station = (record["full_start"] + record["full_end"]) / 2.0
            g = (model.elevation(station + 1) - model.elevation(station - 1)) / 2.0 * 100.0
            combined = math.hypot(g, record["rate"])
            if combined > limit + 1e-6:
                self.add("Superelevation", "Medium", record["full_start"], record["full_end"], "Combined grade above limit",
                         f"Grade {g:+.2f}% with superelevation {record['rate']:g}% gives {combined:.2f}%.", self.c.ref("combined_grade"),
                         f"Reduce the grade or superelevation so √(g² + e²) ≤ {limit:g}%.", value=combined, required=limit, code="E5")

    # ---- phasing ---------------------------------------------------------
    def check_phasing(self, model, groups):
        curves = [c for c in model.curves() if c["kind"] != "flat" and c["length"] > 0]
        for curve in curves:
            speed = self.speed(curve["start"], curve["end"])
            rmin = self.c.min_radius(speed, self.emax) or 0.0
            ssd = self.c.ssd(speed, 0.0) or 0.0
            for group in groups:
                overlap = min(curve["end"], group["end"]) - max(curve["start"], group["start"])
                sharp = group["radius"] < 1.5 * rmin if rmin else False
                start_in = curve["start"] < group["start"] < curve["end"]
                end_in = curve["start"] < group["end"] < curve["end"]
                tag = f"{curve['kind']} PVI {curve['station']:.0f} / curve R{group['radius']:,.0f}"
                ref = self.c.ref("phasing")
                if overlap <= 0:
                    gap = max(group["start"] - curve["end"], curve["start"] - group["end"])
                    if curve["kind"] == "crest" and 0 <= group["start"] - curve["station"] <= curve["length"] / 2 + ssd:
                        self._phasing(curve, group, "P1", "High", f"Start of horizontal curve hidden beyond a crest ({tag})",
                                      "The curve starts on the down grade after the crest, within stopping sight distance.",
                                      "S7.11/7.12", model)
                    elif 0 < gap < max(ssd / 2.0, 30.0):
                        self.add("Phasing", "Low", min(curve["end"], group["end"]), max(curve["start"], group["start"]),
                                 f"Insufficient separation ({tag})", f"Only {gap:.0f} m between the curves.", f"{ref} (S7.13)",
                                 "Separate the curves further or make them concurrent.", code="P5")
                    continue
                if start_in and end_in:
                    if curve["kind"] == "crest":
                        self._phasing(curve, group, "P2", "High" if sharp else "Medium", f"Crest overlaps both ends of the horizontal curve ({tag})",
                                      "A sudden change of direction occurs while sight distance is reduced by the crest.", "S7.14", model)
                    else:
                        self._phasing(curve, group, "P2", "Low", f"Sag overlaps both ends of the horizontal curve ({tag})",
                                      "An illusory dip or crest appears in the alignment.", "S7.14", model)
                elif start_in or end_in:
                    severity = "High" if curve["kind"] == "crest" else "Medium"
                    detail = ("The change of direction is perceived late because the crest limits sight distance."
                              if curve["kind"] == "crest" else "An apparent kink appears where the sag overlaps the curve end.")
                    if curve["kind"] == "sag" and abs(curve["end"] - group["start"]) < curve["length"] / 2:
                        detail = "A sag at the start of the horizontal curve exaggerates its sharpness (S7.5)."
                    self._phasing(curve, group, "P3", severity, f"Vertical curve overlaps one end of the horizontal curve ({tag})",
                                  detail, "S7.12", model)
                else:
                    # Vertical curve within the horizontal curve: good phasing unless much shorter on a sharp curve.
                    if sharp and curve["length"] < 0.5 * group["length"] and curve["kind"] == "crest":
                        self._phasing(curve, group, "P4", "Low", f"Short crest inside a sharp horizontal curve ({tag})",
                                      "The radius may appear to tighten over the crest.", "S7.10", model)
        for group in groups:
            within = [c for c in curves if group["start"] <= c["start"] and c["end"] <= group["end"]]
            kinds = [c["kind"] for c in within]
            if len(within) >= 2 and "crest" in kinds and "sag" in kinds:
                self.add("Phasing", "Low", group["start"], group["end"], f"Rolling grade line on curve R{group['radius']:,.0f}",
                         f"{len(within)} vertical curves within one horizontal curve.", f"{self.c.ref('phasing')} (S7.9)",
                         "Maintain a constant grade through the horizontal curve.", code="P6")

    def _phasing(self, curve, group, code, severity, title, detail, section, model):
        recommendation, fix = self.phase_fix(model, curve, group, allow_separation=code != "P1")
        self.add("Phasing", severity, min(curve["start"], group["start"]), max(curve["end"], group["end"]), title, detail,
                 f"{self.c.ref('phasing')} ({section})", recommendation, code=code)
        if fix:
            self.findings[-1]["fix"] = fix

    def phase_fix(self, model, curve, group, allow_separation=True):
        """Evaluate Chapter 7 corrections and recommend the least disruptive feasible one.

        Candidates: one end coincident by changing only the curve length
        (start or end on the horizontal curve's start or end), separating the
        curves by shortening, and both ends coincident by moving the PVI to the
        horizontal curve and matching its length.
        """
        speed = self.speed(curve["start"], curve["end"])
        i = curve["index"]
        pvi = curve["station"]
        rmin = self.c.min_radius(speed, self.emax) or 0.0
        sharp = rmin and group["radius"] < 1.5 * rmin
        candidates = []
        if not sharp:
            if pvi > group["start"]:
                candidates.append(("start of the vertical curve on the start of the horizontal curve", pvi, 2 * (pvi - group["start"])))
            if pvi < group["end"]:
                candidates.append(("end of the vertical curve on the end of the horizontal curve", pvi, 2 * (group["end"] - pvi)))
        margin = max((self.c.ssd(speed, 0.0) or 0.0) / 2.0, 30.0)
        if allow_separation and pvi < group["start"] - margin:
            candidates.append(("separate the curves (vertical curve ends half an SSD before the horizontal curve)", pvi, 2 * (group["start"] - margin - pvi)))
        if allow_separation and pvi > group["end"] + margin:
            candidates.append(("separate the curves (vertical curve starts half an SSD after the horizontal curve)", pvi, 2 * (pvi - group["end"] - margin)))
        candidates.append(("both ends coincident: PVI at the horizontal curve centre, length matched", group["mid"], None))
        evaluated = [self._evaluate(model, i, curve, group, label, station, length, speed) for label, station, length in candidates]
        evaluated = [e for e in evaluated if e is not None]
        if not evaluated:
            return ("Separate the curves: the horizontal curve lies beyond the neighbouring PVIs; "
                    "adjust the horizontal curve or the neighbouring vertical curves."), None
        feasible = sorted((e for e in evaluated if e["feasible"]), key=lambda e: e["max_change"])
        canonical = [e for e in evaluated if e["label"].startswith("both ends")]
        best = feasible[0] if feasible else (canonical[0] if canonical else min(evaluated, key=lambda e: (len(e["problems"]), e["max_change"])))
        text = (
            f"{best['label'][0].upper() + best['label'][1:]}: PVI {best['station']:.2f} (elev {best['elevation']:.3f}), L = {best['length']:.0f} m "
            f"(K {best['K']:.1f}); grades {best['g1']:+.3f}% / {best['g2']:+.3f}%; profile changes by up to {best['max_change']:.2f} m."
        )
        if best["problems"]:
            text += " Not achievable as is: " + "; ".join(best["problems"]) + "."
        others = [e for e in (feasible[1:] if feasible else []) if e is not best][:1]
        for other in others:
            text += f" Alternative — {other['label']}: PVI {other['station']:.2f}, L {other['length']:.0f} m (change {other['max_change']:.2f} m)."
        fix = {key: best[key] for key in ("station", "elevation", "length", "g1", "g2", "K", "max_change", "feasible")}
        fix.update(pvi_index=i, old_station=pvi, old_elevation=curve["elevation"], old_length=curve["length"], label=best["label"])
        return text, fix

    def _evaluate(self, model, i, curve, group, label, station, length, speed):
        trial = model.copy()
        p = trial.pvis[i]
        g_in, g_out = model.grade(i - 1, i), model.grade(i, i + 1)
        if abs(station - p["station"]) > 1e-6:
            p["elevation"] = p["elevation"] + (g_out if station > p["station"] else g_in) * (station - p["station"])
            p["station"] = station
        if not trial.pvis[i - 1]["station"] < station < trial.pvis[i + 1]["station"]:
            return None
        new_curve = trial.curves()[i - 1]
        needed, _k = self.required_length(new_curve, speed)
        if length is None:
            length = round_up(max(needed, min(group["length"], trial.room(i))), 5.0)
            if trial.room(i) >= needed:
                length = min(length, trial.room(i))
        if length <= 0:
            return None
        p["length"] = length
        problems = []
        if length < needed - 1e-6:
            problems.append(f"L {length:.0f} m is below the {needed:.0f} m required at {speed:g} km/h")
        if not trial.fits(i):
            problems.append("it would overlap a neighbouring vertical curve")
        new_curve = trial.curves()[i - 1]
        limits = self.c.max_grade(self.road_type, self.terrain)
        if self.max_grade_override:
            limits = (self.max_grade_override, self.max_grade_override)
        for grade in (new_curve["g1"], new_curve["g2"]):
            if limits and abs(grade) > limits[1] + 1e-9 and abs(grade) > max(abs(curve["g1"]), abs(curve["g2"])) + 1e-9:
                problems.append(f"grade {grade:+.2f}% exceeds {limits[1]:g}%")
        low, high = trial.pvis[i - 1]["station"], trial.pvis[i + 1]["station"]
        stations = [low + (high - low) * k / 200 for k in range(201)]
        change = max(abs(trial.elevation(s) - model.elevation(s)) for s in stations)
        return {
            "label": label,
            "station": p["station"],
            "elevation": p["elevation"],
            "length": length,
            "g1": new_curve["g1"],
            "g2": new_curve["g2"],
            "K": new_curve["K"] or 0.0,
            "max_change": change,
            "problems": problems,
            "feasible": not problems,
        }

    # ---- roadside ------------------------------------------------------
    def check_barriers(self, views):
        barrier = self.c.get("barrier") or {}
        if not barrier or not views:
            return
        flagged = []
        for view in views:
            design, ground = view.get("design"), view.get("ground")
            if not design or not ground:
                continue
            for side, daylight in (("left", view["daylight"][0]), ("right", view["daylight"][1])):
                if daylight is None:
                    continue
                points = [p for p in design if (p[0] <= 0 if side == "left" else p[0] >= 0)]
                if len(points) < 2:
                    continue
                points.sort(key=lambda p: abs(p[0]))
                outer = min(points, key=lambda p: abs(p[0] - daylight))
                # Hinge: outermost design point before the slope steepens (largest elevation drop starts).
                hinge = None
                for a, b in zip(points, points[1:]):
                    dx, dz = abs(b[0] - a[0]), a[1] - b[1]
                    if dx > 1e-6 and dz / dx > 0.1:
                        hinge = a
                        break
                if hinge is None:
                    continue
                height = hinge[1] - outer[1]
                run = abs(outer[0] - hinge[0])
                if height <= 0.5 or run <= 0:
                    continue
                ratio = run / height
                warrant = None
                if height >= barrier["warrant_height"] and ratio < barrier["warrant_slope"]:
                    warrant = "warranted"
                elif height >= barrier["warrant_height"] and ratio < barrier["judgement_slope"]:
                    warrant = "judgement"
                elif height >= barrier.get("consider_height", barrier["warrant_height"]) and ratio < barrier["warrant_slope"]:
                    warrant = "judgement"
                if warrant:
                    flagged.append((view["station"], side, warrant, height, ratio))
        for side in ("left", "right"):
            rows = sorted(f for f in flagged if f[1] == side)
            groups, current = [], []
            for row in rows:
                if current and row[0] - current[-1][0] > 60:
                    groups.append(current)
                    current = []
                current.append(row)
            if current:
                groups.append(current)
            for group in groups:
                speed = self.speed(group[0][0], group[-1][0])
                runout = (interpolate_dict(barrier.get("runout"), speed) if barrier.get("runout") else None)
                start, end = group[0][0], group[-1][0]
                length = max(end - start, 0.0)
                total = length + (2 * runout if runout else 0.0)
                total = max(total, barrier.get("min_length", 0.0))
                warranted = any(g[2] == "warranted" for g in group)
                worst = max(group, key=lambda g: g[3])
                recommendation = (
                    f"Provide a {side}-side safety barrier from {start - (runout or 0):.0f} to {end + (runout or 0):.0f} "
                    f"(≈ {total:.0f} m including {runout:.0f} m run-out each end)." if runout
                    else f"Provide a {side}-side safety barrier over {start:.0f}–{end:.0f} plus run-out per the standard (≥ {barrier.get('min_length', 30):g} m)."
                )
                self.add("Roadside safety", "High" if warranted else "Medium", start, end,
                         f"Embankment barrier {'warranted' if warranted else 'to be assessed'} ({side})",
                         f"Fill height up to {worst[3]:.1f} m with side slope 1:{worst[4]:.1f}.", self.c.ref("barrier"),
                         recommendation if warranted else "Assess against the barrier warrant chart; " + recommendation,
                         side=side, value=worst[3], required=barrier["warrant_height"], code="R1")

    def check_curve_sight(self, groups, views):
        """Horizontal sight offset on curves against cut slopes on the inside."""
        if not views:
            return
        height = float(self.c.get("sightline_height", 0.84))
        for group in groups:
            inside = [v for v in views if group["arc_start"] <= v["station"] <= group["arc_end"] and v.get("design")]
            if not inside or group["turn"] not in ("left", "right"):
                continue
            speed = self.speed(group["start"], group["end"])
            ssd = self.c.ssd(speed, 0.0)
            radius = group["radius"]
            if not ssd or ssd >= 2 * radius:
                continue
            lane = self.lane_width / 2.0
            r_lane = radius - lane
            hso = r_lane * (1 - math.cos(ssd / (2 * r_lane)))
            sign = 1.0 if group["turn"] == "right" else -1.0
            worst = None
            for view in inside:
                design = sorted(view["design"], key=lambda p: p[0])
                # Beyond the daylight point the natural ground is the obstruction;
                # ground inside the corridor footprint is excavated.
                beyond = [p for p in (view.get("ground") or []) if (p[0] > design[-1][0] if sign > 0 else p[0] < design[0][0])]
                line = design + beyond
                lane_z = _at(design, sign * lane)
                if lane_z is None:
                    continue
                blocking = [abs(o - sign * lane) for o, z in sorted(line, key=lambda p: abs(p[0]))
                            if (o - sign * lane) * sign > 0 and z > lane_z + height]
                if blocking:
                    clearance = min(blocking)
                    if clearance < hso - 1e-6 and (worst is None or clearance < worst[1]):
                        worst = (view["station"], clearance)
            if worst:
                self.add("Sight distance", "Medium", group["arc_start"], group["arc_end"],
                         f"Cut slope limits sight on curve R{radius:,.0f} ({group['turn']})",
                         f"Clearance {worst[1]:.1f} m from the inside lane centre at {worst[0]:.0f}; {hso:.1f} m needed for SSD {ssd:.0f} m.",
                         self.c.ref("ssd"), f"Bench or flatten the inside cut to clear {hso:.1f} m from the inside lane centre (offset {sign * (lane + hso):+.1f} m).",
                         side="right" if sign > 0 else "left", value=worst[1], required=hso, code="S2")

    def sorted_findings(self):
        return sorted(self.findings, key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), f["start"]))


def _at(points, offset):
    for (o0, z0), (o1, z1) in zip(points, points[1:]):
        if o0 <= offset <= o1 and o1 > o0:
            return z0 + (z1 - z0) * (offset - o0) / (o1 - o0)
    return None


def interpolate_dict(mapping, x):
    from .criteria import interpolate

    return interpolate(mapping, x)


class SpeedProfile:
    """Design speed along the alignment from (station, speed) steps, each valid from its station on.

    A single ``default`` speed overrides the steps. ``over`` returns the
    governing (highest) speed over a chainage range, so an element that
    spans a speed change is checked for the faster approach.
    """

    def __init__(self, speeds=(), default=None):
        self.default = default or None
        self.steps = [] if self.default else sorted((float(a), float(b)) for a, b in speeds)

    def __call__(self, station):
        if self.default:
            return self.default
        if not self.steps:
            return None
        value = self.steps[0][1]
        for start, speed in self.steps:
            if station >= start:
                value = speed
        return value

    def over(self, start, end):
        low, high = min(start, end), max(start, end)
        values = [self(low)] + [speed for station, speed in self.steps if low < station <= high]
        values = [v for v in values if v is not None]
        return max(values) if values else None

    def sections(self, start, end):
        """[(from, to, speed)] over [start, end], merging equal consecutive speeds."""
        if self.default or not self.steps:
            speed = self(start)
            return [(start, end, speed)] if speed is not None else []
        result = []
        cuts = [start] + [s for s, _v in self.steps if start < s < end] + [end]
        for a, b in zip(cuts, cuts[1:]):
            speed = self(a)
            if result and result[-1][2] == speed:
                result[-1] = (result[-1][0], b, speed)
            else:
                result.append((a, b, speed))
        return result


def speed_function(speeds, default):
    """speed_at(station) from (station, speed) pairs, each valid from its station on."""
    return SpeedProfile(speeds, default)


def _change(before, after, low, high):
    stations = [low + (high - low) * k / 200 for k in range(201)]
    return max(abs(after.elevation(s) - before.elevation(s)) for s in stations)


def recommended_profile(model, findings, max_change=1.0):
    """Apply feasible fixes whose profile change stays within ``max_change``.

    Larger corrections are left for redesign and flagged on the finding.
    """
    result = model.copy()
    applied = []
    for item in sorted((f for f in findings if f.get("fix") and f["fix"]["feasible"]), key=lambda f: f["fix"]["old_station"]):
        fix = item["fix"]
        if max_change is not None and fix["max_change"] > max_change + 1e-9:
            item["recommendation"] += f" Not applied to the recommended profile (change above {max_change:g} m: redesign this section)."
            continue
        index = fix["pvi_index"]
        p = result.pvis[index]
        if abs(p["station"] - fix["old_station"]) > 1e-6:
            continue  # an earlier fix already moved this PVI
        p.update(station=fix["station"], elevation=fix["elevation"], length=fix["length"])
        if result.fits(index):
            applied.append(item)
        else:
            p.update(station=fix["old_station"], elevation=fix["old_elevation"], length=fix["old_length"])
    for item in findings:
        if item["code"] in ("V1", "V2", "V0") and item.get("required"):
            curve = next((c for c in result.curves() if abs(c["start"] - item["start"]) < 1e-3 or abs(c["station"] - item["start"]) < 1e-3), None)
            if curve is None:
                continue
            p = result.pvis[curve["index"]]
            needed = item["required"] * curve["A"] if item["code"] == "V1" else item["required"]
            new_length = round_up(max(needed, p["length"]), 5.0)
            old = p["length"]
            before = result.copy()
            p["length"] = new_length
            change = _change(before, result, p["station"] - new_length, p["station"] + new_length)
            if not result.fits(curve["index"]):
                p["length"] = old
                item["recommendation"] += " It does not fit between the neighbouring curves: move or flatten them first."
            elif max_change is not None and change > max_change + 1e-9:
                p["length"] = old
                item["recommendation"] += f" Profile change {change:.2f} m exceeds {max_change:g} m: not applied (redesign)."
            else:
                item["recommendation"] += f" Applied in the recommended profile (change {change:.2f} m)."
                applied.append(item)
    return result, applied


def civil3d_profile_text(model, name=""):
    """Civil 3D 'Create Profile from File' text: station, elevation[, curve length]."""
    lines = []
    for i, p in enumerate(model.pvis):
        if 0 < i < len(model.pvis) - 1 and p["length"] > 0:
            lines.append(f"{p['station']:.3f} {p['elevation']:.3f} {p['length']:.3f}")
        else:
            lines.append(f"{p['station']:.3f} {p['elevation']:.3f}")
    return "\n".join(lines) + "\n"


def review_alignment(
    record,
    controls,
    criteria,
    speeds=(),
    design_speed=None,
    emax=None,
    road_type="rural",
    terrain="rolling",
    max_grade=None,
    superelevation=(),
    ground=None,
    section_views=(),
    sight_step=5.0,
    max_change=1.0,
):
    """Run every check for one alignment; returns a result dictionary."""
    from .design_report import horizontal_elements

    emax = emax or float(criteria.get("default_emax", 6))
    speed_at = speed_function(list(speeds), design_speed)
    if speed_at(record and float(record.get("sta_start") or 0.0)) is None:
        raise ValueError("No design speed: the file records none, so enter one.")
    review = Review(criteria, speed_at, emax, road_type, terrain, max_grade)
    elements = horizontal_elements(record)
    groups = curve_groups(elements)
    model = ProfileModel.from_controls(controls) if len(controls) >= 2 else None
    if model is not None:
        review.check_vertical(model)
        review.check_grades(model, ground)
        review.check_sight(model, sight_step)
        review.check_phasing(model, groups)
        review.check_combined_grade(model, superelevation)
    review.check_horizontal(elements, groups, superelevation)
    start = float(record.get("sta_start") or (elements[0]["start"] if elements else 0.0)) if record else 0.0
    end = elements[-1]["end"] if elements else (model.pvis[-1]["station"] if model is not None else start)
    speed_sections = review.check_speed_changes(start, end, groups)
    if section_views:
        review.check_barriers(section_views)
        review.check_curve_sight(groups, section_views)
    findings = review.sorted_findings()
    recommended, applied = (recommended_profile(model, findings, max_change) if model is not None else (None, []))
    return {
        "findings": findings,
        "groups": groups,
        "model": model,
        "recommended": recommended,
        "applied": applied,
        "speed_at": speed_at,
        "speed_sections": speed_sections,
        "emax": emax,
        "criteria": criteria,
    }


def parse_speed_ranges(text):
    """Parse '0-5000:60; 5000-:80' into [(start, speed), ...] sorted by start."""
    result = []
    for chunk in (text or "").replace(",", ";").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise ValueError(f"Speed range '{chunk}' must look like 0-5000:60")
        span, speed = chunk.rsplit(":", 1)
        start = span.split("-")[0].replace("+", "").strip() or "0"
        try:
            result.append((float(start), float(speed)))
        except ValueError as exc:
            raise ValueError(f"Speed range '{chunk}' is not numeric") from exc
    return sorted(result)
