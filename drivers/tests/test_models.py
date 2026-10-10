from decimal import Decimal
from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone
from drivers.models import Driver, DriverTransaction


class DriverModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='driver_mukesh',
            first_name='Mukesh',
            last_name='Singh',
            password='password123'
        )
        self.driver = Driver.objects.create(
            user=self.user,
            employee_id='EMP-404',
            license_number='DL-MH-12-999',
            phone_number='9123456780',
            address='Pune, Maharashtra'
        )

    def test_driver_str_with_full_name_and_emp_id(self):
        """Verify __str__ returns 'First Last (EMP_ID)' when full name and employee_id exist."""
        self.assertEqual(str(self.driver), 'Mukesh Singh (EMP-404)')

    def test_driver_str_with_username_fallback(self):
        """Verify __str__ falls back to username when user has no first or last name."""
        user2 = User.objects.create_user(username='ramesh_user', password='pw')
        driver2 = Driver.objects.create(user=user2, employee_id='EMP-505')
        self.assertEqual(str(driver2), 'ramesh_user (EMP-505)')

    def test_driver_str_without_employee_id(self):
        """Verify __str__ formats correctly without parentheses when employee_id is blank."""
        user3 = User.objects.create_user(username='suresh_user', first_name='Suresh', password='pw')
        driver3 = Driver.objects.create(user=user3, employee_id='')
        self.assertEqual(str(driver3), 'Suresh')

    def test_driver_name_property(self):
        """Verify name property returns full name if present, else username."""
        self.assertEqual(self.driver.name, 'Mukesh Singh')
        self.user.first_name = ''
        self.user.last_name = ''
        self.user.save()
        self.assertEqual(self.driver.name, 'driver_mukesh')

    def test_driver_initial_and_abs_balance(self):
        """Verify initial balance is zero and abs_current_balance returns absolute value."""
        self.assertEqual(self.driver.current_balance_cached, Decimal('0.00'))
        self.assertEqual(self.driver.current_balance, Decimal('0.00'))
        self.assertEqual(self.driver.abs_current_balance, Decimal('0.00'))

        self.driver.current_balance_cached = Decimal('-1500.00')
        self.driver.save(update_fields=['current_balance_cached'])
        self.assertEqual(self.driver.current_balance, Decimal('-1500.00'))
        self.assertEqual(self.driver.abs_current_balance, Decimal('1500.00'))

    def test_driver_cascade_on_user_delete(self):
        """Verify deleting User cascades and deletes the Driver record."""
        driver_id = self.driver.id
        self.user.delete()
        self.assertFalse(Driver.objects.filter(id=driver_id).exists())

    def test_driver_delete_safeguard_flag(self):
        """Verify Driver.delete sets _is_being_deleted flag."""
        driver = self.driver
        driver.delete()
        self.assertTrue(driver._is_being_deleted)


class DriverTransactionModelTests(TestCase):
    def setUp(self):
        self.creator = User.objects.create_user(username='accountant', password='password123')
        self.driver_user = User.objects.create_user(username='driver_user', password='password123')
        self.driver = Driver.objects.create(user=self.driver_user, employee_id='EMP-701')

    def test_driver_transaction_str_formatting(self):
        """Verify transaction __str__ returns '{Driver} - {Type} - {Amount}'."""
        tx = DriverTransaction.objects.create(
            driver=self.driver,
            date=timezone.now().date(),
            transaction_type=DriverTransaction.TYPE_SALARY,
            amount=Decimal('25000.00'),
            created_by=self.creator
        )
        self.assertEqual(str(tx), f"{self.driver} - Salary - 25000.00")

    def test_driver_transaction_type_choices(self):
        """Verify all transaction types are recognized."""
        expected_types = [
            DriverTransaction.TYPE_SALARY,
            DriverTransaction.TYPE_ALLOWANCE,
            DriverTransaction.TYPE_LOAN,
            DriverTransaction.TYPE_PAYMENT,
            DriverTransaction.TYPE_REPAYMENT,
            DriverTransaction.TYPE_OTHER,
        ]
        choice_values = [c[0] for c in DriverTransaction.TYPE_CHOICES]
        for t in expected_types:
            self.assertIn(t, choice_values)

    def test_driver_transaction_ordering(self):
        """Verify transactions are ordered by -date, -created_at."""
        today = timezone.now().date()
        tx1 = DriverTransaction.objects.create(
            driver=self.driver,
            date=today - timezone.timedelta(days=2),
            transaction_type=DriverTransaction.TYPE_ALLOWANCE,
            amount=Decimal('500.00')
        )
        tx2 = DriverTransaction.objects.create(
            driver=self.driver,
            date=today,
            transaction_type=DriverTransaction.TYPE_SALARY,
            amount=Decimal('30000.00')
        )
        tx_list = list(DriverTransaction.objects.filter(driver=self.driver))
        self.assertEqual(tx_list[0], tx2)
        self.assertEqual(tx_list[1], tx1)

    def test_driver_transaction_created_by_set_null(self):
        """Verify deleting created_by user sets transaction.created_by to NULL."""
        tx = DriverTransaction.objects.create(
            driver=self.driver,
            date=timezone.now().date(),
            transaction_type=DriverTransaction.TYPE_OTHER,
            amount=Decimal('100.00'),
            created_by=self.creator
        )
        self.creator.delete()
        tx.refresh_from_db()
        self.assertIsNone(tx.created_by)


class DriverBalanceSignalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='drv_bal_test', password='password123')
        self.driver = Driver.objects.create(user=self.user, employee_id='EMP-BAL-01')

    def test_atomic_balance_increment_on_create(self):
        """Verify post_save signal atomically increments driver current_balance_cached."""
        DriverTransaction.objects.create(
            driver=self.driver,
            transaction_type=DriverTransaction.TYPE_SALARY,
            amount=Decimal('35000.00')
        )
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.current_balance_cached, Decimal('35000.00'))

        # Add allowance
        DriverTransaction.objects.create(
            driver=self.driver,
            transaction_type=DriverTransaction.TYPE_ALLOWANCE,
            amount=Decimal('4500.00')
        )
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.current_balance_cached, Decimal('39500.00'))

    def test_balance_refresh_on_transaction_update(self):
        """Verify modifying an existing transaction triggers refresh_balance and recalculates accurately."""
        tx = DriverTransaction.objects.create(
            driver=self.driver,
            transaction_type=DriverTransaction.TYPE_LOAN,
            amount=Decimal('-10000.00')
        )
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.current_balance_cached, Decimal('-10000.00'))

        # Update loan amount to -6000
        tx.amount = Decimal('-6000.00')
        tx.save()
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.current_balance_cached, Decimal('-6000.00'))

    def test_atomic_balance_decrement_on_delete(self):
        """Verify post_delete signal decrements cached balance upon transaction deletion."""
        tx1 = DriverTransaction.objects.create(
            driver=self.driver,
            transaction_type=DriverTransaction.TYPE_SALARY,
            amount=Decimal('20000.00')
        )
        tx2 = DriverTransaction.objects.create(
            driver=self.driver,
            transaction_type=DriverTransaction.TYPE_ALLOWANCE,
            amount=Decimal('3000.00')
        )
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.current_balance_cached, Decimal('23000.00'))

        tx2.delete()
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.current_balance_cached, Decimal('20000.00'))

    def test_refresh_balance_recalculation(self):
        """Verify refresh_balance() accurately sums all transactions in the database."""
        DriverTransaction.objects.create(driver=self.driver, transaction_type='Salary', amount=Decimal('50000.00'))
        DriverTransaction.objects.create(driver=self.driver, transaction_type='Loan', amount=Decimal('-15000.00'))
        DriverTransaction.objects.create(driver=self.driver, transaction_type='Repayment', amount=Decimal('5000.00'))

        # Manually tamper cached balance to test refresh
        self.driver.current_balance_cached = Decimal('0.00')
        self.driver.save(update_fields=['current_balance_cached'])

        self.driver.refresh_balance()
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.current_balance_cached, Decimal('40000.00'))

    def test_signals_skip_update_when_driver_deleting(self):
        """Verify balance signals gracefully handle transaction cleanup when driver is being deleted."""
        DriverTransaction.objects.create(
            driver=self.driver,
            transaction_type=DriverTransaction.TYPE_SALARY,
            amount=Decimal('25000.00')
        )
        driver_id = self.driver.id
        # Delete driver (which cascades to transactions) without raising exceptions
        self.driver.delete()
        self.assertFalse(Driver.objects.filter(id=driver_id).exists())
        self.assertFalse(DriverTransaction.objects.filter(driver_id=driver_id).exists())
