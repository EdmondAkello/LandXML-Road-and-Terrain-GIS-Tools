"""Horizontal LandXML geometry sampled in source coordinates."""

import math

from .common import child, descendants
from .parser import load_document


def _xy(node):
    if node is None or not (node.text or "").strip():
        raise ValueError("Missing coordinate text")
    vals = [float(v) for v in node.text.split()[:2]]
    if len(vals) != 2:
        raise ValueError("Expected two coordinates")
    return vals[0], vals[1]


def _float_attr(node, name, default=None):
    v = node.attrib.get(name)
    if v in (None, ""):
        return default
    return float(v)


def regular_station_distances(start_station, length, interval, include_end=False):
    """Return (distance from start, station) pairs on the station interval grid."""
    if interval <= 0:
        raise ValueError("Station interval must be positive")
    end_station = start_station + length
    tolerance = max(1e-8, interval * 1e-9)
    first = math.ceil((start_station - tolerance) / interval)
    last = math.floor((end_station + tolerance) / interval)
    result = []
    for index in range(first, last + 1):
        station = index * interval
        distance = station - start_station
        if -tolerance <= distance <= length + tolerance:
            result.append((min(max(distance, 0.0), length), station))
    if include_end and (
        not result or abs(result[-1][1] - end_station) > tolerance
    ):
        result.append((length, end_station))
    return result


def _spiral_points(spiral, segment_length=5.0):
    """Approximate a clothoid spiral from LandXML geometry.

    Uses Start->PI as the tangent direction at the start, linearly varying
    curvature between radiusStart and radiusEnd, and forces the final point
    to the LandXML End coordinate with a smooth positional correction.
    """
    spiral_type = spiral.attrib.get("spiType", "").lower()
    if spiral_type != "clothoid":
        raise ValueError(
            f"Spiral type '{spiral_type or 'undeclared'}' is not supported; only explicitly declared clothoids are sampled"
        )
    start = child(spiral, "Start")
    end = child(spiral, "End")
    pi = child(spiral, "PI")
    if start is None or end is None or pi is None:
        raise ValueError("Spiral requires Start, End and PI")

    sx, sy = _xy(start)
    ex, ey = _xy(end)
    px, py = _xy(pi)

    length = _float_attr(spiral, "length", None)
    if length is None or length <= 0:
        raise ValueError("Clothoid spiral requires a positive declared length")

    rs = spiral.attrib.get("radiusStart", "INF")
    re = spiral.attrib.get("radiusEnd", "INF")
    k0 = 0.0 if str(rs).upper() == "INF" else 1.0 / float(rs)
    k1 = 0.0 if str(re).upper() == "INF" else 1.0 / float(re)
    # Match the Civil 3D/LandXML rotation convention used by <Curve>.
    rotation = spiral.attrib.get("rot", "").lower()
    if rotation not in {"cw", "ccw"}:
        raise ValueError("Spiral has no valid rot='cw' or rot='ccw' direction")
    sign = 1.0 if rotation == "cw" else -1.0
    k0 *= sign
    k1 *= sign

    theta0 = math.atan2(py - sy, px - sx)
    n = max(8, int(math.ceil(length / max(segment_length, 0.001))) + 1)
    ds = length / (n - 1)

    pts = [(sx, sy)]
    x, y = sx, sy
    for i in range(1, n):
        sm = (i - 0.5) * ds
        theta_m = theta0 + k0 * sm + 0.5 * (k1 - k0) * sm * sm / length
        x += ds * math.cos(theta_m)
        y += ds * math.sin(theta_m)
        pts.append((x, y))

    # Apply only a small numerical correction to reach the source endpoint.
    dx, dy = ex - pts[-1][0], ey - pts[-1][1]
    if math.hypot(dx, dy) > max(0.1, 0.02 * length):
        raise ValueError(
            "Clothoid parameters do not match the declared endpoint; geometry was not forced into place"
        )
    if abs(dx) > 1e-12 or abs(dy) > 1e-12:
        corrected = []
        for i, (xx, yy) in enumerate(pts):
            t = i / (len(pts) - 1)
            w = t * t * (3.0 - 2.0 * t)  # smoothstep
            corrected.append((xx + dx * w, yy + dy * w))
        pts = corrected

    pts[0] = (sx, sy)
    pts[-1] = (ex, ey)
    return pts


