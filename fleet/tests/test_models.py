import os
import shutil
import tempfile
from decimal import Decimal
from datetime import timedelta
from django.test import TestCase, override_settings
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from fleet.models import Vehicle, MaintenanceRecord, TyreBrand, Tyre, TyreLog
from ledger.models import Party, FinancialRecord, TransactionCategory


class VehicleModelTests(TestCase):
    def setUp(self):
        self.vendor = Party.objects.create(name='Global Fleet Solutions', party_type=Party.TYPE_CREDITOR)
        self.owned_veh = Vehicle.objects.create(
            registration_plate='DL 04 OW 1111',
            make_model='Tata Prima 4928',
            ownership=Vehicle.OWNERSHIP_OWNED,
            status=Vehicle.STATUS_ACTIVE,
            current_odometer=10000
        )
        self.attached_veh = Vehicle.objects.create(
            registration_plate='DL 04 AT 2222',
            make_model='Ashok Leyland 4020',
            ownership=Vehicle.OWNERSHIP_ATTACHED,
            status=Vehicle.STATUS_MAINTENANCE,
            vendor=self.vendor
        )

    def test_vehicle_str_representation(self):
        """Verify __str__ returns '{registration_plate} - {make_model}'."""
        self.assertEqual(str(self.owned_veh), 'DL 04 OW 1111 - Tata Prima 4928')

    def test_vehicle_properties(self):
        """Verify is_available and is_attached properties."""
        self.assertTrue(self.owned_veh.is_available)
        self.assertFalse(self.owned_veh.is_attached)

        self.assertFalse(self.attached_veh.is_available) # Status is Maintenance
        self.assertTrue(self.attached_veh.is_attached)

    def test_vehicle_chassis_number_alias(self):
        """Verify chassie_number getter and setter aliases chassis_number."""
        self.owned_veh.chassie_number = 'CHAS-XYZ-999'
        self.assertEqual(self.owned_veh.chassis_number, 'CHAS-XYZ-999')
        self.assertEqual(self.owned_veh.chassie_number, 'CHAS-XYZ-999')

    def test_vehicle_vendor_relationship(self):
        """Verify attached vehicle vendor assignment and SET_NULL on vendor delete."""
        self.assertEqual(self.attached_veh.vendor, self.vendor)
        self.vendor.delete()
        self.attached_veh.refresh_from_db()
        self.assertIsNone(self.attached_veh.vendor)

    def test_vehicle_delete_safeguard_flag(self):
        """Verify Vehicle.delete sets _is_being_deleted flag."""
        veh = self.owned_veh
        veh.delete()
        self.assertTrue(veh._is_being_deleted)


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


class TyreModelAndLifecycleTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.user = User.objects.create_user(username='tyre_tech', password='password123')
        self.vendor = Party.objects.create(name='Apollo Tyre Hub', party_type=Party.TYPE_CREDITOR)
        self.brand = TyreBrand.objects.create(
            name='Apollo EnduRace',
            suggestive_price=Decimal('21000.00'),
            vendor=self.vendor
        )
        self.vehicle1 = Vehicle.objects.create(registration_plate='HR 55 TR 1001')
        self.vehicle2 = Vehicle.objects.create(registration_plate='HR 55 TR 2002')

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_tyre_brand_str(self):
        """Verify TyreBrand __str__ returns brand name."""
        self.assertEqual(str(self.brand), 'Apollo EnduRace')

    def test_tyre_str_representation(self):
        """Verify Tyre __str__ returns '{brand} {size} ({serial_number})'."""
        tyre = Tyre.objects.create(
            serial_number='SN-AP-9901',
            brand='Apollo EnduRace',
            size='295/80R22.5'
        )
        self.assertEqual(str(tyre), 'Apollo EnduRace 295/80R22.5 (SN-AP-9901)')

    def test_tyre_initial_stock_creation_logging(self):
        """Verify creating a tyre without vehicle automatically logs action='Stock' and status='In Stock'."""
        tyre = Tyre.objects.create(
            serial_number='SN-STOCK-01',
            brand='Apollo EnduRace',
            purchase_cost=Decimal('20000.00'),
            vendor=self.vendor
        )
        self.assertEqual(tyre.status, Tyre.STATUS_IN_STOCK)
        log = tyre.logs.first()
        self.assertIsNotNone(log)
        self.assertEqual(log.action, TyreLog.ACTION_STOCK)

    def test_tyre_initial_mount_creation_logging(self):
        """Verify creating a tyre with vehicle automatically logs action='Mount' and status='Mounted'."""
        tyre = Tyre.objects.create(
            serial_number='SN-MOUNT-01',
            brand='Apollo EnduRace',
            current_vehicle=self.vehicle1,
            current_position='Front Left'
        )
        self.assertEqual(tyre.status, Tyre.STATUS_MOUNTED)
        log = tyre.logs.first()
        self.assertIsNotNone(log)
        self.assertEqual(log.action, TyreLog.ACTION_MOUNT)
        self.assertEqual(log.vehicle, self.vehicle1)
        self.assertEqual(log.position, 'Front Left')

    def test_tyre_rotation_logging(self):
        """Verify changing position on same vehicle logs action='Rotation'."""
        tyre = Tyre.objects.create(
            serial_number='SN-ROT-01',
            brand='Apollo EnduRace',
            current_vehicle=self.vehicle1,
            current_position='Front Left'
        )
        tyre.current_position = 'Front Right'
        tyre.save()

        rot_log = tyre.logs.filter(action=TyreLog.ACTION_ROTATION).first()
        self.assertIsNotNone(rot_log)
        self.assertEqual(rot_log.vehicle, self.vehicle1)
        self.assertEqual(rot_log.position, 'Front Right')

    def test_tyre_vehicle_swap_logging(self):
        """Verify moving tyre to a new vehicle logs Dismount on old vehicle and Mount on new."""
        tyre = Tyre.objects.create(
            serial_number='SN-SWAP-01',
            brand='Apollo EnduRace',
            current_vehicle=self.vehicle1,
            current_position='Rear Left 1'
        )
        # Move to vehicle2
        tyre.current_vehicle = self.vehicle2
        tyre.current_position = 'Rear Right 1'
        tyre.save()

        dismount = tyre.logs.filter(action=TyreLog.ACTION_DISMOUNT, vehicle=self.vehicle1).first()
        mount = tyre.logs.filter(action=TyreLog.ACTION_MOUNT, vehicle=self.vehicle2).first()
        self.assertIsNotNone(dismount)
        self.assertIsNotNone(mount)

    def test_tyre_status_transitions_to_repair_scrap_and_back_to_stock(self):
        """Verify status transitions log Repair, Scrap, and back to Stock."""
        tyre = Tyre.objects.create(serial_number='SN-STATUS-01', brand='Apollo EnduRace')
        
        # Change to Repair
        tyre.status = Tyre.STATUS_REPAIR
        tyre.save()
        self.assertTrue(tyre.logs.filter(action=TyreLog.ACTION_REPAIR).exists())

        # Change back to Stock
        tyre.status = Tyre.STATUS_IN_STOCK
        tyre.save()
        self.assertTrue(tyre.logs.filter(action=TyreLog.ACTION_STOCK).exists())

        # Change to Scrap
        tyre.status = Tyre.STATUS_SCRAP
        tyre.save()
        self.assertTrue(tyre.logs.filter(action=TyreLog.ACTION_SCRAP).exists())

    def test_tyre_skip_auto_log_flag(self):
        """Verify setting _skip_auto_log=True prevents automatic TyreLog creation."""
        tyre = Tyre(serial_number='SN-SKIP-01', brand='Apollo EnduRace')
        tyre._skip_auto_log = True
        tyre.save()
        self.assertEqual(tyre.logs.count(), 0)

    def test_tyre_photo_cleanup_signals(self):
        """Verify photo cleanup signals on update and deletion."""
        with override_settings(MEDIA_ROOT=self.temp_dir):
            upload1 = SimpleUploadedFile('tyre1.jpg', b'image-bytes-1')
            tyre = Tyre.objects.create(
                serial_number='SN-IMG-01',
                brand='Apollo EnduRace',
                photo=upload1
            )
            initial_path = tyre.photo.path
            self.assertTrue(os.path.exists(initial_path))

            # Update photo
            upload2 = SimpleUploadedFile('tyre2.jpg', b'image-bytes-2')
            tyre.photo = upload2
            tyre.save()
            self.assertFalse(os.path.exists(initial_path))
            self.assertTrue(os.path.exists(tyre.photo.path))

            # Delete tyre
            final_path = tyre.photo.path
            tyre.delete()
            self.assertFalse(os.path.exists(final_path))


