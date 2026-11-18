"""Edge analysis of a graded site from its design and existing-ground TINs (no grid).

The input is a depth surface carrying design levels: vertices ``xyz`` with design
elevations and a ``depth`` (design minus existing, positive = fill) per vertex, as built
by ``tin_overlay.depth_tin(..., with_design=True)``. All heights are in the vertical
units; lengths and areas in the plan units of ``xyz``.

* **Footprint.** Triangles where design and existing ground differ by more than
  ``tolerance``. Its outline is walked edge by edge, so lengths are true plan lengths.
* **Perimeter edges.** Where the footprint edge stands more than ``tie`` above or
  below existing ground, the design stops short of the ground: a **retained edge**
  (wall or missing batter), its height the depth there, cut or fill by its sign.
  Where it ties in, the triangle behind the edge decides: a near-vertical face is a
  retained edge (Civil 3D walls are modelled so), a batter face a **batter**, anything
  flatter a natural **tie-in**. Their height is the design level at the edge minus the
  level of the nearest flat (platform) point: positive, the ground rises above the
  platform (cut); negative, it falls below (fill).
* **Internal steps.** Edge-connected groups of near-vertical or batter triangles that
  do not reach the footprint edge are walls and batters between platforms; each has
  its height (level range), plan length and location. A thin group's length comes from
  its outline: L = (P + sqrt(P^2 - 16 A)) / 4, exact for a rectangle of perimeter P and
  area A.
"""

from __future__ import annotations

import html
import math

import numpy as np

from .grading import BATTER_FROM, STEEP_FROM, connected_triangles, outline_polygons, triangle_slopes
from .plan_features import weld

DEFAULT_BANDS = (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0)
TYPES = (("retained", "Retained edge"), ("batter", "Batter"))
KINDS = ("cut", "fill")


def _band_label(height, bands):
    low = 0.0
    for high in bands:
        if height < high:
            return f"{low:g}–{high:g}"
        low = high
    return f"≥{low:g}"


def _nearest_level(points, targets, levels, budget=4_000_000):
    """Level of the nearest target (plan distance) for each point; NaN without targets."""
    if not len(targets):
        return np.full(len(points), np.nan)
    chunk = max(1, budget // len(targets))
    out = np.empty(len(points))
    for start in range(0, len(points), chunk):
        p = points[start:start + chunk, None, :]
        d = ((targets[None, :, :] - p) ** 2).sum(axis=2)
        out[start:start + chunk] = levels[np.argmin(d, axis=1)]
    return out


def _boundary_rings(xyz, faces):
    """Footprint outline as rings of (vertex a, vertex b, owner face) with the inside on the left."""
    a, b = xyz[faces[:, 1], :2] - xyz[faces[:, 0], :2], xyz[faces[:, 2], :2] - xyz[faces[:, 0], :2]
    cw = (a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]) < 0
    oriented = faces.copy()
    oriented[cw] = oriented[cw][:, [0, 2, 1]]
    starts = np.concatenate([oriented[:, 0], oriented[:, 1], oriented[:, 2]])
    ends = np.concatenate([oriented[:, 1], oriented[:, 2], oriented[:, 0]])
    owners = np.tile(np.arange(len(faces)), 3)
    span = int(faces.max()) + 1
    forward = starts * span + ends
    boundary = ~np.isin(forward, ends * span + starts)
    outgoing = {}
    for s, e, o in zip(starts[boundary].tolist(), ends[boundary].tolist(), owners[boundary].tolist()):
        outgoing.setdefault(s, []).append((e, o))
    rings = []
    while outgoing:
        start = next(iter(outgoing))
        ring, vertex = [], start
        while True:
            options = outgoing.get(vertex)
            if not options:
                break
            nxt, owner = options.pop()
            if not options:
                del outgoing[vertex]
            ring.append((vertex, nxt, owner))
            vertex = nxt
            if vertex == start:
                break
        if ring:
            rings.append(ring)
    return rings


def _strip_length(polygons):
    perimeter = area = 0.0
    for outer, holes in polygons:
        for ring in [outer, *holes]:
            closed = np.vstack([ring, ring[:1]])
            perimeter += float(np.hypot(*np.diff(closed, axis=0).T).sum())
            x, y = ring[:, 0], ring[:, 1]
            signed = 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))
            area += signed            # holes are clockwise, so they subtract
    return (perimeter + math.sqrt(max(perimeter * perimeter - 16.0 * abs(area), 0.0))) / 4.0, abs(area)


