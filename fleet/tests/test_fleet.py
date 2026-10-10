from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User, Permission
from django.utils import timezone
from fleet.models import Vehicle
from fleet.forms import VehicleForm


class VehicleModelAndFormTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username='adminuser',
            email='admin@example.com',
            password='password123'
        )
        self.client = Client()
        self.client.login(username='adminuser', password='password123')

    def test_vehicle_creation_without_optional_fields(self):
        """Test vehicle can be created without purchase_date, chassis_number, or engine_number"""
        vehicle = Vehicle.objects.create(
            registration_plate="RJ14-AB-1001",
            make_model="Tata 1618"
        )
        self.assertIsNone(vehicle.purchase_date)
        self.assertIsNone(vehicle.chassis_number)
        self.assertIsNone(vehicle.engine_number)
        self.assertEqual(vehicle.chassie_number, None)

    def test_vehicle_creation_with_optional_fields(self):
        """Test vehicle can be created with purchase_date, chassis_number, and engine_number"""
        today = timezone.now().date()
        vehicle = Vehicle.objects.create(
            registration_plate="RJ14-AB-1002",
            make_model="Ashok Leyland 4220",
            purchase_date=today,
            chassis_number="MB1A12345XYZ",
            engine_number="ENG987654"
        )
        self.assertEqual(vehicle.purchase_date, today)
        self.assertEqual(vehicle.chassis_number, "MB1A12345XYZ")
        self.assertEqual(vehicle.engine_number, "ENG987654")
        self.assertEqual(vehicle.chassie_number, "MB1A12345XYZ")

        # Test setter alias
        vehicle.chassie_number = "MB1A99999NEW"
        self.assertEqual(vehicle.chassis_number, "MB1A99999NEW")

    def test_vehicle_form_validation_optional_fields(self):
        """Test VehicleForm valid without purchase_date, chassis_number, or engine_number"""
        form_data = {
            'registration_plate': 'RJ14-AB-1003',
            'make_model': 'BharatBenz 2823',
            'current_odometer': 1500,
            'status': Vehicle.STATUS_ACTIVE,
            'ownership': Vehicle.OWNERSHIP_OWNED,
            'purchase_date': '',
            'chassis_number': '',
            'engine_number': '',
        }
        form = VehicleForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        vehicle = form.save()
        self.assertIsNone(vehicle.purchase_date)
        # Empty string or None for CharField
        self.assertIn(vehicle.chassis_number, ['', None])
        self.assertIn(vehicle.engine_number, ['', None])

    def test_vehicle_form_validation_with_chassis_and_engine(self):
        """Test VehicleForm valid with chassis and engine numbers"""
        form_data = {
            'registration_plate': 'RJ14-AB-1004',
            'make_model': 'Tata Signa',
            'current_odometer': 5000,
            'status': Vehicle.STATUS_ACTIVE,
            'ownership': Vehicle.OWNERSHIP_OWNED,
            'purchase_date': '2025-01-15',
            'chassis_number': 'CHAS12345678',
            'engine_number': 'ENG87654321',
        }
        form = VehicleForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        vehicle = form.save()
        self.assertEqual(str(vehicle.purchase_date), '2025-01-15')
        self.assertEqual(vehicle.chassis_number, 'CHAS12345678')
        self.assertEqual(vehicle.engine_number, 'ENG87654321')

    def test_vehicle_create_and_update_views(self):
        """Test creating and updating vehicle via HTTP views"""
        # Create
        create_url = reverse('vehicle-create')
        response = self.client.post(create_url, {
            'registration_plate': 'RJ14-VIEW-01',
            'make_model': 'Eicher Pro',
            'current_odometer': 0,
            'status': Vehicle.STATUS_ACTIVE,
            'ownership': Vehicle.OWNERSHIP_OWNED,
            'chassis_number': 'CHASSIS-VIEW-01',
            'engine_number': 'ENGINE-VIEW-01',
            'purchase_date': '',
        })
        self.assertEqual(response.status_code, 302)
        vehicle = Vehicle.objects.get(registration_plate='RJ14-VIEW-01')
        self.assertEqual(vehicle.chassis_number, 'CHASSIS-VIEW-01')
        self.assertEqual(vehicle.engine_number, 'ENGINE-VIEW-01')
        self.assertIsNone(vehicle.purchase_date)
        self.assertEqual(vehicle.created_by, self.user)

        # Update
        update_url = reverse('vehicle-update', kwargs={'pk': vehicle.pk})
        response = self.client.post(update_url, {
            'registration_plate': 'RJ14-VIEW-01',
            'make_model': 'Eicher Pro 3015',
            'current_odometer': 100,
            'status': Vehicle.STATUS_ACTIVE,
            'ownership': Vehicle.OWNERSHIP_OWNED,
            'chassis_number': 'CHASSIS-UPDATED',
            'engine_number': 'ENGINE-UPDATED',
            'purchase_date': '2024-06-10',
        })
        self.assertEqual(response.status_code, 302)
        vehicle.refresh_from_db()
        self.assertEqual(vehicle.chassis_number, 'CHASSIS-UPDATED')
        self.assertEqual(vehicle.engine_number, 'ENGINE-UPDATED')
        self.assertEqual(str(vehicle.purchase_date), '2024-06-10')
        self.assertEqual(vehicle.created_by, self.user)

    def test_vehicle_detail_view_renders_chassis_and_engine(self):
        """Test vehicle detail view displays chassis and engine numbers"""
        vehicle = Vehicle.objects.create(
            registration_plate='RJ14-DETAIL-01',
            make_model='Tata Prima',
            chassis_number='CHAS-DETAIL-999',
            engine_number='ENG-DETAIL-888'
        )
        url = reverse('vehicle-detail', kwargs={'pk': vehicle.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('CHAS-DETAIL-999', content)
        self.assertIn('ENG-DETAIL-888', content)
        self.assertIn('Chassis No.', content)
        self.assertIn('Engine No.', content)

    def test_vehicle_detail_view_renders_created_by(self):
        """Test vehicle detail view displays creator when present and handles null gracefully"""
        # Vehicle with created_by
        vehicle_with_creator = Vehicle.objects.create(
            registration_plate='RJ14-DETAIL-02',
            make_model='Tata Signa',
            created_by=self.user
        )
        url = reverse('vehicle-detail', kwargs={'pk': vehicle_with_creator.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('Created By', content)
        self.assertIn('adminuser', content)

        # Vehicle without created_by (e.g. historical data)
        vehicle_without_creator = Vehicle.objects.create(
            registration_plate='RJ14-DETAIL-03',
            make_model='BharatBenz',
            created_by=None
        )
        url_no_creator = reverse('vehicle-detail', kwargs={'pk': vehicle_without_creator.pk})
        response_no_creator = self.client.get(url_no_creator)
        self.assertEqual(response_no_creator.status_code, 200)
        content_no_creator = response_no_creator.content.decode('utf-8')
        self.assertIn('Created By', content_no_creator)

    def test_vehicle_search_by_chassis_and_engine(self):
        """Test vehicle list search finds vehicles by chassis or engine number"""
        Vehicle.objects.create(
            registration_plate='RJ14-SRCH-01',
            make_model='Mahindra Furio',
            chassis_number='UNIQUECHASSIS123',
            engine_number='ENG111'
        )
        Vehicle.objects.create(
            registration_plate='RJ14-SRCH-02',
            make_model='Mahindra Blazo',
            chassis_number='CHAS222',
            engine_number='UNIQUEENGINE999'
        )

        list_url = reverse('vehicle-list')
        
        # Search by chassis number
        response = self.client.get(list_url, {'search': 'UNIQUECHASSIS'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['vehicles']), 1)
        self.assertEqual(response.context['vehicles'][0].registration_plate, 'RJ14-SRCH-01')

        # Search by engine number
        response = self.client.get(list_url, {'search': 'UNIQUEENGINE'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['vehicles']), 1)
        self.assertEqual(response.context['vehicles'][0].registration_plate, 'RJ14-SRCH-02')


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


