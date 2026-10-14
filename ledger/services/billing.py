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
        from ledger.models import FinancialRecord, TransactionCategory, BillTrip, Bill
        
        if not bill.pk:
            return

        with transaction.atomic():
            lorry_hire_cat, _ = TransactionCategory.objects.get_or_create(
                name='Lorry Hire',
                defaults={'type': TransactionCategory.TYPE_EXPENSE}
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
                    }
                )
                if bill.original_bill:
                    BillingService.update_bill_financial_caches(bill.original_bill)
                if not bill.is_adjustment:
                    BillingService.settle_bill_with_unallocated_advances(bill)
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
                }
            )

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

            if not bill.is_adjustment:
                BillingService.settle_bill_with_unallocated_advances(bill)

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
                    creditor_bill.date = bill.creditor_invoice_date or bill.date
                    creditor_bill.creditor_invoice_date = bill.creditor_invoice_date or bill.date
                    creditor_bill.creditor_invoice_number = bill.creditor_invoice_number
                    creditor_bill.issuer = bill.issuer
                    creditor_bill.use_roundoff = bill.use_roundoff
                    creditor_bill.gst_rate = bill.gst_rate
                    creditor_bill.gst_type = bill.gst_type
                    if not creditor_bill.bill_number or creditor_bill.bill_number.startswith("CR-DRAFT-"):
                        creditor_bill.bill_number = creditor_bill_no
                    creditor_bill.save()

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
                BillingService.settle_bill_with_unallocated_advances(creditor_bill)

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

            if not bill.is_adjustment:
                BillingService.settle_bill_with_unallocated_advances(bill)

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

        is_creditor = bool(bill.customer_bill_id or (bill.party and bill.party.party_type == Party.TYPE_CREDITOR))

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

    @staticmethod
    def allocate_payment_fifo(payment_record=None, tds_record=None, deduction_record=None):
        """
        Allocates an unassigned/general payment (and optional TDS/deduction records)
        across the oldest unpaid bills for the party on a strict FIFO basis (ordered by date, id).
        Only bills with date <= payment_date are considered.
        """
        from ledger.models import Bill, BillAllocation

        ref_record = payment_record or tds_record or deduction_record
        if not ref_record or not ref_record.party:
            return []

        if any(r and (r.associated_bill_id or r.associated_trip_id) for r in [payment_record, tds_record, deduction_record]):
            return []

        if any(r and (r.bill_allocations.exists() or r.allocations.exists()) for r in [payment_record, tds_record, deduction_record]):
            return []

        if any(r and r.record_type == r.RECORD_TYPE_INVOICE for r in [payment_record, tds_record, deduction_record]):
            return []

        party = ref_record.party

        payment_amount = payment_record.amount if (payment_record and payment_record.amount) else Decimal('0.00')
        tds_amount = tds_record.amount if (tds_record and tds_record.amount) else Decimal('0.00')
        deduction_amount = deduction_record.amount if (deduction_record and deduction_record.amount) else Decimal('0.00')

        total_payment_pool = payment_amount + tds_amount + deduction_amount
        if total_payment_pool <= 0:
            return []

        eligible_bills = Bill.objects.filter(
            party=party
        ).filter(
            Q(category__isnull=True) | ~Q(category__name__in=['Credit Note', 'Debit Note'])
        ).order_by('date', 'id')

        remaining_payment = payment_amount
        remaining_tds = tds_amount
        remaining_ded = deduction_amount
        allocated_bills = []

        for bill in eligible_bills:
            if remaining_payment <= 0 and remaining_tds <= 0 and remaining_ded <= 0:
                break

            outstanding = bill.outstanding_balance
            if outstanding <= 0:
                continue

            available_total = remaining_payment + remaining_tds + remaining_ded
            settle_amount = min(outstanding, available_total)
            if settle_amount <= 0:
                continue

            if total_payment_pool > 0:
                p_alloc = Decimal('0.00')
                t_alloc = Decimal('0.00')
                d_alloc = Decimal('0.00')

                if remaining_payment > 0:
                    p_alloc = (settle_amount * (payment_amount / total_payment_pool)).quantize(Decimal('0.01'))
                    p_alloc = min(remaining_payment, min(settle_amount, p_alloc))

                rem_for_tds_ded = settle_amount - p_alloc
                if rem_for_tds_ded > 0 and remaining_tds > 0:
                    t_alloc = (settle_amount * (tds_amount / total_payment_pool)).quantize(Decimal('0.01'))
                    t_alloc = min(remaining_tds, min(rem_for_tds_ded, t_alloc))

                rem_for_ded = rem_for_tds_ded - t_alloc
                if rem_for_ded > 0 and remaining_ded > 0:
                    d_alloc = min(remaining_ded, rem_for_ded)

                alloc_sum = p_alloc + t_alloc + d_alloc
                if alloc_sum < settle_amount:
                    diff = settle_amount - alloc_sum
                    if remaining_payment - p_alloc >= diff:
                        p_alloc += diff
                    elif remaining_tds - t_alloc >= diff:
                        t_alloc += diff
                    elif remaining_ded - d_alloc >= diff:
                        d_alloc += diff
            else:
                p_alloc = min(remaining_payment, settle_amount)
                t_alloc = Decimal('0.00')
                d_alloc = Decimal('0.00')

            if p_alloc > 0 and payment_record:
                BillAllocation.objects.create(
                    financial_record=payment_record,
                    bill=bill,
                    amount=p_alloc
                )
                remaining_payment -= p_alloc

            if t_alloc > 0 and tds_record:
                BillAllocation.objects.create(
                    financial_record=tds_record,
                    bill=bill,
                    amount=t_alloc
                )
                remaining_tds -= t_alloc

            if d_alloc > 0 and deduction_record:
                BillAllocation.objects.create(
                    financial_record=deduction_record,
                    bill=bill,
                    amount=d_alloc
                )
                remaining_ded -= d_alloc

            BillingService.update_bill_financial_caches(bill)
            allocated_bills.append(bill)

        if allocated_bills:
            bill_numbers = [b.display_invoice_number for b in allocated_bills]
            invoices_str = ', '.join(bill_numbers)
            if payment_record and not payment_record.description:
                payment_record.description = f"Paid across invoices: {invoices_str}"
                payment_record.save(update_fields=['description'])
            if tds_record and not tds_record.description:
                tds_record.description = f"TDS across invoices: {invoices_str}"
                tds_record.save(update_fields=['description'])
            if deduction_record and not deduction_record.description:
                deduction_record.description = f"Deduction across invoices: {invoices_str}"
                deduction_record.save(update_fields=['description'])

        return allocated_bills

    @staticmethod
    def settle_bill_with_unallocated_advances(bill):
        """
        Automatically settles a bill upon creation/update against any
        existing unallocated advance payments for the party in FIFO order (oldest payment first).
        """
        from ledger.models import FinancialRecord, TransactionCategory, BillAllocation

        if not bill or not bill.pk or not bill.party:
            return []

        if bill.is_adjustment:
            return []

        if getattr(bill, '_settling_advances', False):
            return []

        outstanding = bill.outstanding_balance
        if outstanding <= 0:
            return []

        bill._settling_advances = True
        try:
            if bill.is_creditor_bill:
                candidates = FinancialRecord.objects.filter(
                    party=bill.party
                ).exclude(
                    record_type=FinancialRecord.RECORD_TYPE_INVOICE
                ).filter(
                    associated_bill__isnull=True,
                    associated_trip__isnull=True
                ).filter(
                    Q(category__name='Payment Out') |
                    Q(category__type=TransactionCategory.TYPE_EXPENSE)
                ).order_by('date', 'id')
            else:
                candidates = FinancialRecord.objects.filter(
                    party=bill.party
                ).exclude(
                    record_type=FinancialRecord.RECORD_TYPE_INVOICE
                ).filter(
                    associated_bill__isnull=True,
                    associated_trip__isnull=True
                ).filter(
                    Q(category__name__in=['Payment In', 'Invoice Payment']) |
                    Q(category__type=TransactionCategory.TYPE_INCOME)
                ).order_by('date', 'id')

            allocated_records = []
            remaining_needed = outstanding

            for rec in candidates:
                if remaining_needed <= 0:
                    break

                if rec.allocations.exists():
                    continue

                if not rec.amount or rec.amount <= 0:
                    continue

                already_alloc = rec.bill_allocations.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
                unallocated_amount = rec.amount - already_alloc

                if unallocated_amount <= 0:
                    continue

                if rec.bill_allocations.filter(bill=bill).exists():
                    continue

                alloc_to_bill = min(unallocated_amount, remaining_needed)
                if alloc_to_bill <= 0:
                    continue

                BillAllocation.objects.create(
                    financial_record=rec,
                    bill=bill,
                    amount=alloc_to_bill
                )
                remaining_needed -= alloc_to_bill
                allocated_records.append(rec)

                bill_num = bill.display_invoice_number
                if not rec.description:
                    rec.description = f"Paid across invoices: {bill_num}"
                    rec.save(update_fields=['description'])
                elif "Paid across invoices:" in rec.description and bill_num not in rec.description:
                    rec.description = f"{rec.description}, {bill_num}"
                    rec.save(update_fields=['description'])

            if allocated_records:
                BillingService.update_bill_financial_caches(bill)

            return allocated_records
        finally:
            del bill._settling_advances
