from django.test import TestCase
from django.contrib.auth.models import User
from .models import Driver, DriverTransaction
from decimal import Decimal

class DriverModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testdriver', password='password')
        self.driver = Driver.objects.create(
            user=self.user,
            employee_id='EMP001',
            license_number='LIC123',
            phone_number='1234567890'
        )

    def test_balance_calculation(self):
        # Initial balance should be 0
        self.assertEqual(self.driver.current_balance, 0)

        # Add Salary (+1000)
        DriverTransaction.objects.create(
            driver=self.driver,
            transaction_type=DriverTransaction.TYPE_SALARY,
            amount=1000
        )
        self.assertEqual(self.driver.current_balance, 1000)

        # Add Loan (-500)
        DriverTransaction.objects.create(
            driver=self.driver,
            transaction_type=DriverTransaction.TYPE_LOAN,
            amount=-500
        )
        self.assertEqual(self.driver.current_balance, 500)

        # Add Repayment (+200)
        DriverTransaction.objects.create(
            driver=self.driver,
            transaction_type=DriverTransaction.TYPE_REPAYMENT,
            amount=200
        )
        self.assertEqual(self.driver.current_balance, 700)
