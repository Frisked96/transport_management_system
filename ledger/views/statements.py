"""
Statement views for Party, Account, and Unified Ledgers.
Optimized for clean A4 printing and browser Save-to-PDF.
"""
from decimal import Decimal
from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum
from django.utils import timezone

from ledger.models import FinancialRecord, Party, CompanyAccount
from ledger.utils import format_balance, parse_date_range


@login_required
def party_statement_pdf(request, pk):
    """
    Renders print-optimized statement of account for a specific party within a date range.
    """
    party = get_object_or_404(Party, pk=pk)
    start_date, end_date = parse_date_range(request)

    # 1. Calculate Opening Balance before start_date
    opening_bal = party.opening_balance
    pre_totals = FinancialRecord.objects.filter(
        party=party,
        date__lt=start_date
    ).select_related('category')

    for rec in pre_totals:
        debit = rec.debit_amount or Decimal('0')
        credit = rec.credit_amount or Decimal('0')
        opening_bal += (debit - credit)

    # 2. Get records in range
    records = FinancialRecord.objects.filter(
        party=party,
        date__range=[start_date, end_date]
    ).select_related('category', 'associated_trip', 'associated_bill', 'party').order_by('date', 'created_at')

    # 3. Build statement rows with running balance
    statement_rows = []
    current_running_bal = opening_bal
    total_period_debit = Decimal('0')
    total_period_credit = Decimal('0')

    for rec in records:
        debit = rec.debit_amount or Decimal('0')
        credit = rec.credit_amount or Decimal('0')

        if debit == 0 and credit == 0:
            continue

        current_running_bal += (debit - credit)
        total_period_debit += debit
        total_period_credit += credit

        # Reference string
        ref = "-"
        if rec.associated_bill:
            ref = f"INV: {rec.associated_bill.bill_number or 'Draft'}"
        elif rec.associated_trip:
            ref = f"TRP: {rec.associated_trip.trip_number}"
        elif rec.linked_bill:
            ref = f"INV: {rec.linked_bill.bill_number or 'Draft'}"
        elif rec.linked_trip:
            ref = f"TRP: {rec.linked_trip.trip_number}"

        balance_class = "balance-dr" if current_running_bal > 0 else ("balance-cr" if current_running_bal < 0 else "")

        statement_rows.append({
            'date': rec.date,
            'description': rec.description or (rec.category.name if rec.category else 'Transaction'),
            'reference': ref,
            'debit': debit,
            'credit': credit,
            'balance': current_running_bal,
            'balance_formatted': format_balance(current_running_bal, party.party_type),
            'balance_class': balance_class,
        })

    account_id = request.GET.get('account') or request.GET.get('issuer')
    if account_id:
        company = CompanyAccount.objects.filter(pk=account_id).first()
    else:
        company = (
            CompanyAccount.objects.filter(bills__party=party).first() or
            CompanyAccount.objects.filter(financial_records__party=party).first() or
            CompanyAccount.objects.exclude(address='').order_by('id').first() or
            CompanyAccount.objects.order_by('id').first()
        )
    context = {
        'party': party,
        'recipient_name': party.name,
        'recipient_label': f"CLIENT / {party.get_party_type_display().upper()}",
        'recipient_address': party.address,
        'recipient_gstin': party.gstin,
        'recipient_phone': party.phone_number,
        'title': f"Statement of Account - {party.name}",
        'start_date': start_date,
        'end_date': end_date,
        'opening_balance': opening_bal,
        'opening_balance_formatted': format_balance(opening_bal, party.party_type),
        'statement_rows': statement_rows,
        'total_debit': total_period_debit,
        'total_credit': total_period_credit,
        'closing_balance': current_running_bal,
        'closing_balance_formatted': format_balance(current_running_bal, party.party_type),
        'generated_at': timezone.now(),
        'company': company,
    }

    return render(request, 'ledger/statement_print.html', context)


