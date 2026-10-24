"""
Balance calculation and synchronization service for Parties and CompanyAccounts.
"""
from decimal import Decimal
from django.db import transaction
from django.db.models import Sum, Q


class BalanceService:
    """
    Handles balance recalculations and caching for Parties and CompanyAccounts.
    """
    
    @staticmethod
    def calculate_party_totals(party):
        """
        Calculates debits and credits in a single SQL query matching Party rules.
        """
        from ledger.models import Party, FinancialRecord, TransactionCategory
        from django.db.models import Sum, Case, When, F, Value, DecimalField, Q
        from django.db.models.functions import Coalesce

        base_debit = party.opening_balance if party.opening_balance > 0 else Decimal('0')
        base_credit = abs(party.opening_balance) if party.opening_balance < 0 else Decimal('0')

        if party.party_type == Party.TYPE_DEBTOR:
            debit_cond = Case(
                When(record_type=FinancialRecord.RECORD_TYPE_INVOICE, associated_bill__category__name='Credit Note', then=Value(Decimal('0'))),
                When(record_type=FinancialRecord.RECORD_TYPE_INVOICE, then=F('amount')),
                When(Q(category__type=TransactionCategory.TYPE_EXPENSE) & ~Q(category__name__in=['Deductions', 'TDS', 'Shortage']), then=F('amount')),
                default=Value(Decimal('0')),
                output_field=DecimalField()
            )
            credit_cond = Case(
                When(Q(category__type=TransactionCategory.TYPE_INCOME) & ~Q(record_type=FinancialRecord.RECORD_TYPE_INVOICE), then=F('amount')),
                When(category__name__in=['Deductions', 'TDS', 'Shortage'], then=F('amount')),
                When(record_type=FinancialRecord.RECORD_TYPE_INVOICE, associated_bill__category__name='Credit Note', then=F('amount')),
                default=Value(Decimal('0')),
                output_field=DecimalField()
            )
        else: # CREDITOR
            debit_cond = Case(
                When(Q(category__type=TransactionCategory.TYPE_INCOME) & ~Q(record_type=FinancialRecord.RECORD_TYPE_INVOICE), then=F('amount')),
                When(category__name__in=['Deductions', 'TDS', 'Shortage', 'Payment Out'], then=F('amount')),
                When(record_type=FinancialRecord.RECORD_TYPE_INVOICE, associated_bill__category__name='Debit Note', then=F('amount')),
                default=Value(Decimal('0')),
                output_field=DecimalField()
            )
            credit_cond = Case(
                When(record_type=FinancialRecord.RECORD_TYPE_INVOICE, associated_bill__category__name='Debit Note', then=Value(Decimal('0'))),
                When(record_type=FinancialRecord.RECORD_TYPE_INVOICE, then=F('amount')),
                When(Q(category__type=TransactionCategory.TYPE_EXPENSE) & ~Q(category__name__in=['Deductions', 'TDS', 'Shortage', 'Payment Out']), then=F('amount')),
                When(record_type=FinancialRecord.RECORD_TYPE_INVOICE, associated_bill__category__name='Credit Note', then=F('amount')),
                default=Value(Decimal('0')),
                output_field=DecimalField()
            )

        res = party.financial_records.aggregate(
            debit=Coalesce(Sum(debit_cond), Value(Decimal('0'))),
            credit=Coalesce(Sum(credit_cond), Value(Decimal('0')))
        )
        return base_debit + res['debit'], base_credit + res['credit']

    @staticmethod
    def refresh_party_balance(party):
        """
        Recalculate and update the cached balance fields for a Party using SQL aggregation.
        """
        from ledger.models import Party
        
        with transaction.atomic():
            party_obj = Party.objects.select_for_update().get(pk=party.pk)
            total_debit, total_credit = BalanceService.calculate_party_totals(party_obj)
            party_obj.total_debit_amount = total_debit
            party_obj.total_credit_amount = total_credit
            party_obj.current_balance_cached = total_debit - total_credit
            party_obj.save(update_fields=['total_debit_amount', 'total_credit_amount', 'current_balance_cached'])
            
            # Sync local instance fields if it's the same object
            if party == party_obj:
                party.total_debit_amount = party_obj.total_debit_amount
                party.total_credit_amount = party_obj.total_credit_amount
                party.current_balance_cached = party_obj.current_balance_cached
            
            return party_obj.current_balance_cached

    @staticmethod
    def calculate_account_balance(account):
        """
        Calculates CompanyAccount balance in a single SQL query without saving.
        """
        from ledger.models import TransactionCategory, FinancialRecord
        from django.db.models import Sum, Case, When, F, Value, DecimalField, Q
        from django.db.models.functions import Coalesce

        excluded_q = (
            Q(record_type=FinancialRecord.RECORD_TYPE_INVOICE) | 
            Q(category__name__in=['Deductions', 'TDS', 'Shortage', 'Credit Note', 'Debit Note'])
        )
        totals = account.financial_records.exclude(excluded_q).aggregate(
            income=Coalesce(
                Sum(Case(
                    When(category__type=TransactionCategory.TYPE_INCOME, then=F('amount')),
                    default=Value(Decimal('0')),
                    output_field=DecimalField()
                )),
                Value(Decimal('0'))
            ),
            expense=Coalesce(
                Sum(Case(
                    When(category__type=TransactionCategory.TYPE_EXPENSE, then=F('amount')),
                    default=Value(Decimal('0')),
                    output_field=DecimalField()
                )),
                Value(Decimal('0'))
            )
        )
        return account.opening_balance + totals['income'] - totals['expense']

    @staticmethod
    def refresh_account_balance(account):
        """
        Recalculate and update the cached balance fields for a CompanyAccount.
        """
        balance = BalanceService.calculate_account_balance(account)
        account.current_balance_cached = balance
        account.save(update_fields=['current_balance_cached'])
        return balance
