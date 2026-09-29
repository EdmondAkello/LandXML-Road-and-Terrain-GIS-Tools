"""LandXML TIN -> GeoTIFF conversion core for QGIS.

The original LandXML TIN topology is preserved. Coordinates can be interpreted
as stored, axis-swapped, translated, transformed with a 2-D Helmert operation,
or reprojected with a source/target CRS using GDAL/OSR.
"""

from __future__ import annotations

import math

import numpy as np
from osgeo import gdal, osr

from .landxml.common import child, descendants, first_descendant
from .landxml.parser import load_document


def list_surfaces(path):
    surfaces = load_document(path).surfaces()
    return [
        (s.attrib.get("name", f"Surface {i + 1}"), s) for i, s in enumerate(surfaces)
    ]


def declared_epsg(path):
    root = load_document(path).root
    cs = child(root, "CoordinateSystem")
    if cs is None:
        return None
    value = cs.attrib.get("epsgCode")
    if value and str(value).isdigit():
        return int(value)
    return None


def read_tin(path, surface_name=None, progress=None, cancel=None, surface_index=None):
    if progress:
        progress(2, "Parsing LandXML…")
    root = load_document(path).root
    surfaces = list(descendants(root, "Surface"))
    if not surfaces:
        raise ValueError("No <Surface> elements were found in the LandXML file.")
    if surface_index is not None:
        if not 0 <= surface_index < len(surfaces):
            raise ValueError(f"Surface index {surface_index} is out of range.")
        surface = surfaces[surface_index]
        surface_name = surface.attrib.get("name", f"Surface {surface_index + 1}")
    elif surface_name is None:
        surface = surfaces[0]
        surface_name = surface.attrib.get("name", "Surface 1")
    else:
        matches = [
            surface
            for index, surface in enumerate(surfaces)
            if surface.attrib.get("name", f"Surface {index + 1}") == surface_name
        ]
        if not matches:
            raise ValueError(f"Surface not found: {surface_name}")
        if len(matches) > 1:
            raise ValueError(
                f"Surface name '{surface_name}' is ambiguous; {len(matches)} surfaces have that name."
            )
        surface = matches[0]

    pnts = first_descendant(surface, "Pnts")
    faces = first_descendant(surface, "Faces")
    if pnts is None or faces is None:
        raise ValueError(
            f"Surface '{surface_name}' does not contain a TIN Definition with Pnts and Faces."
        )

    point_nodes = list(descendants(pnts, "P"))
    if not point_nodes:
        raise ValueError(f"Surface '{surface_name}' contains no TIN points.")

    xyz = np.empty((len(point_nodes), 3), dtype=np.float64)
    id_to_idx = {}
    for i, p in enumerate(point_nodes):
        try:
            pid = int(p.attrib["id"])
            vals = [float(v) for v in (p.text or "").split()]
            if len(vals) != 3 or not all(math.isfinite(v) for v in vals):
                raise ValueError
        except Exception as exc:
            raise ValueError(f"Invalid LandXML point at position {i + 1}.") from exc
        if pid in id_to_idx:
            raise ValueError(f"Surface '{surface_name}' has duplicate point ID {pid}.")
        xyz[i] = vals
        id_to_idx[pid] = i

    face_nodes = list(descendants(faces, "F"))
    if not face_nodes:
        raise ValueError(f"Surface '{surface_name}' contains no TIN faces.")
    face_list = []
    for i, f in enumerate(face_nodes):
        vals = (f.text or "").split()
        if len(vals) != 3:
            raise ValueError(
                f"Surface '{surface_name}' face {i + 1} must reference exactly three point IDs."
            )
        try:
            refs = [int(v) for v in vals[:3]]
            face_list.append([id_to_idx[r] for r in refs])
        except KeyError as exc:
            raise ValueError(
                f"Face {i + 1} references missing point ID {exc.args[0]}."
            ) from exc
        except ValueError as exc:
            raise ValueError(
                f"Surface '{surface_name}' face {i + 1} contains a noninteger point ID."
            ) from exc
    if not face_list:
        raise ValueError("No valid triangular faces were found.")

    farr = np.asarray(face_list, dtype=np.int32)
    b = xyz[:, :2]
    epsg = declared_epsg(path)
    meta = {
        "surface_name": surface_name,
        "point_count": len(xyz),
        "face_count": len(farr),
        "epsg": epsg,
        "bounds": (
            float(b[:, 0].min()),
            float(b[:, 1].min()),
            float(b[:, 0].max()),
            float(b[:, 1].max()),
        ),
        "zmin": float(xyz[:, 2].min()),
        "zmax": float(xyz[:, 2].max()),
    }
    if progress:
        progress(12, f"Loaded {len(xyz):,} vertices and {len(farr):,} triangles.")
    return xyz, farr, meta


