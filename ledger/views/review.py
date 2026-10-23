"""
Financial Entry Review and Verification views for Transport Management System.
Allows managers and business owners to audit, verify, and flag entries made by users.
"""
from decimal import Decimal
from django.views.generic import ListView
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Sum, Count
from django.utils import timezone
from django.http import JsonResponse, HttpResponseForbidden
from django.contrib.auth.models import User

from ledger.models import FinancialVoucher, FinancialRecord, Party, CompanyAccount
from ledger.views.base import BaseLedgerPermissionMixin


class FinancialEntryReviewView(LoginRequiredMixin, PermissionRequiredMixin, BaseLedgerPermissionMixin, ListView):
    """
    Dedicated review dashboard for verifying financial entries and vouchers
    submitted by operators (e.g. Akash) and other users.
    """
    model = FinancialVoucher
    template_name = 'ledger/financial_entry_review.html'
    context_object_name = 'vouchers'
    permission_required = 'ledger.can_view_financial_records'
    paginate_by = 25

    def get_queryset(self):
        # Drivers have no financial access
        if self.has_driver_profile():
            return FinancialVoucher.objects.none()

        queryset = FinancialVoucher.objects.select_related(
            'party', 'account', 'driver__user', 'created_by', 'verified_by'
        ).prefetch_related(
            'financial_records__category',
            'financial_records__account',
            'financial_records__associated_trip__vehicle',
            'financial_records__associated_trip__route',
            'financial_records__associated_bill',
            'financial_records__allocations__trip__vehicle',
            'financial_records__allocations__trip__route',
            'financial_records__bill_allocations__bill',
        )

        # Status filter (default to 'pending' to keep review focused)
        status = self.request.GET.get('status', 'pending')
        if status and status != 'all':
            queryset = queryset.filter(verification_status=status)

        # User filter
        user_param = self.request.GET.get('user')
        if user_param:
            if user_param.isdigit():
                queryset = queryset.filter(Q(created_by_id=user_param) | Q(financial_records__recorded_by_id=user_param)).distinct()
            else:
                queryset = queryset.filter(Q(created_by__username=user_param) | Q(financial_records__recorded_by__username=user_param)).distinct()

        # Voucher type filter
        v_type = self.request.GET.get('type')
        if v_type:
            queryset = queryset.filter(voucher_type=v_type)

        # Date range filter
        start_date = self.request.GET.get('start_date')
        end_date = self.request.GET.get('end_date')
        if start_date:
            queryset = queryset.filter(date__gte=start_date)
        if end_date:
            queryset = queryset.filter(date__lte=end_date)

        # Search filter
        search = self.request.GET.get('search', '').strip()
        if search:
            queryset = queryset.filter(
                Q(voucher_number__icontains=search) |
                Q(description__icontains=search) |
                Q(party__name__icontains=search) |
                Q(financial_records__entry_number__icontains=search) |
                Q(financial_records__associated_trip__trip_number__icontains=search) |
                Q(financial_records__associated_trip__lr_no__icontains=search) |
                Q(financial_records__associated_trip__vehicle__registration_plate__icontains=search) |
                Q(financial_records__allocations__trip__trip_number__icontains=search) |
                Q(financial_records__allocations__trip__lr_no__icontains=search) |
                Q(financial_records__allocations__trip__vehicle__registration_plate__icontains=search) |
                Q(financial_records__associated_bill__bill_number__icontains=search)
            ).distinct()

        return queryset.order_by('-date', '-created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Count metrics for status tabs
        counts = FinancialVoucher.objects.aggregate(
            pending=Count('id', filter=Q(verification_status=FinancialVoucher.STATUS_PENDING)),
            verified=Count('id', filter=Q(verification_status=FinancialVoucher.STATUS_VERIFIED)),
            flagged=Count('id', filter=Q(verification_status=FinancialVoucher.STATUS_FLAGGED)),
            deleted=Count('id', filter=Q(verification_status=FinancialVoucher.STATUS_DELETED)),
            total=Count('id'),
            pending_amount=Sum('total_settled_amount', filter=Q(verification_status=FinancialVoucher.STATUS_PENDING))
        )

        context['pending_count'] = counts['pending'] or 0
        context['verified_count'] = counts['verified'] or 0
        context['flagged_count'] = counts['flagged'] or 0
        context['deleted_count'] = counts['deleted'] or 0
        context['all_count'] = counts['total'] or 0
        context['pending_total_amount'] = counts['pending_amount'] or Decimal('0.00')

        # Active submitter users for pill filters (including superusers and operators)
        context['submitter_users'] = User.objects.filter(
            Q(created_vouchers__isnull=False) |
            Q(recorded_financials__isnull=False) |
            Q(is_superuser=True)
        ).distinct().order_by('username')

        # Current filter params
        context['current_status'] = self.request.GET.get('status', 'pending')
        context['current_user'] = self.request.GET.get('user', '')
        context['current_type'] = self.request.GET.get('type', '')
        context['current_search'] = self.request.GET.get('search', '')
        context['voucher_type_choices'] = FinancialVoucher.VOUCHER_TYPE_CHOICES

        # Check if user has permission to verify
        user = self.request.user
        context['can_verify'] = (
            user.is_superuser or
            user.has_perm('ledger.can_verify_financial_records') or
            user.has_perm('ledger.can_manage_financial_records')
        )

        return context


def _user_has_verify_permission(user):
    """Helper to check if user can verify financial entries"""
    return (
        user.is_superuser or
        user.has_perm('ledger.can_verify_financial_records') or
        user.has_perm('ledger.can_manage_financial_records')
    )


@login_required
@require_POST
def verify_financial_entry(request, pk):
    """
    1-Click verification action for a FinancialVoucher and its child records.
    Supports both AJAX requests and standard POST redirects.
    """
    if not _user_has_verify_permission(request.user):
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'error': 'Permission denied.'}, status=403)
        messages.error(request, 'You do not have permission to verify financial records.')
        return redirect('financial-entry-review')

    voucher = get_object_or_404(FinancialVoucher, pk=pk)

    with transaction.atomic():
        voucher.verification_status = FinancialVoucher.STATUS_VERIFIED
        voucher.verified_by = request.user
        voucher.verified_at = timezone.now()
        notes = request.POST.get('notes', '').strip()
        if notes:
            voucher.verification_notes = notes
        voucher.save(update_fields=['verification_status', 'verified_by', 'verified_at', 'verification_notes'])

        voucher.financial_records.update(
            verification_status=FinancialVoucher.STATUS_VERIFIED,
            verified_by=request.user,
            verified_at=voucher.verified_at
        )

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({
            'success': True,
            'voucher_id': voucher.id,
            'voucher_number': voucher.voucher_number,
            'status': 'verified',
            'status_display': 'Verified',
            'verified_by': request.user.get_full_name() or request.user.username,
            'verified_at': voucher.verified_at.strftime('%d %b %Y, %H:%M')
        })

    messages.success(request, f"Voucher #{voucher.voucher_number} verified successfully.")
    referer = request.META.get('HTTP_REFERER')
    return redirect(referer or 'financial-entry-review')