@login_required
def account_statement_pdf(request, pk):
    """
    Renders print-optimized statement for a company account within a date range.
    """
    account = get_object_or_404(CompanyAccount, pk=pk)
    start_date, end_date = parse_date_range(request)

    # 1. Calculate Opening Balance before start_date
    opening_bal = account.opening_balance
    pre_records = FinancialRecord.objects.filter(
        account=account,
        date__lt=start_date
    ).exclude(
        Q(record_type=FinancialRecord.RECORD_TYPE_INVOICE) |
        Q(category__name__in=['Deductions', 'TDS', 'Shortage', 'Credit Note', 'Debit Note'])
    ).select_related('category')

    for rec in pre_records:
        if rec.is_income:
            opening_bal += rec.amount
        elif rec.is_expense:
            opening_bal -= rec.amount

    # 2. Get records in range
    records = FinancialRecord.objects.filter(
        account=account,
        date__range=[start_date, end_date]
    ).exclude(
        Q(record_type=FinancialRecord.RECORD_TYPE_INVOICE) |
        Q(category__name__in=['Deductions', 'TDS', 'Shortage', 'Credit Note', 'Debit Note'])
    ).select_related('category', 'associated_trip', 'associated_bill', 'party').order_by('date', 'created_at')

    # 3. Build statement rows
    statement_rows = []
    current_running_bal = opening_bal
    total_period_debit = Decimal('0')
    total_period_credit = Decimal('0')

    for rec in records:
        debit = rec.amount if rec.is_income else Decimal('0')
        credit = rec.amount if rec.is_expense else Decimal('0')

        current_running_bal += (debit - credit)
        total_period_debit += debit
        total_period_credit += credit

        ref = "-"
        if rec.associated_bill:
            ref = f"INV: {rec.associated_bill.bill_number or 'Draft'}"
        elif rec.associated_trip:
            ref = f"TRP: {rec.associated_trip.trip_number}"

        desc = rec.description or (rec.category.name if rec.category else 'Transaction')
        if rec.party:
            desc = f"{desc} (Party: {rec.party.name})"

        balance_class = "balance-dr" if current_running_bal > 0 else ("balance-cr" if current_running_bal < 0 else "")

        statement_rows.append({
            'date': rec.date,
            'description': desc,
            'reference': ref,
            'debit': debit,
            'credit': credit,
            'balance': current_running_bal,
            'balance_formatted': format_balance(current_running_bal),
            'balance_class': balance_class,
        })

    extra_bank_info = f"Bank: {account.bank_name} | A/C: {account.account_number}" if account.bank_name else ""
    context = {
        'party': account,
        'recipient_name': account.name,
        'recipient_label': 'COMPANY ACCOUNT',
        'recipient_address': account.address,
        'recipient_gstin': account.gstin,
        'recipient_phone': account.phone_number,
        'recipient_extra': extra_bank_info,
        'title': f"Account Statement - {account.name}",
        'start_date': start_date,
        'end_date': end_date,
        'opening_balance': opening_bal,
        'opening_balance_formatted': format_balance(opening_bal),
        'statement_rows': statement_rows,
        'total_debit': total_period_debit,
        'total_credit': total_period_credit,
        'closing_balance': current_running_bal,
        'closing_balance_formatted': format_balance(current_running_bal),
        'generated_at': timezone.now(),
        'company': account,
    }

    return render(request, 'ledger/statement_print.html', context)


@login_required
def unified_ledger_pdf(request):
    """
    Renders print-optimized statement for all Company Accounts combined.
    """
    start_date, end_date = parse_date_range(request)

    # 1. Combined Opening Balance
    opening_bal = CompanyAccount.objects.aggregate(total=Sum('opening_balance'))['total'] or Decimal('0')

    pre_records = FinancialRecord.objects.filter(
        account__isnull=False,
        date__lt=start_date
    ).exclude(
        Q(record_type=FinancialRecord.RECORD_TYPE_INVOICE) |
        Q(category__name__in=['Deductions', 'TDS', 'Shortage', 'Credit Note', 'Debit Note'])
    ).select_related('category')

    for rec in pre_records:
        if rec.is_income:
            opening_bal += rec.amount
        elif rec.is_expense:
            opening_bal -= rec.amount

    # 2. Records in range
    records = FinancialRecord.objects.filter(
        account__isnull=False,
        date__range=[start_date, end_date]
    ).exclude(
        Q(record_type=FinancialRecord.RECORD_TYPE_INVOICE) |
        Q(category__name__in=['Deductions', 'TDS', 'Shortage', 'Credit Note', 'Debit Note'])
    ).select_related('category', 'associated_trip', 'associated_bill', 'party', 'account').order_by('date', 'created_at')

    # 3. Build statement rows
    statement_rows = []
    current_running_bal = opening_bal
    total_period_debit = Decimal('0')
    total_period_credit = Decimal('0')

    for rec in records:
        debit = rec.amount if rec.is_income else Decimal('0')
        credit = rec.amount if rec.is_expense else Decimal('0')

        current_running_bal += (debit - credit)
        total_period_debit += debit
        total_period_credit += credit

        ref = "-"
        if rec.associated_bill:
            ref = f"INV: {rec.associated_bill.bill_number or 'Draft'}"
        elif rec.associated_trip:
            ref = f"TRP: {rec.associated_trip.trip_number}"

        desc = rec.description or (rec.category.name if rec.category else 'Transaction')
        extra_info = []
        if rec.account:
            extra_info.append(f"ACC: {rec.account.name}")
        if rec.party:
            extra_info.append(f"PRT: {rec.party.name}")
        if extra_info:
            desc = f"{desc} ({', '.join(extra_info)})"

        balance_class = "balance-dr" if current_running_bal > 0 else ("balance-cr" if current_running_bal < 0 else "")

        statement_rows.append({
            'date': rec.date,
            'description': desc,
            'reference': ref,
            'debit': debit,
            'credit': credit,
            'balance': current_running_bal,
            'balance_formatted': format_balance(current_running_bal),
            'balance_class': balance_class,
        })

    company_main = (
        CompanyAccount.objects.exclude(address='').order_by('id').first() or
        CompanyAccount.objects.order_by('id').first()
    )
    context = {
        'party': company_main,
        'recipient_name': "All Company Accounts",
        'recipient_label': 'CONSOLIDATED FIRMS',
        'recipient_address': "Multi-firm Consolidated Ledger",
        'title': "Unified Ledger Statement",
        'start_date': start_date,
        'end_date': end_date,
        'opening_balance': opening_bal,
        'opening_balance_formatted': format_balance(opening_bal),
        'statement_rows': statement_rows,
        'total_debit': total_period_debit,
        'total_credit': total_period_credit,
        'closing_balance': current_running_bal,
        'closing_balance_formatted': format_balance(current_running_bal),
        'generated_at': timezone.now(),
        'company': company_main,
    }

    return render(request, 'ledger/statement_print.html', context)
