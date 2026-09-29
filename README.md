# LandXML Road & Terrain GIS Tools

A QGIS Processing provider for inspecting and importing LandXML terrain and road-design data. Originally created by **Edmond Akello** under GPL-2.0-or-later; the original attribution and license remain in place.

## Compatibility and validation

- **QGIS 4.2.2:** the updated PR branch passed all 31 parser and headless Processing tests with the macOS QGIS 4.2.2 runtime. The developer also reports that the QGIS 4 desktop regression workflow completed successfully after the profile and stationing changes. An earlier desktop check inspected a private OpenRoads terrain export and created and displayed its TIN GeoTIFF. These visual checks are not survey-control validation.
- **QGIS 3.44:** the parser and 31 headless Processing tests pass in a Windows QGIS 3.44.3 runtime, including provider registration, unload, profile labels, map profile placement and saved-output loading. A private OpenRoads road export was also exercised through the 3D centerline, station-point and profile tools. Survey-control and visual checks remain necessary. Metadata declares 3.44–4.99 to allow testing on both series.
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

**Inspect LandXML** reports vendor/evidence, version, horizontal and vertical units, declared CRS, axis-order uncertainty, surfaces with point/face counts, alignments, profiles, cross sections, breaklines, feature lines, source bounds, elevation range and warnings. It can save JSON. Inspection never changes coordinates.

The Complete Road Design export writes an engineering GeoPackage and optional GeoTIFF/contour outputs. `alignment_elements` retains radius, curve length, spiral length and direction per element. Profiles and profile controls use **station/elevation graph coordinates with no map CRS**; they are not mapped onto the horizontal alignment. Breakline and feature-line layers preserve source names/descriptions when present, surface name, type, code and source IDs. When multiple surfaces are present, the exporter processes all unless a name is selected.

### Profile graphs and labels

In **Extract LandXML Profiles**, choose a **Profile graph lines** output and a **Profile control points** output, then load both result layers. The graph line is labeled with its profile name. The point layer contains source `PVI` controls labeled **VPI**, plus calculated **VPC** and **VPT** points for each supported parabolic curve. Point labels show source station and elevation, incoming/outgoing tangent grades (`G1`/`G2`), and **K** at a parabolic VPI. Both layers include a `label_text` field; to restore a hidden label in QGIS, choose **Layer Properties → Labels → Single Labels → `label_text`**. Select the control-point output explicitly to create those points.

**Vertical exaggeration** defaults to 1. A value such as 10 multiplies the plotted Y coordinate of the graph and control points by 10; the `elevation` field and `Elev` label still show the source elevation, and `plot_elevation` records the scaled point Y. X remains source station. These graph coordinates have no map CRS and are not road-map locations. This parameter belongs to the individual profile extraction tool; Complete Road Design retains source station/elevation graph coordinates.

To see the profile **near its actual alignment in the map**, choose **Map profile lines** and optionally **Map profile control points** in the same tool. Select the same **Coordinate interpretation** and **Output CRS** that place your horizontal alignment correctly. For example, an OpenRoads export that needs **Swap X / Y** for the alignment needs the same choice here. The map outputs use the alignment's stationing, draw the profile to the left of the road at the chosen **Map profile offset** (negative values place it on the right), and carry the same labels and elevation attributes. Vertical exaggeration scales the elevation change around the profile's first elevation and adds it to that offset. The offset is in the source horizontal units; the default is 100. Profile parts outside the horizontal alignment's station range are clipped. These are **schematic plan-view graphics**, not surveyed road features or a true elevation axis. Use **Create 3D Road Centerlines** for an alignment at its actual X/Y location with profile elevation as Z. The map outputs require an explicitly selected CRS; the graph outputs remain available without one.