@login_required
@require_POST
def flag_financial_entry(request, pk):
    """
    Flags a FinancialVoucher with remarks for corrections/inquiries.
    """
    if not _user_has_verify_permission(request.user):
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'error': 'Permission denied.'}, status=403)
        messages.error(request, 'You do not have permission to flag financial records.')
        return redirect('financial-entry-review')

    voucher = get_object_or_404(FinancialVoucher, pk=pk)
    remarks = request.POST.get('notes', '').strip()
    if not remarks:
        remarks = "Flagged by reviewer for correction."

    with transaction.atomic():
        voucher.verification_status = FinancialVoucher.STATUS_FLAGGED
        voucher.verified_by = request.user
        voucher.verified_at = timezone.now()
        voucher.verification_notes = remarks
        voucher.save(update_fields=['verification_status', 'verified_by', 'verified_at', 'verification_notes'])

        voucher.financial_records.update(
            verification_status=FinancialVoucher.STATUS_FLAGGED,
            verified_by=request.user,
            verified_at=voucher.verified_at,
            verification_notes=remarks
        )

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({
            'success': True,
            'voucher_id': voucher.id,
            'voucher_number': voucher.voucher_number,
            'status': 'flagged',
            'status_display': 'Flagged',
            'notes': remarks,
            'verified_by': request.user.get_full_name() or request.user.username,
            'verified_at': voucher.verified_at.strftime('%d %b %Y, %H:%M')
        })

    messages.warning(request, f"Voucher #{voucher.voucher_number} flagged: {remarks}")
    referer = request.META.get('HTTP_REFERER')
    return redirect(referer or 'financial-entry-review')


@login_required
@require_POST
def bulk_verify_financial_entries(request):
    """
    Bulk verification for multiple vouchers selected via checkboxes.
    """
    if not _user_has_verify_permission(request.user):
        messages.error(request, 'You do not have permission to verify financial records.')
        return redirect('financial-entry-review')

    voucher_ids = request.POST.getlist('voucher_ids')
    if not voucher_ids:
        # Check comma-separated string if sent via json/single field
        raw_ids = request.POST.get('voucher_ids_str', '')
        if raw_ids:
            voucher_ids = [vid.strip() for vid in raw_ids.split(',') if vid.strip()]

    if not voucher_ids:
        messages.warning(request, 'No vouchers selected for verification.')
        return redirect('financial-entry-review')

    now = timezone.now()
    with transaction.atomic():
        vouchers = FinancialVoucher.objects.filter(id__in=voucher_ids)
        count = vouchers.count()
        vouchers.update(
            verification_status=FinancialVoucher.STATUS_VERIFIED,
            verified_by=request.user,
            verified_at=now
        )
        FinancialRecord.objects.filter(voucher_id__in=voucher_ids).update(
            verification_status=FinancialVoucher.STATUS_VERIFIED,
            verified_by=request.user,
            verified_at=now
        )

    messages.success(request, f"Successfully verified {count} vouchers.")
    referer = request.META.get('HTTP_REFERER')
    return redirect(referer or 'financial-entry-review')


@login_required
@require_POST
def restore_financial_entry(request, pk):
    """
    Restores a soft-deleted voucher and all its constituent child records.
    Supports both AJAX requests and standard POST redirects.
    """
    if not _user_has_verify_permission(request.user):
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'error': 'Permission denied.'}, status=403)
        messages.error(request, 'You do not have permission to restore financial records.')
        return redirect('financial-entry-review')

    voucher = get_object_or_404(FinancialVoucher, pk=pk)
    reason = request.POST.get('restore_reason', '').strip()
    voucher.restore(user=request.user, reason=reason)

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({
            'success': True,
            'voucher_id': voucher.pk,
            'voucher_number': voucher.voucher_number,
            'status': 'pending',
            'message': f'Voucher {voucher.voucher_number} and constituent records restored successfully.'
        })

    messages.success(request, f'Voucher {voucher.voucher_number} restored successfully! It is now queued for re-verification.')
    referer = request.META.get('HTTP_REFERER')
    return redirect(referer or 'financial-entry-review')
