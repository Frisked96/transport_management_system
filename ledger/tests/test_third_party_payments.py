from django.test import TestCase
from django.utils import timezone
from decimal import Decimal

from ledger.models import (
    Party, CompanyAccount, TransactionCategory, FinancialRecord,
    FinancialVoucher, Bill, BillAllocation, TripAllocation
)
from ledger.services import BalanceService, BillingService, TripFinancialService
from trips.models import Trip, Route
from fleet.models import Vehicle


class ThirdPartyPaymentAndDualRoleTests(TestCase):
    def setUp(self):
        self.account = CompanyAccount.objects.create(
            name='Central Operations Bank',
            opening_balance=Decimal('100000.00'),
            invoice_prefix='INV-{YYYY}/',
            cn_prefix='CN-{YYYY}/'
        )
        self.party_a = Party.objects.create(
            name='Customer A Logistics',
            party_type=Party.TYPE_DEBTOR,
            opening_balance=Decimal('0.00')
        )
        self.party_b = Party.objects.create(
            name='Broker B Enterprises',
            party_type=Party.TYPE_DEBTOR,
            opening_balance=Decimal('0.00')
        )
        self.vendor_c = Party.objects.create(
            name='Vendor C Fleet Services',
            party_type=Party.TYPE_CREDITOR,
            opening_balance=Decimal('0.00')
        )
        self.dual_party = Party.objects.create(
            name='Hybrid D Freight & Fleet',
            party_type=Party.TYPE_BOTH,
            opening_balance=Decimal('0.00')
        )

        self.cat_income, _ = TransactionCategory.objects.get_or_create(
            name='Trip Payment',
            defaults={'type': TransactionCategory.TYPE_INCOME}
        )
        self.cat_payment_in, _ = TransactionCategory.objects.get_or_create(
            name='Payment In',
            defaults={'type': TransactionCategory.TYPE_INCOME}
        )
        self.cat_payment_out, _ = TransactionCategory.objects.get_or_create(
            name='Payment Out',
            defaults={'type': TransactionCategory.TYPE_EXPENSE}
        )
        self.cat_lorry_hire, _ = TransactionCategory.objects.get_or_create(
            name='Lorry Hire',
            defaults={'type': TransactionCategory.TYPE_EXPENSE}
        )

    def test_party_b_pays_for_party_a_single_payment(self):
        """
        Verify Case 1: Party B pays for Party A's bill.
        Bank increases, Party A's balance decreases, Party B's balance remains untouched.
        """
        # Create Bill for Party A: 50,000
        bill_a = Bill.objects.create(
            issuer=self.account,
            party=self.party_a,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('50000.00'),
            category=self.cat_income
        )
        self.party_a.refresh_balance()
        self.assertEqual(self.party_a.current_balance_cached, Decimal('50000.00'))

        # Party B pays 50,000 for Party A
        voucher = FinancialVoucher.objects.create(
            voucher_number=FinancialVoucher.generate_voucher_number(),
            date=timezone.now().date(),
            voucher_type=FinancialVoucher.VOUCHER_TYPE_BILL_PAYMENT,
            party=self.party_a,
            payer_party=self.party_b,
            account=self.account
        )

        record = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            party=self.party_a,
            paid_by_party=self.party_b,
            associated_bill=bill_a,
            category=self.cat_payment_in,
            amount=Decimal('50000.00'),
            description=f"Paid by {self.party_b.name} for {self.party_a.name}",
            voucher=voucher
        )

        BillAllocation.objects.create(
            financial_record=record,
            bill=bill_a,
            amount=Decimal('50000.00')
        )

        # Refresh all caches
        BillingService.update_bill_financial_caches(bill_a)
        BalanceService.refresh_party_balance(self.party_a)
        BalanceService.refresh_party_balance(self.party_b)
        bank_balance = BalanceService.refresh_account_balance(self.account)
        voucher.update_totals()

        # Invariants
        self.assertEqual(bill_a.payment_status, Bill.PAYMENT_STATUS_PAID)
        self.assertEqual(bill_a.outstanding_balance, Decimal('0.00'))
        self.assertEqual(self.party_a.current_balance_cached, Decimal('0.00'))
        self.assertEqual(self.party_b.current_balance_cached, Decimal('0.00')) # Party B not affected!
        self.assertEqual(bank_balance, Decimal('150000.00')) # 100k + 50k
        self.assertEqual(voucher.payer_party, self.party_b)

    def test_inter_party_contra_settlement_no_cash(self):
        """
        Verify Case 3: Inter-Party Contra Set-off between Vendor C and Customer A.
        No cash/bank moves. Vendor C liability and Customer A receivable are offset.
        """
        # Customer A owes 40,000 (Receivable)
        bill_a = Bill.objects.create(
            issuer=self.account,
            party=self.party_a,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('40000.00'),
            category=self.cat_income
        )
        self.party_a.refresh_balance()
        self.assertEqual(self.party_a.current_balance_cached, Decimal('40000.00'))

        # We owe Vendor C 40,000 (Payable / Lorry Hire)
        bill_c = Bill.objects.create(
            issuer=self.account,
            party=self.vendor_c,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('40000.00'),
            category=self.cat_lorry_hire
        )
        self.vendor_c.refresh_balance()
        self.assertEqual(self.vendor_c.current_balance_cached, Decimal('-40000.00')) # 40,000 Cr

        initial_bank_bal = BalanceService.refresh_account_balance(self.account)

        # Record Inter-Party Contra Settlement of 40,000
        voucher = FinancialVoucher.objects.create(
            voucher_number=FinancialVoucher.generate_voucher_number(),
            date=timezone.now().date(),
            voucher_type=FinancialVoucher.VOUCHER_TYPE_INTER_PARTY_SETTLEMENT,
            party=self.vendor_c,
            payer_party=self.vendor_c,
            account=None # No bank cash!
        )

        # Leg 1: Source Party (Vendor C) - Debits Vendor C, reducing liability
        rec_c = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=None,
            party=self.vendor_c,
            paid_by_party=self.vendor_c,
            is_inter_party_adjustment=True,
            adjustment_target_party=self.party_a,
            associated_bill=bill_c,
            category=self.cat_payment_out,
            amount=Decimal('40000.00'),
            description=f"Offset with {self.party_a.name}",
            voucher=voucher
        )
        BillAllocation.objects.create(financial_record=rec_c, bill=bill_c, amount=Decimal('40000.00'))

        # Leg 2: Target Party (Customer A) - Credits Customer A, reducing receivable
        rec_a = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=None,
            party=self.party_a,
            paid_by_party=self.vendor_c,
            is_inter_party_adjustment=True,
            adjustment_target_party=self.vendor_c,
            associated_bill=bill_a,
            category=self.cat_payment_in,
            amount=Decimal('40000.00'),
            description=f"Offset by {self.vendor_c.name}",
            voucher=voucher
        )
        BillAllocation.objects.create(financial_record=rec_a, bill=bill_a, amount=Decimal('40000.00'))

        # Recalculate
        BillingService.update_bill_financial_caches(bill_a)
        BillingService.update_bill_financial_caches(bill_c)
        BalanceService.refresh_party_balance(self.party_a)
        BalanceService.refresh_party_balance(self.vendor_c)
        final_bank_bal = BalanceService.refresh_account_balance(self.account)
        voucher.update_totals()

        # Invariants
        self.assertEqual(bill_a.payment_status, Bill.PAYMENT_STATUS_PAID)
        self.assertEqual(bill_c.payment_status, Bill.PAYMENT_STATUS_PAID)
        self.assertEqual(self.party_a.current_balance_cached, Decimal('0.00'))
        self.assertEqual(self.vendor_c.current_balance_cached, Decimal('0.00'))
        self.assertEqual(final_bank_bal, initial_bank_bal) # Cash balance strictly untouched!
        self.assertEqual(voucher.total_payment_amount, Decimal('0.00'))
        self.assertEqual(voucher.total_settled_amount, Decimal('40000.00'))

    def test_dual_role_party_calculations(self):
        """
        Verify Dual-Role Party (Customer + Vendor):
        Has both customer freight bills and vendor lorry hire bills.
        Calculates separate sub-ledgers (AR and AP) and accurate net position.
        """
        self.assertTrue(self.dual_party.is_debtor)
        self.assertTrue(self.dual_party.is_creditor)
        self.assertTrue(self.dual_party.is_both)

        # 1. Customer freight bill: 80,000 (Receivable)
        bill_cust = Bill.objects.create(
            issuer=self.account,
            party=self.dual_party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('80000.00'),
            category=self.cat_income
        )

        # 2. Vendor lorry hire bill: 30,000 (Payable)
        bill_vend = Bill.objects.create(
            issuer=self.account,
            party=self.dual_party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('30000.00'),
            category=self.cat_lorry_hire
        )

        self.dual_party.refresh_balance()
        # Net balance = 80,000 Dr - 30,000 Cr = 50,000 Dr
        self.assertEqual(self.dual_party.current_balance_cached, Decimal('50000.00'))
        self.assertEqual(self.dual_party.current_balance, "50000.00 Dr")

        # Check sub-ledger breakdown
        subledgers = self.dual_party.calculate_subledger_balances()
        self.assertEqual(subledgers['ar_debit'], Decimal('80000.00'))
        self.assertEqual(subledgers['ar_balance'], Decimal('80000.00'))
        self.assertEqual(subledgers['ap_credit'], Decimal('30000.00'))
        self.assertEqual(subledgers['ap_balance'], Decimal('30000.00'))
        self.assertEqual(subledgers['net_balance'], Decimal('50000.00'))

    def test_multi_party_split_payment_via_view(self):
        """
        Verify multi-party split payment: One bank payment clears bills for two different parties.
        """
        from django.test import Client
        from django.contrib.auth.models import User
        import json

        client = Client()
        user = User.objects.create_superuser(username='admin_split', password='password123')
        client.login(username='admin_split', password='password123')

        # Bill for Party A: 30,000
        bill_a = Bill.objects.create(
            issuer=self.account,
            party=self.party_a,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('30000.00'),
            category=self.cat_income
        )
        # Bill for Party B: 20,000
        bill_b = Bill.objects.create(
            issuer=self.account,
            party=self.party_b,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('20000.00'),
            category=self.cat_income
        )

        initial_bank_bal = BalanceService.refresh_account_balance(self.account)

        # POST multi-party bill distribution
        post_data = {
            'date': timezone.now().date().isoformat(),
            'record_type': 'Transaction',
            'account': self.account.id,
            'party': self.party_b.id,
            'paid_by_party': self.party_b.id,
            'category': self.cat_payment_in.id,
            'amount': '50000.00',
            'bill_distribution': json.dumps([
                {'bill_id': bill_a.id, 'amount': 30000.00},
                {'bill_id': bill_b.id, 'amount': 20000.00}
            ])
        }

        from django.urls import reverse
        resp = client.post(reverse('financialrecord-create'), post_data)
        self.assertEqual(resp.status_code, 302)

        # Verify bills are marked Paid
        bill_a.refresh_from_db()
        bill_b.refresh_from_db()
        self.assertEqual(bill_a.payment_status, Bill.PAYMENT_STATUS_PAID)
        self.assertEqual(bill_b.payment_status, Bill.PAYMENT_STATUS_PAID)

        # Verify balances for Party A and Party B
        self.party_a.refresh_from_db()
        self.party_b.refresh_from_db()
        self.assertEqual(self.party_a.current_balance_cached, Decimal('0.00'))
        self.assertEqual(self.party_b.current_balance_cached, Decimal('0.00'))

        # Verify bank balance increased by 50,000
        new_bank_bal = BalanceService.refresh_account_balance(self.account)
        self.assertEqual(new_bank_bal, initial_bank_bal + Decimal('50000.00'))

    def test_inter_party_contra_via_view(self):
        """
        Verify inter-party contra settlement via view POST.
        """
        from django.test import Client
        from django.contrib.auth.models import User

        client = Client()
        user = User.objects.create_superuser(username='admin_contra', password='password123')
        client.login(username='admin_contra', password='password123')

        # Customer A owes 25,000
        bill_a = Bill.objects.create(
            issuer=self.account,
            party=self.party_a,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('25000.00'),
            category=self.cat_income
        )
        self.party_a.refresh_balance()

        # We owe Vendor C 25,000
        bill_c = Bill.objects.create(
            issuer=self.account,
            party=self.vendor_c,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('25000.00'),
            category=self.cat_lorry_hire
        )
        self.vendor_c.refresh_balance()

        initial_bank = BalanceService.refresh_account_balance(self.account)

        post_data = {
            'date': timezone.now().date().isoformat(),
            'record_type': 'Transaction',
            'party': self.vendor_c.id,
            'is_inter_party_adjustment': 'on',
            'adjustment_target_party': self.party_a.id,
            'associated_bill': bill_a.id,
            'category': self.cat_payment_out.id,
            'amount': '25000.00',
            'description': 'Offset between Vendor C and Customer A'
        }

        from django.urls import reverse
        resp = client.post(reverse('financialrecord-create'), post_data)
        self.assertEqual(resp.status_code, 302)

        # Check balances
        self.party_a.refresh_from_db()
        self.vendor_c.refresh_from_db()
        self.assertEqual(self.party_a.current_balance_cached, Decimal('0.00'))
        self.assertEqual(self.vendor_c.current_balance_cached, Decimal('0.00'))

        # Bank unchanged
        self.assertEqual(BalanceService.refresh_account_balance(self.account), initial_bank)
