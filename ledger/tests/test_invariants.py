from django.test import TestCase, Client
from django.utils import timezone
from django.db import models
from django.urls import reverse
import datetime
from decimal import Decimal
from ledger.models import (
    Bill, Party, CompanyAccount, TransactionCategory, FinancialRecord,
    TripAllocation, BillAllocation
)
from ledger.services import BalanceService, BillingService, TripFinancialService
from django.contrib.auth.models import User, Permission
from trips.models import Trip, Route
from fleet.models import Vehicle

class FinancialBalanceInvariantTests(TestCase):
    def setUp(self):
        self.account = CompanyAccount.objects.create(
            name='Treasury Account',
            opening_balance=Decimal('50000.00'),
            invoice_prefix='INV-{YYYY}/',
            cn_prefix='CN-{YYYY}/'
        )
        self.debtor = Party.objects.create(
            name='Debtor Global Traders',
            party_type=Party.TYPE_DEBTOR,
            opening_balance=Decimal('10000.00')
        )
        self.creditor = Party.objects.create(
            name='Creditor Fuel Station',
            party_type=Party.TYPE_CREDITOR,
            opening_balance=Decimal('5000.00')
        )
        self.cat_income, _ = TransactionCategory.objects.get_or_create(
            name='Standard Income',
            defaults={'type': TransactionCategory.TYPE_INCOME}
        )
        self.cat_expense, _ = TransactionCategory.objects.get_or_create(
            name='Diesel Expense',
            defaults={'type': TransactionCategory.TYPE_EXPENSE}
        )
        self.cat_payment_in, _ = TransactionCategory.objects.get_or_create(
            name='Payment In',
            defaults={'type': TransactionCategory.TYPE_INCOME}
        )
        self.cat_payment_out, _ = TransactionCategory.objects.get_or_create(
            name='Payment Out',
            defaults={'type': TransactionCategory.TYPE_EXPENSE}
        )
        self.cat_tds, _ = TransactionCategory.objects.get_or_create(
            name='TDS',
            defaults={'type': TransactionCategory.TYPE_INCOME}
        )
        self.cat_deduction, _ = TransactionCategory.objects.get_or_create(
            name='Deductions',
            defaults={'type': TransactionCategory.TYPE_INCOME}
        )

    def test_debtor_party_balance_invariants(self):
        """
        Verify debtor balance calculation invariant:
        Balance = Opening Balance + Invoices (Debits) - Payments/TDS/Deductions/Credit Notes (Credits)
        """
        # Initial: opening balance = 10,000
        self.debtor.refresh_balance()
        self.assertEqual(self.debtor.current_balance_cached, Decimal('10000.00'))

        # 1. Invoice generated: 40,000 (Debit) -> Balance = 50,000
        inv = Bill.objects.create(
            issuer=self.account,
            party=self.debtor,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('40000.00'),
            category=self.cat_income
        )
        self.debtor.refresh_balance()
        self.assertEqual(self.debtor.current_balance_cached, Decimal('50000.00'))

        # 2. Bank Payment received: 25,000 (Credit) -> Balance = 25,000
        FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            party=self.debtor,
            category=self.cat_payment_in,
            amount=Decimal('25000.00')
        )
        self.debtor.refresh_balance()
        self.assertEqual(self.debtor.current_balance_cached, Decimal('25000.00'))

        # 3. TDS deduction: 1,000 (Credit) -> Balance = 24,000
        FinancialRecord.objects.create(
            date=timezone.now().date(),
            party=self.debtor,
            category=self.cat_tds,
            amount=Decimal('1000.00')
        )
        self.debtor.refresh_balance()
        self.assertEqual(self.debtor.current_balance_cached, Decimal('24000.00'))

        # 4. Shortage/Damage deduction: 500 (Credit) -> Balance = 23,500
        FinancialRecord.objects.create(
            date=timezone.now().date(),
            party=self.debtor,
            category=self.cat_deduction,
            amount=Decimal('500.00')
        )
        self.debtor.refresh_balance()
        self.assertEqual(self.debtor.current_balance_cached, Decimal('23500.00'))

        # 5. Credit Note issued against invoice: 3,500 (Credit) -> Balance = 20,000
        cat_cn, _ = TransactionCategory.objects.get_or_create(
            name='Credit Note',
            defaults={'type': TransactionCategory.TYPE_INCOME}
        )
        cn = Bill.objects.create(
            issuer=self.account,
            party=self.debtor,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('3500.00'),
            category=cat_cn,
            original_bill=inv
        )
        self.debtor.refresh_balance()
        self.assertEqual(self.debtor.current_balance_cached, Decimal('20000.00'))
        self.assertEqual(self.debtor.total_debit_amount - self.debtor.total_credit_amount, Decimal('20000.00'))

    def test_company_account_cash_balance_invariants(self):
        """
        Verify Company Account balance invariant:
        Balance = Opening Balance + Cash Incomes - Cash Expenses.
        Accrual records (Invoices) and non-cash adjustments (TDS, Deductions) are excluded.
        """
        initial_balance = BalanceService.refresh_account_balance(self.account)
        self.assertEqual(initial_balance, Decimal('50000.00'))

        # 1. Real Cash/Bank Receipt: +20,000
        FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            party=self.debtor,
            category=self.cat_payment_in,
            amount=Decimal('20000.00')
        )
        bal_after_inc = BalanceService.refresh_account_balance(self.account)
        self.assertEqual(bal_after_inc, Decimal('70000.00'))

        # 2. Real Cash/Bank Expense: -15,000
        FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            party=self.creditor,
            category=self.cat_expense,
            amount=Decimal('15000.00')
        )
        bal_after_exp = BalanceService.refresh_account_balance(self.account)
        self.assertEqual(bal_after_exp, Decimal('55000.00'))

        # 3. Create an Accrual Invoice for 100,000 -> MUST NOT affect cash balance
        Bill.objects.create(
            issuer=self.account,
            party=self.debtor,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('100000.00'),
            category=self.cat_income
        )
        bal_after_inv = BalanceService.refresh_account_balance(self.account)
        self.assertEqual(bal_after_inv, Decimal('55000.00'))

        # 4. TDS and Deductions -> MUST NOT affect cash balance
        FinancialRecord.objects.create(
            date=timezone.now().date(),
            party=self.debtor,
            category=self.cat_tds,
            amount=Decimal('2000.00')
        )
        FinancialRecord.objects.create(
            date=timezone.now().date(),
            party=self.debtor,
            category=self.cat_deduction,
            amount=Decimal('1500.00')
        )
        bal_final = BalanceService.refresh_account_balance(self.account)
        self.assertEqual(bal_final, Decimal('55000.00'))


