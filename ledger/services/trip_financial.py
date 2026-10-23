"""
Financial calculations and ledger synchronization for Trips.
"""
from decimal import Decimal
import uuid
from django.db import transaction
from django.db.models import Sum, Q
from django.utils import timezone


class TripFinancialService:
    """
    Handles financial calculations and sync for Trips.
    """
    
    @staticmethod
    def sync_trip_accrual(trip):
        """
        Manage accrual-based revenue for this trip.
        Also manages vendor hire accruals for attached vehicles.
        """
        from ledger.models import FinancialRecord, FinancialVoucher, TransactionCategory, CompanyAccount
        from ledger.services import VoucherService

        if trip.is_billed:
            FinancialRecord.objects.filter(
                associated_trip=trip,
                record_type=FinancialRecord.RECORD_TYPE_INVOICE
            ).delete()
            return

        if not trip.revenue or not trip.party:
            FinancialRecord.objects.filter(
                associated_trip=trip,
                record_type=FinancialRecord.RECORD_TYPE_INVOICE
            ).delete()
            return

        category, _ = TransactionCategory.objects.get_or_create(
            name='Trip Payment',
            type=TransactionCategory.TYPE_INCOME
        )

        account = CompanyAccount.objects.first()
        if not account:
             return

        trip_date = trip.date or timezone.now().date()
        user = trip.created_by or getattr(trip, '_current_user', None)

        batch_voucher = getattr(trip, '_batch_voucher', None)
        existing_rec = FinancialRecord.all_objects.filter(associated_trip=trip, record_type=FinancialRecord.RECORD_TYPE_INVOICE).first()
        if existing_rec and existing_rec.voucher:
            voucher = existing_rec.voucher
        elif batch_voucher:
            voucher = batch_voucher
        else:
            voucher = VoucherService.create_voucher(
                date=trip_date,
                voucher_type=FinancialVoucher.VOUCHER_TYPE_TRIP_ACCRUAL,
                created_by=user,
                party=trip.party,
                account=account,
                description=f"Trip #{trip.trip_number} Accrual"
            )

        # Customer revenue accrual
        FinancialRecord.objects.update_or_create(
            associated_trip=trip,
            record_type=FinancialRecord.RECORD_TYPE_INVOICE,
            party=trip.party,
            defaults={
                'date': trip_date,
                'account': account,
                'category': category,
                'amount': trip.total_revenue,
                'description': f"Accrual for Trip {trip.trip_number}",
                'voucher': voucher,
                'recorded_by': user,
                'verification_status': FinancialVoucher.STATUS_PENDING,
            }
        )

        # Vendor hire accrual for attached vehicles
        if (trip.vehicle and trip.vehicle.is_attached and 
            trip.vehicle.vendor and trip.vendor_hire_amount > 0):
            lorry_hire_cat, _ = TransactionCategory.objects.get_or_create(
                name='Lorry Hire',
                defaults={'type': TransactionCategory.TYPE_EXPENSE}
            )
            FinancialRecord.objects.update_or_create(
                associated_trip=trip,
                record_type=FinancialRecord.RECORD_TYPE_INVOICE,
                party=trip.vehicle.vendor,
                defaults={
                    'date': trip_date,
                    'account': account,
                    'category': lorry_hire_cat,
                    'amount': trip.vendor_hire_amount,
                    'description': f"Lorry Hire accrual for Trip {trip.trip_number}",
                    'voucher': voucher,
                    'recorded_by': user,
                    'verification_status': FinancialVoucher.STATUS_PENDING,
                }
            )
        else:
            # Clean up any stale vendor hire accruals if vehicle is no longer attached
            lorry_hire_cat = TransactionCategory.objects.filter(
                name='Lorry Hire'
            ).first()
            if lorry_hire_cat:
                FinancialRecord.objects.filter(
                    associated_trip=trip,
                    record_type=FinancialRecord.RECORD_TYPE_INVOICE,
                    category=lorry_hire_cat
                ).delete()

        voucher.update_totals()

    @staticmethod
    def update_trip_financial_caches(trip):
        """
        Recalculate and update cached received amount and outstanding balance.
        """
        if not trip.pk:
            return

        received = TripFinancialService.calculate_trip_received_amount(trip)
        total_rev = trip.total_revenue_cached
        
        trip.amount_received_cached = received
        trip.outstanding_balance_cached = total_rev - received
        
        if total_rev <= 0:
            trip.payment_status_cached = trip.PAYMENT_STATUS_UNPAID
        elif received >= total_rev:
            trip.payment_status_cached = trip.PAYMENT_STATUS_PAID
        elif received > 0:
            trip.payment_status_cached = trip.PAYMENT_STATUS_PARTIAL
        else:
            trip.payment_status_cached = trip.PAYMENT_STATUS_UNPAID
            
        trip._updating_financial_caches = True
        try:
            trip.save(update_fields=[
                'amount_received_cached', 'outstanding_balance_cached', 'payment_status_cached'
            ])
        finally:
            del trip._updating_financial_caches

    @staticmethod
    def calculate_trip_received_amount(trip):
        """
        Helper to calculate amount received.
        """
        from ledger.models import FinancialRecord, TransactionCategory
        
        if not trip.pk:
            return 0

        # Direct links
        direct = trip.financial_records.exclude(
            record_type=FinancialRecord.RECORD_TYPE_INVOICE
        ).filter(
            Q(category__type=TransactionCategory.TYPE_INCOME) | 
            Q(category__name__in=["Deductions", "TDS", "Shortage"])
        ).aggregate(total=Sum('amount'))['total'] or 0
        
        # M2M Allocations
        allocated = trip.payment_allocations.aggregate(
            total=Sum('amount')
        )['total'] or 0
        
        # Share of Bill Payments/Adjustments
        bill = trip.associated_bill
        if bill:
            if bill.payment_status_cached == 'Paid':
                return trip.total_revenue_cached
            
            direct_bill = bill.financial_records.exclude(
                record_type=FinancialRecord.RECORD_TYPE_INVOICE
            ).filter(
                Q(category__type=TransactionCategory.TYPE_INCOME) | 
                Q(category__name__in=["Deductions", "TDS", "Shortage", "Credit Note", "Debit Note"])
            ).aggregate(total=Sum('amount'))['total'] or 0
            
            adjustments = 0
            for adj in bill.adjustment_bills.select_related('category').all():
                if adj.category:
                    if adj.category.name == 'Credit Note':
                        adjustments += adj.rounded_total
                    elif adj.category.name == 'Debit Note':
                        adjustments -= adj.rounded_total
            
            bill_pool = direct_bill + adjustments
            if bill_pool > 0:
                bill_total = bill.total_amount_cached
                if bill_total > 0:
                    share = (trip.total_revenue_cached / bill_total) * bill_pool
                    return direct + allocated + share

        return direct + allocated

    @staticmethod
    def recalculate_vehicle_trip_numbers(vehicle):
        """
        Recalculate and update all trip numbers for a specific vehicle.
        """
        from trips.models import Trip
        from ledger.models import Sequence
        
        trips = Trip.objects.filter(vehicle=vehicle).order_by('date', 'created_at')
        reg_plate = vehicle.registration_plate
        
        total_count = 0
        trips_to_update = []
        
        for trip in trips:
            total_count += 1
            new_number = f"{reg_plate}-{total_count}"
            if trip.trip_number != new_number:
                trip.trip_number = new_number
                trips_to_update.append(trip)
        
        if trips_to_update:
            with transaction.atomic():
                # Step 1: Temporary numbers
                for trip in trips_to_update:
                    trip._target_number = trip.trip_number
                    trip.trip_number = f"TEMP-{uuid.uuid4().hex[:8]}-{trip.pk}"
                Trip.objects.bulk_update(trips_to_update, ['trip_number'])
                
                # Step 2: Final numbers
                for trip in trips_to_update:
                    trip.trip_number = trip._target_number
                Trip.objects.bulk_update(trips_to_update, ['trip_number'])
        
        # Update sequences
        Sequence.objects.filter(key=f"trip_total_{vehicle.pk}").update(value=total_count)
