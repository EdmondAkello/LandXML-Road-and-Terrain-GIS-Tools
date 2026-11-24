"""Capture the 3D scene viewer of the demo road as annotated user-guide figures.

    python tools/guide/capture_scene.py <scene viewer.html> <figure folder>

Needs Python Playwright and Pillow, and a Chrome or Chromium that can run WebGL (software rendering is fine). Each
figure is a view set through the viewer's own controls; numbered markers are placed where the page projects the
features they point at, so they stay right when the scene changes. The figure captions in the guide explain them.
"""

import asyncio
import os
import shutil
import sys

from PIL import Image, ImageDraw, ImageFont
from playwright.async_api import async_playwright

VIEWER, OUT = sys.argv[1], sys.argv[2]
CHROME = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"]

# screen position of scene points (relative to the scene origin, levels in the scene's vertical unit)
PROJECT = """(points) => {
  const r = canvas.getBoundingClientRect(), { mvp } = matrices(canvas.width, canvas.height);
  return points.map(([x, y, z]) => {
    const p = [x, y, z * zFactor * ve, 1], c = [0, 1, 2, 3].map(i => mvp[i] * p[0] + mvp[4 + i] * p[1] + mvp[8 + i] * p[2] + mvp[12 + i] * p[3]);
    if (c[3] <= 0) return null;
    return [r.left + (c[0] / c[3] + 1) / 2 * r.width, r.top + (1 - c[1] / c[3]) / 2 * r.height];
  });
}"""
# candidate points of each feature (a marker goes on one that is on screen)
FEATURES = """() => {
  const take = (arr, every) => { const out = []; if (!arr) return out; for (let i = 0; i < arr.length; i += 3 * every) out.push([arr[i], arr[i + 1], arr[i + 2]]); return out; };
  const path = name => ((S.paths || []).find(p => p.name.includes(name)) || { points: [] }).points;
  const water = S.surfaces.find(s => s.role === 'water');
  return { road: path('Demo Road'), farm: path('Farm road'), water: water ? take(S.meshes[water.id].positions, 7) : [],
           plant: S.vegetation ? take(S.vegetation.positions, 13) : [],
           shrubs: S.vegetation ? take(S.vegetation.positions, 1).filter((_p, i) => S.vegetation.kinds[i] === 1) : [] };
}"""


async def place(page, candidates, prefer, avoid=(), width=1200, height=860):
    """The screen point of the candidate nearest ``prefer`` that is on the model's side of the page, away from ``avoid``."""
    best = None
    for point in await page.evaluate(PROJECT, candidates):
        if not point or not (40 < point[0] < width - 40 and 60 < point[1] < height - 40):
            continue
        if any((point[0] - x) ** 2 + (point[1] - y) ** 2 < 70 ** 2 for x, y in avoid):
            continue
        d = (point[0] - prefer[0]) ** 2 + (point[1] - prefer[1]) ** 2
        if best is None or d < best[0]:
            best = (d, point)
    return best[1] if best else None


def drive(path, camera, where, vehicle="car"):
    return f"""(() => {{
      const s = document.getElementById('drivepath'); s.value = [...s.options].find(o => o.text.includes('{path}')).value;
      s.dispatchEvent(new Event('change'));
      const v = document.getElementById('drivevehicle'); v.value = '{vehicle}'; v.dispatchEvent(new Event('change'));
      const c = document.getElementById('drivecam'); c.value = '{camera}'; c.dispatchEvent(new Event('change'));
      const sl = document.getElementById('driveslider'); sl.value = {where}; sl.oninput(); return true; }})()"""


def marker(draw, font, x, y, number):
    r = 15
    draw.ellipse([x - r, y - r, x + r, y + r], fill=(196, 40, 60), outline=(255, 255, 255), width=3)
    draw.text((x, y), str(number), fill=(255, 255, 255), font=font, anchor="mm")


async def shot(page, name, marks, crop=None):
    path = os.path.join(OUT, name)
    await page.screenshot(path=path)
    image = Image.open(path).convert("RGB")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 17)
    except OSError:
        font = ImageFont.load_default()
    for number, (x, y) in marks:
        marker(draw, font, x, y, number)
    if crop:
        image = image.crop(crop)
    image.save(path, optimize=True)
    print("saved", path)


async def element_mark(page, selector):
    box = await page.locator(selector).first.bounding_box()
    return (box["x"] - 4, box["y"] + 12) if box else None


async def main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=CHROME, headless=True, args=ARGS)
        page = await browser.new_page(viewport={"width": 1500, "height": 860})
        await page.goto("file://" + os.path.abspath(VIEWER))
        await page.wait_for_timeout(7000)
        found = await page.evaluate(FEATURES)

        # 1. overview: the road, a context road, the stream and the vegetation, with the layer switches
        await page.evaluate("""(f) => {
          const pts = [f.road[Math.floor(f.road.length * 0.3)], f.farm[Math.floor(f.farm.length / 2)], f.water[Math.floor(f.water.length / 2)]].filter(Boolean);
          cam.target = [0, 1, 2].map(i => pts.reduce((t, p) => t + p[i], 0) / pts.length);
          cam.azimuth += 0.6; cam.elevation = 0.62; cam.distance = 1100; draw(); }""", found)
        await page.wait_for_timeout(1500)
        marks, used = [], []
        for number, key, prefer in ((1, "road", (330, 560)), (2, "farm", (900, 200)), (3, "water", (720, 380)), (4, "plant", (700, 690))):
            point = await place(page, found[key], prefer, used)
            if point:
                used.append(point)
                marks.append((number, (point[0] + 16, point[1] - 16)))
        layers = await element_mark(page, "#layers")
        if layers:
            marks.append((5, (layers[0] - 14, layers[1])))
        await shot(page, "fig_scene_overview.png", marks)

        # 2. chase camera on the road, with the drive-through panel
        await page.evaluate(drive("Demo Road", "chase", 0.42))
        await page.wait_for_timeout(2500)
        await page.evaluate("document.getElementById('drive').scrollIntoView()")
        await page.wait_for_timeout(500)
        marks = []
        for number, selector in ((1, "#drivepath"), (2, "#drivecam"), (3, "#drivevehicle"), (4, "#driveslider")):
            box = await page.locator(selector).first.bounding_box()
            if box:
                marks.append((number, (box["x"] - 18, box["y"] + box["height"] / 2)))
        await shot(page, "fig_scene_drive.png", marks)

        # 3. close to the ground: grass clumps (blades over a patch of grass colour) and shrubs
        await page.evaluate("document.getElementById('driveexit') && document.getElementById('driveexit').click()")
        await page.evaluate("""(f) => { const r = f.road[Math.floor(f.road.length * 0.55)];
          const near = f.shrubs.filter(p => Math.hypot(p[0] - r[0], p[1] - r[1]) > 25).sort((a, b) => Math.hypot(a[0] - r[0], a[1] - r[1]) - Math.hypot(b[0] - r[0], b[1] - r[1]))[0];
          cam.target = [near[0], near[1], near[2]]; cam.elevation = 0.32;
          cam.distance = 45; cam.azimuth += 1.1; draw(); }""", found)
        await page.wait_for_timeout(2500)
        await shot(page, "fig_scene_vegetation.png", [], crop=(0, 40, 1180, 760))
        await browser.close()


asyncio.run(main())
