from django.db.models import Sum
from .models import FinancialRecord

def recalculate_leg_status(leg):
    """
    Recalculates the amount_received and payment_status for a TripLeg
    based on all associated FinancialRecords.
    """
    total_received = 0
    
    # Get all payment records associated with this leg
    # We include both FREIGHT_INCOME (Legacy/Misused) and PARTY_PAYMENT
    records = leg.financial_records.filter(
        category__in=[
            FinancialRecord.CATEGORY_FREIGHT_INCOME,
            FinancialRecord.CATEGORY_PARTY_PAYMENT
        ]
    )
    
    for record in records:
        # How many legs is this record associated with?
        num_legs = record.associated_legs.count()
        if num_legs > 0:
            # Split amount equally among legs
            total_received += (record.amount / num_legs)
            
    leg.amount_received = total_received
    
    if leg.amount_received >= leg.revenue and leg.revenue > 0:
        leg.payment_status = leg.PAYMENT_STATUS_PAID
    elif leg.amount_received > 0:
        leg.payment_status = leg.PAYMENT_STATUS_PARTIAL
    else:
        leg.payment_status = leg.PAYMENT_STATUS_UNPAID
        
    leg.save()