def transform_vertices(vertices, method="stored", **params):
    """Apply a coordinate interpretation/transformation to X/Y; Z is preserved."""
    out = np.array(vertices, dtype=np.float64, copy=True)
    x = out[:, 0].copy()
    y = out[:, 1].copy()

    if method == "stored":
        return out
    if method == "swap":
        out[:, 0], out[:, 1] = y, x
        return out
    if method == "offset":
        out[:, 0] = x + float(params.get("dx", 0.0))
        out[:, 1] = y + float(params.get("dy", 0.0))
        return out
    if method == "swap_offset":
        out[:, 0] = y + float(params.get("dx", 0.0))
        out[:, 1] = x + float(params.get("dy", 0.0))
        return out
    if method == "helmert":
        # 2-D similarity: X' = tx + s(cosθ X - sinθ Y), Y' = ty + s(sinθ X + cosθ Y)
        theta = math.radians(float(params.get("rotation_deg", 0.0)))
        scale = float(params.get("scale", 1.0))
        tx = float(params.get("dx", 0.0))
        ty = float(params.get("dy", 0.0))
        c, s = math.cos(theta), math.sin(theta)
        out[:, 0] = tx + scale * (c * x - s * y)
        out[:, 1] = ty + scale * (s * x + c * y)
        return out
    if method == "reproject":
        src = osr.SpatialReference()
        dst = osr.SpatialReference()
        if params.get("src_wkt") and params.get("dst_wkt"):
            if (
                src.ImportFromWkt(params["src_wkt"]) != 0
                or dst.ImportFromWkt(params["dst_wkt"]) != 0
            ):
                raise ValueError(
                    "Could not load the explicitly selected source or output CRS."
                )
        else:
            src_epsg = int(params["src_epsg"])
            dst_epsg = int(params["dst_epsg"])
            if src.ImportFromEPSG(src_epsg) != 0 or dst.ImportFromEPSG(dst_epsg) != 0:
                raise ValueError("Could not load one of the specified EPSG codes.")
        src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        ct = osr.CoordinateTransformation(src, dst)
        transformed = np.empty_like(out)
        for i in range(len(out)):
            xx, yy, zz = ct.TransformPoint(float(x[i]), float(y[i]), 0.0)
            transformed[i, 0] = xx
            transformed[i, 1] = yy
            if out.shape[1] > 2:
                transformed[i, 2] = out[i, 2]
        return transformed
    raise ValueError(f"Unknown coordinate transformation method: {method}")


def bounds(vertices):
    return (
        float(vertices[:, 0].min()),
        float(vertices[:, 1].min()),
        float(vertices[:, 0].max()),
        float(vertices[:, 1].max()),
    )


