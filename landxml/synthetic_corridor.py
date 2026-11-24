"""A synthetic road corridor for feasibility-stage 3D scenes.

For an alignment that has a vertical profile but no designed corridor, a finished road is
built from a typical section: a cambered carriageway at the profile level, shoulders, and side
slopes in cut or fill that run out to the ground (existing surface or DEM). It is a concept for
early studies, not a design: no superelevation, widening, drainage or structures.
"""

from __future__ import annotations

import numpy as np

from .tin_overlay import sample_tin


def _normals(xy):
    tangent = np.gradient(xy, axis=0)
    tangent /= np.maximum(np.linalg.norm(tangent, axis=1), 1e-12)[:, None]
    return np.column_stack([tangent[:, 1], -tangent[:, 0]])          # right of the direction of travel


def synthetic_corridor(centre, stations, ground, carriageway=7.0, shoulder=1.5, camber=0.025, shoulder_fall=0.04,
                       cut_slope=2.0, fill_slope=2.0, max_width=60.0, step=0.5, vertical_factor=1.0):
    """(xyz, faces, station, offset, daylight) of a corridor surface along ``centre`` ((n, 3) at profile level).

    Slopes are horizontal:vertical (2 = 1V:2H). Each side runs out from the shoulder edge at the cut or
    fill slope until it meets ``ground`` (a TIN), or stops at ``max_width`` from the shoulder edge
    where it does not (or where the ground is missing). ``daylight`` is (n, 2): left and right widths."""
    centre = np.asarray(centre, float)
    stations = np.asarray(stations, float)
    half = carriageway / 2.0
    edge = half + shoulder
    edge_drop = (camber * half + shoulder_fall * shoulder) / vertical_factor
    normal = _normals(centre[:, :2])
    steps = np.arange(0.0, max_width + step, step)
    columns = []                      # per side: offsets and levels of the outer slope point
    daylight = np.zeros((len(centre), 2))
    for side_index, side in enumerate((-1.0, 1.0)):
        base = centre[:, :2] + normal * side * edge
        z_edge = centre[:, 2] - edge_drop
        ground_edge = sample_tin(ground, base)
        fill = ~(ground_edge > z_edge)            # unknown ground counts as fill: the slope runs to max width
        rate = np.where(fill, -1.0 / fill_slope, 1.0 / cut_slope) / vertical_factor
        points = (base[:, None, :] + normal[:, None, :] * side * steps[None, :, None]).reshape(-1, 2)
        ground_z = sample_tin(ground, points).reshape(len(centre), len(steps))
        slope_z = z_edge[:, None] + rate[:, None] * steps[None, :]
        gap = slope_z - ground_z
        sign = np.where(fill[:, None], gap <= 0, gap >= 0)        # slope has reached the ground
        sign &= np.isfinite(ground_z)
        hit = sign.any(axis=1)
        first = np.where(hit, sign.argmax(axis=1), len(steps) - 1)
        t = steps[first].copy()
        # interpolate between the last step short of the ground and the first past it
        prev = np.maximum(first - 1, 0)
        rows = np.arange(len(centre))
        g0, g1 = gap[rows, prev], gap[rows, first]
        ok = hit & (first > 0) & np.isfinite(g0) & np.isfinite(g1) & (g0 != g1)
        t[ok] = steps[prev[ok]] + step * g0[ok] / (g0[ok] - g1[ok])
        daylight[:, side_index] = t
        columns.append((edge + t, z_edge + rate * t))
    (left_off, left_z), (right_off, right_z) = columns
    offsets = np.column_stack([-left_off, np.full(len(centre), -edge), np.full(len(centre), -half), np.zeros(len(centre)),
                               np.full(len(centre), half), np.full(len(centre), edge), right_off])
    levels = np.column_stack([left_z, centre[:, 2] - edge_drop, centre[:, 2] - camber * half / vertical_factor, centre[:, 2],
                              centre[:, 2] - camber * half / vertical_factor, centre[:, 2] - edge_drop, right_z])
    plan = centre[:, None, :2] + normal[:, None, :] * offsets[:, :, None]
    xyz = np.column_stack([plan.reshape(-1, 2), levels.reshape(-1)])
    width = offsets.shape[1]
    index = np.arange(len(centre) * width).reshape(len(centre), width)
    a, b = index[:-1, :-1].ravel(), index[:-1, 1:].ravel()
    c, d = index[1:, :-1].ravel(), index[1:, 1:].ravel()
    faces = np.concatenate([np.column_stack([a, c, b]), np.column_stack([b, c, d])])
    station = np.repeat(stations, width)
    return xyz, faces, station, offsets.reshape(-1), daylight