def edge_analysis(xyz, faces, depth, vertical_factor=1.0, tolerance=0.01, tie=0.1, flat=0.02,
                  bands=DEFAULT_BANDS, min_step=0.5, min_length=3.0):
    """Perimeter edge runs, internal steps and the schedule of a graded site (see module notes)."""
    xyz = np.asarray(xyz, float)
    faces = np.asarray(faces, np.int64)
    depth = np.asarray(depth, float)
    worked = np.abs(depth[faces]).max(axis=1) > tolerance
    if not worked.any():
        raise ValueError("The design does not differ from existing ground anywhere; there are no earthworks edges.")
    w_xyz, w_faces, first = weld(xyz, faces[worked])
    w_depth = depth[first]
    slope = triangle_slopes(w_xyz, w_faces, vertical_factor)
    steep, batter = slope >= STEEP_FROM, (slope >= BATTER_FROM) & (slope < STEEP_FROM)
    flat_faces = slope < flat
    # the nearest flat point to an edge lies on the edge of a flat area, so only those vertices are searched
    if flat_faces.any():
        sub = np.sort(np.concatenate([w_faces[flat_faces][:, [0, 1]], w_faces[flat_faces][:, [1, 2]],
                                      w_faces[flat_faces][:, [2, 0]]]), axis=1)
        unique, counts = np.unique(sub, axis=0, return_counts=True)
        flat_vertices = np.unique(unique[counts == 1])
    else:
        flat_vertices = np.array([], np.int64)
    targets, target_levels = w_xyz[flat_vertices, :2], w_xyz[flat_vertices, 2]

    runs, edges_on_boundary = [], set()
    for ring in _boundary_rings(w_xyz, w_faces):
        a = np.array([item[0] for item in ring])
        b = np.array([item[1] for item in ring])
        owner = np.array([item[2] for item in ring])
        edges_on_boundary.update(owner.tolist())
        da, db = w_depth[a], w_depth[b]
        signed = np.where(np.abs(da) >= np.abs(db), da, db)
        ground = np.abs(signed)
        middle = 0.5 * (w_xyz[a] + w_xyz[b])
        local = middle[:, 2] - _nearest_level(middle[:, :2], targets, target_levels)
        local = np.where(np.isfinite(local), local, 0.0)
        kind = np.where(ground > tie, np.where(signed > 0, "fill", "cut"), np.where(local > 0, "cut", "fill"))
        kind_type = np.where(ground > tie, "retained",
                             np.where(steep[owner], "retained", np.where(batter[owner], "batter", "tie-in")))
        height = np.where(ground > tie, ground, np.abs(local))
        length = np.hypot(*(w_xyz[b, :2] - w_xyz[a, :2]).T)
        labels = [_band_label(h, bands) for h in height]
        # runs of consecutive edges with the same type, side and height band
        keys = list(zip(kind_type.tolist(), kind.tolist(), labels))
        count = len(keys)
        start = 0
        if count > 1 and keys[0] == keys[-1] and len(set(keys)) > 1:
            start = next(k for k in range(count) if keys[k] != keys[0])   # do not split a run at the ring start
        order = [(start + k) % count for k in range(count)]
        current = []
        for k in order + [None]:
            if current and (k is None or keys[k] != keys[current[0]]):
                pts = [w_xyz[a[current[0]]]] + [w_xyz[b[j]] for j in current]
                runs.append({
                    "type": keys[current[0]][0], "kind": keys[current[0]][1], "band": keys[current[0]][2],
                    "length": float(length[current].sum()), "height_max": float(height[current].max()),
                    "height_mean": float(np.average(height[current], weights=np.maximum(length[current], 1e-12))),
                    "face_area": float((height[current] * length[current]).sum()),
                    "points": np.array(pts),
                })
                current = []
            if k is not None:
                current.append(k)

    steps = []
    for name, mask in (("wall", steep), ("batter", batter)):
        labels = connected_triangles(w_faces, mask)
        for label in range(int(labels.max()) + 1 if (labels >= 0).any() else 0):
            member = np.flatnonzero(labels == label)
            if edges_on_boundary.intersection(member.tolist()):
                continue                       # reaches the footprint edge: counted as a perimeter edge
            zs = w_xyz[np.unique(w_faces[member]), 2]
            height = float(zs.max() - zs.min())
            if height < min_step:
                continue
            polygons = outline_polygons(w_xyz, w_faces[member])
            length, area = _strip_length(polygons)
            if length < min_length:
                continue
            centre = w_xyz[w_faces[member]].reshape(-1, 3).mean(axis=0)
            steps.append({"type": name, "height": height, "top": float(zs.max()), "bottom": float(zs.min()),
                          "length": length, "area": area, "x": float(centre[0]), "y": float(centre[1]),
                          "polygons": polygons})
    steps.sort(key=lambda item: -item["height"] * item["length"])
    for number, item in enumerate(steps, 1):
        item["id"] = f"S{number}"
    return {"runs": runs, "steps": steps, "schedule": schedule(runs, bands), "bands": tuple(bands),
            "tie": tie, "tolerance": tolerance, "flat": flat,
            "tie_in_length": float(sum(run["length"] for run in runs if run["type"] == "tie-in")),
            "perimeter": float(sum(run["length"] for run in runs))}


