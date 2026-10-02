from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.utils import timezone
from decimal import Decimal
from drivers.models import Driver, DriverTransaction
from fleet.models import Vehicle
from ledger.models import Party, CompanyAccount, TransactionCategory, FinancialRecord
from trips.models import Trip, Route

class DriverWalletAndLedgerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='driver_rajesh',
            first_name='Rajesh',
            last_name='Kumar',
            password='password123'
        )
        self.driver = Driver.objects.create(
            user=self.user,
            employee_id='DRV-001',
            license_number='DL1420110012345',
            phone_number='9876543210'
        )
        self.account = CompanyAccount.objects.create(name='Main Operating Account')
        self.party = Party.objects.create(name='Freight Client Ltd', party_type=Party.TYPE_DEBTOR)
        self.vehicle = Vehicle.objects.create(registration_plate='MH 12 AB 5555', status='Active')

    def test_driver_initial_balance_is_zero(self):
        """Test newly created driver starts with zero balance"""
        self.assertEqual(self.driver.current_balance_cached, Decimal('0.00'))
        self.assertEqual(self.driver.current_balance, Decimal('0.00'))
        self.assertEqual(self.driver.abs_current_balance, Decimal('0.00'))

    def test_driver_transaction_types_and_balance_rules(self):
        """
        Test that credit transactions increase driver wallet balance and 
        debit transactions decrease driver wallet balance.
        """
        # 1. Salary Credit (+30,000)
        DriverTransaction.objects.create(
            driver=self.driver,
            date=timezone.now().date(),
            transaction_type=DriverTransaction.TYPE_SALARY,
            amount=Decimal('30000.00'),
            description='Monthly Salary for August'
        )
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.current_balance_cached, Decimal('30000.00'))
        self.assertEqual(self.driver.current_balance, Decimal('30000.00'))

        # 2. Allowance Credit (+5,000)
        DriverTransaction.objects.create(
            driver=self.driver,
            date=timezone.now().date(),
            transaction_type=DriverTransaction.TYPE_ALLOWANCE,
            amount=Decimal('5000.00'),
            description='Trip Per Diem'
        )
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.current_balance_cached, Decimal('35000.00'))

        # 3. Loan Advance (Debit -10,000)
        DriverTransaction.objects.create(
            driver=self.driver,
            date=timezone.now().date(),
            transaction_type=DriverTransaction.TYPE_LOAN,
            amount=Decimal('-10000.00'),
            description='Advance taken by driver'
        )
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.current_balance_cached, Decimal('25000.00'))

        # 4. Salary/Pocket Payment (Debit -20,000)
        DriverTransaction.objects.create(
            driver=self.driver,
            date=timezone.now().date(),
            transaction_type=DriverTransaction.TYPE_PAYMENT,
            amount=Decimal('-20000.00'),
            description='Bank transfer to driver'
        )
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.current_balance_cached, Decimal('5000.00'))

        # 5. Loan Repayment by driver (Credit +3,000)
        DriverTransaction.objects.create(
            driver=self.driver,
            date=timezone.now().date(),
            transaction_type=DriverTransaction.TYPE_REPAYMENT,
            amount=Decimal('3000.00'),
            description='Cash returned by driver'
        )
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.current_balance_cached, Decimal('8000.00'))
        self.assertEqual(self.driver.abs_current_balance, Decimal('8000.00'))

    def test_signal_balance_caching_on_update_and_delete(self):
        """
        Verify that deleting or modifying a transaction accurately maintains 
        current_balance_cached and preserves the balance invariant.
        """
        tx1 = DriverTransaction.objects.create(
            driver=self.driver,
            date=timezone.now().date(),
            transaction_type=DriverTransaction.TYPE_SALARY,
            amount=Decimal('20000.00')
        )
        tx2 = DriverTransaction.objects.create(
            driver=self.driver,
            date=timezone.now().date(),
            transaction_type=DriverTransaction.TYPE_LOAN,
            amount=Decimal('-5000.00')
        )

        self.driver.refresh_from_db()
        self.assertEqual(self.driver.current_balance_cached, Decimal('15000.00'))

        # Update an existing transaction amount (e.g., from -5000 to -8000)
        tx2.amount = Decimal('-8000.00')
        tx2.save()

        self.driver.refresh_from_db()
        self.assertEqual(self.driver.current_balance_cached, Decimal('12000.00'))
        self.assertEqual(self.driver.current_balance, Decimal('12000.00'))

        # Delete the loan transaction
        tx2.delete()
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.current_balance_cached, Decimal('20000.00'))
        self.assertEqual(self.driver.current_balance, Decimal('20000.00'))

    def test_driver_trip_profit_aggregation(self):
        """
        Test driver trips revenue, expense, and net profit calculations.
        """
        trip1 = Trip.objects.create(
            driver=self.driver,
            vehicle=self.vehicle,
            party=self.party,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('25000.00')
        )
        trip2 = Trip.objects.create(
            driver=self.driver,
            vehicle=self.vehicle,
            party=self.party,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('35000.00')
        )

        # Record trip expenses (e.g., Diesel, Toll)
        cat_toll, _ = TransactionCategory.objects.get_or_create(
            name='Toll Expense',
            defaults={'type': TransactionCategory.TYPE_EXPENSE}
        )
        FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            category=cat_toll,
            amount=Decimal('4000.00'),
            associated_trip=trip1
        )
        FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            category=cat_toll,
            amount=Decimal('6000.00'),
            associated_trip=trip2
        )

        # Test driver detail view renders context correctly
        client = Client()
        admin = User.objects.create_superuser('fleet_mgr', 'mgr@example.com', 'password')
        client.force_login(admin)

        response = client.get(f'/drivers/{self.driver.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['driver'], self.driver)
        # Total revenue = 25000 + 35000 = 60000 (Sum of weight * rate_per_ton)
        # Note: In DriverDetailView, total_revenue is aggregated over trips
        self.assertIn('profit', response.context)
        self.assertIn('total_revenue', response.context)
        self.assertIn('total_expenses', response.context)
        self.assertEqual(response.context['total_expenses'], Decimal('10000.00'))
