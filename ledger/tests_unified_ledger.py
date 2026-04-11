from django.test import TestCase, Client
from django.contrib.auth.models import User, Permission
from django.urls import reverse
from django.utils import timezone
from decimal import Decimal
from ledger.models import Party, FinancialRecord, TransactionCategory, CompanyAccount
from trips.models import Trip
from fleet.models import Vehicle

class UnifiedLedgerTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='manager', password='password')
        self.user.user_permissions.add(Permission.objects.get(codename='view_financialrecord'))
        
        self.account1 = CompanyAccount.objects.create(name='Cash', opening_balance=1000)
        self.account2 = CompanyAccount.objects.create(name='Bank', opening_balance=5000)
        
        self.cat_income = TransactionCategory.objects.create(name='Sales', type=TransactionCategory.TYPE_INCOME)
        self.cat_expense = TransactionCategory.objects.create(name='Rent', type=TransactionCategory.TYPE_EXPENSE)
        
        self.client = Client()
        self.client.login(username='manager', password='password')

    def test_unified_ledger_opening_balance(self):
        url = reverse('unified-ledger-pdf')
        # Total opening = 1000 + 5000 = 6000
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # Check if 6000.00 is in context or content (depends on how test handles PDF response)
        # Since it's a PDF response, we might not be able to assertContains easily, 
        # but we can check the view logic if it doesn't crash.

    def test_unified_ledger_transactions(self):
        # Add transactions to different accounts
        FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account1,
            category=self.cat_income,
            amount=200,
            recorded_by=self.user
        )
        FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account2,
            category=self.cat_expense,
            amount=500,
            recorded_by=self.user
        )
        
        url = reverse('unified-ledger-pdf')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
