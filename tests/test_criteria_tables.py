"""Check the bundled AASHTO criteria tables against the Green Book equations.

The tables are typed from AASHTO, A Policy on Geometric Design of Highways and
Streets, 7th ed. (2018). These checks catch transcription errors: a value that
disagrees with the equation the table was computed from, or a run of rows
copied from a neighbouring speed.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import unittest

CRITERIA = Path(__file__).resolve().parents[1] / "criteria"


def _load(name):
    return json.loads((CRITERIA / name).read_text(encoding="utf-8"))


def _round_up(value, step):
    return step * math.ceil(value / step - 1e-9)


class _TableChecks:
    """Shared checks; subclasses set the units, file name and equations."""

    data: dict
    side_friction: dict

    def ssd(self, v):
        raise NotImplementedError

    def k_crest(self, s):
        raise NotImplementedError

    def k_sag(self, s):
        raise NotImplementedError

    def r_min(self, v, e):
        raise NotImplementedError

    def test_stopping_sight_distance(self):
        for v, s in self.data["ssd"].items():
            with self.subTest(speed=v):
                self.assertEqual(s, _round_up(self.ssd(int(v)), 5))

    def test_k_values(self):
        # Published design K values are rounded; allow one unit either way.
        for v, s in self.data["ssd"].items():
            with self.subTest(speed=v):
                self.assertLessEqual(abs(self.data["k_crest"][v] - self.k_crest(s)), 1.0)
                self.assertLessEqual(abs(self.data["k_sag"][v] - self.k_sag(s)), 1.0)

    def test_minimum_radius(self):
        for e, row in self.data["min_radius"].items():
            for v, radius in row.items():
                f = self.side_friction.get(int(v))
                if f is None:
                    continue
                with self.subTest(emax=e, speed=v):
                    expected = self.r_min(int(v), int(e) / 100.0 + f)
                    self.assertLessEqual(abs(radius - expected) / expected, 0.02)

    def test_superelevation_rows(self):
        for e, by_speed in self.data["superelevation_by_speed"].items():
            speeds = sorted(by_speed, key=float)
            for v in speeds:
                rows = sorted(((float(r), str(rate)) for r, rate in by_speed[v].items()), reverse=True)
                rates = [float(rate) for _r, rate in rows if rate not in ("NC", "RC")]
                radii = [r for r, rate in rows if rate not in ("NC", "RC")]
                with self.subTest(emax=e, speed=v):
                    self.assertEqual(rates, sorted(rates), "rates must rise as the radius falls")
                    self.assertAlmostEqual(rates[-1], float(e))
                    r_min = self.data["min_radius"].get(e, {}).get(v)
                    if r_min is not None:
                        self.assertLessEqual(abs(radii[-1] - float(r_min)), 1.0)
                    # Neighbouring radii shrink by a smooth ratio; a sudden drop
                    # means rows were taken from another speed.
                    ratios = [b / a for a, b in zip(radii, radii[1:])]
                    for i in range(1, len(ratios) - 1):
                        self.assertGreater(ratios[i], min(ratios[i - 1], ratios[i + 1]) - 0.08)
            for low, high in zip(speeds, speeds[1:]):
                shared = {(r, str(x)) for r, x in by_speed[low].items()} & {
                    (r, str(x)) for r, x in by_speed[high].items()
                }
                with self.subTest(emax=e, speeds=(low, high)):
                    self.assertLessEqual(len(shared), 1, f"rows repeated between speeds: {sorted(shared)}")


class USCustomaryTables(_TableChecks, unittest.TestCase):
    data = _load("aashto_2018_us_customary.json")
    # Green Book Table 3-7, f max for high-speed design (US customary).
    side_friction = {15: .32, 20: .27, 25: .23, 30: .20, 35: .18, 40: .16, 45: .15,
                     50: .14, 55: .13, 60: .12, 65: .11, 70: .10, 75: .09, 80: .08}

    def ssd(self, v):  # Eq. 3-2, t = 2.5 s, a = 11.2 ft/s2
        return 1.47 * v * 2.5 + 1.075 * v * v / 11.2

    def k_crest(self, s):  # Eq. 3-43 with h1 = 3.5 ft, h2 = 2.0 ft
        return s * s / 2158.0

    def k_sag(self, s):  # Eq. 3-49, headlight 2.0 ft, 1 degree
        return s * s / (400.0 + 3.5 * s)

    def r_min(self, v, ef):  # Eq. 3-10
        return v * v / (15.0 * ef)

    def test_stopping_sight_distance_on_grades(self):
        # Eq. 3-3; the 0 % column repeats the design values of Table 3-1.
        table = self.data["ssd_by_grade"]
        for v in self.side_friction:
            for grade, value in zip(table["grades"], table[str(v)]):
                if grade == 0:
                    continue
                d = 1.47 * v * 2.5 + v * v / (30.0 * (11.2 / 32.2 + grade / 100.0))
                with self.subTest(speed=v, grade=grade):
                    self.assertLessEqual(abs(value - d), max(2.0, 0.01 * d))


class MetricTables(_TableChecks, unittest.TestCase):
    data = _load("aashto_2018.json")

    @property
    def side_friction(self):
        return {int(v): f for v, f in self.data["side_friction"].items()}

    def ssd(self, v):  # Eq. 3-2 metric, t = 2.5 s, a = 3.4 m/s2
        return 0.278 * v * 2.5 + 0.039 * v * v / 3.4

    def k_crest(self, s):  # h1 = 1.08 m, h2 = 0.60 m
        return s * s / 658.0

    def k_sag(self, s):
        return s * s / (120.0 + 3.5 * s)

    def r_min(self, v, ef):
        return v * v / (127.0 * ef)


if __name__ == "__main__":
    unittest.main()