def rasterize_tin(
    vertices,
    faces,
    resolution,
    nodata=-9999.0,
    progress=None,
    cancel=None,
    max_cells=250_000_000,
):
    if resolution <= 0:
        raise ValueError("Resolution must be greater than zero.")
    x = vertices[:, 0]
    y = vertices[:, 1]
    xmin, xmax = float(x.min()), float(x.max())
    ymin, ymax = float(y.min()), float(y.max())
    xmin_r = math.floor(xmin / resolution) * resolution
    ymax_r = math.ceil(ymax / resolution) * resolution
    xmax_r = math.ceil(xmax / resolution) * resolution
    ymin_r = math.floor(ymin / resolution) * resolution
    cols = int(round((xmax_r - xmin_r) / resolution))
    rows = int(round((ymax_r - ymin_r) / resolution))
    if cols <= 0 or rows <= 0:
        raise ValueError("Computed raster dimensions are invalid.")
    cells = rows * cols
    if cells > max_cells:
        raise ValueError(
            f"Requested resolution produces {cells:,} cells ({cols:,} × {rows:,}), exceeding the safety limit of {max_cells:,}. Increase the pixel size."
        )

    arr = np.full((rows, cols), np.float32(nodata), dtype=np.float32)
    eps = max(resolution * 1e-8, 1e-9)
    nfaces = len(faces)
    for fi in range(nfaces):
        if cancel and cancel():
            raise RuntimeError("Conversion cancelled by user.")
        ia, ib, ic = faces[fi]
        x1, y1, z1 = vertices[ia]
        x2, y2, z2 = vertices[ib]
        x3, y3, z3 = vertices[ic]
        det = (x2 - x1) * (y3 - y1) - (x3 - x1) * (y2 - y1)
        if abs(det) < 1e-14:
            continue
        c0 = int(math.floor((min(x1, x2, x3) - xmin_r) / resolution))
        c1 = int(math.ceil((max(x1, x2, x3) - xmin_r) / resolution))
        r0 = int(math.floor((ymax_r - max(y1, y2, y3)) / resolution))
        r1 = int(math.ceil((ymax_r - min(y1, y2, y3)) / resolution))
        c0 = max(0, min(cols - 1, c0))
        c1 = max(0, min(cols, c1 + 1))
        r0 = max(0, min(rows - 1, r0))
        r1 = max(0, min(rows, r1 + 1))
        if c0 >= c1 or r0 >= r1:
            continue
        xs = xmin_r + (np.arange(c0, c1, dtype=np.float64) + 0.5) * resolution
        ys = ymax_r - (np.arange(r0, r1, dtype=np.float64) + 0.5) * resolution
        X = xs[None, :]
        Y = ys[:, None]
        e1 = (x2 - x1) * (Y - y1) - (y2 - y1) * (X - x1)
        e2 = (x3 - x2) * (Y - y2) - (y3 - y2) * (X - x2)
        e3 = (x1 - x3) * (Y - y3) - (y1 - y3) * (X - x3)
        inside = ((e1 >= -eps) & (e2 >= -eps) & (e3 >= -eps)) | (
            (e1 <= eps) & (e2 <= eps) & (e3 <= eps)
        )
        if not inside.any():
            continue
        a = (z1 * (y2 - y3) + z2 * (y3 - y1) + z3 * (y1 - y2)) / det
        b = (z1 * (x3 - x2) + z2 * (x1 - x3) + z3 * (x2 - x1)) / det
        c = z1 - a * x1 - b * y1
        Z = (a * X + b * Y + c).astype(np.float32, copy=False)
        arr[r0:r1, c0:c1][inside] = Z[inside]
        if progress and (fi % 5000 == 0 or fi == nfaces - 1):
            progress(
                12 + int(83 * (fi + 1) / nfaces),
                f"Rasterizing triangles: {fi + 1:,}/{nfaces:,}",
            )
    return arr, xmin_r, ymax_r, resolution


def _crs_from_epsg(epsg):
    if not epsg:
        return None
    srs = osr.SpatialReference()
    if srs.ImportFromEPSG(int(epsg)) != 0:
        return None
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return srs.ExportToWkt()


def write_geotiff(
    path,
    array,
    xmin,
    ymax,
    resolution,
    nodata=-9999.0,
    epsg=None,
    creation_options=None,
    wkt=None,
):
    rows, cols = array.shape
    options = creation_options or [
        "TILED=YES",
        "COMPRESS=DEFLATE",
        "PREDICTOR=2",
        "BIGTIFF=IF_SAFER",
    ]
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(path, cols, rows, 1, gdal.GDT_Float32, options=options)
    if ds is None:
        raise IOError(f"Could not create output GeoTIFF: {path}")
    ds.SetGeoTransform((xmin, resolution, 0.0, ymax, 0.0, -resolution))
    projection = wkt or _crs_from_epsg(epsg)
    if projection:
        ds.SetProjection(projection)
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(float(nodata))
    band.WriteArray(array)
    band.SetDescription("LandXML TIN elevation")
    band.FlushCache()
    ds.FlushCache()
    ds = None
    return path
