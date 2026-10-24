from decimal import Decimal, DecimalException
from datetime import datetime
from django.utils import timezone

from transport_mgmt.utils import (
    to_local_datetime,
    format_local_datetime,
    get_local_now,
    get_local_iso_timestamp,
    get_local_time_str,
    get_local_date,
)


def format_indian_comma(amount):
    """
    Formats a number into Indian style commas (e.g., 1,45,140.00).
    """
    try:
        val = Decimal(str(amount))
    except (ValueError, TypeError, DecimalException, Exception):
        return "0.00"

    parts = f"{val:.2f}".split(".")
    whole, decimal = parts[0], parts[1]
    is_negative = whole.startswith("-")
    if is_negative:
        whole = whole[1:]

    if len(whole) <= 3:
        res = whole
    else:
        last_three = whole[-3:]
        remaining = whole[:-3]
        res = ""
        while len(remaining) > 2:
            res = "," + remaining[-2:] + res
            remaining = remaining[:-2]
        res = remaining + res + "," + last_three

    if is_negative:
        res = "-" + res
    return res + "." + decimal


def format_balance(val, party_type=None):
    """
    Formats a balance with Indian commas and Dr / Cr notation.
    If party_type is 'Creditor', positive is Cr and negative is Dr.
    Otherwise (Debtor or Asset/Account), positive is Dr and negative is Cr.
    """
    if val is None:
        return "0.00"
    formatted_val = format_indian_comma(abs(val))
    if party_type == 'Creditor':
        if val > 0:
            return f"{formatted_val}\u00A0Cr"
        elif val < 0:
            return f"{formatted_val}\u00A0Dr"
        return "0.00"
    else:
        if val > 0:
            return f"{formatted_val}\u00A0Dr"
        elif val < 0:
            return f"{formatted_val}\u00A0Cr"
        return "0.00"


def parse_date_range(request):
    """
    Parses start_date and end_date from GET parameters.
    Defaults start_date to first day of current month, and end_date to today.
    """
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')

    if not start_date_str:
        start_date = timezone.now().replace(day=1).date()
    else:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except ValueError:
            start_date = timezone.now().replace(day=1).date()

    if not end_date_str:
        end_date = timezone.now().date()
    else:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            end_date = timezone.now().date()

    return start_date, end_date


def parse_numeric_lr(lr_val):
    """
    Parses a LR number into a numeric value (int or float) if possible.
    Returns None if lr_val is None, empty, or not strictly numeric.
    """
    if lr_val is None:
        return None
    s = str(lr_val).strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return None


def sort_trips_for_bill_generation(trips, bill_trips_map=None):
    """
    Sorts trips when generating a bill:
    Older LR no. on top, newer at bottom (numeric LR ascending).
    If LR is not numeric, falls back to what it was: -date, -created_at (or -id).
    """
    if bill_trips_map is None:
        bill_trips_map = {}

    def sort_key(t):
        bt = bill_trips_map.get(t.id)
        if bt:
            lr_val = bt.lr_no if hasattr(bt, 'lr_no') else bt
        else:
            lr_val = t.lr_no
        num_lr = parse_numeric_lr(lr_val)
        date_ord = t.date.toordinal() if t.date else 0
        created = t.created_at.timestamp() if getattr(t, 'created_at', None) else (t.id or 0)
        if num_lr is not None:
            return (0, num_lr, -date_ord, -created)
        return (1, 0, -date_ord, -created)

    return sorted(trips, key=sort_key)


def sort_bill_trips(bill_trips):
    """
    Sorts BillTrip objects for printed bills and bill views:
    Older LR no. on top, newer at bottom (numeric LR ascending).
    If LR is not numeric, falls back to what it was: trip date ascending.
    """
    import datetime

    def sort_key(bt):
        lr_val = bt.lr_no if bt.lr_no else (bt.trip.lr_no if bt.trip else None)
        num_lr = parse_numeric_lr(lr_val)
        trip_date = bt.trip.date if (bt.trip and bt.trip.date) else datetime.date.min
        trip_id = bt.trip.id if bt.trip else (bt.id or 0)
        if num_lr is not None:
            return (0, num_lr, trip_date, trip_id)
        return (1, 0, trip_date, trip_id)

    return sorted(bill_trips, key=sort_key)

