"""Inclusive UTC calendar dates for columns that store naive UTC timestamps."""

from datetime import date, datetime, time, timedelta
import re
from typing import Annotated

from pydantic import BeforeValidator


def _calendar_date(value):
    # Pydantic's default date parser also accepts Unix seconds and compact ISO
    # dates. Browser calendar filters intentionally accept only YYYY-MM-DD.
    if isinstance(value, str) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Use a calendar date in YYYY-MM-DD format")
    return value


CalendarDate = Annotated[date, BeforeValidator(_calendar_date)]


def validate_range(date_from: date | None, date_to: date | None):
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ValueError("Start date must not be later than end date")
    # Every inclusive end date needs a representable exclusive next midnight.
    if date_to == date.max:
        raise ValueError("End date must be earlier than 9999-12-31")
    if date_from == date.max:
        raise ValueError("Start date must be earlier than 9999-12-31")


def utc_bounds(date_from: date | None, date_to: date | None):
    """Return >= start and < day-after-end bounds, with no server-local timezone."""
    validate_range(date_from, date_to)
    lower = datetime.combine(date_from, time.min) if date_from is not None else None
    upper = datetime.combine(date_to + timedelta(days=1), time.min) if date_to is not None else None
    return lower, upper
