# Test scenarios

Realistic land-development and road scenarios for testing the tools, built on any DEM you have. Nothing in the plugin
is tied to them: they are inputs, defined in `scenarios.json`, that exercise the tools the way a user would.

| Scenario | Kind | What it builds |
|---|---|---|
| `mountain_road` | road | A hill road traced along the contour through the site (hairpins), a profile within 10 %, a synthetic corridor |
| `plain_road` | road | A rural road with spiralled curves, a profile within 4 % |
| `open_pit_and_stockpiles` | shapes | An open pit with benches, a waste dump and an ore stockpile to set volumes, a ROM pad |
| `hillside_terraces` | shapes | Level terraces down a hillside and an access platform |
| `golf_hole` | shapes | Fairway, tee, raised green, two bunkers and a pond |
| `industrial_estate` | shapes | Three plot platforms, a detention pond and a noise bund |
| `borrow_pit_and_mounds` | shapes | A benched borrow pit, a topsoil mound to a volume and a spoil mound to a level |
| `dem_comparison` | dem_tin | DEM to LandXML TIN at several tolerances on the main DEM and on the comparison DEMs |

## Running

Use the Python that QGIS uses (the OSGeo4W Shell on Windows, or a conda environment with QGIS), from the repository:

```
python tests/scenarios/make_scenarios.py --dem "D:/GIS/dem_utm.tif" --vertical-unit metre
python tests/scenarios/run_tools.py
```

Then open `tests/scenarios/out/index.html`: every tool run with its time, key numbers, warnings and links to the
reports, sheets and 3D viewers.

- **The DEM** must be in a projected CRS with an EPSG code, in metres or feet. Give `--vertical-unit` when it does
  not declare its elevation unit (Google GEM, FABDEM and ALOS GeoTIFFs do not); it is never assumed.
- **Sites** are found on the DEM by the mean ground slope each scenario asks for (`terrain` in `scenarios.json`),
  clear of one another. Fix one with `--centre golf_hole=E,N`, or keep the search to an area with
  `--search xmin,ymin,xmax,ymax`.
- **Comparison DEMs** for `dem_comparison`: `--compare other.tif` (repeatable). One in another CRS is warped only when
  you give `--warp-crs EPSG:xxxxx`.
- **Part of the set**: `--only golf_hole industrial_estate` in either script; `--tools landxml_grading_report` in
  `run_tools.py`.
- **In steps**: `run_tools.py --resume --budget 150` skips what already passed and starts no new tool after 150 s, so a
  long sweep can be split over several runs. Results are kept per tool in each scenario's `tool_runs.json`.
- **Slow output folder** (a network drive or a VM mount): `--scratch C:/temp/scn` writes each tool's outputs locally and
  copies them into `out` afterwards.

## Your own scenarios

Add an entry to `scenarios.json` (or pass `--scenarios my.json`). Coordinates are metres from the site centre. Shapes
are `rect [cx, cy, width, height, rotation°]`, `ellipse [cx, cy, semi-axis x, semi-axis y, rotation°]` or
`polygon [[x, y], ...]`; every other key is a Design Surface from Shapes attribute. Levels are numbers or expressions on
the ground inside the shape: `median`, `min-60`, `max+3`, `mean+0.5`. Roads take `pis` (or `"route": "contour"`),
`radius`, `spiral`, `profile_spacing`, `max_grade`, `k_value`, `design_speed` and the typical `section`.

Everything under `out/` (ground clips, LandXML, reports, viewers) is local: git ignores it, and none of it is data the
repository should hold.