class TyreFinancialSyncTests(TestCase):
    def setUp(self):
        self.vendor = Party.objects.create(name='JK Tyre Works', party_type=Party.TYPE_CREDITOR)
        self.brand = TyreBrand.objects.create(
            name='JK Tyre Jetway',
            suggestive_price=Decimal('18500.00'),
            vendor=self.vendor
        )

    def test_tyre_sync_creates_ledger_invoice(self):
        """Verify creating a tyre with purchase_cost and vendor generates a FinancialRecord invoice."""
        tyre = Tyre.objects.create(
            serial_number='SN-FIN-01',
            brand='JK Tyre Jetway',
            vendor=self.vendor,
            purchase_cost=Decimal('18500.00'),
            purchase_date=timezone.now().date()
        )
        record = FinancialRecord.objects.filter(associated_tyre=tyre).first()
        self.assertIsNotNone(record)
        self.assertEqual(record.amount, Decimal('18500.00'))
        self.assertEqual(record.party, self.vendor)
        self.assertEqual(record.category.name, 'Tyre Purchase')
        self.assertEqual(record.record_type, FinancialRecord.RECORD_TYPE_INVOICE)

    def test_tyre_sync_vendor_fallback_from_brand(self):
        """Verify tyre with blank vendor falls back to TyreBrand vendor for ledger sync."""
        tyre = Tyre.objects.create(
            serial_number='SN-FIN-02',
            brand='JK Tyre Jetway',
            vendor=None,
            purchase_cost=Decimal('19000.00'),
            purchase_date=timezone.now().date()
        )
        record = FinancialRecord.objects.filter(associated_tyre=tyre).first()
        self.assertIsNotNone(record)
        self.assertEqual(record.party, self.vendor)
        self.assertEqual(record.amount, Decimal('19000.00'))

    def test_tyre_sync_updates_and_deletes_record(self):
        """Verify modifying purchase_cost updates record, and clearing cost deletes it."""
        tyre = Tyre.objects.create(
            serial_number='SN-FIN-03',
            brand='JK Tyre Jetway',
            vendor=self.vendor,
            purchase_cost=Decimal('15000.00'),
            purchase_date=timezone.now().date()
        )
        record = FinancialRecord.objects.filter(associated_tyre=tyre).first()
        self.assertIsNotNone(record)

        # Update cost
        tyre.purchase_cost = Decimal('17500.00')
        tyre.save()
        record.refresh_from_db()
        self.assertEqual(record.amount, Decimal('17500.00'))

        # Clear cost to 0 -> should delete record
        tyre.purchase_cost = Decimal('0.00')
        tyre.save()
        self.assertFalse(FinancialRecord.objects.filter(associated_tyre=tyre).exists())


class TyreLogModelTests(TestCase):
    def setUp(self):
        self.tyre = Tyre.objects.create(serial_number='SN-LOG-01', brand='Bridgestone')
        self.vehicle = Vehicle.objects.create(registration_plate='MH 12 TL 8888')

    def test_tyre_log_str(self):
        """Verify TyreLog __str__ returns '{tyre} - {action} on {date}'."""
        today = timezone.now().date()
        log = TyreLog.objects.create(
            tyre=self.tyre,
            date=today,
            action=TyreLog.ACTION_MOUNT,
            vehicle=self.vehicle,
            position='Front Left'
        )
        self.assertEqual(str(log), f"{self.tyre} - Mount on {today}")

    def test_tyre_log_cascade_on_tyre_delete(self):
        """Verify deleting tyre cascades and deletes all associated logs."""
        log = TyreLog.objects.create(
            tyre=self.tyre,
            action=TyreLog.ACTION_STOCK
        )
        self.tyre.delete()
        self.assertFalse(TyreLog.objects.filter(pk=log.pk).exists())

    def test_tyre_log_vehicle_set_null(self):
        """Verify deleting vehicle sets TyreLog.vehicle to NULL."""
        log = TyreLog.objects.create(
            tyre=self.tyre,
            action=TyreLog.ACTION_MOUNT,
            vehicle=self.vehicle
        )
        self.vehicle.delete()
        log.refresh_from_db()
        self.assertIsNone(log.vehicle)
