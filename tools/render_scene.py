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


LINE_NAMES = {"daylight": "Daylight line", "platform_outline": "Platform outline", "retained_edge": "Retained edge",
              "batter_edge": "Batter", "road": "Road", "alignment": "Alignment", "feature_line": "Feature line",
              "breakline": "Breakline", "contour": "Contour", "section": "Cross-section"}


def _font(size):
    from PIL import ImageFont

    for name in ("DejaVuSans.ttf", "Arial.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _project_labels(plotter, scene, ve, height, focus):
    """Screen positions (x right, y down) of the labels' points and the x range of the focus surfaces."""
    import vtk

    coordinate = vtk.vtkCoordinate()
    coordinate.SetCoordinateSystemToWorld()

    def screen(point):
        coordinate.SetValue(*point)
        x, y = coordinate.GetComputedDoubleDisplayValue(plotter.renderer)
        return x, height - y

    points = [(label["text"], screen((label["position"][0], label["position"][1], label["position"][2] * ve)))
              for label in scene["labels"]]
    xs = [screen((b[i], b[j], b[k]))[0] for b in focus for i in (0, 1) for j in (2, 3) for k in (4, 5)]
    return points, (min(xs), max(xs)) if xs else None


def _callouts(points, span, width, height, font, draw, unit):
    """Labels in columns left and right of the model, joined to their points by leaders."""
    inside = [(text, p) for text, p in points if 0 <= p[0] <= width and 0 <= p[1] <= height]
    if not inside:
        return
    xs = [p[0] for _t, p in inside]
    low, high = min(xs), max(xs)
    middle = (low + high) / 2
    if span:
        low, high = min(low, span[0]), max(high, span[1])
    gap, row, edge = unit * 3, unit * 1.7, unit
    for side in ("left", "right"):
        group = sorted([(t, p) for t, p in inside if (p[0] < middle) == (side == "left") or len(inside) == 1 and side == "left"],
                       key=lambda item: item[1][1])
        if len(inside) == 1 and side == "right":
            continue
        if not group:
            continue
        widths = [draw.textlength(t, font=font) + unit for t, _p in group]
        widest = max(widths)
        x = max(low - gap - widest, edge) if side == "left" else min(high + gap, width - widest - edge)
        ys = []
        for _t, p in group:
            ys.append(max(p[1], ys[-1] + row if ys else row))
        over = ys[-1] - (height - row)
        if over > 0:
            for j in range(len(ys) - 1, -1, -1):
                ys[j] = min(ys[j] - over, ys[j + 1] - row if j + 1 < len(ys) else float("inf"))
        for (text, p), w, y in zip(group, widths, ys):
            y = max(y, row / 2)
            left = x + widest - w if side == "left" else x
            end = left + w if side == "left" else left
            knee = end + unit if side == "left" else end - unit
            for colour, line_width in (("white", 4), ("#333333", 2)):
                draw.line([p, (knee, y), (end, y)], fill=colour, width=line_width)
            draw.ellipse([p[0] - 4, p[1] - 4, p[0] + 4, p[1] + 4], fill="#333333")
            draw.rectangle([left, y - row * 0.45, left + w, y + row * 0.45], fill="#fffde7", outline="#b9b39a")
            draw.text((left + unit / 2, y), text, fill="black", font=font, anchor="lm")


def _compose(scene, view, image, path, anchors=None):
    """The 3D image with a side panel: compass, legend of the styled surfaces and line kinds, notes.
    Nothing is drawn over the model."""
    from PIL import Image, ImageDraw

    height, width = image.shape[:2]
    panel = max(int(width * 0.24), 360)
    sheet = Image.new("RGB", (width + panel, height), "white")
    sheet.paste(Image.fromarray(image[:, :, :3]), (0, 0))
    draw = ImageDraw.Draw(sheet)
    x0, unit = width + 30, max(height // 60, 14)
    draw.line([(width, 0), (width, height)], fill="#bbbbbb", width=2)
    if anchors:
        _callouts(anchors[0], anchors[1], width, height, _font(int(unit * 1.1)), draw, unit)
    # compass: north as seen from this camera (bearing of the view direction, looking from camera to target)
    look = -np.asarray(view["direction"], float)
    bearing = np.degrees(np.arctan2(look[0], look[1]))
    angle = np.radians(-bearing)          # north, clockwise from screen-up

    def at(a, radius):
        return (cx + radius * np.sin(a), cy - radius * np.cos(a))

    cx, cy, r = x0 + panel // 2 - 30, int(unit * 5.5), int(unit * 3.2)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline="#555555", width=2)
    draw.polygon([at(angle, r * 0.85), at(angle + 2.6, r * 0.45), (cx, cy), at(angle - 2.6, r * 0.45)], fill="#222222")
    draw.polygon([at(angle + np.pi, r * 0.85), at(angle + np.pi + 2.6, r * 0.45), (cx, cy),
                  at(angle + np.pi - 2.6, r * 0.45)], outline="#222222", fill="white")
    draw.text(at(angle, r + unit), "N", fill="#222222", font=_font(int(unit * 1.4)), anchor="mm")
    y = cy + r + int(unit * 2.5)
    title = _font(int(unit * 1.25))
    text = _font(unit)
    for surface in scene["surfaces"]:
        style = surface.get("style") or {}
        classes = style.get("classes") or []
        if not classes:
            continue
        draw.text((x0, y), surface.get("name") or surface["id"], fill="black", font=title)
        y += int(unit * 1.8)
        for item in reversed(classes):
            draw.rectangle([x0, y, x0 + unit, y + unit], fill=item["color"], outline="#888888")
            draw.text((x0 + unit * 1.6, y - 1), item["label"], fill="black", font=text)
            y += int(unit * 1.45)
        y += unit
    kinds = []
    for line in scene["lines"]:
        if line["kind"] not in [k for k, _c in kinds]:
            kinds.append((line["kind"], line.get("color", "#333333")))
    if kinds:
        draw.text((x0, y), "Lines", fill="black", font=title)
        y += int(unit * 1.8)
        for kind, color in kinds:
            draw.line([(x0, y + unit // 2), (x0 + unit * 1.2, y + unit // 2)], fill=color, width=4)
            draw.text((x0 + unit * 1.6, y - 1), LINE_NAMES.get(kind, kind.replace("_", " ").capitalize()), fill="black", font=text)
            y += int(unit * 1.45)
    notes = [f"View: {view['name']}", f"Vertical exaggeration × {scene.get('vertical_exaggeration', 1):g}"]
    crs = (scene.get("crs") or {}).get("authid")
    if crs:
        notes.append(f"CRS: {crs}")
    y = height - int(unit * 1.5) * len(notes) - unit
    for note in notes:
        draw.text((x0, y), note, fill="#555555", font=text)
        y += int(unit * 1.5)
    sheet.save(path)


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
    focus = [m.bounds for s, m, _rgb in meshes if s["role"] == "design"] or [m.bounds for _s, m, _rgb in meshes]
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
        target = np.asarray(view["target"], float)
        target[2] *= ve
        direction = np.asarray(view["direction"], float)
        direction /= np.linalg.norm(direction)
        plotter.camera.focal_point = target
        plotter.camera.position = target + direction * float(view["distance"])
        plotter.camera.up = (0, 0, 1)
        plotter.camera.view_angle = float(view.get("view_angle", 30.0))
        path = os.path.join(out or folder, f"render_{view['name']}.png")
        image = plotter.screenshot(return_img=True)
        anchors = _project_labels(plotter, scene, ve, image.shape[0], focus)
        plotter.close()
        _compose(scene, view, image, path, anchors)
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
