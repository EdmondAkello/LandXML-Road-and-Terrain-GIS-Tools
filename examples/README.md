# Example LandXML data

The repository's committed examples are synthetic fixtures under `tests/fixtures/`. They exercise standard LandXML structures without redistributing engineering project data:

- `tests/fixtures/openroads/terrain_minimal.xml` reproduces an OpenRoads-style TIN and breakline structure with US survey foot horizontal units.
- `tests/fixtures/civil3d/road_minimal.xml` exercises alignment, vertical profile, cross-section and terrain parsing with meter units.
- `tests/fixtures/generic/multiple.xml` exercises multiple alignments and surfaces with an unidentified exporter.

These coordinates are invented. A CRS selected while testing them only checks the Processing workflow and unit safeguards; it does not establish a real-world location.

The updated branch passed all 31 parser and headless Processing tests with QGIS 4.2.2, including the road and terrain fixtures below. The developer also reported a successful QGIS 4 desktop regression check. Repeat the steps below on your own installation and use known control for project data.

## Suggested manual workflow

1. Run **Inspect LandXML** on a fixture. Review the vendor evidence, units, entities, coordinate bounds and warnings.
2. Run **LandXML TIN to GeoTIFF** on the terrain fixture with an explicitly selected output CRS whose horizontal units match the declared units. Check the raster extent, NoData and elevations.
3. Run **Extract LandXML Breaklines** on the terrain fixture and inspect the 3D line and source attributes.
4. Run the alignment, profile and cross-section tools on the road fixture. In **Extract LandXML Profiles**, select both graph and control-point outputs and set vertical exaggeration to 4. The graph uses station X and plotted elevation Y, with no map CRS. Its final plotted Y is 8 while the source elevation remains 2. The control layer has five labeled VPC/VPI/VPT points; the curve VPI reports K 3.75. These numbers exercise the synthetic fixture, not survey control.
5. Run **Create Alignment Station Points** at interval 20. Its point stations are multiples of 20; the optional off-interval alignment endpoint is disabled by default.
6. For a real export, inspect first, choose the CRS and any coordinate operation from verified project metadata, and compare output with known control. If vertical units are undeclared, verify the profile grades and K values before using them for design.

Real Civil 3D and OpenRoads project exports used during development are not bundled. Keep project data outside Git unless it is cleared for redistribution. See the main [README](../README.md) for installation, test commands and current limitations.