def schedule(runs, bands=DEFAULT_BANDS):
    """Length by height band for each edge type and side, with totals and the face area over 1 unit high."""
    labels = [_band_label(low + 1e-9, bands) for low in (0.0, *bands)]
    out = {}
    for kind_type, _title in TYPES:
        for kind in KINDS:
            chosen = [run for run in runs if run["type"] == kind_type and run["kind"] == kind]
            if not chosen:
                continue
            over = [run for run in chosen if run["height_max"] >= 1.0]
            out[f"{kind_type}_{kind}"] = {
                "total": sum(run["length"] for run in chosen),
                "max": max(run["height_max"] for run in chosen),
                "bands": {label: sum(run["length"] for run in chosen if run["band"] == label) for label in labels},
                "over_1": sum(run["length"] for run in over),
                "face_over_1": sum(run["face_area"] for run in over),
            }
    return out


def edges_report_html(result, units, title_surfaces, source_file="", warnings=()):
    from .reports import _cards, _document, _table

    length, depth, area = units.length, units.depth, units.area
    sched = result["schedule"]
    rows = []
    for key, entry in sched.items():
        kind_type, kind = key.split("_")
        rows.append([dict(TYPES)[kind_type], kind, entry["total"], entry["max"], *entry["bands"].values(),
                     entry["over_1"], entry["face_over_1"]])
    band_names = list(next(iter(sched.values()))["bands"]) if sched else []
    retained = sum(entry["total"] for key, entry in sched.items() if key.startswith("retained"))
    batters = sum(entry["total"] for key, entry in sched.items() if key.startswith("batter"))
    body = [
        f"<p class='meta'>Source: {html.escape(source_file)} · {html.escape(title_surfaces)}</p>",
        "<p class='note'>Footprint: where design and existing ground differ by more than "
        f"{result['tolerance']:g} {depth}. A perimeter edge standing more than {result['tie']:g} {depth} above or below "
        "existing ground is a retained edge (wall or missing batter) with that height. Where it ties in, a near-vertical "
        "face behind it is a retained edge and a batter face a batter, with the height from the nearest flat (platform) "
        "level; cut means the ground rises above the platform. Lengths are plan lengths along the footprint edge. "
        "Internal steps are near-vertical or batter faces between platforms that do not reach the edge. Heights come "
        "from the model; confirm wall types and heights against the drawings.</p>",
        _cards([(f"Retained edges ({length})", f"{retained:,.1f}"), (f"Batters ({length})", f"{batters:,.1f}"),
                (f"Tie-ins ({length})", f"{result['tie_in_length']:,.1f}"), ("Internal steps", str(len(result["steps"])))]),
        "<h2>Perimeter schedule</h2>",
        _table(["Edge", "Side", f"Length ({length})", f"Max height ({depth})", *[f"{b} {depth}" for b in band_names],
                f"Over 1 {depth} ({length})", f"Face over 1 {depth} ({area})"], rows, 1) if rows
        else "<p>No retained edges or batters on the perimeter.</p>",
    ]
    if result["steps"]:
        body += ["<h2>Internal steps</h2>", _table(
            ["Step", "Type", f"Height ({depth})", f"Top ({depth})", f"Bottom ({depth})", f"Length ({length})", "X", "Y"],
            [[s["id"], s["type"], s["height"], s["top"], s["bottom"], s["length"], s["x"], s["y"]] for s in result["steps"]], 2)]
    if warnings:
        body.append("".join(f"<p class='note'>{html.escape(text)}</p>" for text in warnings))
    return _document("Grading edge analysis", "".join(body))


def wall_top(points, tolerance=0.15):
    """A drawn edge along the top of a wall: a footprint outline steps down and back up a near-vertical
    face at points that almost coincide in plan, which draws as a comb of ticks; such neighbours are
    merged into one point at their highest level. Only the drawing uses this, not the schedule."""
    points = np.asarray(points, float)
    if len(points) < 3:
        return points
    out = [points[0].copy()]
    for point in points[1:]:
        if np.hypot(*(point[:2] - out[-1][:2])) <= tolerance:
            if point[2] > out[-1][2]:
                out[-1] = point.copy()
        else:
            out.append(point.copy())
    return np.array(out)
