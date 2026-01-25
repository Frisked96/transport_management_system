from django.test import TestCase, Client
from django.contrib.auth.models import User, Permission
from django.urls import reverse
from django.utils import timezone
from .models import Account, FinancialRecord, Party, TransactionCategory

class AccountTest(TestCase):
    def setUp(self):
        # Create user
        self.user = User.objects.create_user(username='manager', password='password')
        
        # Add permission
        perm_add = Permission.objects.get(codename='add_financialrecord')
        perm_change = Permission.objects.get(codename='change_financialrecord')
        self.user.user_permissions.add(perm_add, perm_change)
        
        # Create Account
        self.account = Account.objects.create(
            name='Test Bank',
            account_number='123456',
            opening_balance=1000
        )
        
        # Create Party
        self.party = Party.objects.create(name='Test Party')
        
        # Create categories
        self.income_cat = TransactionCategory.objects.create(name='Freight Income', type=TransactionCategory.TYPE_INCOME)
        self.expense_cat = TransactionCategory.objects.create(name='Fuel Expense', type=TransactionCategory.TYPE_EXPENSE)
        
        self.client = Client()
        self.client.login(username='manager', password='password')

    def test_account_list(self):
        url = reverse('account-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Test Bank')
        self.assertContains(response, '1000.00')

    def test_account_create(self):
        url = reverse('account-create')
        data = {
            'name': 'Cash Box',
            'opening_balance': 0,
            'description': 'Petty Cash'
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Account.objects.filter(name='Cash Box').exists())

    def test_balance_update_on_income(self):
        # Add Income Record linked to Account
        FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            category=self.income_cat,
            amount=500,
            recorded_by=self.user
        )
        
        # Balance should be Opening (1000) + Income (500) = 1500
        self.assertEqual(self.account.current_balance, 1500)
        
        # Verify in Detail View
        url = reverse('account-detail', args=[self.account.pk])
        response = self.client.get(url)
        self.assertContains(response, '1500.00')

    def test_balance_update_on_expense(self):
        # Add Expense Record linked to Account
        FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            category=self.expense_cat,
            amount=200,
            recorded_by=self.user
        )
        
        # Balance should be Opening (1000) - Expense (200) = 800
        self.assertEqual(self.account.current_balance, 800)
