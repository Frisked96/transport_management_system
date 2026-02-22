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

    result = "INR " + words.strip() + " Only"
    # Add paise if needed (optional)
    if d > 0:
        result = result.replace(" Only", f" and {d:02d}/100 Only")
    return result

@register.filter
def sum_attribute(queryset, attr):
    """
    Sum a numeric attribute across a queryset or list.
    Usage: {{ date_group.trips|sum_attribute:'weight' }}
    """
    total = 0
    for item in queryset:
        value = getattr(item, attr, 0)
        if value is None:
            value = 0
        total += value
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
