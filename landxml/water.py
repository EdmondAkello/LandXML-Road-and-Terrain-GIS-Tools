"""Strips along lines for 3D scenes: water ribbons and draped road surfaces (pure numpy, no QGIS)."""

from __future__ import annotations

import numpy as np

MAX_SEGMENTS = 20_000


def densify(points, step):
    """Points along a polyline no further apart than ``step`` (the original vertices are kept)."""
    pts = np.asarray(points, float)
    if len(pts) < 2:
        return pts
    if step <= 0:
        raise ValueError("The step must be positive.")
    out = [pts[0]]
    for a, b in zip(pts[:-1], pts[1:]):
        length = float(np.hypot(*(b - a)))
        pieces = max(int(np.ceil(length / step)), 1)
        for k in range(1, pieces + 1):
            out.append(a + (b - a) * (k / pieces))
    return np.asarray(out)


def orient_downhill(points, z):
    """(points, z, turned): the line turned round when its far end is lower than its start is not, so it runs downhill.

    Only the finite levels decide; a line with no ground under both ends is left as digitised."""
    z = np.asarray(z, float)
    good = np.flatnonzero(np.isfinite(z))
    if len(good) >= 2 and z[good[0]] < z[good[-1]]:
        return np.asarray(points)[::-1], z[::-1], True
    return np.asarray(points), z, False


def downstream_levels(z):
    """Water levels that never rise along the line (digitised downstream): a running minimum of the ground under it."""
    z = np.asarray(z, float)
    return np.minimum.accumulate(z) if len(z) else z


def ribbon(points, levels, width):
    """(xyz, faces) of a strip ``width`` wide centred on ``points``, flat across at each point's level.

    The sides are offset along the averaged normal of the two neighbouring segments, so bends stay joined and
    the strip keeps its width (a sharp bend is limited to twice the half width)."""
    pts = np.asarray(points, float)
    z = np.asarray(levels, float)
    if len(pts) < 2:
        return np.zeros((0, 3)), np.zeros((0, 3), np.int64)
    if width <= 0:
        raise ValueError("The width must be positive.")
    keep = np.r_[True, np.hypot(*np.diff(pts, axis=0).T) > 1e-9]
    pts, z = pts[keep], z[keep]
    if len(pts) < 2:
        return np.zeros((0, 3)), np.zeros((0, 3), np.int64)
    tangents = np.diff(pts, axis=0)
    tangents /= np.hypot(*tangents.T)[:, None]
    normals = np.column_stack([-tangents[:, 1], tangents[:, 0]])
    at = np.empty_like(pts)
    at[0], at[-1] = normals[0], normals[-1]          # the ends are offset square to their one segment
    bisector = normals[:-1] + normals[1:]            # its length is 2 cos(half the turn)
    size = np.hypot(*bisector.T)
    miter = np.minimum(2.0 / np.maximum(size, 1e-9), 2.0)       # 1 / cos(half the turn), limited on a sharp bend
    at[1:-1] = bisector / np.maximum(size, 1e-9)[:, None] * miter[:, None]
    half = width / 2.0
    left, right = pts + at * half, pts - at * half
    n = len(pts)
    xyz = np.vstack([np.column_stack([left, z]), np.column_stack([right, z])])
    faces = []
    for i in range(n - 1):
        faces += [(i, n + i, n + i + 1), (i, n + i + 1, i + 1)]
    return xyz, np.asarray(faces, np.int64)


def side_normals(points):
    """Unit left normals at each point of a plan polyline, mitred at the bends (limited to 2 on a sharp bend)."""
    pts = np.asarray(points, float)[:, :2]
    tangents = np.diff(pts, axis=0)
    tangents /= np.maximum(np.hypot(*tangents.T), 1e-12)[:, None]
    normals = np.column_stack([-tangents[:, 1], tangents[:, 0]])
    at = np.empty_like(pts)
    at[0], at[-1] = normals[0], normals[-1]
    bisector = normals[:-1] + normals[1:]
    size = np.hypot(*bisector.T)
    miter = np.minimum(2.0 / np.maximum(size, 1e-9), 2.0)
    at[1:-1] = bisector / np.maximum(size, 1e-9)[:, None] * miter[:, None]
    return at


def strip(points, offsets):
    """A grid along a plan polyline: (xy, station, offset, faces) with one column per offset (left positive).

    Vertices run point by point, the columns of each point together; consecutive duplicate points are dropped."""
    pts = np.asarray(points, float)[:, :2]
    keep = np.r_[True, np.hypot(*np.diff(pts, axis=0).T) > 1e-9]
    pts = pts[keep]
    offsets = np.asarray(offsets, float)
    if len(pts) < 2:
        return np.zeros((0, 2)), np.zeros(0), np.zeros(0), np.zeros((0, 3), np.int64)
    at = side_normals(pts)
    stations = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
    k = len(offsets)
    xy = (pts[:, None, :] + at[:, None, :] * offsets[None, :, None]).reshape(-1, 2)
    station = np.repeat(stations, k)
    offset = np.tile(offsets, len(pts))
    faces = []
    for i in range(len(pts) - 1):
        for j in range(k - 1):
            a, b = i * k + j, i * k + j + 1
            c, d = a + k, b + k
            faces += [(a, b, d), (a, d, c)]
    return xy, station, offset, np.asarray(faces, np.int64)