[FHWA defines K](https://highways.fhwa.dot.gov/safety/speed-management/speed-concepts-informational-guide/chapter-4-engineering-and-technical) as parabolic curve length divided by the absolute algebraic difference between grades in percent. Here K is in source station units per 1% grade change. A flat curve has no finite K and receives no K label. If horizontal or vertical units are undeclared or differ, verify the computed grades and K before design use; the plugin does not convert vertical units.

## Coordinate and unit handling

LandXML numeric coordinates are kept in stored order. The plugin does not infer X/Y order, datum, false easting/northing, offset, CRS, or elevations. The user must choose an output CRS. Swap, offset, 2-D Helmert and reprojection are available only as explicit Processing choices. Reprojection also requires an explicitly chosen source CRS. A LandXML CRS declaration is shown for reference and is never applied automatically.

`USSurveyFoot`, international `foot` and `meter` are distinct. If LandXML declares one of these units, the selected CRS used to interpret stored coordinates must have matching horizontal units; otherwise Processing stops. Reprojection uses the selected source and output CRSs. The plugin does not infer a missing vertical unit or convert elevations. A source CRS and axis order must be verified against project metadata and control before design use.

## Supported entities and limits

- TIN points and triangular faces, multiple surfaces, TIN edge boundaries, breakline and feature-line `PntList3D`/`PntList2D` geometry.
- Horizontal `Line`, circular `Curve`, and **explicitly declared clothoid** `Spiral`; compound geometry is kept as connected segments. Unsupported or disconnected geometry is reported rather than joined into a false line.
- `PVI` and `ParaCurve` vertical controls, tangent grades, crest/sag classification and sampled profile graph lines.
- Absolute-coordinate cross-section point lists, with source station and elevation where supplied. Station-relative design-template sections are reported and skipped because their map positions cannot be established from the section alone.
- 3D centerlines use the station range covered by a matching vertical profile. When a profile covers only part of an alignment, the covered segment is exported and the omitted range is reported; missing Z values are not filled with zero.
- Alignment station points follow whole multiples of the requested interval (for example, 20, 40, 60) even when the alignment starts at a fractional station. The individual tool can optionally add an off-interval alignment endpoint; this is off by default. Complete Road Design exports interval points only.

Current limits: circular vertical curves and non-clothoid spirals are reported as unsupported; station equations and semantic terrain void/hole classification are not implemented; 2D and 3D line records must be exported separately through individual Processing tools. OpenRoads road behavior beyond the one private alignment/profile export, including corridors, needs further validation. LandXML export from QGIS was considered separately and is not included in 1.5.0. GeoPackage, GeoTIFF and 3D vector output are supported; no DGN writer is attempted.

## Install the development plugin

In QGIS, choose **Settings → User Profiles → Open Active Profile Folder**. Inside that folder, open `python/plugins` (create those directories if needed) and place this repository checkout there as a directory named `landxml_road_terrain`. A symbolic link to a checkout elsewhere also works. Restart QGIS, enable **LandXML Road & Terrain GIS Tools** in **Plugins → Manage and Install Plugins → Installed**, then search the **Processing Toolbox** for **Inspect LandXML**. QGIS supplies NumPy and GDAL/OGR; this plugin has no separately installed Python dependency.

### Package a ZIP for testing

From the repository root, run this in PowerShell:

```powershell
git archive --format=zip --prefix=landxml_road_terrain/ -o landxml_road_terrain-test.zip HEAD
```

The ZIP has the required `landxml_road_terrain` top-level folder. `git archive` packages committed files, so commit any changes you want to test first. In QGIS, choose **Plugins → Manage and Install Plugins → Install from ZIP**, select `landxml_road_terrain-test.zip`, and enable **LandXML Road & Terrain GIS Tools**. Search the **Processing Toolbox** for **Inspect LandXML** to confirm it loaded.

## Test and manual check

Run the parser and headless QGIS Processing suite from an environment with QGIS Python bindings and GDAL/OGR available:

```sh
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v
```

Some QGIS distributions require their bundled Python executable and environment variables. See [the compatibility audit](docs/qgis4-audit.md) for the tested runtime and coverage.

Manual check in QGIS 4.2.2:

The committed fixtures use synthetic coordinates. Any CRS selected for these checks tests Processing unit behavior only and does not establish a real-world location.

1. Run **Inspect LandXML** on `tests/fixtures/openroads/terrain_minimal.xml`; confirm OpenRoads Designer, `USSurveyFoot`, one TIN surface, one breakline, and warnings for missing CRS/vertical unit.
2. Run **Extract LandXML Breaklines** with **Use stored coordinates** and an explicitly selected ftUS CRS for the synthetic fixture. Confirm one 3D line and `feature_code=Breakline`, `surface_name=Synthetic terrain`, `source_user_id=42`.
3. Run **LandXML TIN to GeoTIFF** on the same fixture at pixel size 1; inspect its CRS, extent, NoData and elevation range. Repeat with a deliberately mismatched meter CRS and confirm Processing rejects the unit mismatch.
4. Run alignment, profile and cross-section tools on `tests/fixtures/civil3d/road_minimal.xml` with a meter CRS such as EPSG:26915 for map outputs. For **Extract LandXML Profiles**, select the graph and control outputs and use vertical exaggeration 4: the graph ends at plotted Y 8 while its source elevation is 2; the control layer has five points including VPC, VPI and VPT, and the curve VPI has K 3.75. The profile graph has no map CRS. Optionally select both map profile outputs, set **Map profile offset** to 25, and choose EPSG:26915; the schematic profile should appear beside the synthetic alignment near X 0–28, Y 0–10, with points outside its station range clipped. Check the line/curve/clothoid chain and section Z values separately.
5. On a copy of an actual project export, run **Inspect** first, choose source/output CRSs and any explicit coordinate operation, then compare source and output bounds with known control. Do not assign a CRS solely from coordinate magnitudes.

Only synthetic LandXML fixtures are committed. Private project exports should remain outside Git.

## History and license

See [CHANGELOG.md](CHANGELOG.md) and [HISTORY.md](HISTORY.md). Original work and attribution: [EdmondAkello/LandXML-Road-and-Terrain-GIS-Tools](https://github.com/EdmondAkello/LandXML-Road-and-Terrain-GIS-Tools). GPL-2.0-or-later; see [LICENSE](LICENSE).
