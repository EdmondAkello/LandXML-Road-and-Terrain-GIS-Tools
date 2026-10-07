"""Cut/fill analysis from LandXML profiles and corridor cross-sections.

Sign convention: ``depth = design - ground``; positive is fill (embankment),
negative is cut. Areas and volumes are in source units (square and cubic
source units); nothing is converted.

Three levels of evidence are supported and reported separately:

* centerline profile comparison (design ProfAlign vs existing ProfSurf):
  cut/fill depths, transitions and segment lengths;
* a level-section template estimate from those depths, which is a
  preliminary approximation that ignores ground cross-fall;
* corridor cross-sections (CrossSectSurf + DesignCrossSectSurf): section
  cut/fill areas, side-slope lengths, material shape areas, average-end-area
  volumes and a mass-haul ordinate.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
import math


# ---------------------------------------------------------------- profiles


def _overlaps(part_a, part_b, station_range=None):
    low = max(part_a[0][0], part_b[0][0])
    high = min(part_a[-1][0], part_b[-1][0])
    if station_range is not None:
        low = max(low, station_range[0])
        high = min(high, station_range[1])
    return (low, high) if high > low else None


def compare_profiles(design, ground, station_range=None):
    """Return covered runs of {station, design, ground, depth} rows.

    Every breakpoint of both profiles is kept, and exact zero crossings are
    inserted, so the depth series is exact for the piecewise-linear inputs.
    """
    runs = []
    for design_part in design.parts:
        for ground_part in ground.parts:
            window = _overlaps(design_part, ground_part, station_range)
            if window is None:
                continue
            low, high = window
            stations = {low, high}
            stations.update(s for s, _ in design_part if low < s < high)
            stations.update(s for s, _ in ground_part if low < s < high)
            rows = []
            for station in sorted(stations):
                z_design = design.elevation_at(station)
                z_ground = ground.elevation_at(station)
                if z_design is None or z_ground is None:
                    continue
                row = {
                    "station": station,
                    "design": z_design,
                    "ground": z_ground,
                    "depth": z_design - z_ground,
                }
                if rows:
                    previous = rows[-1]
                    d0, d1 = previous["depth"], row["depth"]
                    if d0 * d1 < 0:
                        t = d0 / (d0 - d1)
                        s = previous["station"] + t * (station - previous["station"])
                        z = previous["design"] + t * (z_design - previous["design"])
                        rows.append({"station": s, "design": z, "ground": z, "depth": 0.0})
                rows.append(row)
            if len(rows) >= 2:
                runs.append(rows)
    runs.sort(key=lambda run: run[0]["station"])
    return runs


def _interpolate(run, station):
    stations = [row["station"] for row in run]
    index = min(max(bisect_right(stations, station), 1), len(run) - 1)
    a, b = run[index - 1], run[index]
    span = b["station"] - a["station"]
    t = 0.0 if span <= 0 else (station - a["station"]) / span
    return {
        key: a[key] + t * (b[key] - a[key]) for key in ("design", "ground", "depth")
    } | {"station": station}


def regular_rows(runs, interval, include_ends=True):
    """Resample runs at whole multiples of the interval (plus run ends)."""
    if interval <= 0:
        raise ValueError("Interval must be positive")
    result = []
    for run in runs:
        low, high = run[0]["station"], run[-1]["station"]
        stations = []
        if include_ends:
            stations.append(low)
        index = math.ceil((low - 1e-9) / interval)
        while index * interval <= high + 1e-9:
            station = index * interval
            if not stations or abs(station - stations[-1]) > 1e-6:
                stations.append(station)
            index += 1
        if include_ends and abs(stations[-1] - high) > 1e-6:
            stations.append(high)
        result.extend(_interpolate(run, station) for station in stations)
    return result


def classify(depth, tolerance=0.0):
    if depth > tolerance:
        return "fill"
    if depth < -tolerance:
        return "cut"
    return "grade"


def cut_fill_segments(runs, tolerance=0.0, high_fill=3.0, deep_cut=3.0):
    """Group runs into cut/fill/on-grade segments and list cut↔fill transitions.

    Depths within ±tolerance are on grade. A transition is recorded whenever
    the side changes between cut and fill, even across an on-grade stretch,
    at the last exact zero crossing before the new side begins.
    """
    segments, transitions = [], []
    for run_index, run in enumerate(runs):
        current = None
        last_side = None
        last_zero = None
        for a, b in zip(run, run[1:]):
            if a["depth"] == 0.0:
                last_zero = a
            ds = b["station"] - a["station"]
            if ds <= 0:
                continue
            if max(abs(a["depth"]), abs(b["depth"])) <= tolerance:
                kind = "grade"
            else:
                kind = "fill" if a["depth"] + b["depth"] > 0 else "cut"
            if current is None or current["kind"] != kind:
                if current is not None:
                    segments.append(current)
                if kind != "grade":
                    if last_side is not None and last_side != kind:
                        where = last_zero or a
                        transitions.append(
                            {
                                "station": where["station"],
                                "elevation": where["design"],
                                "from_kind": last_side,
                                "to_kind": kind,
                                "label": f"{last_side}→{kind}",
                                "run": run_index,
                            }
                        )
                    last_side = kind
                current = {
                    "kind": kind,
                    "run": run_index,
                    "start": a["station"],
                    "end": b["station"],
                    "max_depth": 0.0,
                    "max_station": a["station"],
                    "area": 0.0,
                }
            current["end"] = b["station"]
            current["area"] += ds * (abs(a["depth"]) + abs(b["depth"])) / 2.0
            for row in (a, b):
                if abs(row["depth"]) > current["max_depth"]:
                    current["max_depth"] = abs(row["depth"])
                    current["max_station"] = row["station"]
        if current is not None:
            segments.append(current)
    for segment in segments:
        segment["length"] = segment["end"] - segment["start"]
        segment["mean_depth"] = segment["area"] / segment["length"] if segment["length"] > 0 else 0.0
        if segment["kind"] == "fill" and segment["max_depth"] >= high_fill:
            segment["severity"] = "high fill"
        elif segment["kind"] == "cut" and segment["max_depth"] >= deep_cut:
            segment["severity"] = "deep cut"
        else:
            segment["severity"] = ""
    return segments, transitions


def summarize_segments(segments):
    totals = {"cut": 0.0, "fill": 0.0, "grade": 0.0}
    for segment in segments:
        totals[segment["kind"]] += segment["length"]
    covered = sum(totals.values())
    transitions = 0
    previous = None
    for segment in segments:
        if segment["kind"] == "grade":
            continue
        if previous is not None and previous["run"] == segment["run"] and previous["kind"] != segment["kind"]:
            transitions += 1
        previous = segment
    return {
        "cut_length": totals["cut"],
        "fill_length": totals["fill"],
        "grade_length": totals["grade"],
        "covered_length": covered,
        "cut_percent": 100.0 * totals["cut"] / covered if covered else 0.0,
        "fill_percent": 100.0 * totals["fill"] / covered if covered else 0.0,
        "max_cut": max((s["max_depth"] for s in segments if s["kind"] == "cut"), default=0.0),
        "max_fill": max((s["max_depth"] for s in segments if s["kind"] == "fill"), default=0.0),
        "segments": len(segments),
        "transitions": transitions,
    }


def template_estimate(runs, formation_width, fill_slope, cut_slope):
    """Level-section earthworks estimate from centerline depths.

    Area for depth h is h·(W + s·h) with formation width W and side slope
    s (horizontal per 1 vertical). Each side slope has length h·√(1+s²).
    Ground cross-fall, pavement thickness and ditches are ignored.
    """
    if formation_width < 0 or fill_slope < 0 or cut_slope < 0:
        raise ValueError("Formation width and side slopes must not be negative")

    def section(depth):
        h = abs(depth)
        slope = fill_slope if depth > 0 else cut_slope
        return h * (formation_width + slope * h), h * math.sqrt(1.0 + slope * slope)

    totals = dict(cut_volume=0.0, fill_volume=0.0, fill_slope_area=0.0, cut_slope_area=0.0)
    for run in runs:
        for a, b in zip(run, run[1:]):
            ds = b["station"] - a["station"]
            if ds <= 0:
                continue
            area_a, slope_a = section(a["depth"])
            area_b, slope_b = section(b["depth"])
            kind = "fill" if a["depth"] + b["depth"] > 0 else "cut"
            totals[f"{kind}_volume"] += ds * (area_a + area_b) / 2.0
            totals[f"{kind}_slope_area"] += 2.0 * ds * (slope_a + slope_b) / 2.0
    return totals


# ---------------------------------------------------------- cross-sections


def _value(offsets, values, x, side):
    if side == "right":
        index = bisect_right(offsets, x)
        if index >= len(offsets):
            return values[-1]
        index = max(index, 1)
    else:
        index = bisect_left(offsets, x)
        if index <= 0:
            return values[0]
    o0, o1 = offsets[index - 1], offsets[index]
    if o1 - o0 <= 1e-12:
        return values[index] if side == "right" else values[index - 1]
    return values[index - 1] + (values[index] - values[index - 1]) * (x - o0) / (o1 - o0)


class SectionLine:
    """An offset/elevation polyline evaluated as a function of offset."""

    def __init__(self, points):
        points = sorted(points, key=lambda p: p[0])
        self.offsets = [p[0] for p in points]
        self.values = [p[1] for p in points]

    @property
    def low(self):
        return self.offsets[0]

    @property
    def high(self):
        return self.offsets[-1]

    def at(self, offset, side="right"):
        if offset < self.low - 1e-9 or offset > self.high + 1e-9:
            return None
        return _value(self.offsets, self.values, offset, side)


def section_areas(design, ground):
    """Return (cut area, fill area, low offset, high offset) over the common range."""
    design, ground = SectionLine(design), SectionLine(ground)
    low, high = max(design.low, ground.low), min(design.high, ground.high)
    if high <= low:
        return None
    xs = sorted({low, high} | {x for x in design.offsets + ground.offsets if low < x < high})
    cut = fill = 0.0
    for x0, x1 in zip(xs, xs[1:]):
        d0 = design.at(x0, "right") - ground.at(x0, "right")
        d1 = design.at(x1, "left") - ground.at(x1, "left")
        width = x1 - x0
        if d0 * d1 < 0:
            t = d0 / (d0 - d1)
            first, second = abs(d0) * t * width / 2.0, abs(d1) * (1 - t) * width / 2.0
            if d0 > 0:
                fill, cut = fill + first, cut + second
            else:
                cut, fill = cut + first, fill + second
        else:
            area = abs(d0 + d1) * width / 2.0
            if d0 + d1 > 0:
                fill += area
            else:
                cut += area
    return cut, fill, low, high


def _hinge_from_geometry(design, flat_ratio):
    line = SectionLine(design)
    center = min(range(len(line.offsets)), key=lambda i: abs(line.offsets[i]))
    left = line.low
    for i in range(center, 0, -1):
        dx = line.offsets[i] - line.offsets[i - 1]
        dz = abs(line.values[i] - line.values[i - 1])
        if dx <= 1e-9 or dz / dx >= 1.0 / flat_ratio:
            left = line.offsets[i]
            break
    right = line.high
    for i in range(center, len(line.offsets) - 1):
        dx = line.offsets[i + 1] - line.offsets[i]
        dz = abs(line.values[i + 1] - line.values[i])
        if dx <= 1e-9 or dz / dx >= 1.0 / flat_ratio:
            right = line.offsets[i]
            break
    return left, right


def side_slopes(design, ground, hinge_left=None, hinge_right=None, flat_ratio=10.0, steep_ratio=1.5):
    """Measure sloped lengths between the formation hinge and the daylight point.

    Segments flatter than 1:flat_ratio (V:H) are ignored (shoulders, ditch
    inverts, berms). A segment is a fill (embankment) slope where the design
    is above ground at its midpoint, otherwise a cut slope; without ground the
    outward direction decides. Slopes steeper than 1:steep_ratio are flagged
    as needing more than grassing (for example pitching or benching).
    """
    line = SectionLine(design)
    ground_line = SectionLine(ground) if ground else None
    if hinge_left is None or hinge_right is None:
        geometric_left, geometric_right = _hinge_from_geometry(design, flat_ratio)
        hinge_left = geometric_left if hinge_left is None else hinge_left
        hinge_right = geometric_right if hinge_right is None else hinge_right
    result = {}
    for side, low, high in (("left", line.low, hinge_left), ("right", hinge_right, line.high)):
        record = dict(fill=0.0, cut=0.0, steep_fill=0.0, steep_cut=0.0, hinge=high if side == "left" else low,
                      daylight=low if side == "left" else high)
        if high - low <= 1e-9:
            result[side] = record
            continue
        xs = sorted({low, high} | {x for x in line.offsets if low < x < high})
        for x0, x1 in zip(xs, xs[1:]):
            dx = x1 - x0
            if dx <= 1e-9:
                continue
            z0, z1 = line.at(x0, "right"), line.at(x1, "left")
            dz = z1 - z0
            if abs(dz) / dx < 1.0 / flat_ratio:
                continue
            mid = (x0 + x1) / 2.0
            z_mid = (z0 + z1) / 2.0
            g = ground_line.at(mid) if ground_line is not None else None
            if g is not None:
                kind = "fill" if z_mid >= g - 1e-6 else "cut"
            else:
                outward_drop = -dz if side == "right" else dz
                kind = "fill" if outward_drop > 0 else "cut"
            length = math.hypot(dx, dz)
            record[kind] += length
            if abs(dz) / dx > 1.0 / steep_ratio:
                record[f"steep_{kind}"] += length
        result[side] = record
    return result


def design_line(section, design_name, use_datum=False, grade_elevation=None):
    """Return the design offset/elevation polyline, optionally with the Datum.

    With ``use_datum`` the Datum links replace the design surface across
    their offset range (bottom of pavement), and the design surface is kept
    outside it (side slopes to daylight). Datum elevations are made absolute
    with the grade elevation when they are written relative to grade.
    """
    surface = section.surface(design_name)
    if surface is None:
        return None, False
    top = list(surface.points)
    if not use_datum or not section.datum_links or grade_elevation is None:
        return top, False
    datum = []
    for link in section.datum_links:
        for offset, elevation in link:
            # Relative values lie far closer to zero than to the grade.
            if abs(elevation - grade_elevation) >= abs(elevation):
                elevation = grade_elevation + elevation
            datum.append((offset, elevation))
    datum.sort(key=lambda p: p[0])
    low, high = datum[0][0], datum[-1][0]
    composite = [p for p in top if p[0] < low - 1e-9] + datum + [p for p in top if p[0] > high + 1e-9]
    return composite, True


def parse_station_ranges(text):
    """Parse '1200-1450; 2000-2100' into [(1200, 1450), (2000, 2100)]."""
    ranges = []
    for chunk in (text or "").replace(",", ";").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        pieces = [piece for piece in chunk.replace("to", "-").split("-") if piece.strip()]
        if len(pieces) != 2 or chunk.lstrip().startswith("-"):
            raise ValueError(f"Station range '{chunk}' must look like 1200-1450")
        try:
            low, high = sorted(float(piece.replace("+", "")) for piece in pieces)
        except ValueError as exc:
            raise ValueError(f"Station range '{chunk}' is not numeric") from exc
        ranges.append((low, high))
    return sorted(ranges)


def exclude_ranges(runs, ranges):
    """Cut runs at excluded station ranges (bridges, culverts, tie-ins)."""
    if not ranges:
        return runs
    result = []
    for run in runs:
        pieces = [(run[0]["station"], run[-1]["station"])]
        for low, high in ranges:
            next_pieces = []
            for a, b in pieces:
                if high <= a or low >= b:
                    next_pieces.append((a, b))
                    continue
                if low > a:
                    next_pieces.append((a, low))
                if high < b:
                    next_pieces.append((high, b))
            pieces = next_pieces
        for a, b in pieces:
            if b - a <= 1e-9:
                continue
            inner = [row for row in run if a < row["station"] < b]
            result.append([_interpolate(run, a)] + inner + [_interpolate(run, b)])
    return result


def in_ranges(station, ranges):
    return any(low <= station <= high for low, high in ranges)


def corridor_quantities(
    sections,
    ground_name,
    design_name,
    grade_at=None,
    use_datum=False,
    flat_ratio=10.0,
    steep_ratio=1.5,
    max_spacing=0.0,
    cut_factor=1.0,
    excluded=(),
):
    """Per-section areas and average-end-area volumes for one alignment.

    ``grade_at(station)`` returns the design grade elevation used to make
    relative Datum elevations absolute. ``cut_factor`` scales cut volume in
    the mass-haul ordinate (for example 0.9 for shrinkage). Intervals longer
    than ``max_spacing`` (when positive) are not integrated.
    """
    rows, skipped = [], []
    for section in sections:
        if in_ranges(section.station, excluded):
            skipped.append((section.station, "inside an excluded station range"))
            continue
        ground = section.surface(ground_name)
        grade = grade_at(section.station) if grade_at is not None else None
        design, datum_used = design_line(section, design_name, use_datum, grade)
        materials = {}
        for shape in section.shapes:
            materials[shape.name] = materials.get(shape.name, 0.0) + shape.area
        row = {
            "station": section.station,
            "name": section.name,
            "materials": materials,
            "has_earthworks": False,
            "datum_used": datum_used,
        }
        if ground is not None and design is not None:
            areas = section_areas(design, ground.points)
            if areas is not None:
                extent = section.shape_extent()
                # Grassed slopes are measured on the finished (top) surface.
                slopes = side_slopes(
                    section.surface(design_name).points,
                    ground.points,
                    extent[0] if extent else None,
                    extent[1] if extent else None,
                    flat_ratio,
                    steep_ratio,
                )
                cut, fill, low, high = areas
                row.update(
                    has_earthworks=True,
                    cut_area=cut,
                    fill_area=fill,
                    left_daylight=slopes["left"]["daylight"],
                    right_daylight=slopes["right"]["daylight"],
                    left_hinge=slopes["left"]["hinge"],
                    right_hinge=slopes["right"]["hinge"],
                    fill_slope_length=slopes["left"]["fill"] + slopes["right"]["fill"],
                    cut_slope_length=slopes["left"]["cut"] + slopes["right"]["cut"],
                    steep_fill_length=slopes["left"]["steep_fill"] + slopes["right"]["steep_fill"],
                    steep_cut_length=slopes["left"]["steep_cut"] + slopes["right"]["steep_cut"],
                    sides=slopes,
                    centre_depth=_centre_depth(design, ground.points),
                )
            else:
                skipped.append((section.station, "design and ground do not overlap"))
        else:
            skipped.append(
                (section.station, "no ground section" if ground is None else "no design section")
            )
        rows.append(row)

    intervals = []
    earth = [row for row in rows if row["has_earthworks"]]
    mass = 0.0
    for a, b in zip(earth, earth[1:]):
        ds = b["station"] - a["station"]
        if ds <= 0:
            continue
        if any(a["station"] < low and high < b["station"] for low, high in excluded):
            continue
        if max_spacing and ds > max_spacing + 1e-9:
            skipped.append((a["station"], f"gap of {ds:.2f} to next section exceeds the maximum spacing"))
            continue
        interval = {"start": a["station"], "end": b["station"], "length": ds}
        for key in ("cut", "fill"):
            interval[f"{key}_volume"] = ds * (a[f"{key}_area"] + b[f"{key}_area"]) / 2.0
        for key in ("fill_slope", "cut_slope", "steep_fill", "steep_cut"):
            interval[f"{key}_area"] = ds * (a[f"{key}_length"] + b[f"{key}_length"]) / 2.0
        mass += interval["cut_volume"] * cut_factor - interval["fill_volume"]
        interval["mass_ordinate"] = mass
        interval["sides"] = {
            side: {
                "hinge": (a["sides"][side]["hinge"], b["sides"][side]["hinge"]),
                "daylight": (a["sides"][side]["daylight"], b["sides"][side]["daylight"]),
                "fill_area": ds * (a["sides"][side]["fill"] + b["sides"][side]["fill"]) / 2.0,
                "cut_area": ds * (a["sides"][side]["cut"] + b["sides"][side]["cut"]) / 2.0,
                "steep_area": ds
                * (
                    a["sides"][side]["steep_fill"] + a["sides"][side]["steep_cut"]
                    + b["sides"][side]["steep_fill"] + b["sides"][side]["steep_cut"]
                )
                / 2.0,
            }
            for side in ("left", "right")
        }
        intervals.append(interval)

    material_names = []
    for row in rows:
        for name in row["materials"]:
            if name not in material_names:
                material_names.append(name)
    material_volumes = {name: 0.0 for name in material_names}
    material_rows = [row for row in rows if row["materials"]]
    for a, b in zip(material_rows, material_rows[1:]):
        ds = b["station"] - a["station"]
        if ds <= 0 or (max_spacing and ds > max_spacing + 1e-9):
            continue
        if any(a["station"] < low and high < b["station"] for low, high in excluded):
            continue
        for name in material_names:
            material_volumes[name] += ds * (a["materials"].get(name, 0.0) + b["materials"].get(name, 0.0)) / 2.0

    totals = {
        key: sum(interval[key] for interval in intervals)
        for key in (
            "cut_volume",
            "fill_volume",
            "fill_slope_area",
            "cut_slope_area",
            "steep_fill_area",
            "steep_cut_area",
        )
    }
    totals["net_volume"] = totals["fill_volume"] - totals["cut_volume"] * cut_factor
    totals["length"] = sum(interval["length"] for interval in intervals)
    totals["sections"] = len(rows)
    totals["earthwork_sections"] = len(earth)
    return {
        "rows": rows,
        "intervals": intervals,
        "materials": material_volumes,
        "totals": totals,
        "skipped": skipped,
    }


def _centre_depth(design, ground):
    d = SectionLine(design).at(0.0)
    g = SectionLine(ground).at(0.0)
    return None if d is None or g is None else d - g
