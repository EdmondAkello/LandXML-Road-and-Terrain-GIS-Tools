# Earthworks and profile-viewer validation (1.6.0)

Six private Autodesk Civil 3D LandXML 1.2 exports (Civil 3D 2022, 2024 and 2026) were used to check the 1.6.0 changes. Project names, file names, coordinates and survey details are withheld, and none of the files are in this repository. Labels A–F are arbitrary. Every check ran in Linux QGIS 3.44.14 through the same Processing algorithms and viewer code that ship with the plugin.

| Export | Content relevant to 1.6.0 |
| --- | --- |
| A | 1 alignment (16.7 km), design profile, two existing-ground profiles (one partial), 3,569 station-relative cross-sections with corridor shapes and Datum links, 3 TIN surfaces, Roadway surface references |
| B | 1 alignment (36.2 km) with spirals, design and existing-ground profiles |
| C | 4 alignments, 4 design and existing-ground profile pairs (preliminary design) |
| D | 2 alignments, design and existing-ground profiles |
| E | 1 alignment (2.7 km) crossing a river valley on a bridge, design and existing-ground profiles, 1 TIN (275,000 points) |
| F | 9 short alignments, 22 surface profiles, 14 TIN surfaces, 2 Civil 3D volume surfaces (`SurfVolume`), pipe network and plan features |

## Findings that changed the code

- **Cross-sections:** in A, `CrossSectSurf/PntList2D` values are offset/elevation pairs. Version 1.5.0 wrote 334 of them as map X/Y lines near X ≈ −10, Y ≈ 2,060. Each surface section is now placed along the alignment; placed sections fall on the alignment in the project CRS.
- **Corridor elevations:** the `DesignCrossSectSurf` crown point (0, 0) is relative to the design grade. At one station the design profile elevation equals the absolute corridor-top section crown to 0.1 mm, so relative Datum elevations are made absolute with the design profile.
- **Hidden triangles:** A, E and F contain 12,501, 13,456 and 922 faces flagged `i="1"`. Excluding them changed the F volume-surface comparison from 1,423 m³ to 1,359 m³ of fill.
- **Stationing:** densified chord lengths ended 0.07–0.33 m short of the declared alignment lengths; true arc lengths match all seven declared lengths in B–D to 0.1 mm.

## Quantities compared with Civil 3D

The F file records two Civil 3D `SurfVolume` results. The Surface Cut/Fill tool reproduces both from the TINs in the same file:

| Volume surface | Civil 3D cut / fill (m³) | Plugin, 0.5 m grid (m³) | Plugin, 0.25 m grid (m³) |
| --- | --- | --- | --- |
| F-1 | 354.3 / 4,030.7 | 354.5 / 4,030.8 | 354.4 / 4,031.3 |
| F-2 | 4,150.4 / 1,358.8 | 4,150.4 / 1,358.8 | 4,150.4 / 1,359.0 |

The compared area for F-2 is 2,508–2,509 m², against the 2,509.0 m² area of the corresponding Civil 3D surface.

No Civil 3D section-volume or material report was available for A, so its corridor results are checked for internal consistency only: material volumes from the exported shape areas (pavement 8,541 m³, base 23,099 m³, subbase 25,594 m³), cut/fill and side-slope areas over the 6.15 km covered by existing-ground sections, and the 1.5 % difference between side-slope plan and sloped areas. Compare these with the design software's own reports before relying on them.

## Profile comparison

Design vs existing-ground comparison ran on every alignment in A–E. In E the largest "fill" is about 110 m at the bridge, which is why structure station ranges can be excluded. In B, raw depth sign changes produced 490 cut↔fill transitions over 36 km; an on-grade tolerance of 0.25 m reduces these to 200 without changing cut or fill depths.

## Regression of existing tools

Alignments, profile graphs and map overlays, 3D centerlines, station points and the Complete Road Design GeoPackage (including TIN boundary and DEM for E) were run on B–E with an explicit X/Y swap and a project CRS. Feature counts and elevation ranges were consistent with the source profiles. These runs do not validate survey control or the source CRS.
