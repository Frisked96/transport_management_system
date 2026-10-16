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

    def test_vehicle_company_account_assignment_and_mutual_exclusion(self):
        """Verify company_account assignment and mutual exclusion with vendor."""
        from ledger.models import CompanyAccount
        account = CompanyAccount.objects.create(name='M/S Apex Transport')
        
        # Owned vehicle can have company_account
        self.owned_veh.company_account = account
        self.owned_veh.save()
        self.owned_veh.refresh_from_db()
        self.assertEqual(self.owned_veh.company_account, account)
        self.assertIsNone(self.owned_veh.vendor)

        # Switching to attached clears company_account
        self.owned_veh.ownership = Vehicle.OWNERSHIP_ATTACHED
        self.owned_veh.vendor = self.vendor
        self.owned_veh.save()
        self.owned_veh.refresh_from_db()
        self.assertIsNone(self.owned_veh.company_account)
        self.assertEqual(self.owned_veh.vendor, self.vendor)

        # Switching back to owned clears vendor
        self.owned_veh.ownership = Vehicle.OWNERSHIP_OWNED
        self.owned_veh.company_account = account
        self.owned_veh.save()
        self.owned_veh.refresh_from_db()
        self.assertEqual(self.owned_veh.company_account, account)
        self.assertIsNone(self.owned_veh.vendor)

    def test_vehicle_delete_safeguard_flag(self):
        """Verify Vehicle.delete sets _is_being_deleted flag."""
        veh = self.owned_veh
        veh.delete()
        self.assertTrue(veh._is_being_deleted)

    def test_vehicle_created_by_relationship(self):
        """Verify created_by relationship, timestamps, and SET_NULL on user deletion."""
        user = User.objects.create_user(username='creator_test', password='password')
        vehicle = Vehicle.objects.create(
            registration_plate='DL 04 CB 3333',
            make_model='Tata Signa',
            created_by=user
        )
        self.assertEqual(vehicle.created_by, user)
        self.assertIsNotNone(vehicle.created_at)
        self.assertIsNotNone(vehicle.updated_at)
        
        user.delete()
        vehicle.refresh_from_db()
        self.assertIsNone(vehicle.created_by)


class VehicleListViewOwnershipFilterTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='fleet_manager', password='password123')
        view_perm = Permission.objects.get(codename='view_vehicle', content_type__app_label='fleet')
        self.user.user_permissions.add(view_perm)
        self.client = Client()
        self.client.login(username='fleet_manager', password='password123')

        self.owned_veh = Vehicle.objects.create(
            registration_plate='DL 01 AA 1111',
            make_model='Tata Prima',
            ownership=Vehicle.OWNERSHIP_OWNED,
            status=Vehicle.STATUS_ACTIVE
        )
        self.attached_veh = Vehicle.objects.create(
            registration_plate='DL 02 BB 2222',
            make_model='Ashok Leyland',
            ownership=Vehicle.OWNERSHIP_ATTACHED,
            status=Vehicle.STATUS_ACTIVE
        )

    def test_vehicle_list_defaults_to_company_owned(self):
        url = reverse('vehicle-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        vehicles = list(response.context['vehicles'])
        self.assertIn(self.owned_veh, vehicles)
        self.assertNotIn(self.attached_veh, vehicles)
        self.assertEqual(response.context['current_ownership'], 'owned')
        self.assertEqual(response.context['owned_count'], 1)
        self.assertEqual(response.context['attached_count'], 1)
        self.assertEqual(response.context['total_count'], 2)

    def test_vehicle_list_filter_attached(self):
        url = reverse('vehicle-list')
        response = self.client.get(url, {'ownership': 'attached'})
        self.assertEqual(response.status_code, 200)
        vehicles = list(response.context['vehicles'])
        self.assertIn(self.attached_veh, vehicles)
        self.assertNotIn(self.owned_veh, vehicles)
        self.assertEqual(response.context['current_ownership'], 'attached')

    def test_vehicle_list_filter_all(self):
        url = reverse('vehicle-list')
        response = self.client.get(url, {'ownership': 'all'})
        self.assertEqual(response.status_code, 200)
        vehicles = list(response.context['vehicles'])
        self.assertIn(self.owned_veh, vehicles)
        self.assertIn(self.attached_veh, vehicles)
        self.assertEqual(response.context['current_ownership'], 'all')



