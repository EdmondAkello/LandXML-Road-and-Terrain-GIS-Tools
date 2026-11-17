"""Reference renderer for ``landxmltools-scene`` folders (Export 3D Scene), using PyVista.

    python tools/render_scene.py <scene folder> [--size 2400x1600] [--views sw,se] [--out <folder>]

Writes ``<scene folder>/render_<view>.png`` for each view. Headless Linux needs a display:
``xvfb-run -a -s "-screen 0 2600x1800x24" python tools/render_scene.py ...``. PyVista is a
developer dependency only; the plugin itself never imports it.

What it draws, all from scene.json: the surface meshes with their baked style colours (the
vertical exaggeration applied to z), lines lifted by their ``lift`` (roads with a
``properties.width`` as flat ribbons at profile level, never cut into the terrain), labels
above their points, a legend of the design surface style and each view's camera.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np


def _load(folder):
    with open(os.path.join(folder, "scene.json"), encoding="utf-8") as stream:
        scene = json.load(stream)
    if scene.get("format") != "landxmltools-scene":
        raise SystemExit(f"{folder} does not hold a landxmltools-scene")
    return scene


def _ribbon(pv, points, width, ve, lift):
    points = np.asarray(points, float)
    d = np.gradient(points[:, :2], axis=0)
    n = np.column_stack([-d[:, 1], d[:, 0]])
    n /= np.maximum(np.linalg.norm(n, axis=1), 1e-9)[:, None]
    left = np.column_stack([points[:, :2] + n * width / 2, (points[:, 2] + lift) * ve])
    right = np.column_stack([points[:, :2] - n * width / 2, (points[:, 2] + lift) * ve])
    vertices = np.vstack([left, right])
    k = len(points)
    faces = []
    for i in range(k - 1):
        faces += [3, i, i + 1, k + i, 3, i + 1, k + i + 1, k + i]
    return pv.PolyData(vertices, np.array(faces))


def render(folder, size=(2400, 1600), only=None, out=None):
    import pyvista as pv

    pv.OFF_SCREEN = True
    scene = _load(folder)
    ve = float(scene.get("vertical_exaggeration", 1.0))
    meshes = []
    for surface in scene["surfaces"]:
        mesh = pv.read(os.path.join(folder, surface["mesh"]))
        mesh.points[:, 2] = (mesh.points[:, 2] + float(surface.get("lift", 0.0))) * ve
        rgb = np.column_stack([mesh.point_data[k] for k in ("red", "green", "blue")]) if "red" in mesh.point_data \
            else mesh.point_data.get("RGB")
        meshes.append((surface, mesh, np.asarray(rgb, np.uint8)))
    paths = []
    for view in scene["views"]:
        if only and view["name"] not in only:
            continue
        plotter = pv.Plotter(off_screen=True, window_size=size, lighting="three lights")
        plotter.set_background(scene.get("background", "#ffffff"))
        for surface, mesh, rgb in meshes:
            plotter.add_mesh(mesh, scalars=rgb, rgb=True, smooth_shading=surface["role"] != "design", ambient=0.35,
                             diffuse=0.7, specular=0.0, opacity=float(surface.get("opacity", 1.0)))
        for line in scene["lines"]:
            points = np.asarray(line["points"], float)
            if len(points) < 2:
                continue
            width = (line.get("properties") or {}).get("width")
            if line["kind"] == "road" and width:
                ribbon = _ribbon(pv, points, float(width), ve, float(line.get("lift", 0.15)))
                plotter.add_mesh(ribbon, color="#8a8a8a", ambient=0.35, diffuse=0.7)
                continue
            lifted = np.column_stack([points[:, :2], (points[:, 2] + float(line.get("lift", 0.0))) * ve])
            plotter.add_mesh(pv.lines_from_points(lifted), color=line.get("color", "#333333"),
                             line_width=float(line.get("width", 2)))
        for label in scene["labels"]:
            x, y, z = label["position"]
            plotter.add_point_labels([[x, y, (z + float(label.get("lift", 3.0))) * ve]], [label["text"]], font_size=22,
                                     point_size=8, point_color="black", shape_color="#fffde7", text_color="black",
                                     shape_opacity=0.9, always_visible=True, render_points_as_spheres=True)
        design = next((s for s in scene["surfaces"] if s["role"] == "design"), None)
        if design and design.get("style"):
            classes = [c for c in design["style"]["classes"]]
            plotter.add_legend([[c["label"], c["color"]] for c in classes][::-1], bcolor="white", face="rectangle",
                               size=(0.13, 0.024 * len(classes)), loc="lower right", font_family="arial")
        target = np.asarray(view["target"], float)
        target[2] *= ve
        direction = np.asarray(view["direction"], float)
        direction /= np.linalg.norm(direction)
        plotter.camera.focal_point = target
        plotter.camera.position = target + direction * float(view["distance"])
        plotter.camera.up = (0, 0, 1)
        plotter.camera.view_angle = float(view.get("view_angle", 30.0))
        path = os.path.join(out or folder, f"render_{view['name']}.png")
        plotter.screenshot(path)
        plotter.close()
        paths.append(path)
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder")
    parser.add_argument("--size", default="2400x1600")
    parser.add_argument("--views", default="")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    width, height = (int(v) for v in args.size.lower().split("x"))
    for path in render(args.folder, (width, height), [v for v in args.views.split(",") if v], args.out):
        print(path)


if __name__ == "__main__":
    sys.exit(main())
