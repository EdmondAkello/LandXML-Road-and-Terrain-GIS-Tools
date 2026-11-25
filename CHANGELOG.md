# Changelog

## Unreleased

### Added (corridor design)
- **Corridor Section Editor** dock: designs the cross-section by station on the real ground (TIN in the file, a surface
  in another LandXML, or a DEM) with a live section, drag handles for widths and crossfalls, *Why this point*, regions
  with transitions, build-ups, undo/redo and advisory checks. Elements: lane, shoulder, kerb and channel, sidewalk,
  cycle track, verge, side drain (RDM 1.3 Figure 4.15 types or custom), daylight (slopes by height, benches, retaining
  walls in cut or fill, fill widening, toe and cut-off drains), median, barrier and custom steps; every parameter is
  editable.
- Superelevation from the LandXML or computed from the standard (runoff over the spiral or from the tables, relative
  gradient, rotation about the centreline or an edge, reverse curves) and curve widening from the standard's table.
- **Section standards** as data files in `criteria/sections`: Kenya RDM 1.3 (2025), AASHTO 2018 (metric and US
  customary) and Generic, with templates, build-ups, drain types, slope tables and limits with their clauses. Checks
  are advisory only; nothing is locked to a standard. *Save as standard* writes a user standard.
- **Corridor features** (`criteria/features`): bus bay, truck lay-by, brake check area, climbing lane, descending lane,
  auxiliary lane, widening, ghost-island widening, junction mouth, private access and truck escape ramp (bed length
  from RDM 1.3 Equation 12.1), built into the sections by chainage; definitions ported from the Road Furniture
  Designer RDM data.
- **Plan Editor** dock: PIs on the map with live curve fitting (add, insert, drag, delete, radius handles, spirals),
  refusal of unfittable edits with the reason, criteria checks, start from a line, an alignment or a feature line or
  breakline (levels become the profile), and LandXML output in an explicit axis order.
- The plan, profile and section editors hand results to each other: a profile saved in the Profile Viewer is adopted
  by an open Section Editor for the same alignment.
- Toolbox group **9. Corridor design**: Create Corridor Section Project, Build Corridor from Section Project (LandXML
  with CrossSects, finished and datum TINs and coded feature lines; HTML report; quantities by average end area equal
  to Corridor Section Quantities; Excel bill of quantities from an item list; variant comparison), Export Design for
  Civil 3D or OpenRoads (by Stage: alignment, profiles, surfaces and feature lines, complete) and Export Roads to
  LandXML (lines to alignments and profiles).
- Design Surface from Shapes: benched stockpiles (waste dumps) with `bench` and `berm`.
- Test scenarios with finished road surfaces: an open pit with a haul ramp and a benched dump, a mountain highway with
  stabilisation benches and retaining walls, a rural road and an urban street.
- User guide Part F (Corridor design).

### Fixed (corridor design batch)
- CI: the profile drag GUI test now sends real mouse-move events, which `QTest.mouseMove` does not deliver offscreen on
  Qt 5 (QGIS 3.44).
- NumPy 2: 2D `np.cross` replaced in feature-line welding, the 3D scene and the grading tests.

### Added (land development: design surfaces and DEM to TIN)
- **Design Surface from Shapes** (Site grading): draws design surfaces from polygons on a DEM or a LandXML TIN —
  platforms at a level with cut and fill batters, basins and ponds, open pits with benches and berms, stockpiles and
  mounds (to a top level or to a volume), terraces, and golf fairways, greens and bunkers. Each polygon's attributes
  (shape, level, floor, volume, slopes…) override the defaults. Writes a LandXML with the existing ground, the design
  surface, outlines and daylight lines, a design GeoTIFF and an HTML volume report (cut and fill per shape).
- **DEM to LandXML TIN** (Terrain conversion): an adaptive TIN within a vertical tolerance (large triangles on even
  ground, small ones where it bends; crack-free, every point a DEM cell centre) or a regular grid, for the whole DEM,
  an extent or mask polygons, with an optional triangle limit. Reports the points, triangles and the largest miss.
- The LandXML writer fits alignments from PIs (circular curves with optional spirals) and writes TIN surfaces and
  feature lines.
- **Basin Stage–Storage** takes an optional **basin area** polygon, so one pond can be measured on a surface that covers
  a whole site; without one, a surface whose lowest point is on its edge is reported as holding no water.
- **Test scenarios** (`tests/scenarios`): a generator that builds road and land-development scenarios on any projected
  DEM (sites found by terrain slope; a hill road traced along a contour) and a runner that runs every tool on them and
  writes an index of times, numbers, warnings and reports. Outputs stay local (git-ignored).

### Changed
- Toolbox: *Extract LandXML Alignments to Lines* moved into **2. Road design extraction** and the groups after it are
  renumbered (3. Terrain conversion … 8. Site grading); group and algorithm ids are unchanged, so models and scripts
  keep working. The road 3D scene tool is now *Export 3D Scene and Viewer (Roads and Terrain)*, beside *Export 3D
  Scene and Viewer (Site Grading)*.
- LandXML files the plugin writes declare their elevation unit (when known) and name the plugin as their application,
  so the tools no longer warn that the unit and source are unknown; existing-ground profiles are written as
  `ProfSurf`, as Civil 3D does, so they are read as ground and not as a second design profile.

### Performance
- Site Grading Report: about 4–10× faster and far smaller (17 MB to 2.2 MB on a 250,000-triangle site). Class areas
  are exact without building pieces, the maps draw merged class outlines, and matching holes to their outer rings in
  outlines is no longer quadratic (the report could run out of memory on large sites).
- Grading Quantities, Earthwork Balance, Grading Edge Analysis and the report: a design surface on the existing
  ground's own triangles (as Design Surface from Shapes writes) gets its depth directly at the vertices instead of an
  overlay, about 8× faster with the same volumes.
- Surface Classes: triangles within one class are merged before the union, so only the triangles cut by a class break
  are clipped.

### Fixed
- Rerunning a tool over a GeoTIFF left broken by a cancelled or crashed run failed with "Input file too large to be
  opened"; the old file is now removed first, and a file still open in another program gets a clear message.
- Export Complete Road Design and Create 3D Road Centerlines reported a 3D centreline as partial when the profile ended
  within a millimetre of the alignment end.

### Added (Profile Viewer: design a profile)
- A **Design** tab in the Profile Viewer draws a new vertical profile on the long section: start from the existing
  ground (PVIs at the ends, or every chosen length, averaged) or copy an existing design; drag PVIs on the chart
  (Shift: up and down only), double-click to add one, right-click for its curve length, K value or to delete it; type
  exact stations, levels, curve lengths or K in the PVI table. The grades, curve ends, K values and the cut/fill shading
  follow every change, and a whole drag is one Undo step.
- Live checks against the chosen standard (Kenya RDM or AASHTO), design speed and grade limit: steep or flat grades,
  grade changes without a curve, crest and sag K below the standard and curves below the minimum length.
- **Save to LandXML…** writes the design into a copy of the file (never the source), as a profile of the alignment,
  and opens the copy with the new profile chosen.
- The editing model (`landxml/profile_design.py`) is plain Python and tested without QGIS.

### Added (finding the tools)
- The Profile Viewer has its own toolbar button (it was only in the Plugins menu) and is now named *LandXML Profile
  Viewer and Designer*; the viewer has a **Guide** button and the Design tab a **Help** button that open the user guide
  at their sections, offline.
- Once after installing or upgrading, a message bar names the toolbox group and the Profile Viewer with **Open Profile
  Viewer** and **User Guide** buttons. It is not shown again for the same version.

### Added (LandXML writer, groundwork)
- `landxml/writer.py` lays out horizontal alignments from design elements (tangents, circular curves and clothoid
  spirals, from a start point and bearing; each element must join the next in curvature) and vertical profiles from
  PVIs with symmetric parabolic curves, and writes them as LandXML 1.2: northing first by default as Civil 3D does,
  the units given, and a coordinate system only when one is given. The plugin's own readers get the written
  centreline back within a centimetre and the profile levels exactly. No tools use it yet.

### Changed (3D scene dialog, #84)
- **Generate a feasibility road** switches road generation on and off. While it is off the road inputs are greyed
  out but keep their values and the export is a terrain scene only, said so in the log; ticking it with no road
  alignment stops with a message. Scripts, models, saved settings and history without the switch still build the
  roads they name.
- Clearer labels with the parameter IDs kept: *Limit scene area along alignment* (scene coverage only; it never
  generates a road), *Surrounding terrain width each side*, *Road alignment(s) to generate*, *Ground surface for
  road tie-ins*, *Road: total carriageway width*, *Road: maximum batter search distance*, and *Road* or *Road
  structures* / *Road DEM correction* for the other road inputs.
- Surrounding terrain width: 0 = Auto (the new default): 60 either side of a scene-area alignment, or the road's
  half section plus batter search plus 20 when the alignment is also a generated road, and the same for the strip
  along generated roads. Explicit positive widths in saved projects, models and batch runs keep their meaning and
  also set the strip width.
- A warning when a chosen extent, polygon or alignment area leaves a generated road less room than half its section
  plus the batter search, with the stations; the area is never widened.
- The log names the feasibility roads generated.
- User guide: the scene area, the three widths, the road switch, context roads, pier and deck schedules, CAD
  layers and poles, and ground styling.

### Fixed (triangle budget)
- Cutting the ground along a design's edge (`refine_edges`) no longer overruns the triangle budget. Refinement
  stops before a round that would take the ground past the room left in the budget, plus a twentieth of the budget
  so cut edges stay clean even when the unrefined scene already fills it. Rounds are whole, so the edge stays
  evenly refined. (A scene with a long river crossing had grown to about 1.2 M triangles against a 0.9 M budget.)

### Fixed
- Structure schedules refuse a pipe or box size over 15 m (an unlabelled `Diameter = 600` meant in mm) instead of
  drawing it, and ask for the unit in the heading, e.g. `Diameter (mm)`. Bridge spans are not limited.
- Unit labels in the dialogs read the LandXML `<Units>` header only, so choosing a large file no longer freezes the
  dialog while it is parsed.
- A unit in a schedule heading needs a separator, brackets or capitals (`Dia (mm)`, `Rise_m`, `DiameterMM`):
  `Diam` is no longer read as `Dia` in metres.
