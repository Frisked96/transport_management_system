"""
API, AJAX autocomplete, and export views for Trips app.
"""
import hashlib
from decimal import Decimal
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from transport_mgmt.utils import get_local_time_str

try:
    import openpyxl
    from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
except ImportError:
    openpyxl = None

from trips.models import Trip, Route
from ledger.models import Party, CompanyAccount
from fleet.models import Vehicle, Tyre, TyreBrand
from drivers.models import Driver


@login_required
def reference_data(request):
    """
    Returns a JSON snapshot of all reference data (Parties, Vehicles, Routes, Drivers)
    to be cached in the browser's local storage.
    """
    parties = list(Party.objects.all().order_by('name').values('id', 'name'))
    vehicles = list(Vehicle.objects.filter(status=Vehicle.STATUS_ACTIVE).order_by('registration_plate').values('id', 'registration_plate', 'make_model', 'ownership'))
    routes = list(Route.objects.all().order_by('pickup_location').values('id', 'pickup_location', 'delivery_location', 'route_type', 'default_rate'))
    for r in routes:
        if r['route_type'] == 'intra':
            r['default_gst'] = 'IGST'
        elif r['route_type'] == 'none':
            r['default_gst'] = 'NONE'
        else:
            r['default_gst'] = 'GST'
    drivers = list(Driver.objects.select_related('user').all().order_by('user__username').values('id', 'user__username'))
    
    content_str = f"{parties}{vehicles}{routes}{drivers}"
    version_key = hashlib.md5(content_str.encode()).hexdigest()
    
    return JsonResponse({
        'parties': parties,
        'vehicles': vehicles,
        'routes': routes,
        'drivers': drivers,
        'version': version_key
    })


@login_required
def get_autocomplete_suggestions(request):
    """
    Returns suggestions for Select2.
    """
    field = request.GET.get('field')
    term = request.GET.get('term', request.GET.get('q', ''))
    
    results = []
    
    if field in ['pickup_location', 'delivery_location']:
        seen_names = set()
        query_filter = {f"{field}__icontains": term} if term else {}
        local_qs = Trip.objects.filter(**query_filter).values_list(field, flat=True).distinct().order_by(field)[:10]
        
        for name in local_qs:
            if name and name not in seen_names:
                results.append({
                    'id': name,
                    'text': f"🕒 {name}",
                    'source': 'history'
                })
                seen_names.add(name)

    elif field == 'tyre_brand':
        qs = TyreBrand.objects.filter(name__icontains=term).order_by('name')[:10]
        results = []
        for brand in qs:
            results.append({
                'id': brand.name, 
                'text': brand.name,
                'price': str(brand.suggestive_price) if brand.suggestive_price else ''
            })
        
        if not results:
            qs_old = Tyre.objects.filter(brand__icontains=term).values_list('brand', flat=True).distinct()[:10]
            results = [{'id': x, 'text': x, 'price': ''} for x in qs_old]
    elif field == 'tyre_size':
        qs = Tyre.objects.filter(size__icontains=term).values_list('size', flat=True).distinct()[:10]
        results = [{'id': x, 'text': x} for x in qs]
        
    return JsonResponse({'results': results})


@login_required
@permission_required('trips.view_trip', raise_exception=True)
def trip_export_excel(request):
    """
    Generates an Excel sheet for billed trips with vehicle and firm selection.
    """
    if not openpyxl:
        messages.error(request, "Excel export is not available. Please install 'openpyxl'.")
        return redirect('trip-list')

    # Get available vehicles and company accounts (issuers) for selection
    vehicles = Vehicle.objects.all().order_by('registration_plate')
    issuers = CompanyAccount.objects.all().order_by('name')
    
    if request.method == 'POST':
        selected_vehicles = request.POST.getlist('vehicles')
        selected_issuers = request.POST.getlist('issuers')
        start_date = request.POST.get('start_date')
        end_date = request.POST.get('end_date')

        if not selected_vehicles:
            messages.error(request, "Please select at least one vehicle.")
            return redirect('trip-export-excel')

        # Filter trips: only billed trips for selected vehicles
        trips = Trip.objects.with_billing_info().filter(
            annotated_is_billed=True,
            vehicle_id__in=selected_vehicles
        ).select_related('vehicle', 'party').prefetch_related('bills')

        # Filter by selected issuers (firms) if provided
        if selected_issuers:
            trips = trips.filter(bills__issuer_id__in=selected_issuers).distinct()

        if start_date:
            trips = trips.filter(date__gte=start_date)
        if end_date:
            trips = trips.filter(date__lte=end_date)

        # Sort by invoice number (numeric part) primarily, then by date
        trips = trips.order_by('bills__bill_no', 'date').distinct()

        # Create Workbook
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Billed Trips Report"

        # Define styles
        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="10B981", end_color="10B981", fill_type="solid")
        center_align = Alignment(horizontal='center')
        border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))

        # Header Row
        headers = [
            'Sr no.', 'Invoice No.', 'Date of Invoice', 'Party Name', 'GST No.', 
            'lo-Date', 'Truck no', 'From', 'To', 'Weight', 'Rate', 'Freight', 'GST (18%)', 'Total Taxable Value'
        ]
        
        for col_num, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_num)
            cell.value = header
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = center_align
            cell.border = border

        # Data Rows
        row_num = 2
        for idx, trip in enumerate(trips, 1):
            freight = trip.revenue or Decimal('0')
            gst = trip.gst_amount or Decimal('0')
            total_taxable = trip.total_revenue or Decimal('0')

            ws.cell(row=row_num, column=1, value=idx).border = border
            
            bill = trip.associated_bill
            ws.cell(row=row_num, column=2, value=bill.bill_number if bill else 'Unbilled').border = border
            ws.cell(row=row_num, column=3, value=bill.date.strftime('%d-%m-%Y') if bill and bill.date else '-').border = border
            ws.cell(row=row_num, column=4, value=trip.party.name if trip.party else 'N/A').border = border
            ws.cell(row=row_num, column=5, value=trip.party.gstin if trip.party else 'N/A').border = border
            ws.cell(row=row_num, column=6, value=trip.date.strftime('%d-%m-%Y') if trip.date else '-').border = border
            ws.cell(row=row_num, column=7, value=trip.vehicle.registration_plate).border = border
            ws.cell(row=row_num, column=8, value=trip.pickup_location or 'N/A').border = border
            ws.cell(row=row_num, column=9, value=trip.delivery_location or 'N/A').border = border
            ws.cell(row=row_num, column=10, value=float(trip.weight or 0)).border = border
            ws.cell(row=row_num, column=11, value=float(trip.rate_per_ton or 0)).border = border
            ws.cell(row=row_num, column=12, value=float(freight)).border = border
            ws.cell(row=row_num, column=13, value=float(gst)).border = border
            ws.cell(row=row_num, column=14, value=float(total_taxable)).border = border
            
            row_num += 1

        # Adjust column widths
        for col in ws.columns:
            max_length = 0
            column = col[0].column_letter
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = (max_length + 2)
            ws.column_dimensions[column].width = adjusted_width

        # Response
        response = HttpResponse(
            content_type='application/vnd.openpyxl.sheet',
        )
        filename = f"Billed_Trips_{get_local_time_str('%Y%m%d_%H%M%S')}.xlsx"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        wb.save(response)
        return response

    return render(request, 'trips/trip_export_form.html', {
        'vehicles': vehicles,
        'issuers': issuers
    })
