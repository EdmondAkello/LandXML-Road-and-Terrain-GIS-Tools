"""Land-development design shapes on a ground grid (pure numpy, no QGIS).

Each shape takes the ground as a regular grid (``Grid``) and returns the design levels on the same grid, with
side slopes that run out until they meet the ground (exact daylighting on the grid): building platforms, basins,
open-pit benches, stockpiles and mounds, terraces and golf features (fairways, greens, tees, bunkers). Shapes can be
applied one after another. Slopes are horizontal per 1 vertical (2 = 1V:2H), lengths in the grid's units.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .grading import _inside_many


@dataclass
class Grid:
    """Levels ``z`` (rows, cols) at cell centres; row 0 is the north (top) row, like a raster."""
    z: np.ndarray
    x0: float           # easting of the left edge
    y0: float           # northing of the top edge
    cell: float

    @property
    def xy(self):
        rows, cols = self.z.shape
        xs = self.x0 + (np.arange(cols) + 0.5) * self.cell
        ys = self.y0 - (np.arange(rows) + 0.5) * self.cell
        gx, gy = np.meshgrid(xs, ys)
        return np.column_stack([gx.ravel(), gy.ravel()])

    def sample(self, points):
        """Bilinear levels at (n, 2) points (NaN outside the grid)."""
        points = np.asarray(points, float)
        c = (points[:, 0] - self.x0) / self.cell - 0.5
        r = (self.y0 - points[:, 1]) / self.cell - 0.5
        rows, cols = self.z.shape
        ok = (c >= -0.5) & (r >= -0.5) & (c <= cols - 0.5) & (r <= rows - 0.5)     # out to the grid's edge
        out = np.full(len(points), np.nan)
        c, r = np.clip(c[ok], 0, cols - 1), np.clip(r[ok], 0, rows - 1)          # the edge half-cell holds the edge value
        c0, r0 = np.minimum(np.floor(c).astype(int), cols - 2), np.minimum(np.floor(r).astype(int), rows - 2)
        fc, fr = c - c0, r - r0
        z = self.z
        out[ok] = ((z[r0, c0] * (1 - fc) + z[r0, c0 + 1] * fc) * (1 - fr) + (z[r0 + 1, c0] * (1 - fc) + z[r0 + 1, c0 + 1] * fc) * fr)
        return out

    def resampled(self, cell, box=None):
        """The grid at ``cell`` spacing (bilinear) over ``box`` (xmin, ymin, xmax, ymax), default the whole grid."""
        rows, cols = self.z.shape
        xmin, ymin, xmax, ymax = box or (self.x0, self.y0 - rows * self.cell, self.x0 + cols * self.cell, self.y0)
        n_cols, n_rows = max(int(round((xmax - xmin) / cell)), 2), max(int(round((ymax - ymin) / cell)), 2)
        grid = Grid(np.zeros((n_rows, n_cols)), xmin, ymax, cell)
        grid.z = self.sample(grid.xy).reshape(n_rows, n_cols)
        return grid

    def like(self, z):
        return Grid(np.asarray(z, float).reshape(self.z.shape), self.x0, self.y0, self.cell)

    def tin(self, mask=None):
        """(points (n, 3), faces (m, 3)) of the grid as a TIN; ``mask`` (rows, cols) keeps only those cells' corners."""
        rows, cols = self.z.shape
        xy = self.xy
        z = self.z.ravel()
        index = np.arange(rows * cols).reshape(rows, cols)
        a, b = index[:-1, :-1].ravel(), index[:-1, 1:].ravel()
        c, d = index[1:, :-1].ravel(), index[1:, 1:].ravel()
        faces = np.concatenate([np.column_stack([a, c, d]), np.column_stack([a, d, b])])
        valid = np.isfinite(z)
        keep = valid[faces].all(axis=1)
        if mask is not None:
            keep &= np.asarray(mask, bool).ravel()[faces].all(axis=1)
        faces = faces[keep]
        used = np.unique(faces)
        remap = np.full(len(z), -1)
        remap[used] = np.arange(len(used))
        return np.column_stack([xy[used], z[used]]), remap[faces]


