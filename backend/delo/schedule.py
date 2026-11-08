"""Dates of upcoming tournaments: recurring events and public holidays.

Recurring events repeat weekly (same weekday as their first date) or monthly (same day of
the month; in shorter months the last day). Public holidays are Czech (HOLIDAY_COUNTRY): the
local scene is in Czechia. An event on a holiday gets a "may be cancelled" note.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

RECURRENCES = ("weekly", "monthly")
HOLIDAY_COUNTRY = "CZ"


def easter_sunday(year: int) -> date:
    """Gregorian Easter (anonymous Gregorian / Meeus algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def czech_holidays(year: int) -> dict[date, str]:
    """Czech public holidays (státní svátky and ostatní svátky, days off work)."""
    easter = easter_sunday(year)
    fixed = {
        (1, 1): "New Year's Day / Restoration Day of the Czech State",
        (5, 1): "Labour Day",
        (5, 8): "Liberation Day",
        (7, 5): "Saints Cyril and Methodius Day",
        (7, 6): "Jan Hus Day",
        (9, 28): "Czech Statehood Day",
        (10, 28): "Independent Czechoslovak State Day",
        (11, 17): "Struggle for Freedom and Democracy Day",
        (12, 24): "Christmas Eve",
        (12, 25): "Christmas Day",
        (12, 26): "St. Stephen's Day",
    }
    holidays = {date(year, m, d): name for (m, d), name in fixed.items()}
    holidays[easter - timedelta(days=2)] = "Good Friday"
    holidays[easter + timedelta(days=1)] = "Easter Monday"
    return holidays


def holiday(day: date) -> str | None:
    return czech_holidays(day.year).get(day)


def _monthly(start: date, months_after: int) -> date:
    y, m = divmod(start.month - 1 + months_after, 12)
    year, month = start.year + y, m + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def occurrences(start: date, recurrence: str | None, frm: date, to: date) -> list[date]:
    """Dates of an event between frm and to (inclusive), never before its first date."""
    if not recurrence:
        return [start] if frm <= start <= to else []
    out = []
    if recurrence == "weekly":
        first = start if start >= frm else start + timedelta(days=-(-(frm - start).days // 7) * 7)
        day = first
        while day <= to:
            out.append(day)
            day += timedelta(days=7)
        return out
    n = max(0, (frm.year - start.year) * 12 + frm.month - start.month - 1)
    while (day := _monthly(start, n)) <= to:
        if day >= frm:
            out.append(day)
        n += 1
    return out


def next_occurrence(start: date, recurrence: str | None, today: date) -> date | None:
    """The next date on or after today (one year ahead at most), or None if it's over."""
    found = occurrences(start, recurrence, today, today + timedelta(days=400))
    return found[0] if found else None


def describe(start: date, recurrence: str | None) -> str:
    """"Every Tuesday", "Monthly on the 15th" (empty for one-off events)."""
    if recurrence == "weekly":
        return f"Every {calendar.day_name[start.weekday()]}"
    if recurrence == "monthly":
        d = start.day
        suffix = "th" if 10 <= d % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(d % 10, "th")
        return f"Monthly on the {d}{suffix}"
    return ""
