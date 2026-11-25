"""DEM to TIN: an adaptive, crack-free triangulation of a raster grid (pure numpy, no QGIS).

A restricted quadtree over the grid's nodes (cell centres), built a level at a time with array operations:

* every square of every level gets the largest vertical miss of its two triangles (diagonal top-left to bottom-right)
  at the nodes it covers, made monotone (a square's error is at least its children's), so the leaves for a tolerance
  are the squares within it whose parent is not; a square with a node without data is split, and one with no data at
  all is dropped (holes stay holes);
* neighbouring leaves are balanced to differ by at most one level, and a leaf with a finer neighbour is fanned from its
  centre to the side midpoints that neighbour shares, so no triangle edge ends on another's side (no cracks);
* a fanned leaf whose fan misses a node by more than the tolerance is split as well, so the tolerance holds at every
  node of the grid.

Every TIN vertex is a grid node with its own level: nothing is interpolated or smoothed. With a triangle limit the
tolerance is raised (found by bisection) until the TIN fits.
"""

from __future__ import annotations

import numpy as np

TOP = 512                      # the largest square, in cells
EMPTY = 100                    # the level map's mark for cells without triangles
CHUNK = 4_000_000              # array elements handled at once


def _windows(zp, s):
    """(n_rows, n_cols, s + 1, s + 1) view of the level-s squares' nodes."""
    return np.lib.stride_tricks.sliding_window_view(zp, (s + 1, s + 1))[::s, ::s]


