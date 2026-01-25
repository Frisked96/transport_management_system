"""
Fuel views for Fleet application
"""
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.contrib import messages
from .models import Vehicle, FuelLog
from trips.models import Trip
from django import forms

class FuelLogForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control'})

    class Meta:
        model = FuelLog
        fields = ['date', 'liters', 'rate', 'total_cost', 'odometer', 'trip']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
        }

class FuelLogCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = FuelLog
    form_class = FuelLogForm
    template_name = 'fleet/fuellog_form.html'
    permission_required = 'fleet.add_fuellog'

    def dispatch(self, request, *args, **kwargs):
        self.vehicle = get_object_or_404(Vehicle, pk=kwargs['vehicle_pk'])
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.vehicle = self.vehicle
        response = super().form_valid(form)

        # Update vehicle odometer if new reading is higher
        if form.instance.odometer > self.vehicle.current_odometer:
            self.vehicle.current_odometer = form.instance.odometer
            self.vehicle.save()

        messages.success(self.request, 'Fuel log added successfully!')
        return response

    def get_success_url(self):
        return reverse_lazy('vehicle-detail', kwargs={'pk': self.vehicle.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['vehicle'] = self.vehicle
        return context

class FuelLogUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = FuelLog
    form_class = FuelLogForm
    template_name = 'fleet/fuellog_form.html'
    permission_required = 'fleet.change_fuellog'

    def get_success_url(self):
        messages.success(self.request, 'Fuel log updated successfully!')
        return reverse_lazy('vehicle-detail', kwargs={'pk': self.object.vehicle.pk})

class FuelLogDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    model = FuelLog
    template_name = 'fleet/fuellog_confirm_delete.html'
    permission_required = 'fleet.delete_fuellog'

    def get_success_url(self):
        messages.success(self.request, 'Fuel log deleted successfully!')
        return reverse_lazy('vehicle-detail', kwargs={'pk': self.object.vehicle.pk})
