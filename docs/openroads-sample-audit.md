# OpenRoads terrain structure audit

Private OpenRoads Designer LandXML 1.2 terrain exports were inspected before changing the parser. Project files and coordinates are not included in this branch. The committed synthetic fixture reproduces the relevant standard XML structure; it does not claim to represent every Bentley export.

| Observed structure | Difference from the previous Civil 3D oriented importer |
| --- | --- |
| LandXML 1.2 namespace | The old importer fixed this URI. The shared parser reads the namespace from the root to support other valid LandXML versions. |
| `Application` names OpenRoads Designer and Bentley Systems | Explicit source metadata can identify the exporter. Unknown sources use generic LandXML parsing. |
| `Units/Imperial@linearUnit="USSurveyFoot"` | The old importer did not read units. US survey feet remain distinct from international feet and meters. |
| No vertical unit or `CoordinateSystem` declaration | Vertical units, CRS, datum and axis order remain unknown; the user must verify them before georeferenced output. |
| `Surfaces/Surface/Definition/Pnts/P` and `Faces/F` | The standard TIN point and triangle topology matches the structure handled by the existing terrain code. Point coordinates and face references are validated. |
| `SourceData/Breaklines/Breakline/PntList3D` | The previous breakline geometry search found these lines but did not retain all child metadata. Coordinate triples are validated. |
| Breaklines with `brkType`, child `Feature@code` and `Feature/Property` labels `id` and `UsrID` | A source feature name may be absent. Preserve the code, breakline type and source IDs as separate attributes; do not reinterpret IDs as names or geometry. |
| No alignment, profile, cross-section, feature-line, explicit boundary or void records in the examined terrain exports | These files do not validate OpenRoads road or corridor exports. TIN edge boundaries can be derived from faces, but semantic holes cannot be asserted. |

The upstream README describes Civil 3D validation, but this repository includes no original Civil 3D export fixture. This audit compares OpenRoads terrain structure with the previous code paths, not with a paired Civil 3D XML file. The parser retains coordinates and elevations in stored order and never infers axis order from their magnitudes.

A separate private OpenRoads alignment/profile export was later exercised with QGIS 3.44.3. It produced a profile-covered 3D centerline segment, interval-aligned station points, and labeled VPC/VPI/VPT profile controls. That export is not included here, and the runtime check does not validate survey control or OpenRoads corridor behavior.

An OpenRoads terrain export was inspected and converted to GeoTIFF in QGIS 4.2.2 during the initial desktop smoke test. After the later PR changes, the updated branch passed the 31-test QGIS 4.2.2 headless suite, and the developer reported that the QGIS 4 desktop regression workflow completed successfully. These checks do not establish the source CRS, vertical units, axis order or survey accuracy; the QGIS 4 report does not add a distributable OpenRoads road fixture.
