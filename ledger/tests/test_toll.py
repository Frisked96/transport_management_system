"""
Unit and integration tests for Toll Ledger System.
Tests initial starting balance, lumpsum recharges from Company Accounts,
vehicle toll consumption, balance integrity, and double-counting prevention.
"""
from decimal import Decimal
from django.test import TestCase, Client
from django.contrib.auth.models import User, Permission
from django.urls import reverse
from django.utils import timezone

from ledger.models import (
    TollAccount, TollRecharge, TollEntry, CompanyAccount,
    FinancialRecord, TransactionCategory
)
from fleet.models import Vehicle


class TollLedgerSystemTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username='admin_toll',
            email='admin@test.com',
            password='password123'
        )
        self.client = Client()
        self.client.force_login(self.user)

        # 1. Company Account (Bank)
        self.company_account = CompanyAccount.objects.create(
            name='Test Firm HDFC Bank',
            opening_balance=Decimal('100000.00')
        )
        self.company_account.refresh_balance()

        # 2. Vehicle
        self.vehicle_1 = Vehicle.objects.create(
            registration_plate='HR-55-1234',
            make_model='Tata Signa',
            status=Vehicle.STATUS_ACTIVE,
            company_account=self.company_account
        )
        self.vehicle_2 = Vehicle.objects.create(
            registration_plate='DL-01-9999',
            make_model='Ashok Leyland',
            status=Vehicle.STATUS_ACTIVE,
            company_account=self.company_account
        )

        # 3. Toll Account with Initial Starting Balance
        self.toll_account = TollAccount.objects.create(
            name='Primary FASTag Wallet',
            provider='ICICI Bank FASTag',
            account_number='FASTAG-1001',
            opening_balance=Decimal('15000.00'),
            opening_balance_date=timezone.now().date(),
            is_default=True
        )

    def test_toll_account_initial_starting_balance(self):
        """Verify that opening_balance establishes the starting cached balance"""
        self.assertEqual(self.toll_account.current_balance_cached, Decimal('15000.00'))
        self.assertEqual(self.toll_account.total_recharged, Decimal('0.00'))
        self.assertEqual(self.toll_account.total_spent, Decimal('0.00'))

    def test_toll_recharge_reduces_company_account_and_increases_toll_balance(self):
        """
        Verify that adding lumpsum money from a Company Account:
        1. Reduces Company Account bank balance.
        2. Creates an associated FinancialRecord with category 'Toll Recharge'.
        3. Increases Toll Account balance.
        """
        recharge = TollRecharge.objects.create(
            toll_account=self.toll_account,
            company_account=self.company_account,
            date=timezone.now().date(),
            amount=Decimal('40000.00'),
            payment_mode=TollRecharge.MODE_BANK_TRANSFER,
            reference_number='UTR987654321',
            created_by=self.user
        )

        # Refresh instances from DB
        self.toll_account.refresh_from_db()
        self.company_account.refresh_from_db()

        # 1. Toll balance: 15,000 opening + 40,000 top-up = 55,000
        self.assertEqual(self.toll_account.current_balance_cached, Decimal('55000.00'))

        # 2. Company balance: 100,000 opening - 40,000 recharge = 60,000
        self.assertEqual(self.company_account.current_balance_cached, Decimal('60000.00'))

        # 3. Check FinancialRecord creation
        fin_rec = FinancialRecord.objects.filter(associated_toll_recharge=recharge).first()
        self.assertIsNotNone(fin_rec)
        self.assertEqual(fin_rec.amount, Decimal('40000.00'))
        self.assertEqual(fin_rec.account, self.company_account)
        self.assertEqual(fin_rec.category.name, 'Toll Recharge')
        self.assertEqual(fin_rec.category.type, TransactionCategory.TYPE_EXPENSE)

    def test_toll_recharge_deletion_restores_company_balance(self):
        """Deleting a recharge must restore the Company Account and Toll balance"""
        recharge = TollRecharge.objects.create(
            toll_account=self.toll_account,
            company_account=self.company_account,
            date=timezone.now().date(),
            amount=Decimal('25000.00'),
            created_by=self.user
        )
        self.company_account.refresh_from_db()
        self.toll_account.refresh_from_db()
        self.assertEqual(self.company_account.current_balance_cached, Decimal('75000.00'))
        self.assertEqual(self.toll_account.current_balance_cached, Decimal('40000.00'))

        # Delete recharge
        recharge.delete()

        self.company_account.refresh_from_db()
        self.toll_account.refresh_from_db()
        self.assertEqual(self.company_account.current_balance_cached, Decimal('100000.00'))
        self.assertEqual(self.toll_account.current_balance_cached, Decimal('15000.00'))
        self.assertFalse(FinancialRecord.objects.filter(associated_toll_recharge_id=recharge.pk).exists())

    def test_toll_entry_deducts_strictly_from_toll_balance_not_company_account(self):
        """
        CRITICAL BUSINESS LOGIC:
        Recording a vehicle toll expense MUST deduct from Toll balance,
        and MUST NOT deduct from Company Account again!
        """
        # 1. Fund the toll account with 40,000 from company account
        TollRecharge.objects.create(
            toll_account=self.toll_account,
            company_account=self.company_account,
            date=timezone.now().date(),
            amount=Decimal('40000.00'),
            created_by=self.user
        )
        self.company_account.refresh_from_db()
        self.toll_account.refresh_from_db()
        self.assertEqual(self.toll_account.current_balance_cached, Decimal('55000.00'))
        self.assertEqual(self.company_account.current_balance_cached, Decimal('60000.00'))

        company_fin_rec_count = FinancialRecord.objects.filter(account=self.company_account).count()

        # 2. Record Toll Entry of 450 for Vehicle 1
        toll_entry = TollEntry.objects.create(
            toll_account=self.toll_account,
            vehicle=self.vehicle_1,
            date=timezone.now().date(),
            amount=Decimal('450.00'),
            toll_plaza='Kherki Daula Toll Plaza',
            transaction_reference='SLIP-001',
            created_by=self.user
        )

        self.toll_account.refresh_from_db()
        self.company_account.refresh_from_db()

        # Toll balance is reduced from 55,000 to 54,550
        self.assertEqual(self.toll_account.current_balance_cached, Decimal('54550.00'))
        self.assertEqual(self.toll_account.total_spent, Decimal('450.00'))

        # COMPANY ACCOUNT IS STRICTLY UNTOUCHED!
        self.assertEqual(self.company_account.current_balance_cached, Decimal('60000.00'))
        # No extra FinancialRecord was created
        self.assertEqual(FinancialRecord.objects.filter(account=self.company_account).count(), company_fin_rec_count)

    def test_toll_entry_deletion_restores_toll_balance(self):
        """Deleting a toll entry restores the Toll balance"""
        toll_entry = TollEntry.objects.create(
            toll_account=self.toll_account,
            vehicle=self.vehicle_1,
            date=timezone.now().date(),
            amount=Decimal('800.00'),
            created_by=self.user
        )
        self.toll_account.refresh_from_db()
        self.assertEqual(self.toll_account.current_balance_cached, Decimal('14200.00'))

        toll_entry.delete()
        self.toll_account.refresh_from_db()
        self.assertEqual(self.toll_account.current_balance_cached, Decimal('15000.00'))

    def test_toll_bulk_entry_view(self):
        """Test bulk adding multiple vehicle toll entries via the web view"""
        url = reverse('toll-entry-bulk-create')
        data = {
            'toll_account': self.toll_account.pk,
            'date[]': [timezone.now().date().isoformat(), timezone.now().date().isoformat()],
            'vehicle[]': [self.vehicle_1.pk, self.vehicle_2.pk],
            'amount[]': ['350.00', '650.00'],
            'toll_plaza[]': ['Plaza A', 'Plaza B'],
            'reference[]': ['REF1', 'REF2'],
            'description[]': ['Trip 1 toll', 'Trip 2 toll'],
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)

        self.toll_account.refresh_from_db()
        # 15,000 - 350 - 650 = 14,000
        self.assertEqual(self.toll_account.current_balance_cached, Decimal('14000.00'))
        self.assertEqual(TollEntry.objects.filter(toll_account=self.toll_account).count(), 2)

    def test_toll_ledger_view_and_filtering(self):
        """Test accessing Toll Ledger dashboard and vehicle filtering"""
        # Create a toll entry for vehicle 1
        TollEntry.objects.create(
            toll_account=self.toll_account,
            vehicle=self.vehicle_1,
            date=timezone.now().date(),
            amount=Decimal('500.00'),
            toll_plaza='Main Plaza',
            created_by=self.user
        )

        # 1. Main ledger view
        url = reverse('toll-ledger')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Primary FASTag Wallet')
        self.assertContains(response, 'HR-55-1234')
        self.assertContains(response, '500.00')

        # 2. Filter by vehicle 1
        url_filtered = f"{reverse('toll-ledger')}?vehicle={self.vehicle_1.pk}"
        resp_filtered = self.client.get(url_filtered)
        self.assertEqual(resp_filtered.status_code, 200)
        self.assertContains(resp_filtered, 'HR-55-1234')

        # 3. Print statement view
        statement_url = f"{reverse('toll-statement-print')}?account={self.toll_account.pk}"
        resp_stmt = self.client.get(statement_url)
        self.assertEqual(resp_stmt.status_code, 200)
        self.assertContains(resp_stmt, 'TOLL & FASTAG LEDGER STATEMENT')

    def test_vehicle_detail_shows_toll_expenses(self):
        """Test that vehicle detail page displays toll expenses and recent charges"""
        TollEntry.objects.create(
            toll_account=self.toll_account,
            vehicle=self.vehicle_1,
            date=timezone.now().date(),
            amount=Decimal('720.00'),
            toll_plaza='Panipat Toll',
            created_by=self.user
        )

        veh_url = reverse('vehicle-detail', kwargs={'pk': self.vehicle_1.pk})
        response = self.client.get(veh_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Toll Expenses')
        self.assertContains(response, 'Panipat Toll')
        self.assertContains(response, '720.00')

    def test_toll_ledger_combined_view_multiple_accounts(self):
        """
        Verify that toll ledger view shows a combined view of all toll accounts:
        1. Default view shows transactions and consolidated balances across all accounts.
        2. Filter by specific account shows only that account.
        3. Filter by vehicle in combined view shows entries for that vehicle across all accounts.
        """
        # Create second Toll Account
        second_account = TollAccount.objects.create(
            name='Secondary IDFC Wallet',
            provider='IDFC Bank FASTag',
            account_number='FASTAG-2002',
            opening_balance=Decimal('25000.00'),
            opening_balance_date=timezone.now().date(),
            is_default=False
        )

        # Recharge account 1: 10,000
        TollRecharge.objects.create(
            toll_account=self.toll_account,
            company_account=self.company_account,
            date=timezone.now().date(),
            amount=Decimal('10000.00'),
            payment_mode=TollRecharge.MODE_BANK_TRANSFER,
            created_by=self.user
        )

        # Recharge account 2: 5,000
        TollRecharge.objects.create(
            toll_account=second_account,
            company_account=self.company_account,
            date=timezone.now().date(),
            amount=Decimal('5000.00'),
            payment_mode=TollRecharge.MODE_BANK_TRANSFER,
            created_by=self.user
        )

        # Toll Entry on account 1 for vehicle 1: 800
        TollEntry.objects.create(
            toll_account=self.toll_account,
            vehicle=self.vehicle_1,
            date=timezone.now().date(),
            amount=Decimal('800.00'),
            toll_plaza='Kherki Daula',
            created_by=self.user
        )

        # Toll Entry on account 2 for vehicle 1: 450
        TollEntry.objects.create(
            toll_account=second_account,
            vehicle=self.vehicle_1,
            date=timezone.now().date(),
            amount=Decimal('450.00'),
            toll_plaza='Manesar Plaza',
            created_by=self.user
        )

        # Toll Entry on account 2 for vehicle 2: 1,200
        TollEntry.objects.create(
            toll_account=second_account,
            vehicle=self.vehicle_2,
            date=timezone.now().date(),
            amount=Decimal('1200.00'),
            toll_plaza='Shahjahanpur Plaza',
            created_by=self.user
        )

        self.toll_account.refresh_from_db()
        second_account.refresh_from_db()

        # Account 1 balance: 15,000 + 10,000 - 800 = 24,200
        self.assertEqual(self.toll_account.current_balance_cached, Decimal('24200.00'))
        # Account 2 balance: 25,000 + 5,000 - 450 - 1,200 = 28,350
        self.assertEqual(second_account.current_balance_cached, Decimal('28350.00'))
        # Combined balance: 24,200 + 28,350 = 52,550

        # 1. Access toll ledger without 'account' param -> Combined View
        url = reverse('toll-ledger')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_combined'])
        self.assertEqual(response.context['current_balance'], Decimal('52550.00'))
        self.assertEqual(response.context['initial_starting_balance'], Decimal('40000.00'))
        self.assertContains(response, 'All Toll & FASTag Accounts')
        self.assertContains(response, 'Combined General View')
        self.assertContains(response, 'Primary FASTag Wallet')
        self.assertContains(response, 'Secondary IDFC Wallet')
        self.assertContains(response, 'Kherki Daula')
        self.assertContains(response, 'Manesar Plaza')
        self.assertContains(response, 'Shahjahanpur Plaza')

        # 2. Filter to single account 1 -> Single Account View
        url_acc1 = f"{reverse('toll-ledger')}?account={self.toll_account.pk}"
        resp_acc1 = self.client.get(url_acc1)
        self.assertEqual(resp_acc1.status_code, 200)
        self.assertFalse(resp_acc1.context['is_combined'])
        self.assertEqual(resp_acc1.context['current_balance'], Decimal('24200.00'))
        self.assertContains(resp_acc1, 'Kherki Daula')
        self.assertNotContains(resp_acc1, 'Manesar Plaza')
        self.assertNotContains(resp_acc1, 'Shahjahanpur Plaza')

        # 3. Filter to vehicle 1 in combined view -> shows vehicle 1 entries across BOTH accounts
        url_veh1 = f"{reverse('toll-ledger')}?vehicle={self.vehicle_1.pk}"
        resp_veh1 = self.client.get(url_veh1)
        self.assertEqual(resp_veh1.status_code, 200)
        self.assertTrue(resp_veh1.context['is_combined'])
        self.assertContains(resp_veh1, 'Kherki Daula')
        self.assertContains(resp_veh1, 'Manesar Plaza')
        self.assertNotContains(resp_veh1, 'Shahjahanpur Plaza')

        # 4. Statement print view in combined mode
        stmt_url = reverse('toll-statement-print')
        resp_stmt = self.client.get(stmt_url)
        self.assertEqual(resp_stmt.status_code, 200)
        self.assertTrue(resp_stmt.context['is_combined'])
        self.assertContains(resp_stmt, 'All Toll & FASTag Accounts (Combined View)')
        self.assertContains(resp_stmt, 'Primary FASTag Wallet')
        self.assertContains(resp_stmt, 'Secondary IDFC Wallet')
