from django.test import TestCase, Client
from django.utils import timezone
from django.urls import reverse
import datetime
import json
from decimal import Decimal
from django.contrib.auth.models import User

from ledger.models import (
    Bill, Party, CompanyAccount, TransactionCategory, FinancialRecord,
    BillTrip
)
from trips.models import Trip, Route
from fleet.models import Vehicle
from ledger.utils import parse_numeric_lr, sort_trips_for_bill_generation, sort_bill_trips
from ledger.forms import BillForm


class BillLRSequencingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username='admin', email='admin@test.com', password='password')
        self.client = Client()
        self.client.force_login(self.user)

        self.party = Party.objects.create(name="ABC Logistics Corp", party_type=Party.TYPE_DEBTOR)
        self.vehicle = Vehicle.objects.create(registration_plate="MH12AB1234", purchase_date=timezone.now().date())
        self.route = Route.objects.create(pickup_location="Mumbai", delivery_location="Delhi", default_rate=Decimal('1000.00'))
        self.issuer = CompanyAccount.objects.create(
            name="My Fleet Co",
            invoice_prefix="INV/{YYYY}/"
        )
        self.category_invoice = TransactionCategory.objects.create(name="Freight Income", type=TransactionCategory.TYPE_INCOME)

    def test_parse_numeric_lr(self):
        """Verify parse_numeric_lr handles ints, floats, non-numeric strings, and None."""
        self.assertEqual(parse_numeric_lr("101"), 101)
        self.assertEqual(parse_numeric_lr("  102  "), 102)
        self.assertEqual(parse_numeric_lr("005"), 5)
        self.assertEqual(parse_numeric_lr(55), 55)
        self.assertEqual(parse_numeric_lr("10.5"), 10.5)
        self.assertIsNone(parse_numeric_lr(None))
        self.assertIsNone(parse_numeric_lr(""))
        self.assertIsNone(parse_numeric_lr("   "))
        self.assertIsNone(parse_numeric_lr("LR-101"))
        self.assertIsNone(parse_numeric_lr("ABC"))
        self.assertIsNone(parse_numeric_lr("102-A"))

    def test_unbilled_trips_ajax_sequencing_numeric_lr(self):
        """
        When generating a bill via AJAX endpoint (get_party_unbilled_trips),
        trips with numeric LR should be ordered with older LR on top and newer at bottom (ascending).
        """
        # Create trips with out-of-order LRs and dates
        t1 = Trip.objects.create(
            date=datetime.date(2026, 9, 10),
            party=self.party,
            vehicle=self.vehicle,
            route=self.route,
            weight=Decimal('10.00'),
            rate_per_ton=Decimal('1000.00'),
            lr_no="105"
        )
        t2 = Trip.objects.create(
            date=datetime.date(2026, 9, 1),
            party=self.party,
            vehicle=self.vehicle,
            route=self.route,
            weight=Decimal('10.00'),
            rate_per_ton=Decimal('1000.00'),
            lr_no="98"
        )
        t3 = Trip.objects.create(
            date=datetime.date(2026, 9, 5),
            party=self.party,
            vehicle=self.vehicle,
            route=self.route,
            weight=Decimal('10.00'),
            rate_per_ton=Decimal('1000.00'),
            lr_no="101"
        )

        response = self.client.get(f'/ledger/ajax/get-party-unbilled-trips/?party_id={self.party.id}')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        trips = data.get('trips', [])
        self.assertEqual(len(trips), 3)

        # Expected order: LR 98 (older on top), LR 101, LR 105 (newer at bottom)
        self.assertEqual([t['id'] for t in trips], [t2.id, t3.id, t1.id])
        self.assertEqual([t['lr_no'] for t in trips], ["98", "101", "105"])

    def test_unbilled_trips_ajax_sequencing_non_numeric_fallback(self):
        """
        When LR numbers are not numeric, trips fall back to current behavior: -date, -created_at.
        """
        t1 = Trip.objects.create(
            date=datetime.date(2026, 9, 1),
            party=self.party,
            vehicle=self.vehicle,
            route=self.route,
            weight=Decimal('10.00'),
            rate_per_ton=Decimal('1000.00'),
            lr_no="LR-A"
        )
        t2 = Trip.objects.create(
            date=datetime.date(2026, 9, 15),
            party=self.party,
            vehicle=self.vehicle,
            route=self.route,
            weight=Decimal('10.00'),
            rate_per_ton=Decimal('1000.00'),
            lr_no="LR-B"
        )

        response = self.client.get(f'/ledger/ajax/get-party-unbilled-trips/?party_id={self.party.id}')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        trips = data.get('trips', [])

        # Fallback order: newest date first (Sep 15 before Sep 1)
        self.assertEqual([t['id'] for t in trips], [t2.id, t1.id])

    def test_bill_form_queryset_sequencing(self):
        """Verify BillForm initializes trips queryset ordered by numeric LR ascending."""
        t1 = Trip.objects.create(
            date=datetime.date(2026, 9, 10),
            party=self.party,
            vehicle=self.vehicle,
            route=self.route,
            weight=Decimal('10.00'),
            rate_per_ton=Decimal('1000.00'),
            lr_no="20"
        )
        t2 = Trip.objects.create(
            date=datetime.date(2026, 9, 1),
            party=self.party,
            vehicle=self.vehicle,
            route=self.route,
            weight=Decimal('10.00'),
            rate_per_ton=Decimal('1000.00'),
            lr_no="5"
        )
        t3 = Trip.objects.create(
            date=datetime.date(2026, 9, 5),
            party=self.party,
            vehicle=self.vehicle,
            route=self.route,
            weight=Decimal('10.00'),
            rate_per_ton=Decimal('1000.00'),
            lr_no="12"
        )

        form = BillForm(initial={'party': self.party.id})
        trips_qs = list(form.fields['trips'].queryset)
        self.assertEqual([t.id for t in trips_qs], [t2.id, t3.id, t1.id])
        self.assertEqual([t.lr_no for t in trips_qs], ["5", "12", "20"])

    def test_printed_bill_has_older_lr_on_top_sequence(self):
        """
        Test that when generating a bill with trips and viewing / printing it,
        the trips are sequenced with older LR on top and newer at bottom.
        """
        t1 = Trip.objects.create(
            date=datetime.date(2026, 9, 10),
            party=self.party,
            vehicle=self.vehicle,
            route=self.route,
            weight=Decimal('10.00'),
            rate_per_ton=Decimal('1000.00'),
            lr_no="500"
        )
        t2 = Trip.objects.create(
            date=datetime.date(2026, 9, 1),
            party=self.party,
            vehicle=self.vehicle,
            route=self.route,
            weight=Decimal('10.00'),
            rate_per_ton=Decimal('1000.00'),
            lr_no="100"
        )
        t3 = Trip.objects.create(
            date=datetime.date(2026, 9, 5),
            party=self.party,
            vehicle=self.vehicle,
            route=self.route,
            weight=Decimal('10.00'),
            rate_per_ton=Decimal('1000.00'),
            lr_no="250"
        )

        # Generate bill via BillForm
        form_data = {
            'bill_no': 1,
            'bill_type': 'Trip',
            'issuer': self.issuer.id,
            'party': self.party.id,
            'date': '2026-09-15',
            'gst_rate': 0,
            'gst_type': 'GST',
            'discount': '0.00',
            'trips': [t1.id, t2.id, t3.id],
            'trips_data': json.dumps({
                str(t1.id): {'lr_no': '500', 'discount': '0.00'},
                str(t2.id): {'lr_no': '100', 'discount': '0.00'},
                str(t3.id): {'lr_no': '250', 'discount': '0.00'},
            })
        }
        form = BillForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        bill = form.save()

        # Check print view
        print_url = reverse('print-combined-bill', kwargs={'pk': bill.pk})
        response = self.client.get(print_url)
        self.assertEqual(response.status_code, 200)

        # Check context bill_trips order
        context_bts = response.context['bill_trips']
        self.assertEqual(len(context_bts), 3)
        self.assertEqual([bt.lr_no for bt in context_bts], ["100", "250", "500"])
        self.assertEqual([bt.trip_id for bt in context_bts], [t2.id, t3.id, t1.id])

        # Verify rendered HTML sequence
        content = response.content.decode('utf-8')
        pos_100 = content.find("100")
        pos_250 = content.find("250")
        pos_500 = content.find("500")
        self.assertTrue(pos_100 < pos_250 < pos_500, "Trips in printed bill must appear in LR order: 100, 250, 500")

    def test_printed_bill_non_numeric_fallback(self):
        """
        When LR is not numeric, printed bill falls back to trip date ascending.
        """
        t1 = Trip.objects.create(
            date=datetime.date(2026, 9, 20),
            party=self.party,
            vehicle=self.vehicle,
            route=self.route,
            weight=Decimal('10.00'),
            rate_per_ton=Decimal('1000.00'),
            lr_no="LR-Z"
        )
        t2 = Trip.objects.create(
            date=datetime.date(2026, 9, 5),
            party=self.party,
            vehicle=self.vehicle,
            route=self.route,
            weight=Decimal('10.00'),
            rate_per_ton=Decimal('1000.00'),
            lr_no="LR-A"
        )

        bill = Bill.objects.create(
            bill_no=2,
            bill_type='Trip',
            issuer=self.issuer,
            party=self.party,
            date=datetime.date(2026, 9, 25),
            gst_rate=0,
            gst_type='GST'
        )
        BillTrip.objects.create(bill=bill, trip=t1, lr_no="LR-Z")
        BillTrip.objects.create(bill=bill, trip=t2, lr_no="LR-A")

        print_url = reverse('print-combined-bill', kwargs={'pk': bill.pk})
        response = self.client.get(print_url)
        self.assertEqual(response.status_code, 200)

        context_bts = response.context['bill_trips']
        # Fallback to trip date ascending: Sep 5 before Sep 20
        self.assertEqual([bt.trip_id for bt in context_bts], [t2.id, t1.id])
        self.assertEqual([bt.lr_no for bt in context_bts], ["LR-A", "LR-Z"])

    def test_annexure_sequencing_with_more_than_three_trips(self):
        """
        When bill has > 3 trips, annexure is used and must also be sequenced by numeric LR ascending.
        """
        trips = []
        lrs = ["40", "10", "30", "20"]
        for idx, lr in enumerate(lrs):
            t = Trip.objects.create(
                date=datetime.date(2026, 9, idx + 1),
                party=self.party,
                vehicle=self.vehicle,
                route=self.route,
                weight=Decimal('5.00'),
                rate_per_ton=Decimal('1000.00'),
                lr_no=lr
            )
            trips.append(t)

        bill = Bill.objects.create(
            bill_no=3,
            bill_type='Trip',
            issuer=self.issuer,
            party=self.party,
            date=datetime.date(2026, 9, 25),
            gst_rate=0,
            gst_type='GST'
        )
        for t in trips:
            BillTrip.objects.create(bill=bill, trip=t, lr_no=t.lr_no)

        print_url = reverse('print-combined-bill', kwargs={'pk': bill.pk})
        response = self.client.get(print_url)
        self.assertEqual(response.status_code, 200)

        context_bts = response.context['bill_trips']
        self.assertEqual([bt.lr_no for bt in context_bts], ["10", "20", "30", "40"])

    def test_bill_detail_view_sequencing(self):
        """Verify BillDetailView context['bill_trips'] is sorted by older LR on top."""
        t1 = Trip.objects.create(
            date=datetime.date(2026, 9, 1),
            party=self.party,
            vehicle=self.vehicle,
            route=self.route,
            weight=Decimal('10.00'),
            rate_per_ton=Decimal('1000.00'),
            lr_no="777"
        )
        t2 = Trip.objects.create(
            date=datetime.date(2026, 9, 2),
            party=self.party,
            vehicle=self.vehicle,
            route=self.route,
            weight=Decimal('10.00'),
            rate_per_ton=Decimal('1000.00'),
            lr_no="222"
        )
        bill = Bill.objects.create(
            bill_no=4,
            bill_type='Trip',
            issuer=self.issuer,
            party=self.party,
            date=datetime.date(2026, 9, 25),
            gst_rate=0,
            gst_type='GST'
        )
        BillTrip.objects.create(bill=bill, trip=t1, lr_no="777")
        BillTrip.objects.create(bill=bill, trip=t2, lr_no="222")

        response = self.client.get(reverse('bill-detail', kwargs={'pk': bill.pk}))
        self.assertEqual(response.status_code, 200)
        context_bts = response.context['bill_trips']
        self.assertEqual([bt.lr_no for bt in context_bts], ["222", "777"])

    def test_bulk_print_invoices_sequencing(self):
        """Verify bulk_print_invoices has older LR on top in context."""
        t1 = Trip.objects.create(
            date=datetime.date(2026, 9, 1),
            party=self.party,
            vehicle=self.vehicle,
            route=self.route,
            weight=Decimal('10.00'),
            rate_per_ton=Decimal('1000.00'),
            lr_no="900"
        )
        t2 = Trip.objects.create(
            date=datetime.date(2026, 9, 2),
            party=self.party,
            vehicle=self.vehicle,
            route=self.route,
            weight=Decimal('10.00'),
            rate_per_ton=Decimal('1000.00'),
            lr_no="300"
        )
        bill = Bill.objects.create(
            bill_no=5,
            bill_type='Trip',
            issuer=self.issuer,
            party=self.party,
            date=datetime.date(2026, 9, 25),
            gst_rate=0,
            gst_type='GST'
        )
        BillTrip.objects.create(bill=bill, trip=t1, lr_no="900")
        BillTrip.objects.create(bill=bill, trip=t2, lr_no="300")

        response = self.client.get(reverse('bulk-print-invoices') + f'?issuer={self.issuer.pk}&party={self.party.pk}')
        self.assertEqual(response.status_code, 200)
        bill_contexts = response.context['bill_contexts']
        self.assertEqual(len(bill_contexts), 1)
        bts = bill_contexts[0]['bill_trips']
        self.assertEqual([bt.lr_no for bt in bts], ["300", "900"])

