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

class PartyStatementTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username='statement_admin', password='password')
        self.client.force_login(self.user)

        self.account_a = CompanyAccount.objects.create(
            name="Firm Alpha Logistics",
            address="123 Alpha Road",
            gstin="22AAAAA0000A1Z5",
            phone_number="9876543210"
        )
        self.account_b = CompanyAccount.objects.create(
            name="Firm Beta Transport",
            address="456 Beta Street",
            gstin="22BBBBB0000B1Z6",
            phone_number="9123456780"
        )

        self.party = Party.objects.create(
            name="Universal Client",
            party_type=Party.TYPE_DEBTOR,
            opening_balance=Decimal('1000.00')
        )

        self.cat_payment = TransactionCategory.objects.get_or_create(
            name='Payment In',
            defaults={'type': TransactionCategory.TYPE_INCOME}
        )[0]

        # Transaction with Account A
        self.record_a = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account_a,
            party=self.party,
            record_type=FinancialRecord.RECORD_TYPE_TRANSACTION,
            category=self.cat_payment,
            amount=Decimal('500.00'),
            description="Payment to Alpha"
        )

        # Transaction with Account B
        self.record_b = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account_b,
            party=self.party,
            record_type=FinancialRecord.RECORD_TYPE_TRANSACTION,
            category=self.cat_payment,
            amount=Decimal('700.00'),
            description="Payment to Beta"
        )

    def test_party_detail_view_includes_company_accounts(self):
        """Party detail view context should provide company_accounts for statement selector."""
        response = self.client.get(f'/ledger/parties/{self.party.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('company_accounts', response.context)
        accounts_in_ctx = list(response.context['company_accounts'])
        self.assertIn(self.account_a, accounts_in_ctx)
        self.assertIn(self.account_b, accounts_in_ctx)

    def test_party_statement_default_account(self):
        """Without account query param, statement defaults to first company account."""
        response = self.client.get(f'/ledger/parties/{self.party.pk}/statement/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('company', response.context)
        self.assertIn(response.context['company'], [self.account_a, self.account_b])
        # All transactions across firms are present
        statement_rows = response.context['statement_rows']
        descriptions = [r['description'] for r in statement_rows]
        self.assertIn("Payment to Alpha", descriptions)
        self.assertIn("Payment to Beta", descriptions)

    def test_party_statement_selected_account(self):
        """With account query param, statement header uses the specified company account."""
        response = self.client.get(f'/ledger/parties/{self.party.pk}/statement/?account={self.account_b.pk}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['company'], self.account_b)
        self.assertContains(response, "Firm Beta Transport")
        self.assertContains(response, "456 Beta Street")
        self.assertContains(response, "22BBBBB0000B1Z6")
        # All transactions across firms are present
        statement_rows = response.context['statement_rows']
        descriptions = [r['description'] for r in statement_rows]
        self.assertIn("Payment to Alpha", descriptions)
        self.assertIn("Payment to Beta", descriptions)

    def test_party_statement_custom_header_name(self):
        """Optional header_name parameter overrides the company title in the statement."""
        response = self.client.get(f'/ledger/parties/{self.party.pk}/statement/?account={self.account_a.pk}&header_name=Custom+Alpha+Corp')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['custom_header_name'], "Custom Alpha Corp")
        self.assertContains(response, "Custom Alpha Corp")


