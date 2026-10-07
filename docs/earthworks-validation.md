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

## 1.7.0 additions

- **Name catalog:** on A, E and F the start-tag scan lists the same surfaces, alignments, profiles and section surfaces as the full parser, in 0.18–0.39 s instead of 3.0–4.5 s. In a QGIS 3.44 Processing dialog, choosing export A filled the surface dropdown with its three surfaces.
- **Automatic surfaces:** for A, Surface Cut/Fill picked the existing-ground surface named in the ground profile and the Roadway datum (bottom) surface. Over the TIN overlap it gave cut 67,282 m³, against 67,056 m³ from the independent corridor-section method over the 6.15 km covered by ground sections. Fill differs by about 15%, which reflects the 50 m section spacing and the different coverage of the two methods. For F, the recorded Civil 3D volume pair was used.
- **Superelevation:** in B, every negative full rate belongs to a counter-clockwise (left) curve and every positive rate to a clockwise (right) curve, so the diagram lifts the outside lane accordingly. Transition stations come from the file. The 2.5% normal crown is an assumption the user can change.
- **Sheets and reports:** cross-section sheets were produced for A (125 sections with ground; also a 2 km range at every section). Design reports were produced for all six exports, from nine short straight alignments to the 36 km alignment with 275 elements and 42 superelevation transitions.

## 1.8.0 design review

- **Robustness:** the review ran on all six exports under both standards (design speed 60 km/h, mountainous terrain) without errors, in 0.4–18 s per file; the longest runs are the files with corridor sections.
- **Speed matters:** B records 100 km/h on a mountainous route. At that speed the review gave 813 findings (340 high); at 60 km/h it gave 443 (137 high), and under AASHTO 165. Design speeds by chainage let each terrain section use its own speed, and the report groups findings by category so the pattern is visible first.
- **A, Kenya RDM 1.3:** 44 high, 45 medium and 25 low findings; 10 profile changes were applied within the 1 m limit, and the rest were reported with the change they would need. Barrier warrants were found on both sides on high embankments; 125 sections were checked for barriers and sight on curves.
- **Phasing fix:** in a synthetic case, a crest overlapping a sharp 600 m radius curve (chainage 400–700) is moved to the curve middle at 550 with L = 300 m, so both ends coincide; the tests also check K, grades and the exported Civil 3D text.
- **Figure 12.4:** the chart is only an image in the manual; its stated example is encoded, so treat barrier findings near the thresholds as prompts for judgement.
- **Design speed variations:** C steps from 70 to 50 km/h along three alignments, and D alternates between 50 and 70 km/h in sections of 220–1,010 m. The review followed them, listed the speed sections, and flagged 20 km/h steps as acceptable but not preferred, short oscillating sections, and curves (for example R183 against 185 m at 70 km/h, 63 m past a reduction) that are sharper than the approach speed allows. One alignment in C lists 50 and then 70 km/h at chainage 0; 70 km/h, the later entry, is used.
