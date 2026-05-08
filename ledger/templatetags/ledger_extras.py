"""
Custom template tags and filters for the Ledger app.
"""
from django import template
from decimal import Decimal

register = template.Library()

@register.filter
def calculate_trip_gst(bill, trip):
    """
    Calculates GST amount for a trip based on the bill's GST rate.
    Usage: {{ bill|calculate_trip_gst:trip }}
    """
    if not trip.revenue or bill.gst_rate == 0:
        return 0
    return trip.revenue * (Decimal(bill.gst_rate) / Decimal(100))

@register.filter
def add_decimal(value, arg):
    """
    Adds two decimal values.
    Usage: {{ value|add_decimal:arg }}
    """
    try:
        return Decimal(value) + Decimal(arg)
    except (ValueError, TypeError):
        return 0

@register.filter
def num2words(num):
    """
    Converts a number to Indian currency words (Lakhs/Crores).
    Example: 503626 -> "INR Five Lakh Three Thousand Six Hundred Twenty Six Only"
    """
    if not num:
        return ""
    try:
        num = float(num)
    except (ValueError, TypeError):
        return ""

    n = int(num)
    d = int(round((num - n) * 100))

    units = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"]
    teens = ["Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
             "Seventeen", "Eighteen", "Nineteen"]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

    def convert_upto_999(val):
        if val == 0:
            return ""
        elif val < 10:
            return units[val]
        elif val < 20:
            return teens[val - 10]
        elif val < 100:
            ten = tens[val // 10]
            unit = units[val % 10]
            return ten + (" " + unit if unit else "")
        else:
            hundred = units[val // 100] + " Hundred"
            remainder = val % 100
            if remainder:
                return hundred + " " + convert_upto_999(remainder)
            return hundred

    words = ""
    # Only support up to 99 Crores for now for simplicity, extend if needed
    if n >= 10000000:  # Crores
        words += convert_upto_999(n // 10000000) + " Crore "
        n %= 10000000
    if n >= 100000:  # Lakhs
        words += convert_upto_999(n // 100000) + " Lakh "
        n %= 100000
    if n >= 1000:  # Thousands
        words += convert_upto_999(n // 1000) + " Thousand "
        n %= 1000
    words += convert_upto_999(n)

    result = words.strip()
    # Add paise if needed (optional)
    if d > 0:
        result = result + f" and {d:02d}/100"
    return result

@register.filter
def sum_attribute(queryset, attr):
    """
    Sum a numeric attribute across a queryset or list.
    Supports nested attributes using dot notation (e.g. 'trip.revenue').
    Usage: {{ bill.bill_trips.all|sum_attribute:'trip.revenue' }}
    """
    total = 0
    if not queryset:
        return 0
        
    for item in queryset:
        # Support nested attributes
        val = item
        for part in attr.split('.'):
            if val is None:
                break
            val = getattr(val, part, 0)
        
        if val is None:
            val = 0
        try:
            total += Decimal(str(val))
        except (ValueError, TypeError, Exception):
            pass
    return total

@register.filter
def get_route_description(bill):
    """
    Returns a string describing the unique routes in the bill's trips.
    Format: "Pickup - Delivery"
    Usage: {{ bill|get_route_description }}
    """
    routes = set()
    for trip in bill.trips.all():
        if trip.pickup_location and trip.delivery_location:
            routes.add(f"{trip.pickup_location} - {trip.delivery_location}")
        elif trip.pickup_location:
             routes.add(f"From {trip.pickup_location}")
        elif trip.delivery_location:
             routes.add(f"To {trip.delivery_location}")
    
    if not routes:
        return bill.description or "Transportation Service"
        
    return ", ".join(sorted(list(routes)))

@register.filter
def sum_list(data_list, key):
    """
    Sum a key across a list of dictionaries.
    Usage: {{ statement_rows|sum_list:"debit" }}
    """
    if not data_list:
        return Decimal('0')
    total = Decimal('0')
    for item in data_list:
        try:
            val = item.get(key, 0)
            if val:
                total += Decimal(str(val))
        except (ValueError, TypeError, InvalidOperation):
            continue
    return total

@register.filter
def subtract(value, arg):
    """
    Subtracts arg from value.
    Usage: {{ value|subtract:arg }}
    """
    try:
        return Decimal(str(value)) - Decimal(str(arg))
    except (ValueError, TypeError, Exception):
        return value

@register.filter
def get_trip_gst(bill, trip_or_bt):
    """
    Calculates GST amount for a trip or bill_trip based on the bill's GST rate.
    Accounts for BillTrip specific discount if a BillTrip object is passed.
    Usage: {{ bill|get_trip_gst:bt }}
    """
    revenue = 0
    discount = 0
    
    # Check if we got a BillTrip or a Trip
    if hasattr(trip_or_bt, 'trip'): # It's a BillTrip
        revenue = trip_or_bt.trip.revenue or 0
        discount = trip_or_bt.discount or 0
    else: # It's a Trip
        revenue = trip_or_bt.revenue or 0
        # If it's just a trip, we don't know the bill-specific discount here 
        # unless we were passed the BillTrip.
    
    taxable_value = Decimal(str(revenue)) - Decimal(str(discount))
    if taxable_value <= 0 or bill.gst_rate == 0:
        return Decimal('0')
        
    return taxable_value * (Decimal(str(bill.gst_rate)) / Decimal(100))

@register.filter
def get_trip_total(bill, trip_or_bt):
    """
    Calculates Total amount (Taxable Value + GST) for a trip or bill_trip.
    Usage: {{ bill|get_trip_total:bt }}
    """
    revenue = 0
    discount = 0
    
    if hasattr(trip_or_bt, 'trip'):
        revenue = trip_or_bt.trip.revenue or 0
        discount = trip_or_bt.discount or 0
    else:
        revenue = trip_or_bt.revenue or 0
        
    taxable_value = Decimal(str(revenue)) - Decimal(str(discount))
    gst = get_trip_gst(bill, trip_or_bt)
    return taxable_value + gst

@register.filter
def abs_val(value):
    """
    Returns absolute value of a number.
    Usage: {{ value|abs_val }}
    """
    try:
        return abs(Decimal(str(value)))
    except (ValueError, TypeError, Exception):
        return 0

@register.filter
def indian_comma(value):
    """
    Formats a number into Indian style commas (e.g., 1,45,140.00).
    """
    if value is None or value == "":
        return "0.00"
    try:
        amount = Decimal(str(value))
    except (ValueError, TypeError, Exception):
        return "0.00"

    # Separate decimal and whole part
    parts = f"{amount:.2f}".split(".")
    whole = parts[0]
    decimal = parts[1]

    # Handle negative
    is_negative = whole.startswith("-")
    if is_negative:
        whole = whole[1:]

    # Last 3 digits remain as a block
    if len(whole) <= 3:
        res = whole
    else:
        # Separate the last 3 digits
        last_three = whole[-3:]
        remaining = whole[:-3]
        # Group the rest in 2s
        res = ""
        while len(remaining) > 2:
            res = "," + remaining[-2:] + res
            remaining = remaining[:-2]
        res = remaining + res + "," + last_three

    if is_negative:
        res = "-" + res

    return res + "." + decimal

