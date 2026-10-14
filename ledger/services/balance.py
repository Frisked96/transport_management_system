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
    def refresh_party_balance(party):
        """
        Recalculate and update the cached balance fields for a Party.
        """
        from ledger.models import Party
        
        with transaction.atomic():
            # Lock the party row
            party_obj = Party.objects.select_for_update().get(pk=party.pk)
            
            # 1 & 2. Calculate Total Debits & Credits in a single pass
            base_debit = party_obj.opening_balance if party_obj.opening_balance > 0 else Decimal('0')
            base_credit = abs(party_obj.opening_balance) if party_obj.opening_balance < 0 else Decimal('0')
            records = party_obj.financial_records.select_related('category', 'associated_bill__category').all()
            sum_debit = Decimal('0')
            sum_credit = Decimal('0')
            for r in records:
                sum_debit += (r.debit_amount or Decimal('0'))
                sum_credit += (r.credit_amount or Decimal('0'))

            total_debit = base_debit + sum_debit
            total_credit = base_credit + sum_credit
            
            # 3. Update cached fields
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
    def refresh_account_balance(account):
        """
        Recalculate and update the cached balance fields for a CompanyAccount.
        """
        from ledger.models import TransactionCategory, FinancialRecord
        
        income = account.financial_records.filter(
            category__type=TransactionCategory.TYPE_INCOME
        ).exclude(
            Q(record_type=FinancialRecord.RECORD_TYPE_INVOICE) | 
            Q(category__name__in=['Deductions', 'TDS', 'Shortage', 'Credit Note', 'Debit Note'])
        ).aggregate(total=Sum('amount'))['total'] or 0

        expenses = account.financial_records.filter(
            category__type=TransactionCategory.TYPE_EXPENSE
        ).exclude(
            Q(record_type=FinancialRecord.RECORD_TYPE_INVOICE) |
            Q(category__name__in=['Deductions', 'TDS', 'Shortage', 'Credit Note', 'Debit Note'])
        ).aggregate(total=Sum('amount'))['total'] or 0

        balance = account.opening_balance + Decimal(str(income)) - Decimal(str(expenses))
        
        account.current_balance_cached = balance
        account.save(update_fields=['current_balance_cached'])
        
        return balance