- Complete customary-unit handling for structure schedules, synthetic roads, mixed-unit scene rendering and DEM elevations, edge face areas, drive-through speeds/cameras, section area labels and surface pick readouts. Explicit structure CSV unit headings (including inches and legacy millimetre headings) are converted; generic dimensions stay in their stated model units, without magnitude guessing.
- Complete Road Design places schematic profile lines and controls beside the exported project in the output CRS, scaled to the project size and stacked below the plan and terrain bounds with clear gaps. Large source stations no longer place the drawings far away. Source station/elevation attributes and units are preserved. Synthetic tests cover metric and US survey feet, multiple profiles, terrain, coordinate shifts, reprojection and profile-only exports. Issue #77.

### Added
- Dimensional inputs across the Processing tools state their unit basis. Desktop dialogs show actual m, ft or ftUS, selected volume and criteria speed units beside fields and refresh from files, CRS and explicit unit choices without converting entered values. Unknown units stay unknown. The viewer docks also identify dimensional input units.
- Raster elevation unit selection for DEMs without band-unit metadata. Tagged DEMs are explicitly converted to the scene elevation unit; unknown elevation units require a choice. Scene paths retain source station units through reprojection.

### Documentation
- User guide: four annotated figures for Export 3D Scene (the dialog with its setup summary, the demonstration scene,
  vegetation close up, the drive-through panel) and a walkthrough for an existing-terrain scene against a feasibility
  road; figures renumbered. `tools/guide/make_scene_figures.py` builds the demonstration scene and
  `tools/guide/capture_scene.py` captures and annotates the viewer (headless Chrome with software WebGL).

### Fixed (QGIS 4.4 dialogs, partial)
- The Processing dialog panel's `wrappers` became a method returning a list in QGIS 4.4; the plugin now reads it either
  way (`compat.panel_wrappers`). Other QGIS 4.4 dialog changes remain to be handled.

### Added (3D scene dialog and large scenes)
- **Setup summary** at the top of the Export 3D Scene dialog, updated as inputs change: the road and its alignments, lane
  and shoulder widths, side slopes, the scene width (Auto or the chosen value), what extras are on, and warnings such as an
  alignment that is not in the file or has no design profile (unless *at grade* is ticked).
- **Show/hide buttons** for the Road, Plants and water, and Models inputs. Hidden inputs keep their values.
- **Structures table…** opens a table (road, station, type, details) for the typed culverts, bridges and piers instead of
  typing the text; rows are checked with the scene's own reader before they are accepted.
- **Phone copy of the viewer:** a scene over 1.5 M triangles also writes `viewer_phone.html`, with the ground thinned to about
  600 000 triangles (designs, structures and water are kept whole), so it opens on a phone or tablet.

### Changed (3D scene, bridges)
- A generated bridge is written as separate **Bridge deck**, **Pile caps** and **Piers and abutments** layers, each with its
  own toggle and shade, instead of one merged structures mesh. Culverts stay in **Structures (concept)**.

### Added (3D scene, waterways)
- **Waterways** (optional line layers) in Export 3D Scene are drawn as a flat-across water ribbon of a chosen
  **Water width** that follows the ground along the line. The ribbon runs downhill (a line drawn uphill is turned round and the log says so), so the level
  never rises along it; stretches outside the ground surface are left out and a message reports the length drawn.
  A ribbon is a concept for scale and context, not a hydraulic water surface; use **Water levels** for a level.

### Added (3D scene, vegetation)
- **Vegetation** (optional polygon or point layers) in Export 3D Scene: polygons are planted on a jittered grid
  (**Spacing**, one plant per cell, so the cover is even but not regular) and points become single plants. **Style** is
  *Grass and shrubs* (about 15 % shrubs), *Grass* or *Shrubs*. Grass clumps are three crossed quads whose blades are cut
  out in the viewer, over a soft patch of grass colour (8 triangles each); shrubs are low, irregular faceted domes (30 triangles). Plants stand on the ground
  surface, keep a **Clear distance** away from roads and designs, vary in size (Auto: grass about 0.8 m, shrubs 1.8 m,
  in the output unit) and are the same every run. They are written as `vegetation.csv` and drawn as instances with a
  **Vegetation** switch in the layers panel. The count is held to a quarter of the triangle budget (a message says when
  it was thinned), and a polygon far larger than the scene only gets plants where the scene is.

### Added (3D scene, other roads and drive paths)
- **Other roads:** more LandXML files (*Other roads: more LandXML files*, read in the main file's coordinate system and
  axis order) and line layers (*Other roads: line layers*) are built as roads with the scene's road section, side
  slopes run out to the ground and cut into the terrain, like a feasibility road. A LandXML alignment with a design
  profile follows it; anything without one follows the ground smoothed over 40 m (a context road). Structures can be
  scheduled on them by road name (`A9 @ 0+238.1 = box 1 x 2.0 x 1.5`). A road that lies outside the scene is
  reported and left out.
- **Every generated road can be driven**, including roads on the smoothed ground, which previously had no drive path.
  LandXML alignments without a profile that are not generated get a drive path on the smoothed ground, named
  "(on the ground)".

### Changed (viewer file size)
- The viewer's meshes are packed losslessly (values stored by component with their bytes grouped, indices as deltas,
  then gzip): a 12.2 MB corridor scene became 3.3 MB and a 4.9 MB one 1.9 MB, with every array unchanged. The
  browser unpacks them with its built-in `DecompressionStream` (current Chrome, Edge, Firefox and Safari); older
  files with plain arrays still open.

### Added (viewer, drive-through cameras)
- **Camera** in the Drive through panel: *Driver* (as before), *Chase* (a vehicle drives the road and the camera
  follows behind and above it), *Aerial follow* and *Flythrough* (a high camera that sweeps from one side of the
  road to the other while it follows). **Vehicle** chooses a car (eye 1.3 m) or a truck (eye 2.4 m); the models are
  a few hundred triangles (a car with a tapered body, raked glass, bumpers, lights and round wheels; a truck with a
  cab, cargo box and dual rear axles) and keep their true size under vertical exaggeration and in feet. Recording captures the
  chosen camera. Links accept `#drive=0.4&cam=chase&vehicle=truck`. A 120 km/h (75 mph) speed was added.

### Added (viewer)
- Ground styling: existing ground, context surfaces and DEMs get an elevation tint with hillshading
  instead of flat grey (a Ground section in the panel switches between Terrain, As exported and Grey).
- Contours on the ground, drawn in the viewer so they stay sharp at any zoom: the interval is chosen from
  the ground's range and can be changed, every fifth contour is heavier, and they can be shown on designs
  too. The legend (and A3 sheets) show the tint range and the contour interval. A plain LandXML surface
  with the default style counts as ground; one you styled keeps its colours.

### Added (feasibility roads and context)
- Structures belong to a road: start an item with the road name and `@` (`A9 @ 0+238.1 = box 1 x 2.0 x 1.5`), or
  give a Road column in the CSV. Items without a road apply to every synthetic road, as before.
- Scheduled piers: `1+403 = pier 5.0 x 3.2, cap 9.0 x 2.5` (section across x along, pile cap side x depth or
  L x W x D), or equally spaced `1+403-2+203 = piers every 40, 5.0 x 3.2, cap 10.5 x 2.5`; a later pier row at
  the same chainage overrides it, so pier groups take their own sections. Pier rows under a bridge set its spans.
- Bridge options: `depth 2.6`, `width 12` and fixed deck levels `deck 1235 to 1240` (an existing bridge on a
  context road). CSV columns: Pier width, Pier length, Pile cap, Deck depth, Deck level, Deck width.
- Roads without a profile: with "roads without a profile follow the ground" ticked, a plan-only alignment
  (an existing road, a service road) is drawn on the ground smoothed over 40, said so in the log, and gets no
  automatic bridges; a scheduled bridge's deck levels are blended in. Without the option the error says how.
- When synthetic roads are generated and no corridor, extent or area polygon is chosen, the scene is a strip
  along the roads (half the section plus the longest side slope plus 20 either side) instead of the whole DEM;
  station limits apply to the first road. The log says whether roads were generated (#81).
- Other layers: points become poles (the extrusion height, or 6), and a layer with a CAD `Layer` field (a DXF
  import) is split into one toggle per CAD layer, coloured from the QGIS categorised style.

### Changed
- The ground next to synthetic roads is refined less finely when the triangle budget would be overrun.
- Smaller road scenes: the hidden "before" copy of the ground under synthetic roads keeps the original
  triangles instead of the fine refinement the cut edges need (on a 6 km set of roads over a survey TIN,
  2.08 M triangles became 36 k and the viewer went from 110 MB to 46 MB).

## 1.11.1 — 2026-11-18

### Fixed
- Qt6 compatibility check on plugins.qgis.org: the advanced-parameter flag uses `Qgis.ProcessingParameterFlag.Advanced` only (the plugin needs QGIS 3.44, which has it), without the old `FlagAdvanced` fallback.

## 1.11.0 — 2026-11-18

### Fixed (renders and long roads)
- Flat faces now shade flat right up to their edges: the viewer splits vertex normals where faces meet at
  more than 30°, so a platform no longer picks up the slope of the batter or wall beside it.
- Edge lines draw cleanly: daylight lines, platform outlines and retained edges that share an edge layer
  on top of each other instead of z-fighting into a speckled band, and a retained edge follows the top of
  its wall instead of stepping down and up the face (a comb of ticks). Designs also draw in front of the
  ground where the two meet.
- Callout labels stay inside the model area on high-density screens (their width was measured in CSS
  pixels but placed in device pixels).
- With several synthetic roads, one that lies outside a limited scene area is skipped with a warning
  instead of failing the run.
- A synthetic road that started at chainage 0 skipped the DEM fit to the surveyed ground profile (the
  start chainage shadowed the first-road flag).
- Long corridors are much faster: a 36 km synthetic road on a 30 m DEM took 280 s, now about 105 s. The point-in-polygon test sorts the points once and tests each edge only against its own band,
  and ground refinement first skips triangles nowhere near the road.

### Changed (coordinate order)
- The coordinate option now reads **Coordinate order (Civil 3D and OpenRoads store Northing first: choose Swap X / Y)**.
- When the coordinates, as interpreted, fall outside the output CRS's area of use but inside it in the other axis order, the log says so and names the option to choose. The order is still never changed automatically.
- In QGIS desktop, the order chosen in a project is offered by the next tool you open in that project (stored in the project file). Scripts, models and batch runs are unaffected.

### Added (A3 sheets)
- **A3 sheets from the viewer.** The panel's *A3 sheet* section puts the current view on an A3 landscape sheet at 200 dpi: a single border, the title box bottom right (title, project / drawing no. / revision, "3D view, not to scale", vertical exaggeration, CRS and date), and a column on the left with the north arrow, the legend (surfaces with the same classes share one entry) and numbered notes. The model has its own viewport beside the column and above the title box, fitted to the design, so nothing on the sheet covers it; callouts are kept. **Save A3 PNG**, or **Print / PDF** (the browser's print dialog, A3 landscape, no margins; choose *Save as PDF*). It needs no QGIS 3D, so it works anywhere the viewer opens; `viewer.html#a3preview` shows the sheet for checking.

