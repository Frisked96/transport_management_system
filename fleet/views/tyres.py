"""
Tyre and Tyre Brand views for Fleet app.
"""
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse_lazy
from django.contrib import messages
from django.db.models import Q
from django.http import HttpResponse, HttpResponseRedirect
from django.views.decorators.http import require_POST

from fleet.models import Vehicle, Tyre, TyreLog, TyreBrand
from fleet.forms import TyreForm, TyreLogForm, TyreBrandForm


class TyreListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = Tyre
    template_name = 'fleet/tyre_list.html'
    context_object_name = 'tyres'
    permission_required = 'fleet.view_tyre'
    paginate_by = 20

    def get_queryset(self):
        queryset = Tyre.objects.all().select_related('current_vehicle')
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(serial_number__icontains=search) |
                Q(brand__icontains=search) |
                Q(size__icontains=search) |
                Q(current_vehicle__registration_plate__icontains=search)
            )
        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)
            
        vehicle_id = self.request.GET.get('vehicle')
        if vehicle_id:
            queryset = queryset.filter(current_vehicle_id=vehicle_id)
            
        return queryset.order_by('brand', 'serial_number')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['vehicles'] = Vehicle.objects.all().order_by('registration_plate')
        context['selected_vehicle'] = self.request.GET.get('vehicle', '')
        return context


class TyreDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    model = Tyre
    template_name = 'fleet/tyre_detail.html'
    context_object_name = 'tyre'
    permission_required = 'fleet.view_tyre'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['logs'] = self.object.logs.all().order_by('-date', '-id')
        return context


class TyreCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = Tyre
    form_class = TyreForm
    template_name = 'fleet/tyre_form.html'
    permission_required = 'fleet.add_tyre'
    success_url = reverse_lazy('tyre-list')

    def get_initial(self):
        initial = super().get_initial()
        vehicle_id = self.request.GET.get('vehicle')
        position = self.request.GET.get('position')
        if vehicle_id:
            try:
                initial['current_vehicle'] = Vehicle.objects.get(pk=vehicle_id)
            except (Vehicle.DoesNotExist, ValueError):
                pass
        if position:
            initial['current_position'] = position
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['tyre_brands'] = TyreBrand.objects.all()
        return context

    def form_valid(self, form):
        form.instance._user = self.request.user
        try:
            self.object = form.save()
            messages.success(self.request, 'Tyre added to inventory.')
            return redirect(self.get_success_url())
        except Exception as e:
            messages.error(self.request, f"Storage upload failed: {str(e)}. Please check cloud credentials.")
            return self.form_invalid(form)


class TyreUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = Tyre
    form_class = TyreForm
    template_name = 'fleet/tyre_form.html'
    permission_required = 'fleet.change_tyre'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['tyre_brands'] = TyreBrand.objects.all()
        return context

    def get_success_url(self):
        return reverse_lazy('tyre-detail', kwargs={'pk': self.object.pk})

    def form_valid(self, form):
        form.instance._user = self.request.user
        try:
            self.object = form.save()
            messages.success(self.request, 'Tyre updated.')
            return redirect(self.get_success_url())
        except Exception as e:
            messages.error(self.request, f"Storage upload failed: {str(e)}. Please check cloud credentials.")
            return self.form_invalid(form)


class TyreDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    """
    Delete view for Tyres.
    Permission: Only admin and manager can delete tyres.
    Deletes the tyre, its logs, and its photo from storage.
    Configurable option to delete or keep linked financial ledger entries.
    """
    model = Tyre
    template_name = 'fleet/tyre_confirm_delete.html'
    permission_required = 'fleet.delete_tyre'
    success_url = reverse_lazy('tyre-list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['associated_records'] = self.object.financial_records.all()
        return context

    def form_valid(self, form):
        tyre = self.get_object()
        serial = tyre.serial_number

        # Handle linked financial records
        ledger_action = self.request.POST.get('ledger_action', 'keep')
        associated_records = tyre.financial_records.all()
        if associated_records.exists():
            if ledger_action == 'delete':
                count = associated_records.count()
                associated_records.delete()
                messages.info(self.request, f"{count} linked financial ledger entry was deleted.")
            else:
                count = associated_records.count()
                associated_records.update(associated_tyre=None)
                messages.info(self.request, f"{count} linked financial ledger entry was preserved (unlinked from tyre).")

        if tyre.photo:
            try:
                tyre.photo.delete(save=False)
            except Exception as e:
                messages.warning(self.request, f"Tyre deleted, but photo deletion from storage encountered: {str(e)}")
        messages.success(self.request, f'Tyre {serial} and all its history have been deleted.')
        return super().form_valid(form)

    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        return self.form_valid(None)


class TyreBrandListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = TyreBrand
    template_name = 'fleet/tyre_brand_list.html'
    context_object_name = 'brands'
    permission_required = 'fleet.view_tyrebrand'
    paginate_by = 25

    def get_queryset(self):
        queryset = TyreBrand.objects.all().order_by('name')
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(name__icontains=search)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search_term'] = self.request.GET.get('search', '')
        return context


class TyreBrandCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = TyreBrand
    form_class = TyreBrandForm
    template_name = 'fleet/tyre_brand_form.html'
    permission_required = 'fleet.add_tyrebrand'
    success_url = reverse_lazy('tyre-brand-list')

    def form_valid(self, form):
        messages.success(self.request, 'Tyre brand added successfully.')
        return super().form_valid(form)


class TyreBrandUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = TyreBrand
    form_class = TyreBrandForm
    template_name = 'fleet/tyre_brand_form.html'
    permission_required = 'fleet.change_tyrebrand'
    success_url = reverse_lazy('tyre-brand-list')

    def form_valid(self, form):
        messages.success(self.request, 'Tyre brand updated successfully.')
        return super().form_valid(form)


class TyreBrandDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    model = TyreBrand
    template_name = 'fleet/tyre_brand_confirm_delete.html'
    permission_required = 'fleet.delete_tyrebrand'
    success_url = reverse_lazy('tyre-brand-list')

    def delete(self, request, *args, **kwargs):
        messages.success(self.request, 'Tyre brand deleted successfully.')
        return super().delete(request, *args, **kwargs)


class TyreLogCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = TyreLog
    form_class = TyreLogForm
    template_name = 'fleet/tyre_log_form.html'
    permission_required = 'fleet.change_tyre'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        action = self.request.GET.get('action')
        if action:
            context['action_title'] = f"{action} Tyre"
        else:
            context['action_title'] = "Log Tyre Action"
        return context

    def get_initial(self):
        initial = super().get_initial()
        tyre_id = self.request.GET.get('tyre')
        action = self.request.GET.get('action')
        vehicle_id = self.request.GET.get('vehicle')
        position = self.request.GET.get('position')
        if tyre_id:
            tyre = get_object_or_404(Tyre, pk=tyre_id)
            initial['tyre'] = tyre
            if action == 'Dismount' and tyre.current_vehicle:
                initial['vehicle'] = tyre.current_vehicle
                initial['position'] = tyre.current_position
        if action:
            initial['action'] = action
        if vehicle_id and not initial.get('vehicle'):
            try:
                initial['vehicle'] = Vehicle.objects.get(pk=vehicle_id)
            except (Vehicle.DoesNotExist, ValueError):
                pass
        if position and not initial.get('position'):
            initial['position'] = position
        return initial

    def form_valid(self, form):
        tyre = form.cleaned_data['tyre']
        action = form.cleaned_data['action']
        
        # Synchronize Tyre model status
        if action == TyreLog.ACTION_MOUNT:
            # If tyre was already mounted on another vehicle, log dismount from old vehicle first
            old_vehicle = tyre.current_vehicle
            old_position = tyre.current_position
            if old_vehicle and old_vehicle != form.instance.vehicle:
                TyreLog.objects.create(
                    tyre=tyre,
                    action=TyreLog.ACTION_DISMOUNT,
                    vehicle=old_vehicle,
                    position=old_position,
                    date=form.instance.date,
                    notes=f"Auto-dismount prior to mounting on {form.instance.vehicle.registration_plate}",
                    logged_by=self.request.user
                )
            tyre.current_vehicle = form.instance.vehicle
            tyre.current_position = form.instance.position
            tyre.status = Tyre.STATUS_MOUNTED
        elif action == TyreLog.ACTION_DISMOUNT:
            tyre.current_vehicle = None
            tyre.current_position = ''
            tyre.status = Tyre.STATUS_IN_STOCK
        elif action == TyreLog.ACTION_REPAIR or action == TyreLog.ACTION_REMOLD:
            tyre.current_vehicle = None
            tyre.current_position = ''
            tyre.status = Tyre.STATUS_REPAIR
        elif action == TyreLog.ACTION_SCRAP:
            tyre.current_vehicle = None
            tyre.current_position = ''
            tyre.status = Tyre.STATUS_SCRAP
        elif action == TyreLog.ACTION_ROTATION:
            tyre.current_position = form.instance.position

        tyre._skip_auto_log = True
        tyre.save()
        
        form.instance.logged_by = self.request.user
        messages.success(self.request, f'Tyre action {action} processed.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('tyre-detail', kwargs={'pk': self.object.tyre.pk})


@login_required
def tyre_log_delete(request, pk):
    """
    Deletes a specific tyre log. Only superusers can delete logs.
    """
    log = get_object_or_404(TyreLog, pk=pk)
    tyre_pk = log.tyre.pk
    
    if not request.user.is_superuser:
        messages.error(request, 'Only superusers can delete tyre logs.')
        return redirect('tyre-detail', pk=tyre_pk)
        
    if request.method == 'POST':
        log.delete()
        messages.success(request, 'Tyre log deleted successfully.')
        
    return redirect('tyre-detail', pk=tyre_pk)


@require_POST
@login_required
@permission_required('fleet.change_tyre', raise_exception=True)
def tyre_quick_action(request, pk, action):
    """
    Handles simple status changes without a form.
    Requires POST and change_tyre permission.
    """
    tyre = get_object_or_404(Tyre, pk=pk)
    tyre._user = request.user
    
    if action == 'Dismount':
        tyre.current_vehicle = None
        tyre.current_position = ''
        tyre.status = Tyre.STATUS_IN_STOCK
    elif action == 'Repair':
        tyre.current_vehicle = None
        tyre.current_position = ''
        tyre.status = Tyre.STATUS_REPAIR
    elif action == 'Scrap':
        tyre.current_vehicle = None
        tyre.current_position = ''
        tyre.status = Tyre.STATUS_SCRAP
    elif action == 'Stock':
        tyre.status = Tyre.STATUS_IN_STOCK
        
    tyre.save()
    messages.success(request, f'Tyre action {action} processed successfully.')
    
    referer = request.META.get('HTTP_REFERER')
    if referer:
        return redirect(referer)
    return redirect('tyre-detail', pk=pk)


@login_required
def tyre_photo_serve(request, pk):
    """
    Redirects to the presigned storage URL for the tyre photo.
    Allows the browser to fetch directly from CDN edge with zero web server RAM buffering.
    """
    tyre = get_object_or_404(Tyre, pk=pk)
    if not tyre.photo or not tyre.photo.name:
        return HttpResponse(status=404)

    try:
        url = tyre.photo.url
        if url:
            return HttpResponseRedirect(str(url))
        return HttpResponse(status=404)
    except Exception as e:
        return HttpResponse(f"Error accessing storage: {str(e)}", status=500)
