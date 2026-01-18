from django.test import TestCase, Client
from django.contrib.auth.models import User, Permission, Group
from django.urls import reverse
from django.utils import timezone
from .models import Driver
from ledger.models import FinancialRecord, Party

class DriverLedgerTest(TestCase):
    def setUp(self):
        # Create user and driver
        self.user = User.objects.create_user(username='driver_user', password='password')
        group, _ = Group.objects.get_or_create(name='driver')
        self.user.groups.add(group)
        
        self.driver_profile = Driver.objects.create(
            user=self.user,
            employee_id='D001',
            license_number='LIC123',
            phone_number='1234567890'
        )
        
        # Create manager user
        self.manager = User.objects.create_user(username='manager', password='password')
        # Add permissions
        perm = Permission.objects.get(codename='can_manage_driver_finance')
        self.manager.user_permissions.add(perm)
        
        self.client = Client()
        self.client.login(username='manager', password='password')
        
    def test_driver_ledger_view(self):
        url = reverse('driver-ledger', args=[self.driver_profile.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ledger:')
        
    def test_financial_record_appears_in_ledger(self):
        # Create a financial record for the driver
        FinancialRecord.objects.create(
            date=timezone.now().date(),
            category=FinancialRecord.CATEGORY_DRIVER_PAYMENT,
            amount=500,
            description='Test Payment',
            driver=self.user, # Link to driver user
            recorded_by=self.manager
        )
        
        url = reverse('driver-ledger', args=[self.driver_profile.pk])
        response = self.client.get(url)
        self.assertContains(response, '500')
        self.assertContains(response, 'Test Payment')
