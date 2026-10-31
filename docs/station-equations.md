# Roadway station equations

Issue #40 adds increasing roadway station regions. An ahead jump leaves a gap in engineering stations; a back jump can repeat them. Neither changes alignment geometry, physical length, profile grades or earthwork integration distances.

## Station basis and compatibility

- Distance along starts at zero. Internal station is alignment `staStart` plus that distance. `staInternal` locates an equation in this continuous coordinate.
- The engineering station before the first equation is the internal station. After each equation it is internal station plus `staAhead - staInternal` for that region. Regions are numbered R1, R2, etc. in alignment order.
- The ahead region owns an exact equation boundary by default. Both incoming and outgoing labels remain queryable at that physical position. Inverse lookup requires a region if distinct physical candidates remain; a gap has no candidate.
- Existing numeric station fields, source profile/section stations, range inputs, chart axes and sampling intervals remain internal. This avoids reinterpreting saved models and preserves physical quantity integration. Other existing layer station labels also remain internal.
- Station-point `station_label` applies equations and includes the region; new `engineering_station`, `station_region` and `distance_along` fields preserve that distinction. Complete Road Design includes a `station_equations` table with both labels, internal position and adjacent regions.
- HTML reports include the equation table and basis note. Profile charts identify internal stationing. Section drawings show engineering station/region and internal station. Profile Viewer CSV exports retain the old columns and append engineering context for numeric station columns. Engineering context is blank for source profile rows beyond the horizontal alignment; section drawings identify such stations as internal and outside the alignment.
- Prepared XML and recommended-profile XML preserve the equations. Their geometry/profile/section station values stay internal; equations must not be applied to them a second time.

Metric input uses a 1000-unit separator (`1+950.000`); feet use 100 (`19+50.000`). Equations use source horizontal units without conversion. International feet and US survey feet remain separate unit declarations.

## Format evidence

1. [Autodesk Civil 3D: Supported LandXML Data for Import and Export](https://help.autodesk.com/cloudhelp/2024/ENG/Civil3D-UserGuide/files/GUID-4D10ABA5-5EA0-41A8-BB61-C3F446CE7C6B.htm) documents continuous geometry stations, with equations reserved for display/reporting. This establishes why Civil 3D PVIs, sections and design-speed positions must not be remapped through engineering labels during calculation.
2. The [buildingSMART Finland published schema](https://www.buildingsmart.fi/infra/schema/4.0.2/inframodel402.html) reproduces the internal-station definition and the `staInternal`/`staAhead`/`staIncrement` equation annotation. Its specialized railway posting extensions are outside this feature. Its attribute requirements must not be mistaken for the base LandXML schema's optional attributes.
3. A private, direct OpenRoads Designer export (Application version 26.00.00.154) was inspected read-only. It carries `staInternal`, `staBack` and `staAhead`, with no `staIncrement`; its equation is located by internal station, not by an already-adjusted label. The file and its independently collected ORD reference points are excluded from this repository. This is evidence for that export, not every OpenRoads version or corridor convention.

The same attribute-driven mapping handles Civil 3D and OpenRoads metadata. XML attribute order and vendor namespaces do not change equation interpretation. Missing `staBack` is derived from the incoming region; omitted `staIncrement` means increasing. No coordinate or station shift is selected from the vendor name, filename or coordinate magnitude. Files using nonstandard adjusted profile/section stations need source-software verification and are not certified by these tests.

## Validation

Committed fixtures under `tests/fixtures/civil3d/station_equations.xml` and `tests/fixtures/openroads/station_equations.xml` are synthetic, not copies of vendor exports. They exercise metric and US survey feet, different application metadata, two cumulative equations, internal profile stations and sections on both sides of a back jump. Additional tests cover international feet, namespace variants, optional attributes, negative/nonzero starts, exact boundaries, repeated stations, gaps, invalid equations, CSV, station-point/GeoPackage/report output, preparation and recommended-profile round trips. Unchanged physical profile length and a hand-calculated 56,000 source-unit-cubed fill estimate are checked independently of labels.

The local QGIS 4.2.2 full suite passed 185 tests; CI results are recorded in the PR. The required parser/earthworks/criteria and Bandit checks apply; CI also runs QGIS 3.44 and 4.2.3.

The private ORD reference comparison passed 30 forward/inverse cases, including six equation cases, using a maximum geometry segment of 0.01 source units and the existing 0.001 ftUS station, offset and coordinate tolerances. Maximum residuals were 0.000470106 ftUS station, 0.000000104 ftUS offset and 0.000470106 ftUS coordinate. At the equation boundary the reference coordinates are rounded: both boundary labels were checked within the original station tolerance. These are software comparison tolerances, not certified field accuracy. The locator's default segment remains 0.1; its sampled-geometry accuracy limitation remains in force.

## Unsupported and invalid input

Explicit decreasing station progression is rejected, as are unknown increment values, nonfinite values, out-of-range or unordered equation positions, duplicate positions, and back labels inconsistent with the preceding region (absolute consistency tolerance 0.000001 source units). Dropping an invalid equation would corrupt later regions, so the equation set is rejected as a whole. Equations require supported connected geometry and unique named alignments. Manual equation authoring and railway kilometre-post conventions are not included.

Live Civil 3D round-trip testing, additional real metric/OpenRoads equation exports, and field/control checks remain outside the evidence above. Versions and release dates are unchanged; no release or tag is created by this feature.