def _curve_points(curve, segment_length):
    start = child(curve, "Start")
    end = child(curve, "End")
    center = child(curve, "Center")
    if start is None or end is None or center is None:
        raise ValueError("Curve requires Start, End and Center")
    sx, sy = _xy(start)
    ex, ey = _xy(end)
    cx, cy = _xy(center)
    r = _float_attr(curve, "radius", None)
    if r is None or r <= 0:
        r = math.hypot(sx - cx, sy - cy)
    a0 = math.atan2(sy - cy, sx - cx)
    a1 = math.atan2(ey - cy, ex - cx)
    rot = curve.attrib.get("rot", "").lower()
    if rot not in {"cw", "ccw"}:
        raise ValueError("Curve has no valid rot='cw' or rot='ccw' direction")
    # LandXML/Civil 3D uses the opposite sign convention to mathematical
    # XY angles for the `rot` attribute in these exported alignments:
    # CW -> increasing mathematical angle; CCW -> decreasing.
    da = (a1 - a0) % (2 * math.pi) if rot == "cw" else -((a0 - a1) % (2 * math.pi))
    declared_length = _float_attr(curve, "length")
    if declared_length is not None and abs(abs(da) * r - declared_length) > max(
        0.01, declared_length * 0.01
    ):
        raise ValueError(
            "Curve endpoints, radius, rotation and declared length disagree"
        )
    a1 = a0 + da
    length = abs(da) * r
    n = max(2, int(math.ceil(length / max(segment_length, 0.001))) + 1)
    pts = []
    for i in range(n):
        t = i / (n - 1)
        a = a0 + da * t
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    # Force exact endpoints to avoid floating-point drift.
    pts[0] = (sx, sy)
    pts[-1] = (ex, ey)
    return pts


def _line_points(line):
    return [_xy(child(line, "Start")), _xy(child(line, "End"))]


def read_alignments(path, segment_length=5.0, feedback=None, cancel=None):
    root = load_document(path).root
    alignments = list(descendants(root, "Alignment"))
    if not alignments:
        raise ValueError("No <Alignment> elements were found in the LandXML file.")

    results = []
    unsupported = []
    for ai, a in enumerate(alignments):
        if cancel and cancel():
            raise RuntimeError("Extraction cancelled by user.")
        name = a.attrib.get("name", f"Alignment {ai + 1}")
        desc = a.attrib.get("desc", "")
        length = a.attrib.get("length", "")
        sta_start = a.attrib.get("staStart", a.attrib.get("startStation", ""))
        sta_end = a.attrib.get("staEnd", a.attrib.get("endStation", ""))
        cg = child(a, "CoordGeom")
        if cg is None:
            unsupported.append(f"{name}: no CoordGeom")
            continue
        pts = []
        segments = []
        complete = True
        for geom in list(cg):
            tag = geom.tag.split("}")[-1]
            try:
                if tag == "Line":
                    p = _line_points(geom)
                elif tag == "Curve":
                    p = _curve_points(geom, segment_length)
                elif tag == "Spiral":
                    p = _spiral_points(geom, segment_length)
                else:
                    complete = False
                    unsupported.append(f"{name}: unsupported CoordGeom element {tag}")
                    continue
                segments.append(
                    {
                        "geometry_type": tag.lower(),
                        "points": p,
                        "radius": _float_attr(geom, "radius")
                        if tag == "Curve"
                        else None,
                        "curve_length": _float_attr(geom, "length")
                        if tag == "Curve"
                        else None,
                        "spiral_length": _float_attr(geom, "length")
                        if tag == "Spiral"
                        else None,
                        "direction": geom.attrib.get("rot")
                        if tag in {"Curve", "Spiral"}
                        else None,
                        "station_start": _float_attr(geom, "staStart"),
                    }
                )
                if pts and p:
                    # Avoid duplicate vertices at element junctions.
                    if math.isclose(pts[-1][0], p[0][0], abs_tol=1e-9) and math.isclose(
                        pts[-1][1], p[0][1], abs_tol=1e-9
                    ):
                        pts.extend(p[1:])
                    else:
                        complete = False
                        unsupported.append(
                            f"{name}: {tag} is disconnected from preceding geometry"
                        )
                else:
                    pts.extend(p)
            except Exception as exc:
                complete = False
                unsupported.append(f"{name}: {tag} skipped ({exc})")

        if complete and len(pts) >= 2:
            results.append(
                {
                    "name": name,
                    "desc": desc,
                    "length": length,
                    "sta_start": sta_start,
                    "sta_end": sta_end,
                    "points": pts,
                    "segments": segments,
                }
            )

        if feedback and (ai % 10 == 0 or ai == len(alignments) - 1):
            feedback.setProgress(int(80 * (ai + 1) / len(alignments)))
            feedback.setProgressText(
                f"Extracting alignments: {ai + 1}/{len(alignments)}"
            )
    return results, unsupported