### Added (viewer on phones and tablets)
- **The viewer works on phones and tablets.** On narrow screens the model fills the screen and the panel becomes a sheet from the bottom (☰ Layers and tools; `viewer.html#panel` opens it). One finger rotates, two fingers pinch to zoom and move to pan, a tap reads a surface. Saved views step back on portrait screens so the same width fits; the drawing buffer is capped at about 4 million pixels; station ticks give way to the callouts on small screens; a notice suggests a lower triangle budget when a scene is heavy for a phone.

### Added (choosing instead of typing)
- **Pickers from the file.** Names that were typed can now be chosen from the LandXML: the 3D scene's corridor alignment (dropdown), synthetic-road alignments and the synthetic ground (checklists, which also list the chosen DEM layers), Earthwork Balance's volume surfaces, and Grading Revision Comparison's second-revision surfaces (read from the second file). The fields stay plain text, so models, scripts and batch runs are unchanged.
- **Surfaces and styles table.** The 3D scene's *LandXML surfaces and styles* field has an **Edit…** table: surface (from the file), style, and the surface it is drawn over.
- **Style chooser with your own styles.** Style fields (raster style, site-scene design and ground styles, Surface Classes) list the built-in styles (elevation classes, slope classes, cut/fill depth, aspect, plain colours, finished roads) and the user's saved styles; **★** saves the current style under a name (checked first), **−** removes one. Saved styles live in the QGIS settings and are written into the field as text, so a model never depends on them.
- A DEM that does not cover the scene area now gets a message with both extents in the output CRS and, when the LandXML's X and Y swapped would fall on the DEM, the advice to choose *Swap X / Y*.

### Documentation
- README front page: a one-line summary, three figures from the demonstration data and the highlights before the details. The validation notes of versions 1.5.0 to 1.9.1 moved to `HISTORY.md`.

### Added
- Feature lines exported as `PlanFeatures` are read: Civil 3D writes its feature lines this way, and Road Furniture Designer's feature-line export uses it. Each `PlanFeature` becomes a feature-line record from its `CoordGeom` (lines, curves and spirals sampled as alignments are, irregular lines), in stored order (northing, easting, elevation), with elevations along curves interpolated between their ends, its name, `Feature` code and properties (such as `site`), and `landxml_element=PlanFeature`. They appear in **Extract LandXML Feature Lines**, the Complete Road Design `feature_lines` layer and the Inspect LandXML count. A PlanFeature that cannot be read is skipped with a warning.

### Added (user experience)
- **The user guide ships with the plugin** and opens offline: **Plugins → LandXML Road & Terrain → User Guide**, and each tool's **Help** button opens the tool's own section. The release ZIP carries the HTML guide and its figures (the PDF editions stay out); the package is about 4.7 MB.
- Coordinate shifts, rotation and scale are under **Advanced parameters** in every tool, as are the finer 3D-scene options (lanes, crossfalls, slopes, bridges, channels, water, raised polygons, triangle budget, exaggeration, views, title).
- **Export 3D Scene (Site Grading)** is now named **Export 3D Scene and Viewer (Site Grading)**, as it also writes the offline viewer (its id is unchanged, so saved models keep working).

### Added (large files)
- **Large LandXML files open and grade quickly.** TIN `Pnts`, `Faces` and `Contours` blocks are no longer put into the XML tree: they are located in the memory-mapped file and read in blocks straight into arrays (`landxml/tin_stream.py`), with the same validation and error messages as before. A 1.7 GB Civil 3D export (two sites, 3.3 and 3.5 million existing-ground points) opens in about 15 s with tens of MB of working memory instead of failing; Inspect, the surface and alignment lists, and every tool that only reads alignments or profiles gain the same speed. The hardened parser and its DOCTYPE/entity refusal are unchanged.
- Surfaces can be read **where they matter only**: a triangle is kept when its bounding box touches the requested area. Grading Quantities, Earthwork Balance and Grading Revision Comparison read existing ground only around the design area, and Surface Difference (Earthworks) reads the comparison surface only where it overlaps the base surface. On the 1.7 GB file, design minus existing ground for a site took about 30 s and matched the Civil 3D volume surface within the known existing-ground data differences; a 2 m surface difference dropped from 128 s to 2.7 s.
- Grading Quantities, Earthwork Balance and Grading Revision Comparison accept an **Existing-ground LandXML file** separate from the design file (for example a survey TIN exported on its own); the declared vertical units must agree, and a file that does not reach the design area is refused rather than reporting zero volume.
- Prepare LandXML no longer loads TIN blocks into memory: `Pnts`, `Faces` and `Contours` contents are copied byte for byte from the source files into the prepared file (surfaces copied from another file included), and the rest of the file is rewritten as before. Files where splicing is not safe (UTF-16, a non-UTF-8 declared encoding, prefixed elements inside the blocks) are parsed whole as before. Tests check the copied bytes and that the result equals the whole-file path.
- Prepare LandXML indexes only the terrain triangles within the section width of the alignments, so a site-sized terrain no longer means millions of triangles are indexed one by one; results are identical (synthetic test with 25,000 distant rectangles).
- **Civil 3D feature lines from the grading tools**: an optional LandXML output of `PlanFeatures` (`Feature code="FeatureLine"`, chains of `Line` elements, the form Civil 3D exports and imports) in the input file's stored coordinates and units, so it imports back into the source drawing whatever coordinate interpretation the GIS outputs use. Grading Quantities writes the **daylight lines**: the outline of the earthworks footprint (where design and existing ground differ by more than 0.01), traced on the design TIN with existing levels sampled at its vertices, with untouched islands as holes and lines under 1 unit dropped, carrying design levels (needs the design and existing-ground surfaces; a volume surface holds depths only and is warned about). Platform Levels writes each **platform outline** (and holes) at the design levels along its edge. Basin Storage writes the **rim** and the **design water level** (rim minus freeboard) lines. New `landxml/plan_features.py` (exact level lines on a TIN, PlanFeatures writer); written files read back through the PlanFeatures reader in tests.
- **Road tools are faster on large corridor exports.** A file opened several times during one run (coordinate checks, alignments, profiles, sections) is parsed once and shared while it is in use, then released; a changed file is re-read. Inspect's coordinate bounds are gathered in one pass over the file. On two real road exports, all 22 tools gave the same outputs as 1.10.0; total run time fell from 599 s to 135 s on a 60 MB corridor export (578,000 cross-section points) and from 341 s to 51 s on a 45 MB export with a 550,000-triangle terrain.
- **Optional binary cache of large surfaces** (`landxml/tin_cache.py`), in the system temporary folder only (`<temp>/LandXMLTools/tin-cache`, never beside the source file). Surfaces with a point block of 16 MB or more are saved as binary arrays on first read, and the positions of the TIN blocks of large files are remembered, so a second run skips the text parse and the scan. Entries are keyed by the file's path, size and modification time, so a changed file is read again; the folder is limited to 4 GB, least recently used first; a damaged entry is ignored. On the 1.7 GB export a cropped existing-ground read fell from about 15 s to 1.3 s on the second run, and listing its surfaces became instant. Turn it off with the QGIS setting `LandXMLTools/tinCache` = false or `LANDXMLTOOLS_TIN_CACHE=0`.
- **Grading Edge Analysis (Walls and Batters)** in Site grading (G6): from a design and an existing-ground surface, without a grid. The earthworks footprint (where they differ by more than 0.01) is walked edge by edge on the design TIN, so lengths are true plan lengths. An edge standing more than the tie-in tolerance (default 0.1) above or below existing ground is a retained edge with that height; where it ties in, a near-vertical face behind it is a retained edge and a batter face a batter, with the height from the nearest flat (platform) level. Near-vertical or batter faces between platforms that do not reach the footprint edge are internal steps, with height and length. Outputs: edge runs as 3D lines, internal steps as polygons, an HTML report and a CSV schedule (length by height band, cut and fill, maximum height, length and face area over 1 unit). Synthetic closed-form tests; on two private site-grading exports it runs in under 1 s per site.
- **Surface styles** (`landxml/surface_style.py`): colour a surface by elevation, slope, cut/fill depth or aspect with preset or custom breaks and colours, written as short text (`slope`, `depth: -2, 0, 2`, `elevation: equal 9 | terrain`, `plain #cfd8c4`). Elevation and depth bands are cut exactly inside each triangle. Used by three new tools:
  - **Surface Classes (Slope, Elevation, Depth or Aspect)**: one styled polygon per class with its plan area.
  - **Site Grading Report (HTML)**: one page per site with exact cut and fill, styled plan maps with the daylight line and platform labels, platforms, the edge schedule and internal steps.
  - **Export 3D Scene and Viewer (Site Grading)** (G7): `scene.json` (format `landxmltools-scene`, version 1) and binary PLY meshes — the design and surrounding existing ground coloured by their styles, daylight lines, platform outlines, retained edges and batters, road centrelines at profile level (conceptual, not cut into the terrain), labels and camera views, in the output CRS relative to an origin. `tools/render_scene.py` is a PyVista reference renderer. Checked on two private site-grading exports (scene in about 4 s per site, renders compared with the earlier project renders).
