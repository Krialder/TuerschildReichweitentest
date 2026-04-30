"""Statistische Helfer fuer die Analyse-Endpoints."""
from __future__ import annotations

import math
import re
from typing import Optional


def percentile(values: list[float], p: float) -> Optional[float]:
    """Lineare Interpolation. ``p`` in [0, 100]."""
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p / 100.0
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def wilson(ok: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson-Score-Intervall. Liefert (low, high) als Anteile in [0, 1]."""
    if n <= 0:
        return (0.0, 0.0)
    p = ok / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def longest_fail_streak(rows_in_order: list[int]) -> int:
    """Laengste zusammenhaengende Fail-Strecke. ``rows_in_order``: 0/1-Liste."""
    longest = cur = 0
    for ok in rows_in_order:
        if ok == 0:
            cur += 1
            longest = max(longest, cur)
        else:
            cur = 0
    return longest


_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})Z?$")


def iso_duration_s(start: str, end: str) -> Optional[int]:
    """Robuste Differenz zweier ISO-UTC-Zeitstempel in Sekunden."""
    a, b = _ISO_RE.match(start), _ISO_RE.match(end)
    if not a or not b:
        return None
    import calendar
    ta = calendar.timegm(tuple(int(x) for x in a.groups()) + (0, 0, 0))
    tb = calendar.timegm(tuple(int(x) for x in b.groups()) + (0, 0, 0))
    return max(0, tb - ta)