def _level_errors(zp, s):
    """The level-s squares' two-triangle miss: finite with full data, inf with partial data, nan with none."""
    view = _windows(zp, s)
    rows, cols = view.shape[:2]
    out = np.empty((rows, cols), np.float32)
    i = (np.arange(s + 1, dtype=np.float32) / s)[:, None]
    j = (np.arange(s + 1, dtype=np.float32) / s)[None, :]
    upper = j >= i
    step = max(1, CHUNK // max(cols * (s + 1) ** 2, 1))
    for r in range(0, rows, step):
        block = view[r:r + step]
        z00, z01 = block[..., :1, :1], block[..., :1, -1:]
        z10, z11 = block[..., -1:, :1], block[..., -1:, -1:]
        plane = np.where(upper, z00 + (z01 - z00) * j + (z11 - z01) * i, z00 + (z10 - z00) * i + (z11 - z10) * j)
        with np.errstate(invalid="ignore"):
            miss = np.abs(block - plane).max(axis=(-2, -1))
        finite = np.isfinite(block)
        some, every = finite.any(axis=(-2, -1)), finite.all(axis=(-2, -1))
        out[r:r + step] = np.where(every, miss, np.where(some, np.inf, np.nan))
    return out


class _Pyramid:
    def __init__(self, z):
        z = np.asarray(z, float)
        self.rows, self.cols = z.shape
        cells = max(self.rows - 1, self.cols - 1, 1)
        top = 1
        while top < min(cells, TOP):
            top *= 2
        self.top = top
        self.levels = top.bit_length() - 1
        self.cell_rows = -(-(self.rows - 1) // top) * top if self.rows > 1 else top
        self.cell_cols = -(-(self.cols - 1) // top) * top if self.cols > 1 else top
        zp = np.full((self.cell_rows + 1, self.cell_cols + 1), np.nan, np.float32)
        zp[:self.rows, :self.cols] = z
        self.zp = zp
        finite = np.isfinite(zp)
        valid = finite[:-1, :-1] & finite[1:, :-1] & finite[:-1, 1:] & finite[1:, 1:]
        monotone = {0: np.where(valid, np.float32(0), np.float32(np.nan))}
        for level in range(1, self.levels + 1):
            s = 1 << level
            own = _level_errors(zp, s)
            child = monotone[level - 1]
            rows, cols = own.shape
            with np.errstate(invalid="ignore"):
                children = np.fmax.reduce(np.fmax.reduce(child.reshape(rows, 2, cols, 2), axis=3), axis=1)
            monotone[level] = np.where(np.isnan(own), np.nan, np.fmax(own, children)).astype(np.float32)
        self.error = monotone

    def leaves(self, tolerance):
        """{level: (rows, cols) bool} of the leaves for a tolerance, before balancing."""
        out = {}
        split = None
        for level in range(self.levels, -1, -1):
            error = self.error[level]
            with np.errstate(invalid="ignore"):
                within, over = error <= tolerance, error > tolerance
            if split is not None:
                parent = np.repeat(np.repeat(split, 2, axis=0), 2, axis=1)
                within, over = within & parent, over & parent
            out[level] = within
            split = over
        return out

    def count(self, tolerance):
        return sum(int(mask.sum()) for mask in self.leaves(tolerance).values())


def _blocks(lev, level):
    s = 1 << level
    rows, cols = lev.shape
    return lev.reshape(rows // s, s, cols // s, s).transpose(0, 2, 1, 3)


def _split(lev, level, bi, bj):
    _blocks(lev, level)[bi, bj] = level - 1


def _balance(lev):
    """Split leaves until no neighbour across a side is more than one level finer."""
    while True:
        here = lev.astype(np.int16)
        near = np.full(here.shape, EMPTY, np.int16)
        np.minimum(near[1:], here[:-1], out=near[1:])
        np.minimum(near[:-1], here[1:], out=near[:-1])
        np.minimum(near[:, 1:], here[:, :-1], out=near[:, 1:])
        np.minimum(near[:, :-1], here[:, 1:], out=near[:, :-1])
        rr, cc = np.nonzero((near < here - 1) & (here != EMPTY))
        if not len(rr):
            return
        levels = here[rr, cc]
        for level in np.unique(levels):
            pick = levels == level
            keys = np.unique((rr[pick] >> level) * (lev.shape[1] >> level) + (cc[pick] >> level))
            _split(lev, int(level), keys // (lev.shape[1] >> level), keys % (lev.shape[1] >> level))


def _leaf_list(lev, level):
    s = 1 << level
    bi, bj = np.nonzero(_blocks(lev, level)[:, :, 0, 0] == level)
    r0, c0 = bi * s, bj * s
    rows, cols = lev.shape

    def finer(r, c, inside):
        out = np.zeros(len(r), bool)
        out[inside] = lev[r[inside], c[inside]] < level
        return out

    flags = np.column_stack([
        finer(r0 - 1, c0, r0 > 0),                     # top
        finer(r0, c0 + s, c0 + s < cols),              # right
        finer(r0 + s, c0, r0 + s < rows),              # bottom
        finer(r0, c0 - 1, c0 > 0),                     # left
    ]) if level else np.zeros((len(bi), 4), bool)
    return bi, bj, flags


def _fan_triangles(s):
    """Per side (top, right, bottom, left): its triangle(s) without and with the side's midpoint, as (row, col) corners."""
    h = s // 2
    C, TL, TR, BR, BL = (h, h), (0, 0), (0, s), (s, s), (s, 0)
    T, R, B, L = (0, h), (h, s), (s, h), (h, 0)
    return [
        ([(C, TR, TL)], [(C, T, TL), (C, TR, T)]),
        ([(C, BR, TR)], [(C, R, TR), (C, BR, R)]),
        ([(C, BL, BR)], [(C, B, BR), (C, BL, B)]),
        ([(C, TL, BL)], [(C, L, BL), (C, TL, L)]),
    ]


def _surface_weights(s, triangles):
    """(nodes inside the triangles, vertex (row, col) list, weights (3, s+1, s+1)) for a piecewise-planar patch."""
    i, j = np.mgrid[0:s + 1, 0:s + 1].astype(float)
    inside = np.zeros((s + 1, s + 1), bool)
    vertices, weights = [], []
    for a, b, c in triangles:
        det = (b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])
        wb = ((i - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (j - a[1])) / det
        wc = ((b[0] - a[0]) * (j - a[1]) - (i - a[0]) * (b[1] - a[1])) / det
        wa = 1 - wb - wc
        hit = (wa >= -1e-9) & (wb >= -1e-9) & (wc >= -1e-9) & ~inside
        inside |= hit
        vertices.append((a, b, c))
        weights.append(np.where(hit, 1.0, 0.0) * np.stack([wa, wb, wc]))
    return inside, vertices, weights


def _fan_misses(zp, level, bi, bj, flags):
    """The largest miss of each fanned leaf's actual triangles at its nodes."""
    s = 1 << level
    view = _windows(zp, s)
    sides = _fan_triangles(s)
    patches = [[_surface_weights(s, option) for option in side] for side in sides]
    out = np.zeros(len(bi), np.float32)
    step = max(1, CHUNK // (s + 1) ** 2)
    for start in range(0, len(bi), step):
        block = view[bi[start:start + step], bj[start:start + step]].astype(float)
        worst = np.zeros(len(block))
        for side, options in enumerate(patches):
            flag = flags[start:start + step, side]
            for choice, (inside, vertices, weights) in enumerate(options):
                rows = flag == bool(choice)
                if not rows.any():
                    continue
                sub = block[rows]
                surface = np.zeros_like(sub)
                for (a, b, c), w in zip(vertices, weights):
                    surface += (w[0] * sub[:, a[0], a[1]][:, None, None] + w[1] * sub[:, b[0], b[1]][:, None, None]
                                + w[2] * sub[:, c[0], c[1]][:, None, None])
                miss = np.where(inside, np.abs(sub - surface), 0).max(axis=(1, 2))
                worst[rows] = np.maximum(worst[rows], miss)
        out[start:start + step] = worst
    return out


def _level_map(pyramid, tolerance):
    lev = np.full((pyramid.cell_rows, pyramid.cell_cols), EMPTY, np.int8)
    for level, mask in pyramid.leaves(tolerance).items():
        _blocks(lev, level)[mask] = level
    _balance(lev)
    for _round in range(64):                                    # split fans that miss, until none does
        changed = False
        for level in range(1, pyramid.levels + 1):
            bi, bj, flags = _leaf_list(lev, level)
            fanned = flags.any(axis=1)
            if not fanned.any():
                continue
            miss = _fan_misses(pyramid.zp, level, bi[fanned], bj[fanned], flags[fanned])
            bad = miss > tolerance
            if bad.any():
                _split(lev, level, bi[fanned][bad], bj[fanned][bad])
                changed = True
        if not changed:
            break
        _balance(lev)
    return lev


def _triangulate(lev, pyramid):
    width = pyramid.cell_cols + 1
    faces = []
    for level in range(pyramid.levels + 1):
        bi, bj, flags = _leaf_list(lev, level)
        if not len(bi):
            continue
        s = 1 << level
        r0, c0 = bi * s, bj * s

        def node(dr, dc, pick):
            return (r0[pick] + dr) * width + c0[pick] + dc

        plain = ~flags.any(axis=1)
        if plain.any():
            tl, tr, br, bl = node(0, 0, plain), node(0, s, plain), node(s, s, plain), node(s, 0, plain)
            faces += [np.column_stack([tl, br, tr]), np.column_stack([tl, bl, br])]
        fan = ~plain
        if fan.any():
            for side, (without, with_mid) in enumerate(_fan_triangles(s)):
                for choice, triangles in ((False, without), (True, with_mid)):
                    pick = fan & (flags[:, side] == choice)
                    if pick.any():
                        for tri in triangles:
                            faces.append(np.column_stack([node(v[0], v[1], pick) for v in tri]))
    if not faces:
        return np.zeros((0, 2), int), np.zeros((0, 3), int)
    faces = np.concatenate(faces)
    used, inverse = np.unique(faces, return_inverse=True)
    nodes = np.column_stack([used // width, used % width]).astype(int)
    return nodes, inverse.reshape(-1, 3).astype(int)


def adaptive_tin(z, tolerance, max_triangles=None):
    """(nodes (n, 2) as (row, col) ints, faces (m, 3)) of a crack-free TIN of grid ``z`` within ``tolerance``.

    With ``max_triangles`` the TIN has at most that many triangles: the tolerance is raised (the worst-fitting areas
    keep their detail longest) until it fits, so it may not be met."""
    pyramid = _Pyramid(z)
    tolerance = max(float(tolerance), 0.0)
    if max_triangles:
        target = max_triangles / 2.6
        for _attempt in range(8):
            if pyramid.count(tolerance) > target:
                finite = [e[np.isfinite(e)] for e in pyramid.error.values()]
                low, high = tolerance, float(max(f.max() for f in finite if f.size)) if any(f.size for f in finite) else tolerance
                for _step in range(40):
                    middle = (low + high) / 2
                    if pyramid.count(middle) > target:
                        low = middle
                    else:
                        high = middle
                    if high - low <= 1e-4 * max(high, 1e-9):
                        break
                tolerance = high
            nodes, faces = _triangulate(_level_map(pyramid, tolerance), pyramid)
            if len(faces) <= max_triangles:
                return nodes, faces
            target *= 0.9 * max_triangles / len(faces)
        return nodes, faces
    return _triangulate(_level_map(pyramid, tolerance), pyramid)


def grid_tin(z, step=1):
    """(nodes, faces) of the regular TIN over every ``step``-th node with data (two triangles a cell)."""
    z = np.asarray(z, float)
    rr = np.arange(0, z.shape[0], step)
    cc = np.arange(0, z.shape[1], step)
    sub = z[np.ix_(rr, cc)]
    rows, cols = sub.shape
    index = np.arange(rows * cols).reshape(rows, cols)
    a, b = index[:-1, :-1].ravel(), index[:-1, 1:].ravel()
    c, d = index[1:, :-1].ravel(), index[1:, 1:].ravel()
    faces = np.concatenate([np.column_stack([a, d, b]), np.column_stack([a, c, d])])
    valid = np.isfinite(sub.ravel())
    faces = faces[valid[faces].all(axis=1)]
    used = np.unique(faces)
    remap = np.full(rows * cols, -1)
    remap[used] = np.arange(len(used))
    rc = np.column_stack([np.repeat(rr, cols), np.tile(cc, rows)])
    return rc[used], remap[faces]
