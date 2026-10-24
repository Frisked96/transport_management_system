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

class TyreLifecycleAndLedgerTests(TestCase):
    def setUp(self):
        from ledger.models import Party
        self.vendor = Party.objects.create(name='MRF Direct Vendor', party_type=Party.TYPE_CREDITOR)
        self.vehicle1 = Vehicle.objects.create(registration_plate='MH 12 TY 1001')
        self.vehicle2 = Vehicle.objects.create(registration_plate='MH 12 TY 2002')

    def test_tyre_initial_mount_and_rotation_and_dismount(self):
        """
        Test tyre lifecycle tracking: Initial mount -> Rotation on same vehicle -> Move to another vehicle
        """
        from fleet.models import Tyre, TyreLog
        
        # 1. Mount on creation
        tyre = Tyre.objects.create(
            serial_number='TYRE-SN-001',
            brand='MRF',
            size='295/80R22.5',
            current_vehicle=self.vehicle1,
            current_position='Front Left'
        )
        self.assertEqual(tyre.status, Tyre.STATUS_MOUNTED)

        initial_mount_log = TyreLog.objects.filter(
            tyre=tyre,
            action=TyreLog.ACTION_MOUNT,
            vehicle=self.vehicle1,
            position='Front Left'
        ).first()
        self.assertIsNotNone(initial_mount_log)

        # 2. Rotation on same vehicle
        tyre.current_position = 'Front Right'
        tyre.save()

        rotation_log = TyreLog.objects.filter(
            tyre=tyre,
            action=TyreLog.ACTION_ROTATION,
            vehicle=self.vehicle1,
            position='Front Right'
        ).first()
        self.assertIsNotNone(rotation_log)

        # 3. Move to different vehicle (vehicle1 -> vehicle2)
        tyre.current_vehicle = self.vehicle2
        tyre.current_position = 'Rear Left'
        tyre.save()

        dismount_log = TyreLog.objects.filter(
            tyre=tyre,
            action=TyreLog.ACTION_DISMOUNT,
            vehicle=self.vehicle1
        ).first()
        self.assertIsNotNone(dismount_log)

        mount2_log = TyreLog.objects.filter(
            tyre=tyre,
            action=TyreLog.ACTION_MOUNT,
            vehicle=self.vehicle2,
            position='Rear Left'
        ).first()
        self.assertIsNotNone(mount2_log)

    def test_tyre_status_transitions_to_repair_and_scrap(self):
        """Test tyre status changes generate appropriate audit logs"""
        from fleet.models import Tyre, TyreLog
        
        tyre = Tyre.objects.create(
            serial_number='TYRE-SN-002',
            brand='Apollo',
            size='295/80R22.5'
        )
        self.assertEqual(tyre.status, Tyre.STATUS_IN_STOCK)

        # Change to Under Repair
        tyre.status = Tyre.STATUS_REPAIR
        tyre.save()
        self.assertTrue(TyreLog.objects.filter(tyre=tyre, action=TyreLog.ACTION_REPAIR).exists())

        # Change to Scrap
        tyre.status = Tyre.STATUS_SCRAP
        tyre.save()
        self.assertTrue(TyreLog.objects.filter(tyre=tyre, action=TyreLog.ACTION_SCRAP).exists())

    def test_tyre_purchase_syncs_to_ledger_expense(self):
        """Test tyre purchase with vendor and cost automatically creates/updates ledger entry"""
        from fleet.models import Tyre
        from ledger.models import FinancialRecord
        from decimal import Decimal

        tyre = Tyre.objects.create(
            serial_number='TYRE-SN-003',
            brand='Bridgestone',
            vendor=self.vendor,
            purchase_cost=Decimal('22000.00'),
            purchase_date=timezone.now().date()
        )

        rec = FinancialRecord.objects.filter(associated_tyre=tyre).first()
        self.assertIsNotNone(rec)
        self.assertEqual(rec.amount, Decimal('22000.00'))
        self.assertEqual(rec.party, self.vendor)
        self.assertEqual(rec.category.name, 'Tyre Purchase')
        self.assertEqual(rec.record_type, FinancialRecord.RECORD_TYPE_INVOICE)

        # Update purchase cost
        tyre.purchase_cost = Decimal('24000.00')
        tyre.save()

        rec.refresh_from_db()
        self.assertEqual(rec.amount, Decimal('24000.00'))

    def test_tyre_delete_keep_ledger_record(self):
        """Test Point G: Deleting tyre with ledger_action='keep' unlinks record but preserves ledger invoice"""
        from fleet.models import Tyre
        from ledger.models import FinancialRecord
        from decimal import Decimal

        admin_user = User.objects.create_superuser(username='tyre_admin', password='password123')
        self.client.login(username='tyre_admin', password='password123')

        tyre = Tyre.objects.create(
            serial_number='TYRE-DEL-KEEP',
            brand='MRF',
            vendor=self.vendor,
            purchase_cost=Decimal('15000.00'),
            purchase_date=timezone.now().date()
        )
        rec = FinancialRecord.objects.filter(associated_tyre=tyre).first()
        self.assertIsNotNone(rec)
        rec_id = rec.id

        # Delete with ledger_action='keep'
        resp = self.client.post(reverse('tyre-delete', kwargs={'pk': tyre.pk}), {'ledger_action': 'keep'})
        self.assertEqual(resp.status_code, 302)

        # Tyre is deleted
        self.assertFalse(Tyre.objects.filter(pk=tyre.pk).exists())

        # Financial record is retained and unlinked
        preserved_rec = FinancialRecord.objects.filter(pk=rec_id).first()
        self.assertIsNotNone(preserved_rec)
        self.assertIsNone(preserved_rec.associated_tyre)
        self.assertEqual(preserved_rec.amount, Decimal('15000.00'))
        self.assertEqual(preserved_rec.party, self.vendor)

    def test_tyre_delete_cascade_ledger_record(self):
        """Test Point G: Deleting tyre with ledger_action='delete' removes associated ledger record"""
        from fleet.models import Tyre
        from ledger.models import FinancialRecord
        from decimal import Decimal

        admin_user = User.objects.create_superuser(username='tyre_admin2', password='password123')
        self.client.login(username='tyre_admin2', password='password123')

        tyre = Tyre.objects.create(
            serial_number='TYRE-DEL-PURGE',
            brand='MRF',
            vendor=self.vendor,
            purchase_cost=Decimal('18000.00'),
            purchase_date=timezone.now().date()
        )
        rec = FinancialRecord.objects.filter(associated_tyre=tyre).first()
        self.assertIsNotNone(rec)
        rec_id = rec.id

        # Delete with ledger_action='delete'
        resp = self.client.post(reverse('tyre-delete', kwargs={'pk': tyre.pk}), {'ledger_action': 'delete'})
        self.assertEqual(resp.status_code, 302)

        # Both tyre and record are deleted
        self.assertFalse(Tyre.objects.filter(pk=tyre.pk).exists())
        self.assertFalse(FinancialRecord.objects.filter(pk=rec_id).exists())

    def test_tyre_position_collision_validation(self):
        """Test position collision prevention in TyreForm and TyreLogForm"""
        from fleet.models import Tyre, TyreBrand
        from fleet.forms import TyreForm, TyreLogForm
        from decimal import Decimal

        TyreBrand.objects.get_or_create(name='MRF', defaults={'suggestive_price': Decimal('10000.00')})

        # Tyre 1 mounted at Front Left
        Tyre.objects.create(
            serial_number='TYRE-POS-1',
            brand='MRF',
            current_vehicle=self.vehicle1,
            current_position='Front Left'
        )

        # Form attempting to mount Tyre 2 at Front Left
        form_data = {
            'serial_number': 'TYRE-POS-2',
            'brand': 'MRF',
            'current_vehicle': self.vehicle1.id,
            'current_position': 'Front Left',
            'purchase_cost': '0'
        }
        form = TyreForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn('current_position', form.errors)
        self.assertIn('already occupied', form.errors['current_position'][0])

        # TyreLogForm collision test
        tyre2 = Tyre.objects.create(serial_number='TYRE-POS-2B', brand='MRF')
        log_form_data = {
            'tyre': tyre2.id,
            'date': timezone.now().date(),
            'action': 'Mount',
            'vehicle': self.vehicle1.id,
            'position': 'Front Left'
        }
        log_form = TyreLogForm(data=log_form_data)
        self.assertFalse(log_form.is_valid())
        self.assertIn('position', log_form.errors)
        self.assertIn('already occupied', log_form.errors['position'][0])

    def test_tyre_cannot_mount_scrapped_tyre(self):
        """Test that TyreLogForm rejects mounting a scrapped tyre"""
        from fleet.models import Tyre
        from fleet.forms import TyreLogForm

        scrapped_tyre = Tyre.objects.create(serial_number='TYRE-SCRAP-01', brand='MRF', status=Tyre.STATUS_SCRAP)
        log_form = TyreLogForm(data={
            'tyre': scrapped_tyre.id,
            'date': timezone.now().date(),
            'action': 'Mount',
            'vehicle': self.vehicle1.id,
            'position': 'Rear Right'
        })
        self.assertFalse(log_form.is_valid())
        self.assertIn('Cannot mount a scrapped tyre', str(log_form.errors))

    def test_tyre_quick_action_requires_post_and_logs_user(self):
        """Test tyre_quick_action requires POST, enforces permissions, and sets user on logs"""
        from fleet.models import Tyre, TyreLog

        user = User.objects.create_superuser(username='qa_user', password='password123')
        self.client.login(username='qa_user', password='password123')

        tyre = Tyre.objects.create(
            serial_number='TYRE-QA-01',
            brand='Apollo',
            current_vehicle=self.vehicle1,
            current_position='Front Left'
        )

        url = reverse('tyre-action', kwargs={'pk': tyre.pk, 'action': 'Scrap'})

        # GET request should be rejected (405 Method Not Allowed)
        get_resp = self.client.get(url)
        self.assertEqual(get_resp.status_code, 405)

        # POST request should succeed
        post_resp = self.client.post(url)
        self.assertEqual(post_resp.status_code, 302)

        tyre.refresh_from_db()
        self.assertEqual(tyre.status, Tyre.STATUS_SCRAP)
        self.assertIsNone(tyre.current_vehicle)
        self.assertEqual(tyre.current_position, '')

        # Check logged_by on the generated TyreLog
        last_log = tyre.logs.filter(action=TyreLog.ACTION_SCRAP).first()
        self.assertIsNotNone(last_log)
        self.assertEqual(last_log.logged_by, user)

    def test_tyre_remount_from_different_vehicle_logs_dismount(self):
        """Test TyreLogCreateView logs auto-dismount when moving from vehicle1 to vehicle2"""
        from fleet.models import Tyre, TyreLog

        user = User.objects.create_superuser(username='remount_user', password='password123')
        self.client.login(username='remount_user', password='password123')

        tyre = Tyre.objects.create(
            serial_number='TYRE-REMOUNT-01',
            brand='MRF',
            current_vehicle=self.vehicle1,
            current_position='Front Left'
        )

        url = reverse('tyre-log-create')
        post_data = {
            'tyre': tyre.pk,
            'date': timezone.now().date(),
            'action': 'Mount',
            'vehicle': self.vehicle2.pk,
            'position': 'Rear Left 1',
            'notes': 'Moving to vehicle 2'
        }
        resp = self.client.post(url, post_data)
        self.assertEqual(resp.status_code, 302)

        tyre.refresh_from_db()
        self.assertEqual(tyre.current_vehicle, self.vehicle2)
        self.assertEqual(tyre.current_position, 'Rear Left 1')

        # Check that a dismount log was created for vehicle1
        dismount_log = TyreLog.objects.filter(
            tyre=tyre,
            action=TyreLog.ACTION_DISMOUNT,
            vehicle=self.vehicle1
        ).first()
        self.assertIsNotNone(dismount_log)
        self.assertEqual(dismount_log.logged_by, user)




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
