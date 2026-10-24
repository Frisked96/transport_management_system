"""
General utility functions for Transport Management System (TMS).
Provides centralized date, time, and timezone utilities to ensure consistent
Kolkata (Asia/Kolkata / IST) local time handling across all applications.
"""
from datetime import datetime, date
import zoneinfo
from django.utils import timezone
from django.utils.dateparse import parse_datetime, parse_date
from django.conf import settings

KOLKATA_TZ = zoneinfo.ZoneInfo(getattr(settings, 'TIME_ZONE', 'Asia/Kolkata'))
UTC_TZ = zoneinfo.ZoneInfo('UTC')


def to_local_datetime(val):
    """
    Converts any timestamp (ISO string, datetime, date) to an aware datetime
    in the application's local timezone (Asia/Kolkata).

    Handles:
    - aware datetime in any timezone (converted to local)
    - naive datetime (assumed UTC from server/db, converted to local)
    - ISO 8601 strings with or without offset (e.g. '2026-10-23T09:12:55.503468+00:00')
    - datetime.date instances (converted to midnight local datetime)
    - returns None if value is empty or cannot be parsed
    """
    if val is None or val == '':
        return None

    if isinstance(val, datetime):
        if timezone.is_naive(val):
            val = val.replace(tzinfo=UTC_TZ)
        return val.astimezone(KOLKATA_TZ)

    if isinstance(val, date):
        return datetime.combine(val, datetime.min.time(), tzinfo=KOLKATA_TZ)

    if isinstance(val, str):
        val_str = val.strip()
        dt = parse_datetime(val_str)
        if dt is None:
            try:
                dt = datetime.fromisoformat(val_str)
            except (ValueError, TypeError):
                pass
        if dt is None:
            d = parse_date(val_str)
            if d:
                return datetime.combine(d, datetime.min.time(), tzinfo=KOLKATA_TZ)
            return None

        if timezone.is_naive(dt):
            dt = dt.replace(tzinfo=UTC_TZ)
        return dt.astimezone(KOLKATA_TZ)

    return None


def format_local_datetime(val, fmt='%d %b %Y, %H:%M', default=''):
    """
    Takes any timestamp (ISO string, datetime, date) and formats it into a human-readable
    string in Kolkata local time.

    Usage:
        format_local_datetime('2026-10-23T09:12:55+00:00') -> '23 Oct 2026, 14:42'
        format_local_datetime(voucher.verified_at) -> '24 Oct 2026, 12:11'
        format_local_datetime(val, fmt='%Y-%m-%d %H:%M') -> '2026-10-23 14:42'
    """
    local_dt = to_local_datetime(val)
    if local_dt is None:
        return default or (str(val) if val else '')
    return local_dt.strftime(fmt)


def get_local_now():
    """
    Returns the current datetime in the local (Asia/Kolkata) timezone.
    """
    return timezone.localtime()


def get_local_iso_timestamp():
    """
    Returns the current local datetime as an ISO-8601 string with +05:30 offset.
    e.g. '2026-10-24T12:11:11.123456+05:30'
    Use this for json audit logs, activity payloads, and event logs.
    """
    return timezone.localtime().isoformat()


def get_local_time_str(fmt='%d %b %Y %H:%M'):
    """
    Returns the current local time formatted as a string.
    e.g. '24 Oct 2026 12:11'
    """
    return timezone.localtime().strftime(fmt)


def get_local_date():
    """
    Returns the current date in Asia/Kolkata timezone.
    Prevents off-by-one errors near UTC midnight.
    """
    return timezone.localdate()
