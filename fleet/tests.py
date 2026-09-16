from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User, Permission
from django.utils import timezone
from .models import Vehicle
from .forms import VehicleForm


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
