"""
Service layer for Ledger application.
Handles business logic, complex calculations, and cross-model synchronizations.
"""
from decimal import Decimal
from django.db import transaction, models
from django.db.models import Sum, Q, F

class BalanceService:
    """
    Handles balance recalculations and caching for Parties and CompanyAccounts.
    """
    
    @staticmethod
    def refresh_party_balance(party):
        """
        Recalculate and update the cached balance fields for a Party.
        """
        from ledger.models import Party, TransactionCategory
        
        with transaction.atomic():
            # Lock the party row
            party_obj = Party.objects.select_for_update().get(pk=party.pk)
            
            # 1. Calculate Total Debits
            # Total Debits: Opening Balance (if positive) + Debits (Revenue/Invoices/Notes)
            base_debit = party_obj.opening_balance if party_obj.opening_balance > 0 else Decimal('0')
            records = party_obj.financial_records.select_related('category', 'associated_bill__category').all()
            total_debit = base_debit + sum((r.debit_amount or Decimal('0')) for r in records)
            
            # 2. Calculate Total Credits
            # Total Credits: Opening Balance (if negative) + Credits (Payments/Notes)
            base_credit = abs(party_obj.opening_balance) if party_obj.opening_balance < 0 else Decimal('0')
            total_credit = base_credit + sum((r.credit_amount or Decimal('0')) for r in records)
            
            # 3. Update cached fields
            party_obj.total_debit_amount = total_debit
            party_obj.total_credit_amount = total_credit
            party_obj.current_balance_cached = total_debit - total_credit
            party_obj.save(update_fields=['total_debit_amount', 'total_credit_amount', 'current_balance_cached'])
            
            # Sync local instance fields if it's the same object
            if party == party_obj:
                party.total_debit_amount = party_obj.total_debit_amount
                party.total_credit_amount = party_obj.total_credit_amount
                party.current_balance_cached = party_obj.current_balance_cached
            
            return party_obj.current_balance_cached

    @staticmethod
    def refresh_account_balance(account):
        """
        Recalculate and update the cached balance fields for a CompanyAccount.
        """
        from ledger.models import CompanyAccount, TransactionCategory, FinancialRecord
        
        income = account.financial_records.filter(
            category__type=TransactionCategory.TYPE_INCOME
        ).exclude(
            Q(record_type=FinancialRecord.RECORD_TYPE_INVOICE) | 
            Q(category__name__in=['Deductions', 'TDS', 'Shortage', 'Credit Note', 'Debit Note'])
        ).aggregate(total=Sum('amount'))['total'] or 0

        expenses = account.financial_records.filter(
            category__type=TransactionCategory.TYPE_EXPENSE
        ).exclude(
            Q(record_type=FinancialRecord.RECORD_TYPE_INVOICE) |
            Q(category__name__in=['Deductions', 'TDS', 'Shortage', 'Credit Note', 'Debit Note'])
        ).aggregate(total=Sum('amount'))['total'] or 0

        balance = account.opening_balance + Decimal(str(income)) - Decimal(str(expenses))
        
        account.current_balance_cached = balance
        account.save(update_fields=['current_balance_cached'])
        
        return balance

class LedgerService:
    """
    Handles general ledger maintenance and sequencing.
    """
    
    @staticmethod
    def resequence_financial_records():
        """
        Resequence all entry numbers to remove gaps.
        """
        from ledger.models import FinancialRecord, Sequence, Max
        
        with transaction.atomic():
            records = list(FinancialRecord.objects.all().order_by('date', 'created_at'))
            records_to_update = []
            
            for i, record in enumerate(records, start=1):
                if record.entry_number != i:
                    record._new_entry_number = i
                    records_to_update.append(record)
            
            if records_to_update:
                max_val = FinancialRecord.objects.aggregate(max_val=Max('entry_number'))['max_val'] or 0
                offset = max_val + 1000

                # Step A: Temporary high numbers
                for record in records_to_update:
                    record.entry_number = offset + record.pk
                FinancialRecord.objects.bulk_update(records_to_update, ['entry_number'])

                # Step B: Final numbers
                for record in records_to_update:
                    record.entry_number = record._new_entry_number
                FinancialRecord.objects.bulk_update(records_to_update, ['entry_number'])
                
            Sequence.objects.filter(key='financial_record_entry_number').update(value=len(records))

