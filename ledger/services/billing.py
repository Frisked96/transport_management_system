"""
Billing generation, numbering, and financial ledger synchronization service.
"""
from decimal import Decimal
from collections import defaultdict
from django.db import transaction, models
from django.db.models import Sum, Q
from django.utils import timezone

from .trip_financial import TripFinancialService


class BillingService:
    """
    Handles bill generation, numbering, and financial synchronization.
    """
    
    @staticmethod
    def get_next_available_no(issuer, date=None, category=None):
        """
        Finds the next numeric invoice number for the specific prefix series.
        Uses select_for_update on CompanyAccount to prevent concurrency race conditions.
        """
        from ledger.models import Bill, CompanyAccount
        
        if not issuer:
            return 1
            
        with transaction.atomic():
            try:
                issuer_obj = CompanyAccount.objects.select_for_update().get(pk=issuer.pk)
            except (CompanyAccount.DoesNotExist, Exception):
                issuer_obj = issuer

            dt = date or timezone.now()
            year = dt.year
            
            if category:
                if category.name == 'Credit Note':
                    prefix = issuer_obj.cn_prefix.replace("{YYYY}", str(year))
                elif category.name == 'Debit Note':
                    prefix = issuer_obj.dn_prefix.replace("{YYYY}", str(year))
                else:
                    prefix = issuer_obj.invoice_prefix.replace("{YYYY}", str(year))
            else:
                prefix = issuer_obj.invoice_prefix.replace("{YYYY}", str(year))
            
            max_no = Bill.objects.filter(
                bill_number__startswith=prefix
            ).aggregate(max_val=models.Max('bill_no'))['max_val']
            
            if max_no is not None:
                return max_no + 1
            
            return issuer_obj.invoice_sequence_start

    @staticmethod
    def sync_bill_to_ledger(bill):
        """
        Synchronize the bill to the ledger by creating/updating a consolidated invoice record.
        """
        from ledger.models import FinancialRecord, FinancialVoucher, TransactionCategory, BillTrip, Bill
        from ledger.services import VoucherService

        if not bill.pk:
            return

        with transaction.atomic():
            lorry_hire_cat, _ = TransactionCategory.objects.get_or_create(
                name='Lorry Hire',
                defaults={'type': TransactionCategory.TYPE_EXPENSE}
            )

            # Resolve or create voucher for this bill
            voucher = None
            if bill.customer_bill:
                cust_rec = FinancialRecord.all_objects.filter(associated_bill=bill.customer_bill).first()
                if cust_rec and cust_rec.voucher:
                    voucher = cust_rec.voucher

            if not voucher:
                existing_rec = FinancialRecord.all_objects.filter(associated_bill=bill).first()
                if existing_rec and existing_rec.voucher:
                    voucher = existing_rec.voucher
                else:
                    voucher = VoucherService.create_voucher(
                        date=bill.date,
                        voucher_type=FinancialVoucher.VOUCHER_TYPE_BILL_INVOICE,
                        created_by=bill.created_by or getattr(bill, '_current_user', None),
                        party=bill.party,
                        account=bill.issuer,
                        description=f"Bill #{bill.bill_number or 'Draft'}"
                    )

            # Case A: If this bill is a Creditor Bill or Creditor Debit Note (linked to a customer bill)
            if bill.customer_bill:
                cat = bill.category or lorry_hire_cat
                inv_disp = bill.creditor_invoice_number or bill.bill_number or 'Pending'
                cust_ref = bill.customer_bill.bill_number or f"Draft-{bill.customer_bill.pk}"
                if cat.name == 'Debit Note':
                    parent_ref = bill.original_bill.bill_number if bill.original_bill else cust_ref
                    desc = f"Debit Note #{inv_disp} (Against Bill {parent_ref})"
                    if bill.item_type:
                        desc += f": {bill.item_type}"
                else:
                    desc = f"Lorry Hire Inv #{inv_disp} (Against Bill {cust_ref})"

                FinancialRecord.objects.update_or_create(
                    associated_bill=bill,
                    record_type=FinancialRecord.RECORD_TYPE_INVOICE,
                    party=bill.party,
                    defaults={
                        'date': bill.date,
                        'account': bill.issuer,
                        'category': cat,
                        'amount': bill.rounded_total,
                        'description': desc,
                        'voucher': voucher,
                        'recorded_by': bill.created_by or getattr(bill, '_current_user', None),
                        'verification_status': FinancialVoucher.STATUS_PENDING,
                    }
                )
                voucher.update_totals()
                if bill.original_bill:
                    BillingService.update_bill_financial_caches(bill.original_bill)
                bill.party.refresh_balance()
                return

            # Case B: Customer / Standard Bill
            # 1. Update/Create consolidated record for Customer
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
                    'voucher': voucher,
                    'recorded_by': bill.created_by or getattr(bill, '_current_user', None),
                    'verification_status': FinancialVoucher.STATUS_PENDING,
                }
            )
            voucher.update_totals()

            # Auto-mirror Customer Credit Notes as Debit Notes for Creditors of attached vehicles
            if bill.category and bill.category.name == 'Credit Note' and bill.original_bill:
                parent_cust_bill = bill.original_bill
                debit_note_cat, _ = TransactionCategory.objects.get_or_create(
                    name='Debit Note',
                    defaults={'type': TransactionCategory.TYPE_INCOME}
                )
                for cr_bill in parent_cust_bill.creditor_bills.all():
                    cr_dn = Bill.objects.filter(customer_bill=bill, party=cr_bill.party).first()
                    base_no = bill.bill_number or f"DRAFT-{bill.pk}"
                    cr_dn_number = f"DN-{base_no}"
                    if not cr_dn:
                        cr_dn = Bill(
                            customer_bill=bill,
                            party=cr_bill.party,
                            original_bill=cr_bill,
                            bill_type=Bill.TYPE_STANDARD,
                            category=debit_note_cat,
                            date=bill.date,
                            issuer=bill.issuer,
                            bill_number=cr_dn_number,
                            item_type=bill.item_type or "Shortage",
                            amount_override=bill.subtotal,
                            gst_rate=bill.gst_rate,
                            gst_type=bill.gst_type,
                            use_roundoff=bill.use_roundoff,
                        )
                        cr_dn.save()
                    else:
                        cr_dn.original_bill = cr_bill
                        cr_dn.date = bill.date
                        cr_dn.item_type = bill.item_type or "Shortage"
                        cr_dn.amount_override = bill.subtotal
                        cr_dn.gst_rate = bill.gst_rate
                        cr_dn.gst_type = bill.gst_type
                        cr_dn.use_roundoff = bill.use_roundoff
                        if not cr_dn.bill_number or cr_dn.bill_number.startswith("DN-DRAFT-"):
                            cr_dn.bill_number = cr_dn_number
                        cr_dn.save()

                    BillingService.update_bill_financial_caches(cr_dn)
                    cr_dn.sync_to_ledger()
                    BillingService.update_bill_financial_caches(cr_bill)
                    cr_bill.party.refresh_balance()

            if bill.category and bill.category.name in ['Credit Note', 'Debit Note']:
                return

            if bill.bill_type != Bill.TYPE_TRIP:
                return

            # 2. Clean up individual trip accruals (both customer and vendor)
            FinancialRecord.objects.filter(
                associated_trip__in=bill.trips.all(),
                record_type=FinancialRecord.RECORD_TYPE_INVOICE
            ).delete()

            # Clean up any legacy direct-linked vendor records on customer bill
            FinancialRecord.objects.filter(
                associated_bill=bill,
                record_type=FinancialRecord.RECORD_TYPE_INVOICE,
                category=lorry_hire_cat
            ).delete()

            # 3. Create or update linked Creditor Bills for attached vehicles
            vendor_trips = defaultdict(list)
            for trip in bill.trips.select_related('vehicle__vendor').all():
                if (trip.vehicle and trip.vehicle.is_attached and 
                    trip.vehicle.vendor and trip.vendor_hire_amount > 0):
                    vendor_trips[trip.vehicle.vendor].append(trip)

            # Remove stale creditor bills if vendors were removed
            current_vendor_pks = [v.pk for v in vendor_trips.keys()]
            stale_creditor_bills = list(bill.creditor_bills.exclude(party_id__in=current_vendor_pks))
            for stale_cb in stale_creditor_bills:
                stale_cb.delete()

            for vendor, trips_list in vendor_trips.items():
                base_no = bill.bill_number or f"DRAFT-{bill.pk}"
                creditor_bill_no = f"CR-{base_no}" if len(vendor_trips) == 1 else f"CR-{vendor.pk}-{base_no}"

                creditor_bill = Bill.objects.filter(customer_bill=bill, party=vendor).first()
                if not creditor_bill:
                    creditor_bill = Bill(
                        customer_bill=bill,
                        party=vendor,
                        bill_type=Bill.TYPE_TRIP,
                        category=lorry_hire_cat,
                        date=bill.creditor_invoice_date or bill.date,
                        issuer=bill.issuer,
                        creditor_invoice_number=bill.creditor_invoice_number,
                        creditor_invoice_date=bill.creditor_invoice_date or bill.date,
                        bill_number=creditor_bill_no,
                        use_roundoff=bill.use_roundoff,
                        gst_rate=bill.gst_rate,
                        gst_type=bill.gst_type,
                    )
                    creditor_bill.save()
                else:
                    if bill.creditor_invoice_number:
                        creditor_bill.creditor_invoice_number = bill.creditor_invoice_number
                    if bill.creditor_invoice_date:
                        creditor_bill.creditor_invoice_date = bill.creditor_invoice_date
                        creditor_bill.date = bill.creditor_invoice_date
                    elif not creditor_bill.creditor_invoice_date:
                        creditor_bill.date = bill.date
                        creditor_bill.creditor_invoice_date = bill.date
                    creditor_bill.issuer = bill.issuer
                    creditor_bill.use_roundoff = bill.use_roundoff
                    creditor_bill.gst_rate = bill.gst_rate
                    creditor_bill.gst_type = bill.gst_type
                    if not creditor_bill.bill_number or creditor_bill.bill_number.startswith("CR-DRAFT-"):
                        creditor_bill.bill_number = creditor_bill_no
                    creditor_bill.save()

                    if len(vendor_trips) == 1 and creditor_bill.creditor_invoice_number and not bill.creditor_invoice_number:
                        bill.creditor_invoice_number = creditor_bill.creditor_invoice_number
                        bill.creditor_invoice_date = creditor_bill.creditor_invoice_date
                        Bill.objects.filter(pk=bill.pk).update(
                            creditor_invoice_number=creditor_bill.creditor_invoice_number,
                            creditor_invoice_date=creditor_bill.creditor_invoice_date
                        )

                # Sync BillTrip relationships for creditor bill
                creditor_bill._suppress_billtrip_sync = True
                try:
                    creditor_bill.bill_trips.exclude(trip__in=trips_list).delete()
                    for t in trips_list:
                        cust_bt = bill.bill_trips.filter(trip=t).first()
                        lr_no = cust_bt.lr_no if cust_bt else t.lr_no
                        BillTrip.objects.update_or_create(
                            bill=creditor_bill,
                            trip=t,
                            defaults={
                                'lr_no': lr_no,
                                'discount': Decimal('0'),
                            }
                        )
                finally:
                    del creditor_bill._suppress_billtrip_sync

                # Recalculate creditor bill caches
                BillingService.update_bill_financial_caches(creditor_bill)

                # Create or update consolidated financial record for vendor
                inv_disp = creditor_bill.creditor_invoice_number or creditor_bill.bill_number or 'Pending'
                cust_ref = bill.bill_number or f"Draft-{bill.pk}"
                FinancialRecord.objects.update_or_create(
                    associated_bill=creditor_bill,
                    record_type=FinancialRecord.RECORD_TYPE_INVOICE,
                    party=vendor,
                    defaults={
                        'date': creditor_bill.date,
                        'account': bill.issuer,
                        'category': lorry_hire_cat,
                        'amount': creditor_bill.rounded_total,
                        'description': f"Lorry Hire Inv #{inv_disp} (Against Bill {cust_ref})",
                    }
                )

                # Mirror any customer credit notes against this bill as debit notes on creditor_bill
                for cust_cn in bill.adjustment_bills.filter(category__name='Credit Note'):
                    cust_cn.sync_to_ledger()

    @staticmethod
    def update_bill_financial_caches(bill):
        """
        Recalculate and update all cached financial fields for the bill.
        """
        bill._bypass_cache = True
        try:
            bill.subtotal_cached = bill.subtotal
            bill.gst_amount_cached = bill.gst_amount
            bill.total_amount_cached = bill.rounded_total
            
            if bill.is_adjustment:
                bill.amount_received_cached = Decimal('0.00')
                bill.outstanding_balance_cached = Decimal('0.00')
                bill.payment_status_cached = 'Applied'
            else:
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
        Helper to calculate amount received/paid against a bill.
        For debtor bills: income, deductions, TDS, shortage, credit note/debit note.
        For creditor bills: payment out, deductions, TDS, shortage, debit note.
        """
        from ledger.models import FinancialRecord, TransactionCategory, TripAllocation, Party
        
        if not bill.pk:
            return Decimal('0')

        is_creditor = bool(
            bill.customer_bill_id or 
            (bill.category and bill.category.name in ['Lorry Hire', 'Expense', 'Payment Out', 'Tyre Purchase']) or 
            (bill.party and bill.party.party_type == Party.TYPE_CREDITOR)
        )

        if is_creditor:
            # Direct links for creditor bill
            direct = bill.financial_records.exclude(
                record_type=FinancialRecord.RECORD_TYPE_INVOICE
            ).exclude(
                bill_allocations__bill=bill
            ).filter(
                Q(category__name='Payment Out') |
                Q(category__type=TransactionCategory.TYPE_INCOME) |
                Q(category__name__in=["Deductions", "TDS", "Shortage", "Debit Note"])
            ).aggregate(total=Sum('amount'))['total'] or 0

            bill_allocations = bill.payment_allocations.aggregate(total=Sum('amount'))['total'] or 0

            adjustments = 0
            for adj in bill.adjustment_bills.select_related('category').all():
                if adj.category:
                    if adj.category.name == 'Debit Note':
                        adjustments += adj.total_amount_cached
                    elif adj.category.name == 'Credit Note':
                        adjustments -= adj.total_amount_cached

            return Decimal(str(direct)) + Decimal(str(bill_allocations)) + Decimal(str(adjustments))

        # Direct links for debtor bill
        direct = bill.financial_records.exclude(
            record_type=FinancialRecord.RECORD_TYPE_INVOICE
        ).exclude(
            bill_allocations__bill=bill
        ).filter(
            Q(category__type=TransactionCategory.TYPE_INCOME) | 
            Q(category__name__in=["Deductions", "TDS", "Shortage", "Credit Note", "Debit Note"])
        ).aggregate(total=Sum('amount'))['total'] or 0

        bill_allocations = bill.payment_allocations.aggregate(total=Sum('amount'))['total'] or 0

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

        adjustments = 0
        for adj in bill.adjustment_bills.select_related('category').all():
            if adj.category:
                if adj.category.name == 'Credit Note':
                    adjustments += adj.total_amount_cached
                elif adj.category.name == 'Debit Note':
                    adjustments -= adj.total_amount_cached

        return Decimal(str(direct)) + Decimal(str(bill_allocations)) + Decimal(str(trip_payments)) + Decimal(str(adjustments))

