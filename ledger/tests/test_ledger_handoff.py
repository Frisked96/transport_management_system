from django.test import TestCase, Client
from django.utils import timezone
from django.db import models
from django.urls import reverse
import datetime
from decimal import Decimal
from ledger.models import (
    Bill, Party, CompanyAccount, TransactionCategory, FinancialRecord,
    TripAllocation, BillAllocation
)
from ledger.services import BalanceService, BillingService, TripFinancialService
from django.contrib.auth.models import User, Permission
from trips.models import Trip, Route
from fleet.models import Vehicle

class LedgerHandoffAndAccrualTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('acc_admin', 'acc@test.com', 'password')
        self.account = CompanyAccount.objects.create(name='Primary Corp Account', invoice_prefix='INV-{YYYY}/')
        self.party = Party.objects.create(name='Zenith Logistics', party_type=Party.TYPE_DEBTOR)
        self.vehicle = Vehicle.objects.create(registration_plate='MH 14 ZZ 1111')
        self.route = Route.objects.create(
            pickup_location='Nashik',
            delivery_location='Surat',
            route_type=Route.ROUTE_TYPE_LOCAL,
            default_rate=Decimal('1000.00')
        )

    def test_unbilled_trip_accrual_lifecycle_and_bill_restoration(self):
        """
        Verify the complete hand-off lifecycle:
        1. Unbilled trip creates individual accrual FinancialRecord.
        2. Billing the trip deletes individual accrual and creates consolidated Bill accrual.
        3. Deleting the Bill restores the individual trip accrual.
        """
        # 1. Create Trip (20 tons * 1000 = 20,000 revenue + 18% GST = 23,600 total)
        trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.party,
            route=self.route,
            revenue_type=Trip.REVENUE_PER_TON,
            weight=Decimal('20.00'),
            rate_per_ton=Decimal('1000.00')
        )

        trip_accrual = FinancialRecord.objects.filter(
            associated_trip=trip,
            record_type=FinancialRecord.RECORD_TYPE_INVOICE,
            party=self.party
        ).first()
        self.assertIsNotNone(trip_accrual)
        self.assertEqual(trip_accrual.amount, Decimal('23600.00'))

        # 2. Add Trip to a Bill
        bill = Bill.objects.create(
            issuer=self.account,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_TRIP,
            gst_rate=Bill.GST_RATE_18
        )
        bill.trips.add(trip)
        bill.sync_to_ledger()

        # Individual trip accrual should now be DELETED
        self.assertFalse(
            FinancialRecord.objects.filter(
                associated_trip=trip,
                record_type=FinancialRecord.RECORD_TYPE_INVOICE
            ).exists()
        )

        # Consolidated Bill invoice record should now EXIST
        bill_accrual = FinancialRecord.objects.filter(
            associated_bill=bill,
            record_type=FinancialRecord.RECORD_TYPE_INVOICE,
            party=self.party
        ).first()
        self.assertIsNotNone(bill_accrual)
        self.assertEqual(bill_accrual.amount, bill.rounded_total)

        # 3. Delete the Bill -> Individual trip accrual should be RESTORED
        bill_id = bill.pk
        bill.delete()

        # Consolidated Bill record should be DELETED
        self.assertFalse(
            FinancialRecord.objects.filter(
                associated_bill_id=bill_id,
                record_type=FinancialRecord.RECORD_TYPE_INVOICE
            ).exists()
        )

        # Individual trip accrual should be RESTORED
        restored_trip_accrual = FinancialRecord.objects.filter(
            associated_trip=trip,
            record_type=FinancialRecord.RECORD_TYPE_INVOICE,
            party=self.party
        ).first()
        self.assertIsNotNone(restored_trip_accrual)
        self.assertEqual(restored_trip_accrual.amount, Decimal('23600.00'))


