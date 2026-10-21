"""Indexed sections through original TIN triangles, without raster resampling."""

from __future__ import annotations

from bisect import bisect_right
import math

import numpy as np
from qgis.core import QgsFeature, QgsGeometry, QgsPointXY, QgsRectangle, QgsSpatialIndex


class TinSampler:
    def __init__(self, vertices, faces, feedback):
        self.vertices = vertices
        self.faces = faces
        self.feedback = feedback
        self.index = QgsSpatialIndex()
        skipped = 0
        for index, face in enumerate(faces):
            if index % 1000 == 0:
                self.check_cancel()
                feedback.setProgressText(f"Indexing terrain triangles: {index:,}/{len(faces):,}")
            points = vertices[face, :2]
            a, b = points[1] - points[0], points[2] - points[0]
            determinant = a[0] * b[1] - a[1] * b[0]
            if abs(determinant) <= np.finfo(float).eps * max(np.dot(a, a), np.dot(b, b)) * 16:
                skipped += 1
                continue
            feature = QgsFeature(index)
            ring = [QgsPointXY(float(x), float(y)) for x, y in points]
            feature.setGeometry(QgsGeometry.fromPolygonXY([ring + [ring[0]]]))
            self.index.addFeature(feature)
        if skipped:
            feedback.pushWarning(f"Skipped {skipped:,} degenerate terrain triangles.")

    def check_cancel(self):
        if self.feedback.isCanceled():
            raise RuntimeError("LandXML preparation cancelled.")

    def segment(self, start, end, low, high):
        """Return continuous [(distance, elevation)] parts along a line segment.

        ``low``/``high`` map its endpoints to station or section offset. Triangle
        boundaries become vertices; uncovered intervals never get connected.
        """
        self.check_cancel()
        start, end = np.asarray(start), np.asarray(end)
        bounds = QgsRectangle(float(min(start[0], end[0])), float(min(start[1], end[1])),
                              float(max(start[0], end[0])), float(max(start[1], end[1])))
        bounds.grow(1e-8)
        spans = []
        for count, index in enumerate(sorted(self.index.intersects(bounds))):
            if count % 1000 == 0:
                self.check_cancel()
            triangle = self.vertices[self.faces[index]]
            a, b = triangle[1, :2] - triangle[0, :2], triangle[2, :2] - triangle[0, :2]
            det = a[0] * b[1] - a[1] * b[0]

            def weights(point):
                x, y = point - triangle[0, :2]
                u = (x * b[1] - y * b[0]) / det
                v = (a[0] * y - a[1] * x) / det
                return np.array([1 - u - v, u, v])

            w0, w1 = weights(start), weights(end)
            begin, finish = 0.0, 1.0
            for value, change in zip(w0, w1 - w0):
                if abs(change) < 1e-14:
                    if value < -1e-10:
                        finish = -1.0
                        break
                elif change > 0:
                    begin = max(begin, -value / change)
                else:
                    finish = min(finish, -value / change)
            if finish - begin <= 1e-12:
                continue
            z0, z1 = float(np.dot(w0, triangle[:, 2])), float(np.dot(w1, triangle[:, 2]))
            spans.append((low + begin * (high - low), z0 + begin * (z1 - z0),
                          low + finish * (high - low), z0 + finish * (z1 - z0)))
        parts = []
        for s0, z0, s1, z1 in sorted(spans):
            if parts and s0 <= parts[-1][-1][0] + 1e-7:
                previous = parts[-1]
                if s0 < previous[-1][0] - 1e-7:
                    # Shared edges may be covered twice, but stacked TINs are ambiguous.
                    first = max(0, bisect_right(previous, (s0, math.inf)) - 1)
                    for a, b in zip(previous[first:], previous[first + 1:]):
                        if a[0] >= s1:
                            break
                        for position in (max(s0, a[0]), min(s1, b[0])):
                            expected = a[1] + (position - a[0]) / (b[0] - a[0]) * (b[1] - a[1])
                            actual = z0 + (position - s0) / (s1 - s0) * (z1 - z0)
                            if not math.isclose(actual, expected, abs_tol=1e-6, rel_tol=0):
                                raise ValueError("Overlapping terrain triangles have different elevations.")
                elif not math.isclose(z0, previous[-1][1], abs_tol=1e-6, rel_tol=0):
                    raise ValueError("Terrain triangles disagree at a shared edge.")
                if s1 > previous[-1][0] + 1e-7:
                    previous.append((s1, z1))
            else:
                parts.append([(s0, z0), (s1, z1)])
        return parts


def regular_stations(start, end, interval):
    """Absolute station multiples plus both alignment endpoints."""
    count = max(0, math.ceil(end / interval) - math.floor(start / interval) - 1)
    return [start] + [(math.floor(start / interval) + i + 1) * interval for i in range(count)] + [end]


def sample_profile(sampler, polyline, interval):
    stations = sorted(set(polyline.stations + regular_stations(polyline.station_start, polyline.station_end, interval)))
    parts = []
    for start, end in zip(stations, stations[1:]):
        for run in sampler.segment(polyline.point_at(start), polyline.point_at(end), start, end):
            if parts and abs(parts[-1][-1][0] - run[0][0]) < 1e-7:
                if not math.isclose(parts[-1][-1][1], run[0][1], abs_tol=1e-6, rel_tol=0):
                    raise ValueError("Terrain elevation is discontinuous at an alignment sample.")
                parts[-1].extend(run[1:])
            else:
                parts.append(run)
    return parts
