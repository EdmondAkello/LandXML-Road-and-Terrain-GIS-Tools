"""Exact depth (volume) surface from two elevation TINs (pure NumPy).

Civil 3D can export a TIN volume surface; OpenRoads and other packages
usually export only the design and the existing-ground surfaces. This module
builds the equivalent depth surface: every design triangle is clipped against
every existing triangle it overlaps, and on each intersection polygon both
surfaces are linear, so depth (design − existing) is linear too. The polygons
are fanned into triangles carrying the depth at their vertices, which the
exact prism integration in :mod:`landxml.grading` then measures without a grid.

Only the area covered by both surfaces is kept, as in a Civil 3D volume
surface.
"""

from __future__ import annotations

import numpy as np

CHUNK = 200_000


def _planes(xyz, faces):
    """(valid, px, py, q, boxes, ccw vertices) for z = px·x + py·y + q on each triangle."""
    a, b, c = xyz[faces[:, 0]], xyz[faces[:, 1]], xyz[faces[:, 2]]
    d1, d2 = b - a, c - a
    det = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    valid = np.abs(det) > 1e-12 * np.maximum(np.abs(d1[:, :2]).max(axis=1) * np.abs(d2[:, :2]).max(axis=1), 1e-300)
    with np.errstate(divide="ignore", invalid="ignore"):
        px = (d1[:, 2] * d2[:, 1] - d2[:, 2] * d1[:, 1]) / det
        py = (d2[:, 2] * d1[:, 0] - d1[:, 2] * d2[:, 0]) / det
    q = a[:, 2] - px * a[:, 0] - py * a[:, 1]
    corners = np.stack([a[:, :2], b[:, :2], c[:, :2]], axis=1)
    clockwise = det < 0
    corners[clockwise] = corners[clockwise][:, ::-1]
    boxes = np.concatenate([corners.min(axis=1), corners.max(axis=1)], axis=1)
    keep = np.flatnonzero(valid)
    return px[keep], py[keep], q[keep], boxes[keep], corners[keep]


def _area(corners):
    """Plan areas of (n, 3, ≥2) triangle corner arrays."""
    u, v = corners[:, 1, :2] - corners[:, 0, :2], corners[:, 2, :2] - corners[:, 0, :2]
    return 0.5 * np.abs(u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0])


def _cells(boxes, origin, cell):
    """Integer cell ranges (x0, y0, x1, y1) covered by each box."""
    low = np.floor((boxes[:, :2] - origin) / cell).astype(np.int64)
    high = np.floor((boxes[:, 2:] - origin) / cell).astype(np.int64)
    return low[:, 0], low[:, 1], high[:, 0], high[:, 1]


def _expand(owner, start, count):
    """Repeat ``owner`` and enumerate start, start + 1, ... for each of ``count`` entries."""
    rep_owner = np.repeat(owner, count)
    offset = np.arange(int(count.sum())) - np.repeat(np.cumsum(count) - count, count)
    return rep_owner, np.repeat(start, count) + offset


def _row_join(boxes_big, boxes_small, cell, origin, limit):
    """Yield (big, small) index arrays for small boxes in the cells under each big box.

    Small boxes (extent no larger than ``cell``) are indexed by cell with
    key = row * W + column, so each grid row under a big box is one
    searchsorted range.
    """
    width = np.int64(1) << 31
    sx0, sy0, sx1, sy1 = _cells(boxes_small, origin, cell)
    nx, ny = sx1 - sx0 + 1, sy1 - sy0 + 1
    owner, index = _expand(np.arange(len(boxes_small)), np.zeros(len(boxes_small), np.int64), nx * ny)
    cx = np.repeat(sx0, nx * ny) + index % np.repeat(nx, nx * ny)
    cy = np.repeat(sy0, nx * ny) + index // np.repeat(nx, nx * ny)
    keys = cy * width + cx
    order = np.argsort(keys, kind="stable")
    keys, owner = keys[order], owner[order]
    bx0, by0, bx1, by1 = _cells(boxes_big, origin, cell)
    big_row, row = _expand(np.arange(len(boxes_big)), by0, by1 - by0 + 1)
    lo = np.searchsorted(keys, row * width + bx0[big_row], "left")
    hits = np.searchsorted(keys, row * width + bx1[big_row], "right") - lo
    # Expand in pieces so a dense overlap never allocates everything at once.
    total = np.cumsum(hits)
    start = 0
    while start < len(hits):
        done = total[start - 1] if start else 0
        stop = max(int(np.searchsorted(total, done + limit, "right")), start + 1)
        big, position = _expand(big_row[start:stop], lo[start:stop], hits[start:stop])
        yield big, owner[position]
        start = stop


