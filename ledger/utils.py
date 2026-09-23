from decimal import Decimal, InvalidOperation, DecimalException
from datetime import datetime
from django.utils import timezone
from django.db.models import Q, Sum


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
