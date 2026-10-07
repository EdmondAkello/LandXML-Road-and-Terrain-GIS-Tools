# LandXML Road & Terrain GIS Tools

A QGIS Processing provider for inspecting and importing LandXML terrain and road-design data. Originally created by **Edmond Akello** under GPL-2.0-or-later; the original attribution and license remain in place.

The **[User Guide and Technical Manual](docs/user-guide/LandXML_Road_and_Terrain_GIS_Tools_v1.8.1_User_Guide.pdf)** covers installation, every tool, the design review and the computation methods, with figures from a synthetic demonstration road. Its source and figure scripts are in `docs/user-guide` and `tools/guide`.

## Compatibility and validation

- **Version 1.8.0 (QGIS 3.44):** 89 tests pass on Linux QGIS 3.44.14. The release ZIP built by `tools/build_release.py` passes the plugins.qgis.org upload validator checks and security scan (Bandit with the repository's enabled rules, detect-secrets, flake8), and was installed from the ZIP and loaded, run and unloaded through QGIS's plugin loader. The design review ran on six private Civil 3D exports under both standards.

- **Version 1.7.0 (QGIS 3.44):** 72 tests pass on Linux QGIS 3.44.14, including the new cross-section sheets, design report, name catalog and dropdown sources. The dropdown widget was also exercised in a real QGIS Processing dialog, and every new tool ran on the six private Civil 3D exports. Desktop QGIS 4 checks remain outstanding.
- **Version 1.6.0 (QGIS 3.44):** the 60-test suite — parser, earthworks engine, headless Processing and offscreen profile-viewer GUI — passes on Linux QGIS 3.44.14 (conda-forge build). Six private Civil 3D 2022–2026 exports (alignments, design and existing-ground profiles, corridor sections, TIN surfaces and volume surfaces; project names withheld) were run through every tool; see [the earthworks validation notes](docs/earthworks-validation.md). Two TIN-difference results reproduce the volumes Civil 3D recorded in the same files to within 0.1 m³. The profile chart widget was also exercised under PyQt6 6.11 (the Qt binding used by QGIS 4), and every Qt enum used by the new GUI code resolves in PyQt6; a QGIS 4 runtime was not available for 1.6.0, so desktop QGIS 4 checks remain outstanding.
- **Version 1.5.0 — QGIS 4.2.2:** the updated PR branch passed all 31 parser and headless Processing tests with the macOS QGIS 4.2.2 runtime. The developer also reports that the QGIS 4 desktop regression workflow completed successfully after the profile and stationing changes. An earlier desktop check inspected a private OpenRoads terrain export and created and displayed its TIN GeoTIFF. These visual checks are not survey-control validation.
- **Version 1.5.0 — QGIS 3.44:** the parser and 31 headless Processing tests pass in a Windows QGIS 3.44.3 runtime, including provider registration, unload, profile labels, map profile placement and saved-output loading. A private OpenRoads road export was also exercised through the 3D centerline, station-point and profile tools. Survey-control and visual checks remain necessary. Metadata declares 3.44–4.99 to allow testing on both series.
- **Autodesk Civil 3D:** standard LandXML road and terrain entities are covered by synthetic regression fixtures. The upstream project documents earlier validation against a large Civil 3D export; that file is not in this repository and the result is not reproduced by this test suite.
- **Bentley OpenRoads Designer:** terrain and breakline structures were examined in private LandXML 1.2 exports and reproduced in a committed synthetic fixture. A private OpenRoads alignment/profile export also produced a partially covered 3D centerline in QGIS 3.44.3 with the requested coordinate swap. This is a runtime check, not a survey-control validation; the private file is not redistributed. OpenRoads corridor exports remain untested.

## OpenRoads Designer

**OpenRoads Designer → Export LandXML → QGIS LandXML Road & Terrain GIS Tools → Inspect LandXML → import terrain, alignment, profile, or features.**

The plugin reads exported LandXML. It does **not** parse DGN files or proprietary Bentley data. Export the entities needed for the QGIS workflow, inspect the file first, and compare results with known survey control. Bentley lists LandXML among OpenRoads Designer's export formats: [Bentley product documentation](https://www.bentley.com/products/openroads-designer/).

The examined OpenRoads terrain exports contain `Surfaces/Surface/Definition/Pnts` and `Faces`, plus `SourceData/Breaklines/Breakline/PntList3D`. Breaklines carry `Feature@code` and `Property` IDs, but no source feature name. The importer retains that code, breakline type and IDs without treating IDs as names. The exports declare `USSurveyFoot` but omit a CRS and vertical unit. The structural comparison is in [docs/openroads-sample-audit.md](docs/openroads-sample-audit.md).

## Processing tools

| Group | Tools |
| --- | --- |
| Utilities | Inspect LandXML |
| Terrain conversion | TIN to GeoTIFF |
| Vector extraction | Horizontal alignments |
| Road design extraction | Labeled vertical profile graph, optional map profile overlay and VPC/VPI/VPT controls with vertical exaggeration; 3D centerlines; cross sections and optional points; station points; Complete Road Design to GeoPackage |
| Surface extraction | TIN surface boundary; feature lines; breaklines |
| Earthworks and quantities | Profile Cut/Fill Analysis (existing vs design); Corridor Section Quantities (cut/fill, materials, side slopes for grassing, mass haul); Surface Cut/Fill (TIN difference); Cross-Section Sheets and Report |
| Reports | Road Design Report: plan, curvature, gradient, long-section and superelevation diagrams with element tables, and an optional design review (under Utilities) |
| Design review | Geometric Design Review: alignment phasing, sight distance, vertical and horizontal curves, grades, superelevation and barrier warrants against Kenya RDM 1.3 (2025) or AASHTO 2018, with recommended PVI changes for Civil 3D |

Outside the Processing Toolbox, the **LandXML Profile Viewer** dock and the **Pick Alignment Profile on Map** toolbar button (menu **Plugins → LandXML Road & Terrain**) show existing-ground and design profiles for an alignment you click in the map.

**Inspect LandXML** reports vendor/evidence, version, horizontal and vertical units, declared CRS, axis-order uncertainty, surfaces with point/face counts, alignments, profiles, cross sections, breaklines, feature lines, source bounds, elevation range and warnings. It can save JSON. Inspection never changes coordinates.

The Complete Road Design export writes an engineering GeoPackage and optional GeoTIFF/contour outputs. `alignment_elements` retains radius, curve length, spiral length and direction per element. Profiles and profile controls use **station/elevation graph coordinates with no map CRS**; they are not mapped onto the horizontal alignment. Breakline and feature-line layers preserve source names/descriptions when present, surface name, type, code and source IDs. When multiple surfaces are present, the exporter processes all unless a name is selected.

### Profile graphs and labels

In **Extract LandXML Profiles**, choose a **Profile graph lines** output and a **Profile control points** output, then load both result layers. The graph line is labeled with its profile name. The point layer contains source `PVI` controls labeled **VPI**, plus calculated **VPC** and **VPT** points for each supported parabolic curve. Point labels show source station (formatted `0+000.00` when the file declares metric units, `0+00.00` for feet or undeclared units) and elevation, incoming/outgoing tangent grades (`G1`/`G2`), and **K** at a parabolic VPI. Both layers include a `label_text` field; to restore a hidden label in QGIS, choose **Layer Properties → Labels → Single Labels → `label_text`**. Select the control-point output explicitly to create those points.

**Vertical exaggeration** defaults to 1. A value such as 10 multiplies the plotted Y coordinate of the graph and control points by 10; the `elevation` field and `Elev` label still show the source elevation, and `plot_elevation` records the scaled point Y. X remains source station. These graph coordinates have no map CRS and are not road-map locations. This parameter belongs to the individual profile extraction tool; Complete Road Design retains source station/elevation graph coordinates.

To see the profile **near its actual alignment in the map**, choose **Map profile lines** and optionally **Map profile control points** in the same tool. Select the same **Coordinate interpretation** and **Output CRS** that place your horizontal alignment correctly. For example, an OpenRoads export that needs **Swap X / Y** for the alignment needs the same choice here. The map outputs use the alignment's stationing, draw the profile to the left of the road at the chosen **Map profile offset** (negative values place it on the right), and carry the same labels and elevation attributes. Vertical exaggeration scales the elevation change around the profile's first elevation and adds it to that offset. The offset is in the source horizontal units; the default is 100. Profile parts outside the horizontal alignment's station range are clipped. These are **schematic plan-view graphics**, not surveyed road features or a true elevation axis. Use **Create 3D Road Centerlines** for an alignment at its actual X/Y location with profile elevation as Z. The map outputs require an explicitly selected CRS; the graph outputs remain available without one.

[FHWA defines K](https://highways.fhwa.dot.gov/safety/speed-management/speed-concepts-informational-guide/chapter-4-engineering-and-technical) as parabolic curve length divided by the absolute algebraic difference between grades in percent. Here K is in source station units per 1% grade change. A flat curve has no finite K and receives no K label. If horizontal or vertical units are undeclared or differ, verify the computed grades and K before design use; the plugin does not convert vertical units.

## Choosing surfaces, alignments and profiles

Name fields in every tool are editable dropdowns that list the names found in the selected LandXML file (surfaces, alignments, design and surface profiles, or cross-section surfaces), refreshed when you change the file. You can also type: matching is case-insensitive and accepts a unique part of a name, so `ogl` finds `OGL-LOT_REV 0`. A name that is not found is reported with the list of available names. In scripts and models these parameters stay plain text.

Blank surface names are filled from evidence in the file, and the choice is printed in the log. Surface Cut/Fill uses a Civil 3D volume-surface pair if one is recorded. Otherwise the base is the surface named in an existing-ground profile and the comparison is a `Roadway` corridor surface, preferring the datum/bottom surface for earthworks. Section tools use the same rule for section surfaces.

## Profile Viewer: click to view profiles

Press **Pick Alignment Profile on Map** and click an alignment line (or station points, cut/fill segments, cross-sections — any layer with an `alignment_name` or `alignment` field). The dock opens the alignment's long-section with the clicked chainage marked:

- **Existing vs design:** the longest existing-ground `ProfSurf` and the first design `ProfAlign` are chosen by default; any profile can be selected for either role, and **Other profiles** overlays the rest (for example option surfaces).
- **Cut/fill:** the area between the lines is shaded (fill green, cut orange), with a depth panel below, VPIs labelled with K values, and black dots at cut↔fill transitions. A table lists every cut/fill segment; selecting one zooms to it and marks its deepest point.
- **Interaction:** hover for chainage, both elevations and depth; the same chainage is marked on the map. Wheel to zoom, drag to pan, double-click to fit, click to centre the map there. **Grade tol.** treats small depths as on grade so ground noise does not create transitions; **Exclude** removes structure ranges such as bridges (`1200-1450; 2010-2030`).
- **Export:** PNG, SVG, PDF (A3 landscape), clipboard image, station table CSV at any interval, cut/fill segment CSV, and a self-contained HTML report.
- **Cross-sections tab:** when the alignment has corridor sections, the tab shows the section nearest the clicked chainage (or follows the cursor), with ◀ ▶ navigation, vertical exaggeration and PNG, SVG or PDF-sheet export. Stepping through sections moves the profile and map markers.

Layers created by this plugin's Processing tools remember their LandXML file as a layer property stored in the QGIS project (never in the output data), so clicking them opens the right file. For other layers — for example a GeoPackage loaded later — the viewer uses the file already open in the dock, or asks for it once and remembers it for that layer. Map positions come from the clicked feature's own geometry and its `sta_start`/`station_start`, `sta_end`/`station_end` or `length` attributes, so the marker follows whatever coordinate interpretation produced the layer.

## Earthworks and quantities

All three tools keep source units (volumes in cubic source units) and label their evidence. They are design aids, not a substitute for the design software's own quantity reports.

**Profile Cut/Fill Analysis (Existing vs Design)** compares the design profile with the existing-ground profile along each alignment. Depth = design − ground (positive fill, negative cut). Every source breakpoint is kept and zero crossings are inserted exactly. Outputs: a station table at any interval; cut/fill segments with length, maximum and mean depth and *high fill*/*deep cut* flags (default 3 m); cut↔fill transition points on the map; and an HTML report with the long-section and a depth diagram. A positive **formation width** adds a *level-section estimate* — area h·(W + s·h) with your fill and cut slopes, side-slope length h·√(1+s²) per side — for early-stage quantities when no corridor sections exist. It ignores ground cross-fall, pavement depth and ditches.

**Corridor Section Quantities** uses station-relative `CrossSect` records, as exported from Civil 3D sample lines:

- *Cut and fill* areas between the existing-ground and design section lines, average-end-area volumes, and a mass-haul ordinate with an optional cut-to-fill (shrink/bulk) factor. With **Use Datum** (default), earthworks are measured to the exported `Datum` links (bottom of pavement) and the design surface outside them.
- *Materials:* volumes of every closed corridor shape (for example pavement, base and subbase layers), using the shape area Civil 3D wrote.
- *Side slopes for grassing:* sloped surface areas between the formation hinge (outer edge of the corridor shapes) and the daylight point, measured on the finished design surface and split into embankment (fill) and cut slopes. Segments flatter than 1:10 (shoulders, ditch inverts, berms) are excluded, and slopes steeper than 1:1.5 are reported separately as needing stabilisation beyond grassing. Both ratios are parameters.
- *Map layers:* side-slope band polygons per interval and side (attributes: fill, cut and steep slope area, plan area) and an earthworks footprint between daylight lines, styled cut/fill/mixed.

Section surfaces are identified from source evidence when the names are left blank: existing ground is the section surface named inside an existing-ground `ProfSurf`, and the design surface matches a `Roadway@surfaceRefs` corridor surface. If either cannot be identified, the tool says so and you enter the name; material quantities are still reported. Sections farther apart than the maximum spacing (default 100) and excluded station ranges are not integrated, and the report lists what was skipped. No curvature correction is applied.

**Cross-Section Sheets and Report** plots corridor sections as multi-page PDF sheets (A4, A3 or A1; 1–12 sections per sheet) with a title block and legend, an optional HTML report (a table of every section plus drawings) and one PNG per section. Each drawing shows ground and design lines, shaded cut and fill, pavement layers from the corridor shapes, the Datum, side-slope ratios (red when steeper than the stabilisation threshold), daylight offsets, centreline FRL and EG levels, and cut/fill areas. Each section fits its own width at the chosen vertical exaggeration (default 2) unless a fixed half-width is set. A station range and interval select, for example, every 50 m between 10000 and 12000.

**Surface Cut/Fill (TIN Difference)** grids two TINs on a shared raster and writes compare − base as a GeoTIFF (styled cut-orange/fill-green), with cut, fill and net volumes and areas. Triangles hidden by a surface boundary are excluded. When the LandXML contains a Civil 3D `SurfVolume` for the same pair, its recorded volumes are printed and tabulated beside the grid result.

## Road Design Report

**Road Design Report** writes one HTML page per file, with a section for each alignment:

- Summary cards: length, chainage, curves and spirals, minimum radius, maximum grade, minimum crest and sag K, design speed (from Civil 3D speed stations or Roadway speeds) and maximum superelevation.
- A schematic plan with lines, curves and spirals in different colours, chainage ticks, start and end marks, a north arrow and a scale bar. Choose whether the file stores northing first (Civil 3D) or easting first. The plan is not georeferenced.
- Curvature diagram (1000/R, right +, left −) with radius labels and spiral bands.
- Gradient diagram with grade labels and crest/sag vertical curve bands.
- Long-section against existing ground with a cut/fill depth diagram.
- Superelevation diagram of left and right lane cross-slopes, built from the transition stations and full rates in the file. LandXML does not store the normal crown rate, so it is an input (default 2.5%).
- Tables of horizontal elements (length, radius, spiral radii and A), vertical controls (grades, curve length, K) and superelevation transitions.

On long alignments, charts are split into strips (default 5,000 units per row). Print the page to PDF for a paginated report: each alignment starts on a new page and tables print in full.

## Geometric design review

**Geometric Design Review** checks the horizontal alignment, vertical profile and corridor sections in a LandXML file against a selectable standard. It only reviews what the designer controls in the geometric design (alignment, profile, superelevation and side slopes); pavement, drainage and structures are out of scope.

**Standards.** Two criteria files ship in `criteria/`:

- **Kenya RDM 1.3 (2025), Geometric Design of Highways, Rural and Urban Roads** (default). Values are taken from the manual's tables and referenced in every finding (for example Table 3.16 stopping sight distance, Table 6.1/6.4 K values, Table 5.1 minimum radii, Tables 5.9–5.11 superelevation, Chapter 7 phasing, Figure 12.4 and Table 12.6 barriers).
- **AASHTO Green Book 2018**, the basis of Civil 3D's default design criteria. AASHTO maximum grades depend on road class and are not tabulated in the file, so enter a **Maximum grade %** for grade checks. AASHTO run-out lengths for barriers are not included; the barrier warrant still runs.

Choose a **Custom criteria file** to use another standard: copy one of the JSON files and edit the tables. Only metric criteria and metric LandXML files are supported.

**Checks** (each finding has a code, severity, chainage range, the value found, the value required, the source table and a recommendation):

| Code | Check |
| --- | --- |
| V0–V2 | Missing vertical curve, crest/sag K below minimum, vertical curve too short (including the S7.3 small grade change rule) |
| G1–G4 | Grade above the absolute or desirable maximum, grade longer than the critical length, flat grade in cut |
| S1 | Stopping sight distance along the profile (line of sight from eye to object height on the sampled profile) |
| S2 | Sight distance on horizontal curves, using cut slopes beyond the daylight line from corridor sections |
| H0–H9 | Radius below minimum, missing or short transition spirals, short or long curves, broken-back curves, short and long tangents, isolated sharp curves |
| E1–E5 | Superelevation required, too low or above e max, run-off too short, combined grade too steep |
| P1–P7 | Alignment phasing: curve start hidden beyond a crest, crests or sags overlapping a horizontal curve, one-end overlaps, short crests inside sharp curves, horizontal curve changes near a sag, missing constant grade between crest and sag |
| D1–D3 | Design speed changes: steps above 20 km/h (10 km/h preferred) with the intermediate sections to add on the faster side, transition and oscillating speed sections shorter than 1 km, and curves sharper than the faster speed allows within 1 km of a speed reduction |
| R1 | Embankment height and side slope warranting a safety barrier, with run-out length and a barrier schedule by side |

**Design speed.** The review follows the design speeds recorded along each alignment, so a road whose speed changes with terrain is checked section by section. Define them in Civil 3D before exporting (**Alignment Properties → Design Criteria**, adding speed stations); the export writes them as `SpeedStation` features, and Roadway `DesignSpeed` records are also read. Where Civil 3D lists two speeds at one station, the later one applies. Each element (curve, vertical curve, straight) is checked at the highest speed over its length, so an element spanning a speed change meets the faster approach. The report lists the speed sections. Leave **Design speed** at 0 to use the file; a single speed, or **Design speed by chainage** such as `0-5000:60; 5000-:80`, overrides it, and the log says when file speeds are ignored. Terrain, road type and e max (4, 6 or 8 %) select the table rows.

**Back to Civil 3D.** For each phasing, K and length finding the tool searches PVI moves and curve lengths (one end coincident, both ends coincident, or a clear separation) that satisfy K, overlap and grade limits, and picks the one that changes levels least. Fixes that change any level by more than **Apply profile fixes … at most (m)** (default 1 m) are reported but not applied. The recommended profile is written as:

- a text file for **Profiles → Create Profile from File** (station, elevation, curve length), and
- a LandXML file with the original alignment and the recommended profile, for **Insert → LandXML**.

The HTML report has summary cards, findings by category, a two-lane phasing diagram (horizontal over vertical curves), the change in elevation along the route, a table of recommended PVIs, a barrier schedule and the findings table. The findings are also written as a table and as points on the alignment.

**Road Design Report** runs the same review when a standard is selected and adds it to each alignment's section.

Limits: Figure 12.4 of RDM 1.3 is a chart; only its stated example (6 m height with slopes steeper than 1:3) is encoded, with a judgement band between 1:3 and 1:4 and from 3 m height. Sight distance on curves needs corridor sections; without them only the profile sight distance is checked. Intersection, passing and decision sight distances are not checked. Recommendations are a starting point for the designer, not a substitute for design judgement or a Departure from Standards process.

## Coordinate and unit handling

LandXML numeric coordinates are kept in stored order. The plugin does not infer X/Y order, datum, false easting/northing, offset, CRS, or elevations. The user must choose an output CRS. Swap, offset, 2-D Helmert and reprojection are available only as explicit Processing choices. Reprojection also requires an explicitly chosen source CRS. A LandXML CRS declaration is shown for reference and is never applied automatically.

`USSurveyFoot`, international `foot` and `meter` are distinct. If LandXML declares one of these units, the selected CRS used to interpret stored coordinates must have matching horizontal units; otherwise Processing stops. Reprojection uses the selected source and output CRSs. The plugin does not infer a missing vertical unit or convert elevations. A source CRS and axis order must be verified against project metadata and control before design use.

## Supported entities and limits

- TIN points and triangular faces (faces flagged invisible with `i="1"`, which Civil 3D writes for triangles hidden by a surface boundary, are excluded from rasters, boundaries and volumes), multiple surfaces, TIN edge boundaries, breakline and feature-line `PntList3D`/`PntList2D` geometry.
- Horizontal `Line`, circular `Curve`, and **explicitly declared clothoid** `Spiral`; compound geometry is kept as connected segments. Unsupported or disconnected geometry is reported rather than joined into a false line.
- `PVI` and `ParaCurve` vertical controls, tangent grades, crest/sag classification and sampled profile graph lines.
- Cross-sections: absolute `PntList3D` lists keep their coordinates. Station-relative `CrossSectSurf` offset/elevation lists (Civil 3D sample-line exports) are positioned along their own alignment as 3D lines, one per surface; earlier versions read these offset/elevation pairs as map X/Y. Corridor links and shapes (`DesignCrossSectSurf`) feed Corridor Section Quantities.
- Existing-ground and other surface profiles (`ProfSurf`), including gaps where the surface does not cover the alignment; nothing is interpolated across a gap.
- Stationing uses true arc length along curves and spirals for station points, 3D centerlines, map profiles and station/offset placement.
- 3D centerlines use the station range covered by a matching vertical profile. When a profile covers only part of an alignment, the covered segment is exported and the omitted range is reported; missing Z values are not filled with zero.
- Alignment station points follow whole multiples of the requested interval (for example, 20, 40, 60) even when the alignment starts at a fractional station. The individual tool can optionally add an off-interval alignment endpoint; this is off by default. Complete Road Design exports interval points only.

Current limits: the design review encodes only the tables listed above; the superelevation diagram assumes the usual two-lane rotation about the centreline; earthworks volumes use average end areas without curvature correction; superelevation, pipe networks, COGO points and plan features are not imported yet; circular vertical curves and non-clothoid spirals are reported as unsupported; station equations and semantic terrain void/hole classification are not implemented; 2D and 3D line records must be exported separately through individual Processing tools. OpenRoads road behavior beyond the one private alignment/profile export, including corridors, needs further validation. LandXML export from QGIS was considered separately and is not included in this release. GeoPackage, GeoTIFF and 3D vector output are supported; no DGN writer is attempted.

## Install the development plugin

In QGIS, choose **Settings → User Profiles → Open Active Profile Folder**. Inside that folder, open `python/plugins` (create those directories if needed) and place this repository checkout there as a directory named `landxml_tin_to_geotiff` (the plugin's permanent folder name; a different name installs a second copy). A symbolic link to a checkout elsewhere also works. Restart QGIS, enable **LandXML Road & Terrain GIS Tools** in **Plugins → Manage and Install Plugins → Installed**, then search the **Processing Toolbox** for **Inspect LandXML**. QGIS supplies NumPy and GDAL/OGR; this plugin has no separately installed Python dependency.

### Package a ZIP for testing or release

From the repository root:

```
pip install bandit detect-secrets flake8
python tools/build_release.py
```

This writes `dist/landxml_tin_to_geotiff-<version>.zip` from committed files (the plugin runtime only; tests and developer documents are left out) and runs the checks plugins.qgis.org applies on upload: the package layout, forbidden and hidden files, required metadata, LICENSE, executable permissions, Bandit with the repository's enabled rules, detect-secrets and flake8. In QGIS, choose **Plugins → Manage and Install Plugins → Install from ZIP**, select the ZIP, and enable **LandXML Road & Terrain GIS Tools**. Search the **Processing Toolbox** for **Inspect LandXML** to confirm it loaded.

## Test and manual check

Run the parser and headless QGIS Processing suite from an environment with QGIS Python bindings and GDAL/OGR available:

```sh
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v
```

`tests/test_landxml.py` and `tests/test_earthworks.py` need only Python and NumPy (plus GDAL for `core.py`). `tests/test_qgis_processing.py` and `tests/test_gui.py` need the QGIS bindings; the GUI tests run headless with the `offscreen` Qt platform. The synthetic corridor fixture is a level-section template whose areas, volumes and slope lengths have closed-form answers.

Some QGIS distributions require their bundled Python executable and environment variables. See [the compatibility audit](docs/qgis4-audit.md) for the tested runtime and coverage.

Manual check in QGIS 4.2.2:

The committed fixtures use synthetic coordinates. Any CRS selected for these checks tests Processing unit behavior only and does not establish a real-world location.

1. Run **Inspect LandXML** on `tests/fixtures/openroads/terrain_minimal.xml`; confirm OpenRoads Designer, `USSurveyFoot`, one TIN surface, one breakline, and warnings for missing CRS/vertical unit.
2. Run **Extract LandXML Breaklines** with **Use stored coordinates** and an explicitly selected ftUS CRS for the synthetic fixture. Confirm one 3D line and `feature_code=Breakline`, `surface_name=Synthetic terrain`, `source_user_id=42`.
3. Run **LandXML TIN to GeoTIFF** on the same fixture at pixel size 1; inspect its CRS, extent, NoData and elevation range. Repeat with a deliberately mismatched meter CRS and confirm Processing rejects the unit mismatch.
4. Run alignment, profile and cross-section tools on `tests/fixtures/civil3d/road_minimal.xml` with a meter CRS such as EPSG:26915 for map outputs. For **Extract LandXML Profiles**, select the graph and control outputs and use vertical exaggeration 4: the graph ends at plotted Y 8 while its source elevation is 2; the control layer has five points including VPC, VPI and VPT, and the curve VPI has K 3.75. The profile graph has no map CRS. Optionally select both map profile outputs, set **Map profile offset** to 25, and choose EPSG:26915; the schematic profile should appear beside the synthetic alignment near X 0–28, Y 0–10, with points outside its station range clipped. Check the line/curve/clothoid chain and section Z values separately.
5. Run **Profile Cut/Fill Analysis** on `tests/fixtures/civil3d/corridor_synthetic.xml` with a meter CRS (for example EPSG:32637), segment and transition outputs and a report. Expect fill from 0+000 to 0+100, cut to 0+200, one fill→cut transition at 0+100 and maximum depths of 1.00.
6. Run **Corridor Section Quantities** on the same fixture with **Use Datum** off: cut and fill 575, embankment and cut slope areas 223.61 each, Pave 200 and Base 400. Load the side-slope layer; the 0+000–0+050 left band has plan area 75.
7. Open the **LandXML Profile Viewer**, load the extracted alignment layer, press **Pick Alignment Profile on Map** and click the line: the long-section opens at the clicked chainage, hover moves the map marker, and **Export** writes PNG/SVG/PDF/CSV/HTML.
8. On a copy of an actual project export, run **Inspect** first, choose source/output CRSs and any explicit coordinate operation, then compare source and output bounds with known control. Do not assign a CRS solely from coordinate magnitudes.

Only synthetic LandXML fixtures are committed. Private project exports should remain outside Git.

## History and license

See [CHANGELOG.md](CHANGELOG.md) and [HISTORY.md](HISTORY.md). Original work and attribution: [EdmondAkello/LandXML-Road-and-Terrain-GIS-Tools](https://github.com/EdmondAkello/LandXML-Road-and-Terrain-GIS-Tools). GPL-2.0-or-later; see [LICENSE](LICENSE).
