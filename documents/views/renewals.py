"""
Document renewal and history views for Documents app.
"""
from django.views.generic import DetailView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.contrib import messages
from django.utils import timezone

from documents.models import Document, DocumentRenewal
from documents.forms import DocumentRenewalForm


class DocumentRenewView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """
    View to record a renewal for a document with valid_from, valid_to, and renewal cost.
    """
    model = DocumentRenewal
    form_class = DocumentRenewalForm
    template_name = 'documents/document_renew_form.html'
    permission_required = 'documents.change_document'

    def dispatch(self, request, *args, **kwargs):
        self.document = get_object_or_404(Document, pk=kwargs.get('pk'))
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        initial = super().get_initial()
        # Pre-fill valid_from with current expiry_date or today
        if self.document.expiry_date:
            initial['valid_from'] = self.document.expiry_date
        else:
            initial['valid_from'] = timezone.now().date()
        if self.document.document_number:
            initial['document_number'] = self.document.document_number
        if self.document.cost:
            initial['cost'] = self.document.cost
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['document'] = self.document
        context['vehicle'] = self.document.vehicle
        context['driver'] = self.document.driver
        return context

    def form_valid(self, form):
        form.instance.document = self.document
        form.instance.renewed_by = self.request.user
        self.object = form.save()
        messages.success(self.request, f"Renewal for '{self.document.document_name}' recorded successfully.")
        return redirect('document-history', pk=self.document.pk)


class DocumentHistoryView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    """
    Detail view showing full renewal history, validity periods, and expenses for a document.
    """
    model = Document
    template_name = 'documents/document_history.html'
    context_object_name = 'document'
    permission_required = 'documents.view_document'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['renewals'] = self.object.renewals.all().order_by('-valid_to', '-created_at')
        context['total_cost'] = self.object.total_expenses
        context['vehicle'] = self.object.vehicle
        context['driver'] = self.object.driver
        return context


class DocumentRenewalUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """
    View to edit a historical renewal entry.
    """
    model = DocumentRenewal
    form_class = DocumentRenewalForm
    template_name = 'documents/document_renewal_form.html'
    permission_required = 'documents.change_document'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['document'] = self.object.document
        context['vehicle'] = self.object.document.vehicle
        return context

    def form_valid(self, form):
        self.object = form.save()
        messages.success(self.request, 'Renewal entry updated successfully.')
        return redirect('document-history', pk=self.object.document.pk)


class DocumentRenewalDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    """
    View to delete an accidental historical renewal entry.
    """
    model = DocumentRenewal
    template_name = 'documents/document_renewal_confirm_delete.html'
    permission_required = 'documents.change_document'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['document'] = self.object.document
        context['vehicle'] = self.object.document.vehicle
        return context

    def get_success_url(self):
        return reverse_lazy('document-history', kwargs={'pk': self.object.document.pk})

    def form_valid(self, form):
        messages.success(self.request, 'Renewal history entry deleted.')
        return super().form_valid(form)
