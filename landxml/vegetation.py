"""Vegetation (grass clumps and shrubs) for 3D scenes: plant polygons on a jittered grid, keep them clear of roads and designs, size them.

Everything is plan geometry in the scene's horizontal units (the output CRS), so no unit is converted here, and
everything is deterministic: the same layers and settings give the same trees every run. Heights, crowns and the
clear distance are in the same horizontal units.
"""

from __future__ import annotations

import numpy as np

from .grading import _inside_many
from .tin_overlay import sample_tin

GRASS, SHRUB = 0, 1
PLANT_TRIANGLES = {GRASS: 8, SHRUB: 30}      # what one plant costs the viewer (``grassTemplate``, ``shrubTemplate`` in viewer.html)
STYLES = ("Grass and shrubs", "Grass", "Shrubs")
SHRUB_SHARE = 0.15                           # of the plants in the mixed style
AUTO_HEIGHT = {GRASS: 0.8, SHRUB: 1.8}       # metres, when no height is given
MAX_CELLS = 8_000_000        # most grid cells tested for one polygon


def ring_area(ring):
    """Absolute area of a closed or open ring."""
    ring = np.asarray(ring, float)[:, :2]
    x, y = ring[:, 0], ring[:, 1]
    return abs(float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))) / 2.0


def polygon_area(rings):
    """Area of a polygon given as exterior ring first, then its holes."""
    return max(ring_area(rings[0]) - sum(ring_area(hole) for hole in rings[1:]), 0.0)


def scatter(rings, spacing, seed=0, box=None):
    """One tree per ``spacing`` × ``spacing`` cell of a jittered grid, kept where the cell's tree falls inside the polygon
    (exterior ring first, then holes). ``box`` = (x0, y0, x1, y1) limits the grid to that window, so a polygon far
    larger than the scene costs only the scene's share. Returns an (n, 2) array."""
    if spacing <= 0:
        raise ValueError("The tree spacing must be positive.")
    exterior = np.asarray(rings[0], float)[:, :2]
    low, high = exterior.min(axis=0), exterior.max(axis=0)
    if box is not None:
        low, high = np.maximum(low, box[:2]), np.minimum(high, box[2:])
    if (high <= low).any():
        return np.empty((0, 2))
    columns = max(int(np.ceil((high[0] - low[0]) / spacing)), 1)
    rows = max(int(np.ceil((high[1] - low[1]) / spacing)), 1)
    if columns * rows > MAX_CELLS:         # a very wide polygon at a tiny spacing: coarsen rather than run out of memory
        spacing *= float(np.sqrt(columns * rows / MAX_CELLS))
        columns = max(int(np.ceil((high[0] - low[0]) / spacing)), 1)
        rows = max(int(np.ceil((high[1] - low[1]) / spacing)), 1)
    rng = np.random.default_rng(seed)
    gx, gy = np.meshgrid(np.arange(columns), np.arange(rows))
    count = columns * rows
    points = np.column_stack([low[0] + (gx.ravel() + rng.random(count)) * spacing,
                              low[1] + (gy.ravel() + rng.random(count)) * spacing])
    inside = np.zeros(count, bool)
    for ring in rings:                      # even-odd: the exterior in, each hole out
        inside ^= _inside_many(points, np.asarray(ring, float)[:, :2])
    return points[inside]


def clear_of(points, footprint, distance):
    """Mask of the points that are at least ``distance`` from the TIN ``footprint`` (xyz, faces or a TinIndex): not on it,
    and none of the probes on two rings round them (half the distance, and the distance) lie on it either."""
    points = np.asarray(points, float)[:, :2]
    if footprint is None or not len(points):
        return np.ones(len(points), bool)
    probes = [(0.0, 0.0)]
    if distance > 0:
        for radius in (distance / 2.0, distance):
            probes += [(radius * np.cos(a), radius * np.sin(a)) for a in np.arange(8) * np.pi / 4]
    clear = np.ones(len(points), bool)
    for dx, dy in probes:
        clear &= ~np.isfinite(sample_tin(footprint, points + [dx, dy]))
    return clear


def plant_kinds(count, style, seed=0):
    """The kind of each plant (GRASS or SHRUB) for a style index of ``STYLES``; the mix is random but stable."""
    if style == 1:
        return np.full(count, GRASS, np.uint8)
    if style == 2:
        return np.full(count, SHRUB, np.uint8)
    rng = np.random.default_rng(seed)
    return np.where(rng.random(count) < SHRUB_SHARE, SHRUB, GRASS).astype(np.uint8)


def limit_for(budget, style):
    """How many plants a quarter of the triangle budget pays for, given the style's mix."""
    share = {0: SHRUB_SHARE, 1: 0.0, 2: 1.0}[style]
    each = PLANT_TRIANGLES[GRASS] * (1 - share) + PLANT_TRIANGLES[SHRUB] * share
    return max(int(budget // 4 // each), 100)


def plant_sizes(kinds, spacing, height=0.0, metres=1.0, seed=0):
    """Heights and widths in the scene's horizontal units (``metres`` of them make a metre). Grass clumps are about as wide
    as the spacing so a planted area reads as cover; shrubs are a little wider than tall. ``height`` 0 is Auto."""
    kinds = np.asarray(kinds)
    rng = np.random.default_rng(seed)
    vary = 0.7 + 0.6 * rng.random(len(kinds))
    mean = np.where(kinds == SHRUB, AUTO_HEIGHT[SHRUB], AUTO_HEIGHT[GRASS]) / metres if height <= 0 else \
        np.where(kinds == SHRUB, height, height * AUTO_HEIGHT[GRASS] / AUTO_HEIGHT[SHRUB])
    heights = mean * vary
    clump = np.clip(spacing * 0.9, 0.8 / metres, 4.0 / metres) * (0.8 + 0.4 * rng.random(len(kinds)))
    widths = np.where(kinds == SHRUB, heights * 1.4 * (0.85 + 0.3 * rng.random(len(kinds))), clump)
    return heights, widths


def thin(count, limit, seed=0):
    """Indices of ``limit`` of ``count`` plants chosen at random (in order), so a cap thins a wood evenly."""
    if count <= limit:
        return np.arange(count)
    rng = np.random.default_rng(seed)
    return np.sort(rng.choice(count, size=limit, replace=False))
