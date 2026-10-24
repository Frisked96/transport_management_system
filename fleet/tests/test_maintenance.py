import os
import shutil
import tempfile
from decimal import Decimal
from datetime import timedelta
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.contrib.auth.models import User, Permission
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from fleet.models import Vehicle, MaintenanceRecord, TyreBrand, Tyre, TyreLog
from fleet.forms import VehicleForm, MaintenanceRecordForm, TyreForm
from ledger.models import Party, FinancialRecord, TransactionCategory
from fleet.services import (
    get_vehicle_tyre_layout,
    normalize_tyre_position,
    TYRE_POSITION_MAP,
)

class MaintenanceRecordLifecycleTests(TestCase):
    def setUp(self):
        from fleet.models import MaintenanceRecord
        self.user = User.objects.create_superuser('fleet_admin', 'fleet@example.com', 'password')
        self.vehicle = Vehicle.objects.create(
            registration_plate='MH 12 MN 7777',
            make_model='Tata Prima 4928',
            current_odometer=50000,
            status=Vehicle.STATUS_ACTIVE
        )

    def test_maintenance_overdue_by_date_and_odometer(self):
        """Test overdue checks by calendar date and odometer threshold"""
        from fleet.models import MaintenanceRecord
        from datetime import timedelta
        today = timezone.now().date()

        # 1. Overdue by date
        rec_date_overdue = MaintenanceRecord.objects.create(
            vehicle=self.vehicle,
            name='Engine Oil Change',
            expiry_date=today - timedelta(days=5),
            is_completed=False
        )
        self.assertTrue(rec_date_overdue.is_overdue)

        # 2. Not overdue by date (future)
        rec_future = MaintenanceRecord.objects.create(
            vehicle=self.vehicle,
            name='Gearbox Service',
            expiry_date=today + timedelta(days=15),
            is_completed=False
        )
        self.assertFalse(rec_future.is_overdue)

        # 3. Overdue by odometer (current_odometer 50000 >= expiry_km 48000)
        rec_km_overdue = MaintenanceRecord.objects.create(
            vehicle=self.vehicle,
            name='Differential Oil',
            expiry_km=48000,
            is_completed=False
        )
        self.assertTrue(rec_km_overdue.is_overdue)

        # 4. Completed records are never overdue
        rec_date_overdue.is_completed = True
        rec_date_overdue.save()
        self.assertFalse(rec_date_overdue.is_overdue)

    def test_maintenance_mark_as_completed_and_auto_recurrence(self):
        """
        Test marking maintenance completed saves details and automatically 
        spawns the next recurring pending record based on intervals.
        """
        from fleet.models import MaintenanceRecord
        from datetime import timedelta
        from decimal import Decimal
        today = timezone.now().date()

        rec = MaintenanceRecord.objects.create(
            vehicle=self.vehicle,
            name='Major Scheduled Service',
            expiry_date=today,
            expiry_km=50000,
            interval_days=90,
            interval_km=10000,
            is_completed=False
        )

        # Complete service at 52,000 km
        rec.mark_as_completed(
            date=today,
            km=52000,
            cost=Decimal('12500.00'),
            provider='Authorized Tata Workshop',
            notes='All filters replaced',
            user=self.user
        )

        rec.refresh_from_db()
        self.assertTrue(rec.is_completed)
        self.assertEqual(rec.completion_km, 52000)
        self.assertEqual(rec.cost, Decimal('12500.00'))

        # Verify vehicle total maintenance cost reflects completed service
        self.assertEqual(self.vehicle.total_maintenance_cost, Decimal('12500.00'))

        # Verify automatic creation of next pending record
        next_rec = MaintenanceRecord.objects.filter(
            vehicle=self.vehicle,
            name='Major Scheduled Service',
            is_completed=False
        ).first()

        self.assertIsNotNone(next_rec)
        self.assertEqual(next_rec.expiry_date, today + timedelta(days=90))
        self.assertEqual(next_rec.expiry_km, 62000) # 52000 + 10000




class MaintenanceRecordModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='mechanic_john', password='password123')
        self.vehicle = Vehicle.objects.create(
            registration_plate='MH 14 MN 3333',
            make_model='BharatBenz 3528',
            current_odometer=60000,
            status=Vehicle.STATUS_ACTIVE
        )

    def test_maintenance_record_str(self):
        """Verify MaintenanceRecord __str__ returns '{plate} - {name} ({status})'."""
        rec = MaintenanceRecord.objects.create(
            vehicle=self.vehicle,
            name='Engine Oil Flush',
            is_completed=False
        )
        self.assertEqual(str(rec), 'MH 14 MN 3333 - Engine Oil Flush (Pending)')

        rec.is_completed = True
        rec.save()
        self.assertEqual(str(rec), 'MH 14 MN 3333 - Engine Oil Flush (Completed)')

    def test_maintenance_record_is_overdue_conditions(self):
        """Verify is_overdue returns True for overdue by date or odometer, False if completed."""
        today = timezone.now().date()

        # 1. Overdue by date
        rec_date = MaintenanceRecord.objects.create(
            vehicle=self.vehicle,
            name='Air Filter Replacement',
            expiry_date=today - timedelta(days=2),
            is_completed=False
        )
        self.assertTrue(rec_date.is_overdue)

        # 2. Overdue by km (current_odometer 60000 >= expiry_km 55000)
        rec_km = MaintenanceRecord.objects.create(
            vehicle=self.vehicle,
            name='Coolant Replacement',
            expiry_km=55000,
            is_completed=False
        )
        self.assertTrue(rec_km.is_overdue)

        # 3. Not overdue (future date & higher km)
        rec_future = MaintenanceRecord.objects.create(
            vehicle=self.vehicle,
            name='Tyre Rotation Service',
            expiry_date=today + timedelta(days=20),
            expiry_km=70000,
            is_completed=False
        )
        self.assertFalse(rec_future.is_overdue)

        # 4. Completed records are never overdue
        rec_date.is_completed = True
        rec_date.save()
        self.assertFalse(rec_date.is_overdue)

    def test_mark_as_completed_non_recurring(self):
        """Verify mark_as_completed updates fields without creating a recurring record if no intervals."""
        today = timezone.now().date()
        rec = MaintenanceRecord.objects.create(
            vehicle=self.vehicle,
            name='One-time Dent Repair',
            is_completed=False
        )
        rec.mark_as_completed(
            date=today,
            km=60500,
            cost=Decimal('5000.00'),
            provider='City Body Shop',
            notes='Front bumper touched up',
            user=self.user
        )

        rec.refresh_from_db()
        self.assertTrue(rec.is_completed)
        self.assertEqual(rec.completion_date, today)
        self.assertEqual(rec.completion_km, 60500)
        self.assertEqual(rec.cost, Decimal('5000.00'))
        self.assertEqual(rec.service_provider, 'City Body Shop')
        self.assertEqual(rec.logged_by, self.user)
        self.assertIn('Front bumper touched up', rec.notes)

        # No recurring records spawned
        self.assertEqual(
            MaintenanceRecord.objects.filter(vehicle=self.vehicle, name='One-time Dent Repair').count(),
            1
        )

    def test_mark_as_completed_with_interval_recurrence(self):
        """Verify mark_as_completed automatically creates next pending record with updated intervals."""
        today = timezone.now().date()
        rec = MaintenanceRecord.objects.create(
            vehicle=self.vehicle,
            name='Periodic Engine Service',
            interval_days=60,
            interval_km=8000,
            is_completed=False
        )
        rec.mark_as_completed(
            date=today,
            km=61000,
            cost=Decimal('8500.00'),
            provider='Tata Authorized Service',
            user=self.user
        )

        # Next recurring record should be created
        next_rec = MaintenanceRecord.objects.filter(
            vehicle=self.vehicle,
            name='Periodic Engine Service',
            is_completed=False
        ).first()

        self.assertIsNotNone(next_rec)
        self.assertEqual(next_rec.expiry_date, today + timedelta(days=60))
        self.assertEqual(next_rec.expiry_km, 69000) # 61000 + 8000
        self.assertEqual(next_rec.interval_days, 60)
        self.assertEqual(next_rec.interval_km, 8000)
        self.assertEqual(self.vehicle.total_maintenance_cost, Decimal('8500.00'))


