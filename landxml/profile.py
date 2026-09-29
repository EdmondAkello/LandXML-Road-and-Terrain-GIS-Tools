"""Vertical LandXML profile controls and source-unit sampling."""

from bisect import bisect_left
import math


def read_profile_controls(prof_align):
    """Return source PVI and parabolic curve controls with derived tangent grades."""
    controls = []
    for element in prof_align:
        tag = element.tag.split("}")[-1]
        if tag == "CircCurve":
            raise ValueError(
                "Circular vertical curve is unsupported; it cannot be substituted with a parabola."
            )
        if tag not in {"PVI", "ParaCurve"}:
            continue
        parts = (element.text or "").split()
        if len(parts) != 2:
            raise ValueError(f"{tag} requires station and elevation")
        try:
            station, elevation = map(float, parts)
            length = float(element.attrib["length"]) if tag == "ParaCurve" else None
        except (ValueError, KeyError) as exc:
            raise ValueError(
                f"{tag} has invalid station, elevation or curve length"
            ) from exc
        if (
            not math.isfinite(station)
            or not math.isfinite(elevation)
            or (length is not None and (not math.isfinite(length) or length <= 0))
        ):
            raise ValueError(f"{tag} has nonfinite or nonpositive geometry")
        controls.append(
            {
                "station": station,
                "elevation": elevation,
                "curve_length": length,
                "control_type": tag,
                "curve_type": None,
                "grade_in": None,
                "grade_out": None,
            }
        )
    controls.sort(key=lambda item: item["station"])
    for index, item in enumerate(controls):
        if index and item["station"] <= controls[index - 1]["station"]:
            raise ValueError("Profile control stations must be strictly increasing")
        if index:
            previous = controls[index - 1]
            item["grade_in"] = (item["elevation"] - previous["elevation"]) / (
                item["station"] - previous["station"]
            )
        if index + 1 < len(controls):
            following = controls[index + 1]
            item["grade_out"] = (following["elevation"] - item["elevation"]) / (
                following["station"] - item["station"]
            )
        if item["control_type"] == "ParaCurve":
            if item["grade_in"] is None or item["grade_out"] is None:
                raise ValueError(
                    "Parabolic curve requires preceding and following profile controls"
                )
            item["curve_type"] = (
                "crest"
                if item["grade_out"] < item["grade_in"]
                else "sag"
                if item["grade_out"] > item["grade_in"]
                else "flat"
            )
            half = item["curve_length"] / 2.0
            if (
                half > item["station"] - controls[index - 1]["station"]
                or half > controls[index + 1]["station"] - item["station"]
            ):
                raise ValueError(
                    "Parabolic curve extends beyond an adjacent profile control"
                )
    return controls


def profile_control_points(controls):
    """Expand parabolic curve PVIs into VPC, VPI and VPT point records."""
    points = []
    for control in controls:
        point = dict(control)
        point["source_control_type"] = control["control_type"]
        point["control_type"] = "VPI"
        point["k_value"] = None
        length = control["curve_length"]
        if length is None:
            points.append(point)
            continue

        grade_in = control["grade_in"]
        grade_out = control["grade_out"]
        half = length / 2.0
        grade_difference_percent = abs(grade_out - grade_in) * 100.0
        if grade_difference_percent > 1e-12:
            point["k_value"] = length / grade_difference_percent

        vpc = dict(point)
        vpc.update(
            control_type="VPC",
            station=control["station"] - half,
            elevation=control["elevation"] - grade_in * half,
            grade_out=None,
        )
        vpt = dict(point)
        vpt.update(
            control_type="VPT",
            station=control["station"] + half,
            elevation=control["elevation"] + grade_out * half,
            grade_in=None,
        )
        points.extend((vpc, point, vpt))
    return sorted(points, key=lambda item: item["station"])


