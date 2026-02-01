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