def distance_to_ring(points, ring):
    """Distance from each (n, 2) point to the closed polygon ``ring`` (its edges), in chunks to bound memory."""
    points = np.asarray(points, float)
    ring = np.asarray(ring, float)[:, :2]
    if np.allclose(ring[0], ring[-1]):
        ring = ring[:-1]
    a, b = ring, np.roll(ring, -1, axis=0)
    ab = b - a
    length2 = np.maximum((ab ** 2).sum(axis=1), 1e-12)
    out = np.full(len(points), np.inf)
    step = max(1, 4_000_000 // max(len(ring), 1))
    for start in range(0, len(points), step):
        p = points[start:start + step, None, :]
        t = np.clip(((p - a) * ab).sum(axis=2) / length2, 0.0, 1.0)
        nearest = a + t[..., None] * ab
        out[start:start + step] = np.sqrt(((p - nearest) ** 2).sum(axis=2)).min(axis=1)
    return out


def signed_distance(grid, ring):
    """Distance to the footprint edge for every grid cell: negative inside, positive outside."""
    xy = grid.xy
    d = distance_to_ring(xy, ring)
    inside = _inside_many(xy, np.asarray(ring, float)[:, :2])
    return np.where(inside, -d, d).reshape(grid.z.shape)


def _batters(ground, level, outside, cut_slope, fill_slope):
    """Ground beyond a footprint at ``level``: cut faces rising and fill faces falling from the edge until they meet it."""
    up = level + outside / cut_slope
    down = level - outside / fill_slope
    return np.where(ground > level, np.minimum(ground, up), np.maximum(ground, down))


def platform(grid, ring, level, cut_slope=1.5, fill_slope=2.0):
    """A pad at ``level`` over the footprint, with batters daylighting onto the ground around it."""
    sd = signed_distance(grid, ring)
    design = _batters(grid.z, level, np.maximum(sd, 0.0), cut_slope, fill_slope)
    return grid.like(np.where(sd <= 0, level, design))


def basin(grid, ring, floor, side_slope=3.0):
    """An excavation: a floor at ``floor`` over the footprint and side slopes up to the ground (never filled)."""
    sd = signed_distance(grid, ring)
    walls = floor + np.maximum(sd, 0.0) / side_slope
    return grid.like(np.minimum(grid.z, np.where(sd <= 0, floor, walls)))


def pit(grid, ring, floor, bench_height=10.0, berm=8.0, face_slope=0.5, rim=None):
    """An open pit inside ``ring``: level benches ``bench_height`` apart with ``berm`` wide berms and faces at
    ``face_slope`` (H per V), from ``rim`` (default the lowest ground on the ring) down to ``floor``."""
    sd = signed_distance(grid, ring)
    inside = np.maximum(-sd, 0.0)
    if rim is None:
        rim = float(np.nanmin(grid.sample(np.asarray(ring, float)[:, :2])))
    face = bench_height * face_slope
    period = face + berm
    k = np.floor(inside / period)
    into = inside - k * period
    depth = k * bench_height + np.minimum(into / max(face, 1e-9), 1.0) * bench_height
    surface = np.maximum(rim - depth, floor)
    return grid.like(np.where(sd <= 0, np.minimum(grid.z, surface), grid.z))


def stockpile(grid, ring, top=None, slope=1.5, volume=None, bench=0.0, berm=0.0):
    """A stockpile or mound on the footprint: sides at ``slope`` (H per V; 1.5 is about the angle of repose of loose
    earth) from the toe to a flat top at ``top``, or to a peak where the footprint is too narrow. With ``volume``
    instead of ``top``, the top is found so the pile holds that volume above the ground. With ``bench`` (a lift
    height) and ``berm``, the sides are built in lifts with berms between them, as a waste dump is."""
    sd = signed_distance(grid, ring)
    inside = np.maximum(-sd, 0.0)
    toe = float(np.nanmean(grid.sample(np.asarray(ring, float)[:, :2])))
    if bench > 0 and berm > 0:
        face = bench * slope
        period = face + berm
        k = np.floor(inside / period)
        rise = k * bench + np.minimum((inside - k * period) / max(face, 1e-9), 1.0) * bench
    else:
        rise = inside / slope

    def build(level):
        pile = np.minimum(level, toe + rise)
        return np.where(sd <= 0, np.maximum(grid.z, pile), grid.z)

    if top is None:
        if volume is None:
            raise ValueError("Give the stockpile a top level or a volume.")
        low, high = toe, toe + float(rise.max()) + 1.0
        if (build(high) - grid.z).sum() * grid.cell ** 2 < volume:
            raise ValueError(f"The footprint cannot hold {volume:,.0f} at slope {slope:g}; enlarge it or steepen the sides.")
        for _ in range(60):
            mid = (low + high) / 2
            if (build(mid) - grid.z).sum() * grid.cell ** 2 < volume:
                low = mid
            else:
                high = mid
        top = high
    return grid.like(build(top))


def terraces(grid, ring, step=2.0, riser=0.3, smoothing=3):
    """Terraces following the contours inside ``ring``: flat benches every ``step`` of height with risers taking
    ``riser`` (a fraction) of each step, from the ground smoothed over ``smoothing`` cells; they blend into the ground
    over one step's width at the footprint edge."""
    z = _smooth(grid.z, smoothing)
    n = np.floor(z / step)
    frac = z / step - n
    rise = np.clip((frac - (1.0 - riser)) / max(riser, 1e-9), 0.0, 1.0)
    design = (n + rise) * step
    sd = signed_distance(grid, ring)
    blend = np.clip(-sd / max(step * 4, grid.cell), 0.0, 1.0)
    return grid.like(np.where(sd <= 0, design * blend + grid.z * (1 - blend), grid.z))


def smooth_area(grid, ring, window=40.0, blend=10.0):
    """Ground eased inside ``ring`` (a fairway): the ground averaged over ``window``, blended into it over ``blend``."""
    cells = max(int(round(window / grid.cell / 2)), 1)
    smooth = _smooth(grid.z, cells)
    sd = signed_distance(grid, ring)
    weight = np.clip(-sd / max(blend, grid.cell), 0.0, 1.0)
    return grid.like(np.where(sd <= 0, smooth * weight + grid.z * (1 - weight), grid.z))


def raised(grid, ring, height=0.6, slope=4.0):
    """A green or tee: the footprint raised ``height`` above its mean ground and kept level, sides at ``slope``."""
    level = float(np.nanmean(grid.sample(np.asarray(ring, float)[:, :2]))) + height
    return platform(grid, ring, level, slope, slope)


def bunker(grid, ring, depth=1.0, slope=2.0):
    """A bunker: a hollow ``depth`` below the lowest ground on its rim, sides at ``slope``."""
    floor = float(np.nanmin(grid.sample(np.asarray(ring, float)[:, :2]))) - depth
    sd = signed_distance(grid, ring)
    hollow = floor + np.maximum(sd + depth * slope, 0.0) / slope        # the rim at sd = 0, the floor depth * slope inside
    return grid.like(np.where(sd <= 0, np.minimum(grid.z, hollow), grid.z))


def volumes(ground, design):
    """(cut, fill) between two grids on the same cells: cut where the design is below the ground."""
    d = design.z - ground.z
    area = ground.cell ** 2
    return float(-np.nansum(np.minimum(d, 0.0)) * area), float(np.nansum(np.maximum(d, 0.0)) * area)


def _smooth(z, cells):
    """Box average over (2 cells + 1)² using cumulative sums (edges use the cells that exist)."""
    if cells <= 0:
        return np.array(z, float)
    z = np.asarray(z, float)
    rows, cols = z.shape
    pad = np.pad(z, cells, mode="edge")
    c = pad.cumsum(axis=0).cumsum(axis=1)
    c = np.pad(c, ((1, 0), (1, 0)))
    k = 2 * cells + 1
    total = c[k:k + rows, k:k + cols] - c[:rows, k:k + cols] - c[k:k + rows, :cols] + c[:rows, :cols]
    return total / (k * k)