class BillingService:
    """
    Handles bill generation, numbering, and financial synchronization.
    """
    
    @staticmethod
    def get_next_available_no(issuer, date=None, category=None):
        """
        Finds the next numeric invoice number for the specific prefix series.
        """
        from ledger.models import Bill
        from django.utils import timezone
        
        if not issuer:
            return 1
        
        dt = date or timezone.now()
        year = dt.year
        
        if category:
            if category.name == 'Credit Note':
                prefix = issuer.cn_prefix.replace("{YYYY}", str(year))
            elif category.name == 'Debit Note':
                prefix = issuer.dn_prefix.replace("{YYYY}", str(year))
            else:
                prefix = issuer.invoice_prefix.replace("{YYYY}", str(year))
        else:
            prefix = issuer.invoice_prefix.replace("{YYYY}", str(year))
        
        max_no = Bill.objects.filter(
            bill_number__startswith=prefix
        ).aggregate(max_val=models.Max('bill_no'))['max_val']
        
        if max_no is not None:
            return max_no + 1
        
        return issuer.invoice_sequence_start

    @staticmethod
    def sync_bill_to_ledger(bill):
        """
        Synchronize the bill to the ledger by creating/updating a consolidated invoice record.
        """
        from ledger.models import FinancialRecord, TransactionCategory
        
        if not bill.pk:
            return

        # 1. Update/Create consolidated record
        category = bill.category
        if not category:
            category, _ = TransactionCategory.objects.get_or_create(
                name='Trip Payment',
                type=TransactionCategory.TYPE_INCOME
            )

        total_revenue = bill.rounded_total

        if bill.bill_type == bill.TYPE_TRIP:
            description = f"Invoice {bill.bill_number or 'Draft'} for {bill.trips.count()} trips"
        else:
            against_info = ""
            if category.name in ['Credit Note', 'Debit Note']:
                if bill.original_bill:
                    against_info = f"Against Invoice {bill.original_bill.bill_number}: "
                elif bill.manual_original_bill_number:
                    against_info = f"Against Invoice {bill.manual_original_bill_number}: "
            
            description = f"{against_info}{category.name} {bill.bill_number or 'Draft'}"
            if bill.item_type:
                description = f"{description}: {bill.item_type}"

        FinancialRecord.objects.update_or_create(
            associated_bill=bill,
            record_type=FinancialRecord.RECORD_TYPE_INVOICE,
            party=bill.party,
            defaults={
                'date': bill.date,
                'account': bill.issuer,
                'category': category,
                'amount': total_revenue,
                'description': description,
            }
        )
        
        # 2. Clean up individual trip accruals (both customer and vendor)
        FinancialRecord.objects.filter(
            associated_trip__in=bill.trips.all(),
            record_type=FinancialRecord.RECORD_TYPE_INVOICE
        ).delete()

        # 3. Create consolidated vendor hire records for attached vehicles
        # Group trips by vendor to create one entry per vendor
        from collections import defaultdict
        vendor_totals = defaultdict(Decimal)
        for trip in bill.trips.select_related('vehicle__vendor').all():
            if (trip.vehicle and trip.vehicle.is_attached and 
                trip.vehicle.vendor and trip.vendor_hire_amount > 0):
                vendor_totals[trip.vehicle.vendor] += trip.vendor_hire_amount
        
        lorry_hire_cat, _ = TransactionCategory.objects.get_or_create(
            name='Lorry Hire',
            defaults={'type': TransactionCategory.TYPE_EXPENSE}
        )
        
        # Remove any stale vendor hire records for this bill that are no longer valid
        existing_vendor_pks = [v.pk for v in vendor_totals.keys()]
        FinancialRecord.objects.filter(
            associated_bill=bill,
            record_type=FinancialRecord.RECORD_TYPE_INVOICE,
            category=lorry_hire_cat
        ).exclude(party_id__in=existing_vendor_pks).delete()
        
        for vendor, total in vendor_totals.items():
            FinancialRecord.objects.update_or_create(
                associated_bill=bill,
                record_type=FinancialRecord.RECORD_TYPE_INVOICE,
                party=vendor,
                category=lorry_hire_cat,
                defaults={
                    'date': bill.date,
                    'account': bill.issuer,
                    'amount': total,
                    'description': f"Lorry Hire for Bill {bill.bill_number or 'Draft'}",
                }
            )

    @staticmethod
    def update_bill_financial_caches(bill):
        """
        Recalculate and update all cached financial fields for the bill.
        """
        # Set bypass cache to get real-time calculated values
        bill._bypass_cache = True
        try:
            bill.subtotal_cached = bill.subtotal
            bill.gst_amount_cached = bill.gst_amount
            bill.total_amount_cached = bill.rounded_total
            
            received = BillingService.calculate_bill_received_amount(bill)
            total = bill.total_amount_cached
            
            bill.amount_received_cached = received
            bill.outstanding_balance_cached = total - received
            
            if total <= 0:
                bill.payment_status_cached = bill.PAYMENT_STATUS_UNPAID
            elif received >= total:
                bill.payment_status_cached = bill.PAYMENT_STATUS_PAID
            elif received > 0:
                bill.payment_status_cached = bill.PAYMENT_STATUS_PARTIAL
            else:
                bill.payment_status_cached = bill.PAYMENT_STATUS_UNPAID
        finally:
            del bill._bypass_cache
            
        bill._updating_financial_caches = True
        try:
            bill.save(update_fields=[
                'subtotal_cached', 'gst_amount_cached', 'total_amount_cached',
                'amount_received_cached', 'outstanding_balance_cached', 'payment_status_cached'
            ])
        finally:
            del bill._updating_financial_caches
        
        for trip in bill.trips.all():
            TripFinancialService.update_trip_financial_caches(trip)

    @staticmethod
    def calculate_bill_received_amount(bill):
        """
        Helper to calculate amount received.
        """
        from ledger.models import FinancialRecord, TransactionCategory, TripAllocation
        
        if not bill.pk:
            return Decimal('0')

        # Direct links
        direct = bill.financial_records.exclude(
            record_type=FinancialRecord.RECORD_TYPE_INVOICE
        ).filter(
            Q(category__type=TransactionCategory.TYPE_INCOME) | 
            Q(category__name__in=["Deductions", "TDS", "Shortage", "Credit Note", "Debit Note"])
        ).aggregate(total=Sum('amount'))['total'] or 0

        # Bill allocations
        bill_allocations = bill.payment_allocations.aggregate(total=Sum('amount'))['total'] or 0

        # Trip-based
        trip_payments = 0
        if bill.bill_type == bill.TYPE_TRIP:
             trip_payments = TripAllocation.objects.filter(
                 trip__in=bill.trips.all()
             ).aggregate(total=Sum('amount'))['total'] or 0
             
             direct_trip_payments = FinancialRecord.objects.filter(
                 associated_trip__in=bill.trips.all()
             ).exclude(
                 Q(record_type=FinancialRecord.RECORD_TYPE_INVOICE) |
                 Q(associated_bill=bill) |
                 Q(bill_allocations__bill=bill)
             ).filter(
                 Q(category__type=TransactionCategory.TYPE_INCOME) | 
                 Q(category__name__in=["Deductions", "TDS", "Shortage", "Credit Note", "Debit Note"])
             ).aggregate(total=Sum('amount'))['total'] or 0
             trip_payments += direct_trip_payments

        # Adjustments
        adjustments = 0
        for adj in bill.adjustment_bills.select_related('category').all():
            if adj.category:
                if adj.category.name == 'Credit Note':
                    adjustments += adj.total_amount_cached
                elif adj.category.name == 'Debit Note':
                    adjustments -= adj.total_amount_cached

        return direct + bill_allocations + trip_payments + adjustments

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
        from ledger.models import FinancialRecord, TransactionCategory, CompanyAccount

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

        # Customer revenue accrual
        FinancialRecord.objects.update_or_create(
            associated_trip=trip,
            record_type=FinancialRecord.RECORD_TYPE_INVOICE,
            party=trip.party,
            defaults={
                'date': trip.date,
                'account': account,
                'category': category,
                'amount': trip.total_revenue,
                'description': f"Accrual for Trip {trip.trip_number}",
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
                    'date': trip.date,
                    'account': account,
                    'category': lorry_hire_cat,
                    'amount': trip.vendor_hire_amount,
                    'description': f"Lorry Hire accrual for Trip {trip.trip_number}",
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
        import uuid
        
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
