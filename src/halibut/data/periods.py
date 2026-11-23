"""Observation periods: parsing publisher time codes into canonical periods."""

from __future__ import annotations

import calendar
import re
from datetime import date

_MONTH = re.compile(r"^(\d{4})M(\d{2})$")
_QUARTER = re.compile(r"^(\d{4})[KQ]([1-4])$")
_YEAR = re.compile(r"^(\d{4})$")


def parse_period(code: str, frequency: str) -> tuple[str, str, str]:
    """Return (canonical period, start date, end date) as ISO strings.

    Canonical forms: ``YYYY-MM`` (monthly), ``YYYY-Qn`` (quarterly), ``YYYY``.
    Accepts SSB codes ``1995M01``, ``2024K1`` (Norwegian 'kvartal') and ``2025``.
    """
    code = code.strip()
    if frequency == "M":
        m = _MONTH.match(code) or re.match(r"^(\d{4})-(\d{2})$", code)
        if m:
            y, mo = int(m.group(1)), int(m.group(2))
            if 1 <= mo <= 12:
                last = calendar.monthrange(y, mo)[1]
                return f"{y:04d}-{mo:02d}", date(y, mo, 1).isoformat(), date(y, mo, last).isoformat()
    elif frequency == "Q":
        m = _QUARTER.match(code) or re.match(r"^(\d{4})-Q([1-4])$", code)
        if m:
            y, q = int(m.group(1)), int(m.group(2))
            m0, m1 = 3 * q - 2, 3 * q
            return (f"{y:04d}-Q{q}", date(y, m0, 1).isoformat(),
                    date(y, m1, calendar.monthrange(y, m1)[1]).isoformat())
    elif frequency == "A":
        m = _YEAR.match(code)
        if m:
            y = int(m.group(1))
            return f"{y:04d}", date(y, 1, 1).isoformat(), date(y, 12, 31).isoformat()
    raise ValueError(f"cannot parse period {code!r} at frequency {frequency!r}")