- **Export 3D Scene and Viewer** (Reports and sheets): 3D scenes of any area for road or site work — LandXML surfaces each with its own style (`EG = plain #cfd8c4; FG = depth over EG`, ground under a design left out), DEM rasters thinned to a triangle budget, contours with index contours, alignments (3D at profile level, otherwise draped) with station labels, LandXML feature lines and breaklines, and QGIS line or polygon layers (own Z or draped). The area is a map extent, polygons, or a corridor along an alignment between two stations, which gets side and along views. Checked on a 60 MB road export (600 m corridor stretch), a 45 MB export with a 550,000-triangle terrain and a DEM made from it.
- **Interactive 3D viewer** (`landxml/viewer.html`, `landxml/scene_viewer.py`), written with every scene: one self-contained HTML file, plain WebGL 2 with no libraries and no network, so nothing is installed. Rotate, pan, zoom; layer switches and opacity; vertical exaggeration; click a surface for its coordinates, level and cut/fill; saved views, a plan view and the current view as text for the tool; PNG export at up to 3 × with the legend and compass beside the model. Labels are callouts with leaders beside the model; station labels sit at their points and are thinned where they would overlap. The PyVista reference renderer gained the same callouts and a side panel with the legend and a compass.
- **Finished-road style and drive-through.** A surface styled `road: carriageway, shoulder, lanes` (for example `TOP = road: 7, 1.5, 2`) is painted in the viewer from each point's station and offset from the alignment: asphalt, edge lines, dashed centre and lane lines, shoulders, verges, and side slopes by slope. Scenes carry the alignments at profile level as drive paths, and the viewer's **Drive through** panel moves a camera at eye height along a path, with a station slider, play at 30, 50 or 80 km/h, sky and distance haze, and video recording (WebM). `viewer.html#drive=0.4` opens a drive 40 % along.
- **Before/after switch** in the viewer: ground removed under a design is kept as a hidden surface, so the viewer can show the existing ground or the design.
- Alignments stay visible, faintly, where the ground hides them (cuts), instead of seeming to drop out.
- Scene format version 2: optional `paths`, extra per-vertex PLY properties (`station`, `offset`), the `road` style, and `visible`/`pair` on surfaces; readers ignore what they do not know.
- **Long roads in the viewer**: an **Along the road** panel with a plan minimap of the drive paths (click to look at that part of the road from its side, the driver's position marked) and a go-to-station box; long corridors also get a side view per stretch (`STRETCH`, default every 1,000 stations). Links ending in `#station=12+350` or `#drive=0.4&play` open at a station or start driving.
- **Water and raised polygons in 3D scenes**: `WATER` draws a flat water surface at a level over the parts of a surface below it (exact shoreline), and polygon layers are raised to solids from a height field (`EXTRUDE_FIELD`) or a fixed height.
- A finished-road surface without `over` now cuts every other LandXML ground surface under it, so the ground cannot cover the road; 3D scene paths and lines have no gaps longer than 5 m, so a long tangent is no longer cut short at the edge of the area.
- **Synthetic corridor for feasibility studies.** For an alignment with a profile but no designed corridor, `SYNTHETIC` (the alignment name) builds a concept finished road on the profile: a cambered carriageway, shoulders and cut or fill side slopes that run out to the first ground surface or DEM in the scene (carriageway, shoulder, lanes, crossfalls, cut and fill slopes and the longest slope are parameters). It is drawn with the road style, cuts the ground under it (before/after works), becomes the drive path, and the log gives approximate cut and fill and warns where the slopes do not reach the ground (a deep crossing or high embankment, likely a structure). When the ground is a DEM and the alignment carries a surveyed ground profile (`ProfSurf`), the log reports the DEM's median height difference from it (a vertical datum difference such as ellipsoidal against local heights shows up as a steady offset), and `SYN_MATCH_PROFILE` shifts the DEMs by that amount. The log also gives the volumes with the no-daylight stretches left out. No superelevation, widening or drainage.
- **Concept structures on a synthetic corridor.** Stretches where the side slopes do not reach the ground become bridges (deck, parapets, piers at `SYN_BRIDGE_SPAN`, abutments; `SYN_BRIDGES`), and the earthworks leave them out. Culverts come from a CSV schedule (`SYN_STRUCTURES_CSV`) or typed entries (`SYN_STRUCTURES`, e.g. `1+250 = box 3 x 2.0 x 2.0; 1+840 = pipe 2 x 0.9, skew 15; 1+364-2+293 = bridge, span 25`): box culverts with their cells, walls and slabs, pipes, headwalls and splayed wingwalls, barrels running toe to toe. Common headings are recognised (chainage/station, cells, span or cell_b_m, rise or cell_d_m, diameter, skew, invert_in/invert_out, type, site/id) and the log lists the columns used and ignored. A barrel without 0.6 m cover under the road is drawn lower with a warning (raise the grade or lower the invert); ground low points under 1.5 m or more of fill with no structure within 50 m are listed as possible crossings, not sized. A scheduled bridge takes `span`, `spans` or `piers` (pier chainages; CSV columns `spans` and `piers`) and replaces the automatic bridge it overlaps. Each structure gets a callout label and a named view; `viewer.html#view=C3` opens one. A coarse DEM is split finely near the road so it is cut cleanly along it.
- Synthetic corridors take several alignments (`SYNTHETIC = A; B`), cut the ground once for all of them, and `SYN_GROUND` names the ground the side slopes run out to, with fall-backs where it does not reach (for example `Platforms FWL, EG`: the graded platforms, then the existing ground).
- **Ground at structures.** `SYN_FIT_PROFILE` fits DEMs to the alignment's surveyed ground profile along the road, fading out over `SYN_FIT_WIDTH` each side, so valleys a coarse DEM smooths over come back at the crossings. `SYN_CHANNELS` (on by default) cuts a channel at each culvert: the barrel's width at its inverts between the headwalls, rising 1:20 for 30 m beyond them, with 1V:1.5H banks. Headwalls stand over the openings, so the cells show, with an apron at bed level; a culvert's view looks at its outlet.
- **Faster throughout.** Descendant lookups in parsed LandXML use an index built once per tree (two binary searches instead of a walk); the parser builds elements without per-event method calls; TINs are rasterized by sampling cell centres in row bands against a triangle index instead of a Python loop per triangle; station and offset against a path use a grid join with a growing search radius. On the test files: Export Complete Road Design 24.6 s → 9.7 s, Surface Cut/Fill 9.8 s → 5.0 s, Export 3D Scene and Viewer (corridor with a finished-road style) 40.6 s → 9.4 s; results unchanged.
- **Clean edges where a design sits over the ground.** Ground triangles that cross a design's edge are split finely before the ground is cut, and ground under the design is only ever lowered to it, never raised, so platform edges, walls and fill toes no longer show saw-teeth or spikes.
- Surface Cut/Fill, Corridor Section Quantities and Profile Cut/Fill take the same **Vertical unit** choice as the site-grading tools, for files that do not declare it (Civil 3D metric exports usually omit it).
- `SYN_GROUND` can name a designed surface (one drawn `over` the ground), and a scene keeps one hidden before/after surface per ground however many designs cut it.
- **Faster 3D scenes.** Surfaces sampled many times (draping lines and labels, side slopes, ground cutting) are indexed once (`TinIndex`): a run with a DEM, a synthetic corridor and seven culverts went from 86 s to 18 s.
- `road_features.centerlines_3d` (shared by 3D Centerlines and the scene export); `tin_overlay.sample_tin` samples a TIN at points.
- `elevationUnit` on the LandXML `Units` element is accepted as the vertical unit (`verticalLinearUnit` wins when both are present).

### Fixed
- **3D Centerlines and Station Points: levels and stations no longer shift after element joins.** Where two alignment elements met at points that agree to 1e-9 but not exactly, the join was kept twice while the stationing kept it once, so every later point took the station, and level, of a point further back (a 42 m step on a 16.7 km road export). Joins now merge exactly as the alignment reader does. 3D centrelines also follow the profile's own samples, so vertical curves on long tangents are no longer cut straight.
- Civil 3D feature lines (corridor feature lines included) were skipped: Civil 3D writes a feature-line `Curve` as three points on the arc (`Start`, `Center` = the middle of the arc, with its elevation, `End`) with only a `radius` and no `rot`. Such curves are now drawn through those three points, with chords within 5 mm of the arc.
- Feature-line elevation points (`<Location desc="FeatureLine Elevation Point">`, where Civil 3D keeps levels between PIs) were ignored, so the levels between PIs were a straight grade. They are now inserted into the line, and the points sampled along curves are graded between them, as Civil 3D grades them. Checked against a Civil 3D 2022 export of a 2.7 km corridor: lines read back within 6 mm of Civil 3D's levels.

### Changed
- Geometric Design Review barrier warrant (R1): the fill height is measured from the hinge to original ground at the toe, where the design line meets the ground, instead of to the daylight point, and the side slope is the batter from hinge to toe. Toe drains, benches and walls beyond the toe no longer change the height or slope. Where the design line ends above the ground, its last point is taken as the toe. New `earthworks.embankment_toe`; synthetic tests with a toe drain, a truncated section, sloping ground and a cut.

## 1.10.0 — 2026-11-07

### Added
- Grading Quantities, Earthwork Balance and Grading Revision Comparison work without a volume surface (OpenRoads, or Civil 3D files exported without one): enter the design surface and an existing-ground surface, and the exact depth surface is built by intersecting the two TINs (`landxml/tin_overlay.py`), so volumes stay exact with no grid. Balance sites are written `Design | Existing`; the comparison takes an existing-ground surface per revision. Only the area both surfaces cover is counted. Synthetic closed-form tests; on a private site the result equalled the Civil 3D volume surface and summary within 0.001 m³ (1.2 million existing-ground triangles, about 6 s).
- **Plan and profile sheets** from Geometric Design Review: an optional A3 landscape PDF with one sheet per chainage window (default 1,000 m). Each sheet has a plan strip with chainage ticks and numbered findings, a banded long section (design, recommended and existing-ground profiles, changed PVIs, finding ranges, then design speed, horizontal curves, vertical curves and grade bands, with phasing findings outlined across the horizontal and vertical bands), a findings table, a floating legend and notes, and a title box. The HTML review report gains the same banded long section; findings are numbered consistently in the report, the sheets and a new `finding_no` field of the findings layers. The phasing diagram is retained as the whole-alignment overview. Road Design Report keeps its own long-section and does not repeat the banded one in its optional review section.
- **Grading Revision Comparison (Volume Surfaces)** in Site grading (land development): compares two revisions' volume surfaces, from one LandXML file or two, with exact cut and fill for each and the change, overall and by zone from an optional polygon layer (centroid assignment). Optional depth-change raster (second − first; missing depth counts as zero) and HTML report. Synthetic closed-form tests; on a private site-grading project both revisions matched the Civil 3D summaries.
- **Earthwork Balance (Volume Surfaces)** in Site grading (land development): balances cut and fill across one or more volume surfaces (one per site) under shrinkage scenarios (default 0.85, 0.90, 0.95 compacted per in-place). Each site uses its own cut first, surplus then supplies the other sites, and the rest is reported as import (compacted and in-place borrow) or spoil. Optional unsuitable share of cut and topsoil stripping, reported separately. HTML report and CSV table. Synthetic two-site fixture and tests; checked on two private site-grading exports.
- **Basin Stage–Storage** in Site grading (land development): exact stage–storage table (level, depth, water area, storage) of a pond or basin TIN, integrating water depth over each triangle (no grid). The rim defaults to the spill level, the lowest point on the surface edge; a rim above it is flagged. With a required volume, capacity is checked with freeboard and reported as PASS, PASS TO RIM ONLY, FAIL or NOT CHECKED. HTML report and CSV table. Synthetic closed-form fixture and tests; reproduced two private site-grading pond checks.
- **Platform Levels and Falls** in Site grading (land development): classifies a design (finished-level) TIN by slope (flat, sloping, batter, near-vertical) and groups edge-connected flat triangles into platforms. Each platform gets its area, mean/minimum/maximum level, overall fall and fall direction from an area-weighted plane fit, mean local grade and the share of its area flatter than the minimum fall; platforms below the minimum fall (default 0.5 %) are flagged as ponding risks. Outputs platform polygons and an HTML report with slope-class areas. Slopes account for horizontal and vertical units. Synthetic fixture and tests; checked on two private site-grading exports.
- **Grading Quantities (Volume Surface)** in Site grading (land development): exact cut, fill and net volumes from a volume (depth) TIN surface such as a Civil 3D TIN volume surface, integrating each triangle as a linear prism split exactly at zero depth (no grid). Reports plan, cut, fill and at-grade areas, maximum depths and exact plan area by depth band, with an HTML report and optional depth-band CSV. Optional reconciliation against the Civil 3D cut/fill summary reports PASS or FAIL within a tolerance (default 0.01). The volume surface is detected only from unambiguous evidence; an elevation surface selected by mistake is flagged. Synthetic closed-form fixture and tests; on two private exports the results matched the Civil 3D summaries within 0.005 m³.
- Roadway station equations: shared validated internal/engineering mapping, explicit locator regions for repeated stations, gap errors and back/ahead boundary labels in metric and foot notation. Station-point exports add engineering station, region and distance; Complete Road Design retains an equation table. Reports identify internal stationing; section drawings and Profile Viewer CSVs expose engineering labels/regions. Preparation and recommended LandXML retain equations. Numeric station fields, physical sampling, profiles and quantities retain internal stationing. Decreasing progression is explicitly unsupported. Synthetic Civil 3D/OpenRoads metadata fixtures and a private ORD comparison cover the feature. Issue #40.
- Developer instructions for generating local Graphify architecture graphs, with source-commit provenance and review of inferred links and health warnings. All graph outputs are ignored by Git and excluded from plugin release ZIPs, with packaging regression coverage. Issue #43.
- GitHub Actions runs the full synthetic test suite in headless QGIS 3.44.15 and 4.2.3 containers on pull requests and pushes to `main`, including Processing, preparation, GUI, project settings, styles and station/offset locator regressions. Tests use QGIS's application fixture for initialization and shutdown. The existing parser/earthworks and Bandit checks remain. Issue #35.
- Interactive **Station & Offset Locator** dock with LandXML alignment selection, arbitrary map clicks, signed left/right offsets, alignment/position markers and manual station/offset lookup. Reuses true-distance stationing for sampled tangents, circular curves and clothoids; supports the existing explicit coordinate operations and CRS/unit validation, preserves source station/offset units, and works with QGIS 3.44 and QGIS 4. Unsupported station equations and missing start stations are reported. Issue #29.
- Complete Road Design has a **Load exported layers into the project** checkbox, enabled by default. Successful exports queue the generated GeoPackage layers, retained DEMs and contours for QGIS Processing to load into the run's project, with source XML links for the Profile Viewer. Disabling the option exports files only. Temporary DEMs, disabled outputs, failed/cancelled runs and runs without a project do not add layers. Part of issue #22.
- Each tool dialog remembers its last successful inputs (files, alignment, surface and profile names, units, intervals, coordinate settings and criteria) in the current QGIS project, restored when the project is reopened. Explicit inputs from Processing history take precedence; outputs and the Prepare LandXML confirmation are not remembered; scripts, models and batch runs keep their normal defaults. Issue #21.
- Shared road-review presets for complete-package and individual vector outputs, with role-based existing/design/datum colors where attributes support them, station and profile labels, and index/minor contour styling. Default vector styles are embedded in GeoPackages after sinks close, so they persist when reopening or sharing the file, including file-only exports. Memory outputs receive the same presentation. Existing earthworks styling is preserved; geometry, units and quantities are unchanged. Issue #25.

### Changed
- The site-grading tools (Grading Quantities, Platform Levels and Falls, Basin Stage–Storage, Earthwork Balance, Grading Revision Comparison) have their own toolbox group, **9. Site grading (land development)**, apart from the road-corridor earthworks. Algorithm IDs are unchanged, so saved models keep working. Each has a **Vertical unit** choice for files that do not declare it (Civil 3D metric exports usually omit it); the unit is never inferred, and a choice that contradicts the declared unit is an error.
- Geometric Design Review's HTML report drops the separate 'Recommended profile — change in elevation' chart; the banded long section and the recommended PVI table show the proposed changes.
- The grading reports are visual: summary figures at the top, plus a depth-band chart (Grading Quantities), a platform plan with fall arrows (Platform Levels and Falls), a stage–storage curve with the freeboard level, rim and required storage (Basin Stage–Storage), cut/fill and balance bars (Earthwork Balance) and cut/fill bars by zone (Grading Revision Comparison). Charts are inline SVG and print with their colours.
- The user guide illustrates every grading tool with figures from a synthetic grading site (`tools/guide/make_grading_demo.py`, `tools/guide/make_grading_figures.py`): a depth map with the platforms, each report, and the depth-change map.
- Station-point layers from Create Alignment Station Points and Complete Road Design always add `engineering_station`, `station_region` and `distance_along`, including files without equations (`station_region = 1`). Existing numeric station fields remain internal; scripts and joins should account for the added fields. Issue #40.
- `AGENTS.md` is kept out of the release ZIP; it is for contributors' coding agents, not plugin users.
- Contribution instructions use branches in the main repository for collaborators, require both CI checks, an up-to-date branch and Edmond's approval, and reserve version tags and releases for Edmond. Feature PRs keep changelog entries under `Unreleased` without changing version numbers or release dates; release preparation retains semantic versioning and consistent metadata checks.
- Complete Road Design automatically names DEMs and contour GeoPackages using the XML basename, embedded surface name, and a supported terrain role (existing ground, finished grade, datum, design or proposed). Missing names have numbered fallbacks, unsafe characters are replaced, and collisions are disambiguated. Generic design surfaces are not assumed to be finished grade. The main mixed-data GeoPackage and its tables keep their existing names. Existing exports are not renamed; scripts expecting the former numbered terrain filenames need updating. See issue #22.
- Station-point presets use a persisted `station_label` field with source-unit stationing (feet: `50+00`; metres: `5+000`) and omit `.00` on whole stations. Unknown units stay numeric. Contour labels omit unnecessary decimal zeros while retaining fractional elevations; existing-ground minor contours are brown/dashed, proposed/design contours blue/solid, and minor lines are thicker for contrast.

### Fixed
- Profile Viewer: clicking the profile centres the map on that chainage again when the profile was opened from the file, or picked on a station point, a findings point or a single alignment element. The viewer now finds the alignment's full line in the project (the longest line feature for that alignment from the same LandXML) whenever the picked feature does not cover the clicked chainage, and drops a map link left over from another alignment.
- The Profile Viewer and Station & Offset Locator docks scroll their contents instead of imposing a large minimum size, so docking them no longer squeezes the Processing Toolbox and other panels out of view.
- Recommended profile back into Civil 3D: the LandXML output is now a comparison file. It carries both the design and the recommended profiles on a copy of the alignment renamed `… (review)`, because Civil 3D's LandXML import always creates a new alignment. A new changed-PVI CSV lists each PVI whose station, level or curve length changed. The help, README and user guide say to apply fixes to the design alignment with the text file and Create Profile from File.
- Geometric Design Review creates its findings map points by default, so findings appear on the alignment without enabling an extra output. Without an output CRS the review still runs and warns that the points were skipped.
- Every tool warns when stored coordinate order is kept but the first plan coordinate looks like a northing (Civil 3D and OpenRoads write Northing, Easting), so layers are not placed millions of metres apart by mistake. The interpretation is never changed automatically.
- Invalid station equations are reported by inspection without blocking independent terrain exports or surface cut/fill. Station-dependent Processing tools reject them with a clear QGIS error; Complete Road Design rejects the package before creating outputs. Issue #40.
- Complete Road Design automatically omits empty output layers when optional LandXML data or required dependencies are absent, including after alignment/surface filtering. The Processing log reports skipped outputs; unchecked options and existing invalid-data validation are preserved. Empty engineering and contour GeoPackages are not retained or loaded. Issue #39.
- Complete Road Design: layers loaded into the project are stacked for reading. DEMs sit at the bottom, then surface boundaries, contours, other lines, the alignment and points on top. Before, the DEMs were added last and hid everything under them. Issue #37.
- Station & Offset Locator uses the shared **Coordinate interpretation (OpenRoads: usually Swap X / Y)** label and help text to match the Processing tools. Issue #29.
- Station & Offset Locator accepts engineering station input with `+` (feet: `803+19.359`; metric: `1+025.359`). Station fields and results always use engineering notation after map clicks and manual lookup when source units are declared. Stations and clicked offsets show three decimal places while markers retain calculation precision. Issue #29.
- Terrain names from prepared LandXML use the original surface name preserved in `LandXMLTools.Prepare` metadata instead of generated `Prepared …` labels. Role wording already present in the surface name is not repeated. The preserved source filename also supplies role evidence, so an explicitly named finished-surface export can refine a proposed role to finished grade; datum surfaces remain distinct.
- Processing's no-argument layer-registry refresh callback now works with LandXML name dropdowns and preserves their selected value, preventing the reported missing-argument Python error.
- Main could not load after #24 and #26 were merged: conflicting class lines were both kept in `road_features.py` and `complete_export.py`, and the alignment tool lost its default style. Each tool now has both the remembered inputs and the default styles, and CI byte-compiles every module and rejects merge-conflict markers. Issue #27.
- Remembered Processing inputs work on QGIS 4, where `processing.gui.AlgorithmDialog` was replaced by `AlgorithmWidget`. Before, every LandXML tool dialog failed to open on QGIS 4.2. Issue #27.

## 1.9.2 — 2026-10-21

### Fixed
- Profile Viewer cross-sections: moving the mouse over the section chart raised `TypeError: 'NoneType' object is not subscriptable`. The hover now uses the same offset range the chart is drawn with. The bug dated from 1.7.0.
- Profile Viewer: clicking the long section beyond the stations covered by cross-sections kept showing the end section with no explanation and pulled the marker back to it. The nearest section is still shown, but the label now says there is no section at the clicked chainage, and the marker stays where you clicked.

## 1.9.1 — 2026-10-21

### Fixed
- AASHTO metric reviews with e max 4% and 6% now read the required superelevation from Tables 3-8 and 3-9. Before, the rate was estimated as V²/127R − f max, which gave lower rates than the Green Book on most curves. The US customary e max 4% and 6% tables were checked against the printed Green Book and match.

## 1.9.0 — 2026-10-21

### Added
- A shared Volume units selector in all three earthworks tools: Automatic (CY for feet, m³ for metric), CY, CF or m³. Explicit choices apply to reports, tables, material volumes, mass-haul ordinates and Processing totals across input unit systems.
- AASHTO 2018 e max 10% in Geometric Design Review and Road Design Report, with Table 3-11 radius/rate thresholds for US customary and metric criteria. Existing e max selection indices are unchanged; unsupported standards and out-of-table speeds are rejected.
- Prepare LandXML from Geometry and Terrains combines matching exports, preserves complete inputs, and samples missing surface profiles and terrain cross-sections directly from visible TIN triangles. Includes explicit metadata validation, disconnected-profile support, coverage warnings and atomic output.
- Continuous integration: GitHub Actions runs the parser and earthworks tests (GDAL, no QGIS) and a Bandit scan on every push to `main` and every pull request. The release script already leaves `.github/` out of the plugin zip.

### Changed
- Earthwork volume exports now use cubic yards (CY) for foot-based projects and cubic metres (m³) for metric projects, including cut/fill, materials, mass-haul, interval tables, footprint attributes and Processing totals. Reports and tables identify the unit; existing numeric volume field names are retained. Regenerate earlier outputs. Volume calculations require known horizontal and vertical units; profile/section units must match. Surface volumes account for output CRS and elevation units separately, and recorded Civil 3D comparisons honor declared volume units.
- Coordinate-interpretation selectors include an OpenRoads axis-order reminder and shared help explaining standard LandXML Northing/Easting order, Civil 3D, control-point verification and matching geometry/terrain settings. Existing choices and defaults are preserved.
- Numbered Processing groups follow the suggested workflow from inspection and preparation through extraction, quantities and review to reports and sheets. Algorithm IDs remain unchanged.
- Raster tools expose a configurable large-grid warning threshold (250 million cells by default; 0 disables it). Exceeding the threshold logs estimated minimum memory and continues, including Surface Cut/Fill, which previously retained a hard default limit.

### Fixed
- AASHTO metric reviews with e max 8% now read the required superelevation from Table 3-10. Before, the rate was estimated as V²/127R − f max, which gave far lower rates than the Green Book on most curves (for example 0% instead of 5.8% at 80 km/h and R = 500 m). e max 4% and 6% still use that estimate until Tables 3-8 and 3-9 are added.
- AASHTO US customary criteria: Table 3-10 (e max 8%) at 55 mph had the radii for 7.2–8.0% copied from the 50 mph column, and Table 3-2 gave 200 ft instead of 190 ft for 30 mph on a 3% upgrade. A new test checks the bundled AASHTO tables against the Green Book equations.
- Release ZIPs include non-ignored untracked runtime files, preventing a newly added module from being omitted and breaking plugin startup.
- Section quantities no longer integrate across intervening stations with missing earthworks data.
- Explicitly import GDAL and OSR so core reprojection and GeoTIFF writing work in a fresh Python process.

## 1.8.1 — 2026-10-15

### Added
- AASHTO 2018 US customary criteria (ft, mph) alongside metric criteria, including grade-dependent stopping sight distance, crest/sag K, radius, superelevation and runoff tables. Design Review and Road Design Report support international-foot and US survey-foot LandXML; findings, speed inputs and recommended-profile exports retain the appropriate units.

### Changed
- **Printable reports.** All HTML reports now print to PDF from Chrome or Edge with A4 page layout, the report title and page numbers in the footer, chart colours and legend swatches kept, headings kept with their charts, and long tables split across pages with repeated headers. Previously, long tables were moved whole to the next page, leaving blank pages (a 4-alignment design report went from 89 to 54 pages), and the first page held only the title.
- Tables align numbers and chainages right and text left.
- Review findings are grouped by category, with severity, code and chainage range in compact columns and wider issue and recommendation columns.
- The Road Design Report opens with a table of its alignments (chainage, length, design speeds, findings).
- User guide: where to find the Profile Viewer (Plugins menu and toolbar, not the Processing Toolbox) and how to save reports as PDF.

### Fixed
- The design-speed card listed speeds recorded beyond the end of an alignment.
- A curve that spans a speed change is described as such instead of "0 m from the speed change".

## 1.8.0 — unreleased

### Fixed
- Where Civil 3D records two speeds at the same station, the later one is used (previously the higher).

### Added
- **Geometric Design Review.** Checks alignment phasing, stopping sight distance (on the profile and on horizontal curves), crest and sag K, vertical curve length, grades and critical lengths, radii, transition spirals, tangents, broken-back and isolated curves, superelevation and run-off, combined grade, and safety barrier warrants from embankment height and side slope.
- **Selectable standards:** Kenya RDM 1.3 (2025) Geometric Design manual (default) and AASHTO 2018 (Civil 3D's default basis), as editable JSON criteria files. Every finding cites its table.
- **Recommended profile for Civil 3D:** phasing and K fixes are computed as PVI moves and curve lengths, capped by a maximum level change, and written as a Create Profile from File text file and a LandXML profile.
- Findings table, findings on the map, and an HTML report with a phasing diagram, level-change chart, recommended PVI table and barrier schedule.
- **Design speed variations.** Speed stations defined in Civil 3D are followed section by section, and each element is checked at the highest speed over its length. New checks flag speed steps above the standard (with the intermediate sections to add), short transition or oscillating speed sections, and sharp curves just past a speed reduction (RDM S3.10.3, S5.5, S5.14). The report tabulates the speed sections.
- Design speed by chainage ranges, to override the file.
- The Road Design Report can include the review for each alignment.
- **User Guide and Technical Manual** (PDF, `docs/user-guide`), with figures produced by the plugin from a synthetic demonstration road (`tools/guide`).
- `tools/build_release.py` builds the plugin repository ZIP and runs the plugins.qgis.org upload and security checks.

## 1.7.0 — unreleased

### Added
- **Name dropdowns and forgiving matching.** Surface, alignment, profile and section-surface fields list the names in the chosen LandXML file and refresh when the file changes. Typed names match case-insensitively or by a unique part (`ogl`). Unknown names are reported with the available list. A fast catalog scans start tags only (0.2–0.4 s on 45–75 MB exports).
- **Automatic surface choice.** Surface Cut/Fill can leave both names blank. It then uses a recorded Civil 3D volume-surface pair, or the existing-ground profile surface against the Roadway corridor surface (datum/bottom preferred), and logs the reason.
- **Cross-Section Sheets and Report.** Multi-page PDF sheets (A4/A3/A1, 1–12 per sheet), an HTML report with a section table and drawings, and one PNG per section. Drawings show ground and design lines, cut/fill shading, pavement layers, the Datum, slope ratios with steep-slope highlighting, daylight offsets, centreline levels and areas. A station range and interval select which sections to plot.
- **Cross-sections tab in the Profile Viewer**, synchronised with the profile chainage, the map marker and section navigation, with PNG/SVG/PDF export.
- **Road Design Report.** A schematic plan, curvature, gradient, long-section/depth and superelevation diagrams, horizontal, vertical and superelevation tables, summary cards and design speeds, with long alignments split into chart strips.
- Alignment elements now carry their true length and spiral radii, and superelevation transitions are parsed.

### Changed
- Tables in HTML reports show whole numbers without decimals, and chainage labels at chart edges stay inside the drawing.

## 1.6.0 — unreleased

### Added
- **LandXML Profile Viewer** dock and **Pick Alignment Profile on Map** tool: click an alignment-based layer to open its existing-ground vs design long-section at that chainage. Shaded cut/fill, depth panel, VPI/K labels, transition markers, segment table, hover readout linked to a map marker, zoom/pan, on-grade tolerance and structure exclusions. Exports PNG, SVG, PDF (A3), clipboard image, station-table and segment CSV, and an HTML report. Drawn with QPainter; no plotting dependency.
- **Profile Cut/Fill Analysis (Existing vs Design)**: depth table, cut/fill segments with high-fill/deep-cut flags, cut↔fill transition points on the map, optional level-section earthworks and side-slope estimate, HTML report with long-section and depth diagrams.
- **Corridor Section Quantities**: from Civil 3D sample-line cross-sections — cut/fill areas, average-end-area volumes, mass-haul ordinate with shrink/bulk factor, Datum-based earthworks, corridor material (shape) volumes, embankment and cut side-slope areas for grassing with steep-slope flags, side-slope and footprint polygons along the alignment, and an HTML report.
- **Surface Cut/Fill (TIN Difference)**: shared-grid TIN difference GeoTIFF with cut/fill/net volumes and a comparison with any Civil 3D `SurfVolume` recorded in the file.
- Existing-ground (`ProfSurf`) profile parsing with gap handling; Inspect LandXML now lists surface profiles and recorded Civil 3D surface volumes.
- Processing outputs remember their LandXML source as a QGIS project layer property (not in the data) so the viewer can reopen it; new map outputs are styled on load.

### Fixed
- **Station-relative cross-sections were placed at false map coordinates.** `CrossSectSurf/PntList2D` holds offset/elevation pairs, but they were written as map X/Y (sections appeared near X = offset, Y = elevation). They are now positioned along their alignment as 3D lines, one per section surface; previously only the first surface of each section was read.
- **Hidden TIN triangles were rasterized.** Faces flagged `i="1"` (Civil 3D triangles hidden by surface boundaries) are now excluded from GeoTIFFs, boundaries and volumes. On a private export this changed a TIN-difference fill volume from 1,423 to 1,359 m³, matching the 1,358.8 m³ Civil 3D recorded.
- **Chainage drift on curves.** Station points, 3D centerlines and map profile overlays used chord lengths between densified vertices; on a 36 km private alignment the end station was 0.33 m short. True arc lengths now match declared alignment lengths to 0.1 mm.
- Profile control labels use `0+000.00` chainage for metric files (previously `1+00.00` style for all units).

### Validation
- 60 tests (parser, earthworks with closed-form expectations, Processing and offscreen GUI) pass on Linux QGIS 3.44.14. Six private Civil 3D exports were run through every tool (names withheld; see `docs/earthworks-validation.md`). The chart widget was exercised under PyQt6 6.11; desktop QGIS 4 checks remain outstanding. Ruff (`F,E4,E7,E9`) and Bandit report no findings.

## 1.5.0 — unreleased

- Removed the default 250-million-cell cap from TIN rasterization for both TIN to GeoTIFF and Complete Road Design exports, allowing the requested pixel size on larger surfaces.
- Ported Processing field creation to `QMetaType`, removed fixed EPSG defaults, required explicit CRS choices, and verified provider and algorithm execution under QGIS 4.2.2 and the headless QGIS 3.44.3 runtime.
- Added shared, namespace-tolerant LandXML parsing, conservative application/vendor detection, unit/CRS inspection and source-backed breakline/cross-section/profile handling.
- Examined private OpenRoads Designer terrain exports and added a synthetic fixture reproducing the relevant structure without project data. Exercised one private OpenRoads alignment/profile export in QGIS 3.44.3; survey-control validation remains outstanding.
- Added **Inspect LandXML** with JSON output and explicit axis-order, CRS and vertical-unit uncertainty. Preserved breakline `Feature@code`, `brkType`, IDs and surface attribution.
- Added engineering attributes, per-element alignment output, profile controls, multiple-surface handling in Complete Road Design, and safeguards against fabricated Z values or disconnected alignment geometry.
- Added parser and QGIS Processing tests plus installation, example-data, development-history and engineering cautions in the Markdown documentation. No release was published.
- Fixed provider unload when QGIS has already deleted its C++ object, and clipped 3D centerlines to the station range with profile elevations in both individual and Complete Road Design exports.
- Aligned station points to whole station interval multiples in both export paths; the individual tool now omits an off-interval alignment endpoint by default.
- Added profile-name labels to loaded profile graph lines and station/elevation labels to the optional profile control point output.
- Added optional vertical exaggeration to profile graph coordinates and expanded parabolic controls into labeled VPC, VPI and VPT points with tangent grade and K attributes.
- Added optional georeferenced schematic profile lines and control points beside the corresponding horizontal alignment, with explicit coordinate interpretation, CRS, lateral offset, and station-range clipping. The original station/elevation graph remains unchanged.
- Reran the updated branch's 31 parser and Processing tests under macOS QGIS 4.2.2; all passed. The developer also reported that the updated desktop workflow worked in QGIS 4.2.2. These checks do not establish survey-control accuracy or validate OpenRoads corridor exports.

## 1.4.11 — 2026-08-23

### Security
- **Fixed: QGIS Plugin Repository security scan (Bandit) still BLOCKED v1.4.10 for 1 finding: "Using ElementTree to parse untrusted XML data" at `safe_xml.py:45`.** v1.4.10's `safe_xml.py` already parsed untrusted LandXML exclusively through hardened `xml.parsers.expat` callbacks (not through `ElementTree.parse()`/`.fromstring()`), but it still imported the inert plumbing classes `ElementTree`, `ParseError` and `TreeBuilder` *from* `xml.etree.ElementTree` to assemble the parsed tree. Bandit's rule for this (B405, `import_xml_etree`) is a blanket "was anything imported from `xml.etree.ElementTree`/`cElementTree`" match — confirmed by reading Bandit's own `bandit.blacklists.imports` source — with no inspection of which names were imported or how they were used, so importing even non-parsing helper classes from that module trips it. Fixed by removing every import from `xml.etree.ElementTree`/`cElementTree`: `safe_xml.py` now implements its own minimal `Element`/`TreeBuilder`/`ElementTree`/`ParseError` equivalents, and uses `xml.etree.ElementPath` (confirmed *not* on Bandit's blacklist, and structurally can't be — it only evaluates a path expression against an already-built tree, it never parses raw XML text) to implement `.find()`/`.findall()` with CPython's own tested path-query semantics rather than a hand-rolled subset-XPath parser. No behavior change for any of the plugin's algorithms: verified zero Bandit findings across the entire plugin source (`bandit -r .`), byte-identical parse output against plain `ElementTree` across all 825,498 nodes of a real multi-megabyte sample LandXML file, and that DOCTYPE/entity-bomb/XXE attack payloads are still correctly rejected while ordinary malformed XML still raises a parse error.
- **Fixed: 15 Pyflakes findings (F403/F405) in `complete_export.py`** from `from qgis.core import *` making every name it used ambiguous ("may be undefined, or defined from star imports"). Replaced with an explicit import list of the 9 names actually used (`QgsCoordinateReferenceSystem`, `QgsProcessingAlgorithm`, `QgsProcessingException`, `QgsProcessingParameterBoolean`, `QgsProcessingParameterCrs`, `QgsProcessingParameterEnum`, `QgsProcessingParameterFile`, `QgsProcessingParameterFolderDestination`, `QgsProcessingParameterString`), matching the explicit-import style already used in every other file in this plugin.

## 1.4.10 — 2026-08-23

### Security
- **Fixed: QGIS Plugin Repository security scan (Bandit) blocked this release for 14 findings of "Using `xml.etree.ElementTree` [`.parse`] to parse untrusted XML data is known to be vulnerable to XML attacks."** Every LandXML file this plugin opens comes from Processing's file-picker parameter — i.e. it is user-supplied, untrusted input, not something the plugin author controls. Plain `xml.etree.ElementTree` inherits CPython's `expat` parser with its default entity-expansion behaviour, so a crafted file (billion-laughs entity bomb, or an external entity reference for XXE) could hang/crash QGIS or read local files before any of the plugin's own code ran. Replaced every `ET.parse(path).getroot()` call site (`core.py`, `alignment_algorithm.py`, `complete_export.py`, `road_features.py`) with a new internal `safe_xml` module that drives `xml.parsers.expat` directly and rejects any DOCTYPE declaration, entity declaration, or external entity reference outright — real LandXML exports never declare any of these, so a file that does is refused with a clear error instead of being parsed. Deliberately does not add `defusedxml` as a dependency (not part of any stock QGIS Python environment, would break the plugin on install); verified byte-identical output against plain `ElementTree` on synthetic data and on real multi-megabyte sample LandXML files, confirmed ~40% slower but correct, and confirmed entity-bomb/XXE/bare-DOCTYPE payloads are rejected while ordinary malformed XML still raises the normal parse error.
- **Fixed: Bandit "Try, Except, Pass detected" finding in `complete_export.py`.** `_delete_layer()` previously attempted `ds.DeleteLayer(name)` inside a bare `try/except Exception: pass`, silently swallowing any GDAL/OGR error alongside the expected "layer doesn't exist yet" case. Replaced with an explicit `GetLayerByName()` existence check before deleting, so a genuine OGR error is no longer masked.

### Changed
- **QGIS 4 / PyQt6 compatibility: fixed all "QT6 Check" enum-scoping findings** reported by the plugin repository's `pyqgis4-checker` across `alignment_algorithm.py`, `complete_export.py`, `processing_algorithm.py`, `params.py`, and `road_features.py`. PyQt6 requires fully-scoped enum access (e.g. `QgsWkbTypes.Type.LineString`, `QgsProcessing.SourceType.TypeVectorLine`, `QgsProcessingParameterFile.Behavior.File`, `QgsProcessingParameterNumber.Type.Double`) where PyQt5 accepted the legacy unscoped form; this check is informational rather than blocking for repository approval, but fixed anyway so the plugin loads cleanly under QGIS 4 without relying on PyQt6's temporary backward-compatibility shims.

## 1.4.9 — 2026-08-23

### Fixed
- **`ContourGenerate` crash: `TypeError: in method 'ContourGenerate', argument 1 of type 'GDALRasterBandShadow *'`**, reported against `Export Complete Road Design to GeoPackage` on real sample data. Root cause: `complete_export.py` called `gdal.Open(dem).GetRasterBand(1)` — the intermediate `Dataset` returned by `gdal.Open()` had no persisted reference, so it was garbage-collected immediately, invalidating the `Band` object before `gdal.ContourGenerate()` used it. Fixed by holding the `Dataset` in a named variable for the lifetime of the call. Reproduced and confirmed fixed with a minimal, real-GDAL repro script before being re-verified against both real sample LandXML files end-to-end.
- **Vertical profile extraction silently produced zero features on real data.** The code assumed a `<ProfileGeom>` wrapper with `<Line>`/`<Curve>` children carrying `staStart`/`staEnd`/`elevStart`/`elevEnd` attributes — a structure that does not exist in the real LandXML 1.2 schema or in either real sample file. The real structure is a flat, ordered `<PVI>`/`<ParaCurve>`/`<CircCurve>` sequence directly under `<ProfAlign>`. Added a new `core.read_vertical_profile()` that parses this correctly, including AASHTO symmetric parabolic curve sampling for `<ParaCurve>`/`<CircCurve>` elements, and wired it into `ProfileAlgorithm`, `Centerline3DAlgorithm`, and the Complete Road Design export. Verified against real sample data: profile elevations now match the raw PVI values in the source XML exactly.
- **3D centerlines always reported `z_source: "No profile; Z=0"` even when a matching profile existed.** `Centerline3DAlgorithm` and the Complete Road Design export both looked for an `alignment=` attribute on `<ProfAlign>` to associate a profile with its alignment, but real `<ProfAlign>` elements carry no such attribute — the owning alignment's name lives on the parent `<Alignment>` element, with `<Profile><ProfAlign>` nested inside it. Fixed by walking `<Alignment>` elements and searching within each for its nested profile, with a fallback pass for any `ProfAlign` that does carry an explicit `alignment=` reference. Verified: centerlines now correctly report `z_source: "Vertical profile"` with Z-values matching the source profile's elevation range.

### Known limitation (documented, not fixed)
- **Design-template cross-sections (`DesignCrossSectSurf`/`CrossSectPnt`) are not extracted.** Only absolute-coordinate `CrossSectSurf`/`PntList2D`|`PntList3D` cross-sections were ever supported. In one private sample, 3,235 of 3,569 `CrossSect` records are design-template-only and are skipped. Full support would require reconstructing XY geometry from alignment station + offset, which is a larger feature than a bug fix and was not attempted here to avoid an unverified, rushed change. `CrossSectionsAlgorithm` and the Complete Road Design export now emit an explicit `pushWarning()` reporting the skipped count instead of silently dropping the data, and the limitation is documented in the tool's help text.

### Changed
- Trademark/branding review for this release: no ArcGIS/ArcHydro/ESRI references were found in this plugin. "Civil 3D" file-format-origin references were kept, since they describe the source file format rather than a comparison claim.

### Verification
All 10 Processing algorithms were run end-to-end against two private sample LandXML files (project names withheld), including `Complete Road Design` run twice per sample mirroring the exact parameter combinations from the reported crash. Verification used a QGIS test stub whose parameter-reading and sink/geometry glue is backed by real `osgeo.gdal`/`ogr`/`osr` bindings rather than mocks, so algorithms ran to completion and produced real, inspectable GeoTIFF/GeoPackage output — not just "no exception raised." Final run: 22/22 cases passed, with output feature counts, geometries, and elevation ranges checked directly against the source XML.

## 1.4.8 — 2026-08-23

### Changed
- Documentation pass: replaced project-identifying client/site names with
  generic labels throughout `README.md`, `HISTORY.md`, `CHANGELOG.md`, and
  `examples/README.md` ahead of public distribution. No functional changes.

## 1.4.7 — 2026-08-23

### Fixed
This release fixes a systemic class of bug: several QGIS Processing parameter
constructors were called with keyword arguments that read naturally but do
not exist in the real PyQGIS API. These are silent no-ops under plain Python
(and under a permissive test double), but the real SIP-generated QGIS
bindings reject unknown keywords outright, so every one of these raised
`TypeError` the moment QGIS tried to build the algorithm's parameter dialog
— which is what produced the "'decimals' is an unknown keyword argument"
error, and (because the crash aborts `initAlgorithm()` partway through)
made it look like later input parameters were simply missing from the
dialog.

- **`decimals=` is not a constructor argument of `QgsProcessingParameterNumber`**, in any QGIS version. It never was — spin-box precision is set after construction via `setMetadata({'widget_wrapper': {'decimals': N}})`, which is what QGIS's own Processing dialog reads. Fixed at all 26 call sites across `processing_algorithm.py`, `alignment_algorithm.py`, `road_features.py`, and `complete_export.py` via a new shared `params.number_param()` helper that does this correctly (and preserves the intended precision, e.g. 8 decimals for rotation, 10 for the Helmert scale factor, rather than just dropping it).
- **`fields=` is not a constructor argument of `QgsProcessingParameterFeatureSink`.** The output feature schema is supplied at run time to `parameterAsSink()`, not at parameter-definition time. Fixed at all 7 call sites (one in `alignment_algorithm.py`, six in `road_features.py`).
- **`fileFilter=` is not a constructor argument of `QgsProcessingParameterRasterDestination`** (unlike `QgsProcessingParameterFile`, where it is valid). Fixed in `processing_algorithm.py`.
- **`self.parameterDefinition('OUTPUT').fields()` does not exist** — `QgsProcessingParameterFeatureSink` has no `.fields()` method. Five algorithms (`ProfileAlgorithm`, `Centerline3DAlgorithm`, `StationPointsAlgorithm`, `CrossSectionsAlgorithm`, and the shared `FeatureLinesAlgorithm`/`BreaklinesAlgorithm` base) relied on this to recover the output field schema inside `processAlgorithm()`; it would have raised `AttributeError` the moment any of these tools actually ran (i.e. even after the dialog opened successfully). Fixed by building the `QgsFields` schema directly inside `processAlgorithm()`, matching the pattern the two unaffected algorithms (`LandXMLAlignmentsAlgorithm`, `SurfaceBoundaryAlgorithm`) already used correctly.
- Also caught in the same audit: `LandXMLAlignmentsAlgorithm`'s `SEGMENT` parameter was missing `type=QgsProcessingParameterNumber.Double`, so it defaulted to the constructor's `Integer` type despite a fractional default value.

### Verification
Every one of the plugin's 10 Processing algorithms now has its
`initAlgorithm()` re-run against a QGIS stub whose constructor signatures
are transcribed from the official PyQGIS API docs and strictly reject
unknown keyword arguments (rather than silently accepting them, as a
permissive test double would) — this is the same class of check that would
have caught all four bugs above before release. The stub was cross-checked
by running it against the pre-fix code, where it reproduces the exact
`TypeError: 'decimals' is an unknown keyword argument` seen in real QGIS
3.44.6. TIN and alignment parsing were re-verified against both real sample
LandXML files with identical results to prior releases.

Live install/run testing inside actual QGIS was still not performed here —
this sandbox has no QGIS/GDAL runtime available — so a real install-and-run
smoke test in QGIS remains the recommended final check before store
submission.

## 1.4.6 — 2026-08-23

### Fixed
- **Packaging: plugin directory renamed from `landxml_tin_to_geotiff_v1.4.5` to the stable `landxml_tin_to_geotiff`.** A folder name containing dots is not a valid Python package name; QGIS imports the plugin using its folder name, so a version-suffixed, dotted directory name caused the plugin to fail to load (the regression this reintroduced was previously fixed once already, in 1.4.2 — the versioned name crept back into later release zips). The plugin folder name must now stay constant across all future versions so QGIS treats upgrades as updates rather than separate installs.
- Added the missing `QgsProcessingParameterString` import in `alignment_algorithm.py`. The "Extract LandXML Alignments to Lines" algorithm referenced this class in `initAlgorithm()` without importing it, raising a `NameError` as soon as the algorithm was registered.
- Corrected the `gdal.ContourGenerate()` argument list in `complete_export.py`. The previous call passed the wrong number/order of arguments (`dstLayer` was passed as `None` and the contour layer was passed in the `callback` slot, with an extra trailing argument), which raised `TypeError: ContourGenerate() takes from 9 to 11 positional arguments but 12 were given` any time "Export Complete Road Design to GeoPackage" ran with contour generation enabled (the default).
- Added a `LICENSE` file (GPL-2.0-or-later) — required for listing on the official QGIS plugin repository.
- Standardized `metadata.txt`: correct author/email, repository/tracker/homepage URLs, and a changelog reference.

## 1.4.5
- Completed Processing-parameter audit across the provider.
- Added alignment/profile/surface/name filters where the source structure supports selection.
- Added alignment curve/spiral segment-length control to downstream road exports.
- Added station endpoint control.
- Added DEM NoData control.
- Added complete-export layer inclusion controls.
- Added configurable station and vertical-profile sampling intervals to the complete export.
- Wired exposed parameters into processing logic so they affect results.
- Fixed LandXML point-list handling where empty XML child elements could evaluate as false.
- Corrected Feature Line extraction so it no longer also exports Breakline records.

## 1.4.3 — 2026-08-23

### Fixed
- Corrected `road_features.py` imports so TIN functions are loaded from `core.py`, not `alignment_algorithm.py`.
- Prevents provider-load failure before Processing algorithms are registered.
- Removed Python bytecode caches from the distributable package.

## 1.4.2 — 2026-08-23

### Fixed
- Replaced unavailable `QgsProcessingParameterDouble` imports with `QgsProcessingParameterNumber` using the `Double` type for QGIS 3.44 compatibility.
- Corrected the internal plugin package directory name so QGIS can import `landxml_tin_to_geotiff`.

## 1.4.1 — 2026-08-23

### Fixed
- Added clothoid spiral approximation for LandXML alignment spirals.
- Corrected LandXML/Civil 3D curve rotation handling for circular curves.
- Validated the sample bridge-realignment alignment against its LandXML length and element structure.

## 1.4.0 — 2026-08-23

### Added
- Expanded the plugin into a broader LandXML road and terrain GIS Processing provider.
- Added vertical profiles, 3D centerlines, cross-sections, station points, surface boundaries, feature lines, breaklines, and Complete Road Design → GeoPackage export.

## 1.3.0 — 2026-08-22

### Added
- Added horizontal alignment extraction, curve densification, alignment attributes, coordinate transformations and CRS controls.

## 1.2.0 — 2026-08-22

### Changed
- Converted the plugin into a native QGIS Processing provider.
- Added reusable coordinate interpretation and transformation options.

## 1.1.0 — 2026-08-22

### Added
- Added coordinate-operation controls, transformed-bounds diagnostics, and explicit output CRS handling.

## 1.0.0 — 2026-08-21

### Added
- Initial LandXML TIN → GeoTIFF conversion using original TIN topology and planar interpolation.