def candidate_pairs(boxes_a, boxes_b, limit=4_000_000):
    """Index pairs (i, j) whose bounding boxes overlap.

    Triangles are binned by size into levels. For each level of one set, its
    triangles are indexed on a grid as fine as they are, and every larger or
    equal triangle of the other set looks up the grid rows it spans, so the
    work follows the real overlaps even when a coarse surface meets a fine one.
    """
    if not len(boxes_a) or not len(boxes_b):
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    extent_a = (boxes_a[:, 2:] - boxes_a[:, :2]).max(axis=1)
    extent_b = (boxes_b[:, 2:] - boxes_b[:, :2]).max(axis=1)
    positive = np.concatenate([extent_a, extent_b])
    positive = positive[positive > 0]
    base = float(np.median(positive)) if len(positive) else 1.0
    level_a = np.maximum(0, np.ceil(np.log2(np.maximum(extent_a, 1e-300) / base))).astype(np.int64)
    level_b = np.maximum(0, np.ceil(np.log2(np.maximum(extent_b, 1e-300) / base))).astype(np.int64)
    origin = np.minimum(boxes_a[:, :2].min(axis=0), boxes_b[:, :2].min(axis=0))
    keys = []
    for small_boxes, small_level, big_boxes, big_level, a_is_small in (
        (boxes_a, level_a, boxes_b, level_b, True), (boxes_b, level_b, boxes_a, level_a, False)
    ):
        for level in np.unique(small_level):
            small = np.flatnonzero(small_level == level)
            # Equal levels are joined once, with A as the indexed (small) set.
            big = np.flatnonzero(big_level >= level if a_is_small else big_level > level)
            if not len(big):
                continue
            cell = base * 2.0 ** level * 1.0001
            for i, j in _row_join(big_boxes[big], small_boxes[small], cell, origin, limit):
                gb, gs = big[i], small[j]
                ia, ib = (gs, gb) if a_is_small else (gb, gs)
                a, b = boxes_a[ia], boxes_b[ib]
                overlap = (a[:, 0] <= b[:, 2]) & (b[:, 0] <= a[:, 2]) & (a[:, 1] <= b[:, 3]) & (b[:, 1] <= a[:, 3])
                keys.append(np.unique(ia[overlap] * len(boxes_b) + ib[overlap]))
    pair = np.unique(np.concatenate(keys)) if keys else np.zeros(0, np.int64)
    return pair // len(boxes_b), pair % len(boxes_b)


def _clip(poly, count, e0, e1):
    """Sutherland–Hodgman step: keep the part of each polygon left of its edge e0→e1."""
    n, m, _ = poly.shape
    out = np.zeros((n, m + 1, 2))
    filled = np.zeros(n, np.int64)
    rows = np.arange(n)
    ex, ey = (e1 - e0).T
    side = ex[:, None] * (poly[:, :, 1] - e0[:, 1:2]) - ey[:, None] * (poly[:, :, 0] - e0[:, 0:1])
    for i in range(m):
        valid = i < count
        j = np.where(count > 0, (i + 1) % np.maximum(count, 1), 0)
        cur, nxt = poly[:, i], poly[rows, j]
        sc, sn = side[:, i], side[rows, j]
        inside, next_inside = sc >= 0, sn >= 0
        emit = valid & inside
        out[rows[emit], filled[emit]] = cur[emit]
        filled += emit
        crossing = valid & (inside != next_inside)
        with np.errstate(divide="ignore", invalid="ignore"):
            t = np.where(crossing, sc / (sc - sn), 0.0)
        point = cur + t[:, None] * (nxt - cur)
        out[rows[crossing], filled[crossing]] = point[crossing]
        filled += crossing
    return out, filled


def depth_tin(design, existing):
    """Depth surface (design − existing) on the area both TINs cover.

    ``design`` and ``existing`` are (vertices (n, 3), faces (m, 3)) in the same
    plan coordinates. Returns (xyz, faces, stats); stats gives the design plan
    area, the overlaid area and the number of triangles.
    """
    dxyz, dfaces = (np.asarray(design[0], float), np.asarray(design[1], np.int64))
    exyz, efaces = (np.asarray(existing[0], float), np.asarray(existing[1], np.int64))
    dpx, dpy, dq, dbox, dcorner = _planes(dxyz, dfaces)
    epx, epy, eq, ebox, ecorner = _planes(exyz, efaces)
    design_area = float(_area(dcorner).sum()) if len(dcorner) else 0.0
    first, second = candidate_pairs(dbox, ebox)
    pieces = []
    for start in range(0, len(first), CHUNK):
        i, j = first[start:start + CHUNK], second[start:start + CHUNK]
        poly = dcorner[i].copy()
        count = np.full(len(i), 3, np.int64)
        clip = ecorner[j]
        for k in range(3):
            poly, count = _clip(poly, count, clip[:, k], clip[:, (k + 1) % 3])
        for k in range(1, poly.shape[1] - 1):
            mask = count >= k + 2
            if not mask.any():
                continue
            tri = np.stack([poly[mask, 0], poly[mask, k], poly[mask, k + 1]], axis=1)
            area2 = (tri[:, 1, 0] - tri[:, 0, 0]) * (tri[:, 2, 1] - tri[:, 0, 1]) - (tri[:, 2, 0] - tri[:, 0, 0]) * (tri[:, 1, 1] - tri[:, 0, 1])
            good = np.abs(area2) > 1e-12
            if not good.any():
                continue
            tri = tri[good]
            ii, jj = i[mask][good], j[mask][good]
            x, y = tri[:, :, 0], tri[:, :, 1]
            depth = (dpx[ii, None] * x + dpy[ii, None] * y + dq[ii, None]) - (epx[jj, None] * x + epy[jj, None] * y + eq[jj, None])
            pieces.append(np.stack([x, y, depth], axis=2))
    if not pieces:
        raise ValueError("The design and existing-ground surfaces do not overlap.")
    triangles = np.concatenate(pieces).reshape(-1, 3)
    faces = np.arange(len(triangles), dtype=np.int64).reshape(-1, 3)
    corners = triangles.reshape(-1, 3, 3)
    overlay_area = float(_area(corners).sum())
    return triangles, faces, {"design_area": design_area, "overlay_area": overlay_area, "triangles": len(faces)}
