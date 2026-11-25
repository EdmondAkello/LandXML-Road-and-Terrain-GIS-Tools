"""Interactive vertical-profile design: the editing model behind the profile editor (pure Python, no Qt).

A design is a list of PVIs [station, level, curve length] between the first and last station of an alignment. The
editor moves, adds and deletes PVIs and sets curve lengths (directly or from K); every change is checked so the profile
stays valid (stations increase, vertical curves fit between their neighbours, the ends stay at the ends), and can be
undone. ``checks`` compares the design with a design standard. ``splice_profile`` writes it into a copy of the LandXML
text without touching anything else in the file.
"""

from __future__ import annotations

import re

from .writer import TOLERANCE, VerticalProfile, prof_align, quoteattr

MIN_GAP = 1.0            # least distance between PVIs, in stations


class ProfileDesign:
    """An editable profile. Stations and levels are in the file's own units; grades are fractions."""

    def __init__(self, pvis, name="Design FRL"):
        self.name = name
        self.pvis = [[float(s), float(z), float(length or 0.0)] for s, z, length in pvis]
        self._validate(self.pvis)
        self._undo, self._redo = [], []

    # ---------------------------------------------------------- building
    @classmethod
    def from_ground(cls, ground, start, end, spacing=0.0, name="Design FRL", window=None):
        """A first design from a ground profile [(station, level)]: PVIs at the ends on the ground and, with
        ``spacing``, PVIs every ``spacing`` on the ground averaged over ``window`` (default the spacing)."""
        ground = sorted((float(s), float(z)) for s, z in ground)
        if len(ground) < 2 or end <= start:
            raise ValueError("A design needs a ground profile and an end station beyond the start.")
        stations = [start]
        if spacing > 0:
            count = int((end - start) // spacing)
            stations += [start + k * spacing for k in range(1, count + 1) if end - (start + k * spacing) > spacing / 2]
        stations.append(end)
        half = (window or spacing or 0.0) / 2

        def level(s):
            if half <= 0 or s in (start, end):
                return _interp(ground, s)
            inside = [z for t, z in ground if s - half <= t <= s + half]
            return sum(inside) / len(inside) if inside else _interp(ground, s)

        return cls([[s, round(level(s), 3), 0.0] for s in stations], name)

    @classmethod
    def from_controls(cls, controls, name="Design FRL"):
        """From ``read_profile_controls`` rows (an existing design profile)."""
        return cls([[c["station"], c["elevation"], c.get("curve_length") or 0.0] for c in controls], name)

    # ---------------------------------------------------------- reading
    def grade(self, i):
        """Grade from PVI ``i`` to ``i + 1``."""
        s0, z0 = self.pvis[i][:2]
        s1, z1 = self.pvis[i + 1][:2]
        return (z1 - z0) / (s1 - s0)

    def curves(self):
        """[{index, station, length, k, kind, start, end, change}] for every PVI between the ends (k None without a curve);
        ``change`` is the algebraic grade change in %."""
        out = []
        for i in range(1, len(self.pvis) - 1):
            s, _z, length = self.pvis[i]
            change = (self.grade(i) - self.grade(i - 1)) * 100.0
            kind = "crest" if change < 0 else "sag" if change > 0 else "flat"
            k = length / abs(change) if length and abs(change) > 1e-9 else None
            out.append({"index": i, "station": s, "length": length, "k": k, "kind": kind, "change": change,
                        "start": s - length / 2, "end": s + length / 2})
        return out

    def profile(self):
        return VerticalProfile(self.name, [tuple(p) for p in self.pvis])

    def level(self, station):
        return self.profile().elevation(station)

    def sample(self, step=2.0):
        """[(station, level)] every ``step`` and at every curve end and PVI, for drawing and cut/fill."""
        profile = self.profile()
        start, end = self.pvis[0][0], self.pvis[-1][0]
        stations = {start, end}
        s = start
        while s < end:
            stations.add(s)
            s += step
        for c in self.curves():
            stations.update((c["start"], c["station"], c["end"]))
        return [(s, profile.elevation(s)) for s in sorted(stations) if start <= s <= end]

    # ---------------------------------------------------------- editing
    def _apply(self, pvis):
        pvis = [list(p) for p in pvis]
        self._validate(pvis)
        self._undo.append([list(p) for p in self.pvis])
        self._redo.clear()
        self.pvis = pvis

    def move(self, i, station=None, level=None):
        """Move PVI ``i``; the end PVIs keep their station. A station is limited so the curves still fit."""
        pvis = [list(p) for p in self.pvis]
        if station is not None and 0 < i < len(pvis) - 1:
            low = pvis[i - 1][0] + pvis[i - 1][2] / 2 + pvis[i][2] / 2 + MIN_GAP
            high = pvis[i + 1][0] - pvis[i + 1][2] / 2 - pvis[i][2] / 2 - MIN_GAP
            if low > high:
                raise ValueError("There is no room to move this PVI with its curve; shorten a curve first.")
            pvis[i][0] = min(max(float(station), low), high)
        if level is not None:
            pvis[i][1] = float(level)
        self._apply(pvis)
        return pvis[i][0]

    def add(self, station, level=None, curve=0.0):
        """Add a PVI at ``station`` (on the current line when ``level`` is None); returns its index."""
        station = float(station)
        if not self.pvis[0][0] < station < self.pvis[-1][0]:
            raise ValueError("A new PVI must lie between the first and the last.")
        level = self.level(station) if level is None else float(level)
        i = next(k for k, p in enumerate(self.pvis) if p[0] > station)
        pvis = [list(p) for p in self.pvis]
        if station - pvis[i - 1][0] < MIN_GAP or pvis[i][0] - station < MIN_GAP:
            raise ValueError("Too close to an existing PVI.")
        pvis.insert(i, [station, level, float(curve)])
        for k in (i - 1, i + 1):                       # the neighbours' curves may no longer fit: shorten them
            if 0 < k < len(pvis) - 1:
                pvis[k][2] = min(pvis[k][2], self._room(pvis, k))
        self._apply(pvis)
        return i

    def delete(self, i):
        if i in (0, len(self.pvis) - 1):
            raise ValueError("The first and last PVIs cannot be deleted.")
        pvis = [list(p) for p in self.pvis]
        del pvis[i]
        self._apply(pvis)

    def set_curve(self, i, length):
        """Set the curve length at PVI ``i`` (0 removes it); refused when it does not fit."""
        if i in (0, len(self.pvis) - 1):
            raise ValueError("The first and last PVIs cannot have a vertical curve.")
        length = max(float(length), 0.0)
        room = self._room(self.pvis, i)
        if length > room + TOLERANCE:
            raise ValueError(f"A {length:g} curve does not fit here; at most {room:.1f} is free.")
        pvis = [list(p) for p in self.pvis]
        pvis[i][2] = length
        self._apply(pvis)

    def set_k(self, i, k):
        """Set the curve at PVI ``i`` from K (length per 1 % of grade change)."""
        change = abs((self.grade(i) - self.grade(i - 1)) * 100.0)
        self.set_curve(i, round(float(k) * change, 3))

    def nearest(self, station, level=None, tolerance=None):
        """Index of the PVI nearest ``station`` (and ``level`` when given), or None beyond ``tolerance``."""
        best, index = None, None
        for k, (s, z, _l) in enumerate(self.pvis):
            d = abs(s - station) if level is None else ((s - station) ** 2 + (z - level) ** 2) ** 0.5
            if best is None or d < best:
                best, index = d, k
        return index if tolerance is None or best <= tolerance else None

    def undo(self):
        if not self._undo:
            return False
        self._redo.append([list(p) for p in self.pvis])
        self.pvis = self._undo.pop()
        return True

    def redo(self):
        if not self._redo:
            return False
        self._undo.append([list(p) for p in self.pvis])
        self.pvis = self._redo.pop()
        return True

    def mark(self):
        """A point in the undo history; ``squash(mark)`` folds every change since it into one undo step (a drag)."""
        return len(self._undo)

    def squash(self, mark):
        if len(self._undo) > mark + 1:
            del self._undo[mark + 1:]

    @property
    def can_undo(self):
        return bool(self._undo)

    @property
    def can_redo(self):
        return bool(self._redo)

    # ---------------------------------------------------------- checking
    def checks(self, criteria=None, speed=None, max_grade=None, min_grade=None):
        """[(severity, station, text)]: curves shorter than the standard's K for ``speed``, grades steeper than
        ``max_grade`` %, flatter than ``min_grade`` % (drainage), and curves shorter than the minimum length."""
        found = []
        if criteria is not None and min_grade is None:
            min_grade = criteria.get("min_grade_percent")
        for i in range(len(self.pvis) - 1):
            g = self.grade(i) * 100.0
            where = (self.pvis[i][0] + self.pvis[i + 1][0]) / 2
            if max_grade is not None and abs(g) > max_grade + 1e-9:
                found.append(("high", where, f"Grade {g:+.2f} % is steeper than {max_grade:g} %."))
            if min_grade and abs(g) < min_grade - 1e-9:
                found.append(("low", where, f"Grade {g:+.2f} % is flatter than {min_grade:g} % (drainage)."))
        for c in self.curves():
            if abs(c["change"]) < 1e-9:
                continue
            if not c["length"]:
                found.append(("medium", c["station"], f"{c['kind'].capitalize()} at {c['station']:.1f} has no vertical curve "
                                                     f"(grade change {c['change']:+.2f} %)."))
                continue
            if criteria is not None and speed:
                need = criteria.k_crest(speed) if c["kind"] == "crest" else criteria.k_sag(speed)
                if need and c["k"] + 1e-9 < need:
                    found.append(("high", c["station"], f"{c['kind'].capitalize()} K {c['k']:.1f} is below {need:g} for "
                                                        f"{speed:g} {criteria.speed_unit} ({criteria.name})."))
                shortest = criteria.min_vc_length(speed) if hasattr(criteria, "min_vc_length") else 0.0
                if shortest and c["length"] + 1e-9 < shortest:
                    found.append(("medium", c["station"], f"Curve length {c['length']:.1f} is below {shortest:.0f} for "
                                                          f"{speed:g} {criteria.speed_unit}."))
        return sorted(found, key=lambda item: item[1])

    # ---------------------------------------------------------- helpers
    @staticmethod
    def _room(pvis, i):
        """The longest curve that fits at PVI ``i`` between its neighbours' curves."""
        before = pvis[i][0] - pvis[i - 1][0] - pvis[i - 1][2] / 2
        after = pvis[i + 1][0] - pvis[i][0] - pvis[i + 1][2] / 2
        return max(2 * min(before, after), 0.0)

    def _validate(self, pvis):
        VerticalProfile(self.name, [tuple(p) for p in pvis])        # stations increase, ends without curves, no overlap


def _interp(points, s):
    if s <= points[0][0]:
        return points[0][1]
    for (s0, z0), (s1, z1) in zip(points, points[1:]):
        if s <= s1:
            return z0 + (z1 - z0) * (s - s0) / ((s1 - s0) or 1.0)
    return points[-1][1]


def splice_profile(text, alignment, profile):
    """The LandXML ``text`` with ``profile`` (a VerticalProfile) written into ``alignment``: it replaces a design
    profile of the same name, or is added to the alignment's <Profile> (made when there is none). Everything else in
    the file is kept as it is."""
    found = None
    for match in re.finditer(r"<(?:\w+:)?Alignment\b[^>]*>", text):
        name = re.search(r'\bname\s*=\s*"([^"]*)"', match.group(0))
        if name and _unescape(name.group(1)) == alignment:
            found = match
            break
    if found is None:
        raise ValueError(f"Alignment '{alignment}' is not in the file.")
    prefix = re.match(r"<(\w+:)?", found.group(0)).group(1) or ""
    close = text.find(f"</{prefix}Alignment>", found.end())
    if close < 0:
        raise ValueError(f"Alignment '{alignment}' is not closed in the file.")
    body = text[found.end():close]
    new = prof_align(profile)
    if prefix:
        new = re.sub(r"<(/?)(ProfAlign|PVI|ParaCurve)\b", lambda m: f"<{m.group(1)}{prefix}{m.group(2)}", new)
    same = None
    for m in re.finditer(rf"<{prefix}ProfAlign\b[^>]*>.*?</{prefix}ProfAlign>", body, re.S):
        name = re.search(r'\bname\s*=\s*"([^"]*)"', m.group(0)[:m.group(0).index(">")])
        if name and _unescape(name.group(1)) == profile.name:
            same = m
            break
    if same is not None:
        body = body[:same.start()] + new + body[same.end():]
    elif re.search(rf"</{prefix}Profile>", body):
        end = body.rfind(f"</{prefix}Profile>")
        body = body[:end] + new + body[end:]
    else:
        body = body + f"<{prefix}Profile name={quoteattr(alignment)}>{new}</{prefix}Profile>"
    return text[:found.end()] + body + text[close:]


def _unescape(value):
    return (value.replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">").replace("&apos;", "'")
            .replace("&amp;", "&"))