class ProfileMapPlacement:
    """Place a schematic elevation offset beside a mapped alignment."""

    def __init__(
        self,
        source_points,
        map_points,
        alignment_start,
        elevation_datum,
        offset,
        exaggeration,
    ):
        if len(source_points) != len(map_points):
            raise ValueError("Source and mapped alignment vertices do not match")
        self.source = []
        self.mapped = []
        self.distance = []
        for source, mapped in zip(source_points, map_points):
            source_xy = (float(source[0]), float(source[1]))
            map_xy = (float(mapped[0]), float(mapped[1]))
            if not self.source:
                self.source.append(source_xy)
                self.mapped.append(map_xy)
                self.distance.append(0.0)
                continue
            length = math.dist(source_xy, self.source[-1])
            if length > 1e-9:
                self.source.append(source_xy)
                self.mapped.append(map_xy)
                self.distance.append(self.distance[-1] + length)
        if len(self.source) < 2:
            raise ValueError("Alignment needs at least two distinct vertices")
        self.alignment_start = float(alignment_start)
        self.elevation_datum = float(elevation_datum)
        self.offset = float(offset)
        self.exaggeration = float(exaggeration)

    @property
    def station_end(self):
        return self.alignment_start + self.distance[-1]

    def clipped_samples(self, samples, max_step=None):
        """Clip to alignment stations and densify tangents to follow map curves."""
        start, end = self.alignment_start, self.station_end
        result = []
        for (s0, e0), (s1, e1) in zip(samples, samples[1:]):
            low, high = max(s0, start), min(s1, end)
            if high < low or s1 <= s0:
                continue
            steps = (
                max(1, math.ceil((high - low) / max_step))
                if max_step is not None and max_step > 0
                else 1
            )
            for i in range(steps + 1):
                station = low + (high - low) * i / steps
                if result and abs(result[-1][0] - station) < 1e-9:
                    continue
                fraction = (station - s0) / (s1 - s0)
                result.append((station, e0 + fraction * (e1 - e0)))
        return result

    def point(self, station, elevation):
        """Return schematic map XY, or None outside the alignment station range."""
        distance = float(station) - self.alignment_start
        if distance < -1e-8 or distance > self.distance[-1] + 1e-8:
            return None
        distance = min(max(distance, 0.0), self.distance[-1])
        index = min(max(1, bisect_left(self.distance, distance)), len(self.source) - 1)
        source_length = self.distance[index] - self.distance[index - 1]
        fraction = (distance - self.distance[index - 1]) / source_length
        x0, y0 = self.mapped[index - 1]
        x1, y1 = self.mapped[index]
        dx, dy = x1 - x0, y1 - y0
        map_length = math.hypot(dx, dy)
        if map_length <= 1e-12:
            raise ValueError("Mapped alignment segment has no usable direction")
        local_scale = map_length / source_length
        lateral = (
            self.offset + (float(elevation) - self.elevation_datum) * self.exaggeration
        ) * local_scale
        return (
            x0 + fraction * dx - dy / map_length * lateral,
            y0 + fraction * dy + dx / map_length * lateral,
        )


def read_vertical_profile(prof_align, sample_interval=5.0):
    """Sample a LandXML <ProfAlign> vertical profile into (station, elevation) points.

    Real Civil 3D/LandXML exports do NOT wrap the profile in a <ProfileGeom>
    element with <Line>/<Curve> children carrying staStart/staEnd/elevStart/
    elevEnd attributes -- that structure does not exist in the LandXML 1.2
    schema and never matched real exports. The actual profile is a flat,
    ordered sequence of <PVI> (tangent vertex; text is "station elevation")
    and <ParaCurve>/<CircCurve> (vertical curve; text is the curve's own PVI
    station/elevation, i.e. where the incoming and outgoing tangent grades
    would otherwise intersect; the `length` attribute is the curve's
    horizontal length, symmetric about that station -- length/2 before and
    length/2 after) elements, directly under <ProfAlign>.

    Grades for a curve are derived from its immediate neighbours in the
    sequence and the standard AASHTO symmetric parabolic vertical curve
    formula is used to sample through the curve; PVI vertices and the
    straight tangents between control points are otherwise exact.
    """
    ctrl = [
        (item["station"], item["elevation"], item["curve_length"])
        for item in read_profile_controls(prof_align)
    ]
    if not ctrl:
        return []
    ctrl.sort(key=lambda c: c[0])

    samples = []
    n = len(ctrl)
    for i, (sta, elev, length) in enumerate(ctrl):
        if not length:
            samples.append((sta, elev))
            continue
        prev_sta, prev_elev, _ = ctrl[i - 1] if i > 0 else (sta, elev, None)
        next_sta, next_elev, _ = ctrl[i + 1] if i < n - 1 else (sta, elev, None)
        g_in = (elev - prev_elev) / (sta - prev_sta) if sta != prev_sta else 0.0
        g_out = (next_elev - elev) / (next_sta - sta) if next_sta != sta else 0.0
        half = length / 2.0
        bvc_sta = sta - half
        bvc_elev = elev - g_in * half
        count = max(2, int(math.ceil(length / max(sample_interval, 0.01))) + 1)
        for k in range(count):
            x = length * k / (count - 1)
            e = bvc_elev + g_in * x + (g_out - g_in) / (2.0 * length) * x * x
            samples.append((bvc_sta + x, e))

    samples.sort(key=lambda s: s[0])
    dedup = []
    for s in samples:
        if dedup and abs(s[0] - dedup[-1][0]) < 1e-9:
            continue
        dedup.append(s)
    return dedup
