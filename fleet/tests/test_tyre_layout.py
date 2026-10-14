import os
import shutil
import tempfile
from decimal import Decimal
from datetime import timedelta
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.contrib.auth.models import User, Permission
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from fleet.models import Vehicle, MaintenanceRecord, TyreBrand, Tyre, TyreLog
from fleet.forms import VehicleForm, MaintenanceRecordForm, TyreForm
from ledger.models import Party, FinancialRecord, TransactionCategory
from fleet.services import (
    get_vehicle_tyre_layout,
    normalize_tyre_position,
    TYRE_POSITION_MAP,
)

class TyrePositionDiagramAndLayoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username='tyre_tester', password='password123')
        self.client = Client()
        self.client.login(username='tyre_tester', password='password123')
        self.vehicle = Vehicle.objects.create(registration_plate="MH12AB1801", make_model="Tata Signa 5530.S")

    def test_vehicle_tyre_layout_structure(self):
        """Test vehicle tyre layout data structure has 18 running + 1 stepney wheel positions"""
        layout = self.vehicle.tyre_layout
        stats = layout['stats']
        self.assertEqual(stats['total_positions'], 19)
        self.assertEqual(stats['total_mounted'], 0)
        self.assertEqual(stats['total_vacant'], 19)
        self.assertFalse(layout['stepney']['is_mounted'])
        self.assertEqual(layout['stepney']['code'], 'STP')

        # Check Horse Axles: 2 axles, 6 wheel positions
        horse_axles = layout['horse']['axles']
        self.assertEqual(len(horse_axles), 2)
        # Axle 1: Steer (2 wheels: FL, FR)
        self.assertEqual(horse_axles[0]['wheels']['left']['code'], 'FL')
        self.assertEqual(horse_axles[0]['wheels']['right']['code'], 'FR')
        # Axle 2: Drive Duals (4 wheels: DLO, DLI, DRI, DRO)
        drive_codes = [horse_axles[1]['wheels'][k]['code'] for k in ['left_outer', 'left_inner', 'right_inner', 'right_outer']]
        self.assertEqual(drive_codes, ['DLO', 'DLI', 'DRI', 'DRO'])

        # Check Trailer Axles: 3 axles, 12 wheel positions
        trailer_axles = layout['trailer']['axles']
        self.assertEqual(len(trailer_axles), 3)
        t1_codes = [trailer_axles[0]['wheels'][k]['code'] for k in ['left_outer', 'left_inner', 'right_inner', 'right_outer']]
        t2_codes = [trailer_axles[1]['wheels'][k]['code'] for k in ['left_outer', 'left_inner', 'right_inner', 'right_outer']]
        t3_codes = [trailer_axles[2]['wheels'][k]['code'] for k in ['left_outer', 'left_inner', 'right_inner', 'right_outer']]
        self.assertEqual(t1_codes, ['T1_LO', 'T1_LI', 'T1_RI', 'T1_RO'])
        self.assertEqual(t2_codes, ['T2_LO', 'T2_LI', 'T2_RI', 'T2_RO'])
        self.assertEqual(t3_codes, ['T3_LO', 'T3_LI', 'T3_RI', 'T3_RO'])

    def test_vehicle_tyre_layout_mounted_mapping(self):
        """Test that tyres mounted to canonical codes and legacy aliases correctly map to slots"""
        from fleet.models import Tyre
        t1 = Tyre.objects.create(serial_number="TYRE-001", brand="Apollo", current_vehicle=self.vehicle, current_position="FL")
        t2 = Tyre.objects.create(serial_number="TYRE-002", brand="JK", current_vehicle=self.vehicle, current_position="DLO")
        t3 = Tyre.objects.create(serial_number="TYRE-003", brand="Bridgestone", current_vehicle=self.vehicle, current_position="T1_RO")
        t4 = Tyre.objects.create(serial_number="TYRE-004", brand="CEAT", current_vehicle=self.vehicle, current_position="STP")
        # Legacy alias: 'Front Right' maps to FR
        t5 = Tyre.objects.create(serial_number="TYRE-005", brand="MRF", current_vehicle=self.vehicle, current_position="Front Right")

        layout = self.vehicle.tyre_layout
        stats = layout['stats']
        self.assertEqual(stats['total_mounted'], 5)
        self.assertEqual(stats['total_vacant'], 14)
        self.assertTrue(layout['stepney']['is_mounted'])
        self.assertEqual(layout['stepney']['tyre'], t4)

        # Slot checks
        horse_axles = layout['horse']['axles']
        self.assertTrue(horse_axles[0]['wheels']['left']['is_mounted'])
        self.assertEqual(horse_axles[0]['wheels']['left']['tyre'], t1)
        self.assertTrue(horse_axles[0]['wheels']['right']['is_mounted'])
        self.assertEqual(horse_axles[0]['wheels']['right']['tyre'], t5)

        self.assertTrue(horse_axles[1]['wheels']['left_outer']['is_mounted'])
        self.assertEqual(horse_axles[1]['wheels']['left_outer']['tyre'], t2)

        trailer_axles = layout['trailer']['axles']
        self.assertTrue(trailer_axles[0]['wheels']['right_outer']['is_mounted'])
        self.assertEqual(trailer_axles[0]['wheels']['right_outer']['tyre'], t3)
        self.assertFalse(trailer_axles[1]['wheels']['left_outer']['is_mounted'])
        self.assertIsNone(trailer_axles[1]['wheels']['left_outer']['tyre'])

    def test_fixed_position_form_validation(self):
        """Test that TyreForm and TyreLogForm enforce choices and check for position collisions"""
        from fleet.models import Tyre, TyreLog, TyreBrand
        from fleet.forms import TyreForm, TyreLogForm

        TyreBrand.objects.create(name='MRF')
        TyreBrand.objects.create(name='Apollo')

        # 1. Invalid choice rejected
        form_invalid = TyreForm(data={
            'serial_number': 'T-INV',
            'brand': 'MRF',
            'purchase_cost': '10000.00',
            'status': Tyre.STATUS_MOUNTED,
            'current_vehicle': self.vehicle.pk,
            'current_position': 'INVALID_POSITION',
        })
        self.assertFalse(form_invalid.is_valid())
        self.assertIn('current_position', form_invalid.errors)

        # 2. Valid choice accepted
        form_valid = TyreForm(data={
            'serial_number': 'T-VAL-1',
            'brand': 'MRF',
            'purchase_cost': '10000.00',
            'status': Tyre.STATUS_MOUNTED,
            'current_vehicle': self.vehicle.pk,
            'current_position': 'FL',
        })
        self.assertTrue(form_valid.is_valid(), form_valid.errors)
        tyre1 = form_valid.save()
        self.assertEqual(tyre1.current_position, 'FL')

        # 3. Collision detected when mounting another tyre in already occupied position
        form_collision = TyreForm(data={
            'serial_number': 'T-COLLIDE',
            'brand': 'Apollo',
            'purchase_cost': '12000.00',
            'status': Tyre.STATUS_MOUNTED,
            'current_vehicle': self.vehicle.pk,
            'current_position': 'FL',
        })
        self.assertFalse(form_collision.is_valid())
        self.assertIn('current_position', form_collision.errors)
        self.assertIn("already occupied", form_collision.errors['current_position'][0])

        # 4. TyreLogForm collision detection on mount
        stock_tyre = Tyre.objects.create(serial_number='T-STOCK', brand='Apollo')
        log_collide = TyreLogForm(data={
            'tyre': stock_tyre.pk,
            'date': timezone.now().date(),
            'action': TyreLog.ACTION_MOUNT,
            'vehicle': self.vehicle.pk,
            'position': 'FL',
        })
        self.assertFalse(log_collide.is_valid())
        self.assertIn('position', log_collide.errors)

    def test_tyre_query_param_prefill_and_views(self):
        """Test GET views prefill vehicle and position from URL query parameters"""
        from fleet.models import Tyre, TyreLog

        # TyreCreateView with query params
        resp_create = self.client.get(
            reverse('tyre-create') + f"?vehicle={self.vehicle.pk}&position=T1_LO"
        )
        self.assertEqual(resp_create.status_code, 200)
        self.assertEqual(resp_create.context['form'].initial.get('current_vehicle'), self.vehicle)
        self.assertEqual(resp_create.context['form'].initial.get('current_position'), 'T1_LO')

        # TyreLogCreateView with query params
        resp_log = self.client.get(
            reverse('tyre-log-create') + f"?vehicle={self.vehicle.pk}&position=T2_RO&action=Mount"
        )
        self.assertEqual(resp_log.status_code, 200)
        self.assertEqual(resp_log.context['form'].initial.get('vehicle'), self.vehicle)
        self.assertEqual(resp_log.context['form'].initial.get('position'), 'T2_RO')
        self.assertEqual(resp_log.context['form'].initial.get('action'), TyreLog.ACTION_MOUNT)

    def test_vehicle_detail_view_renders_diagram(self):
        """Test vehicle detail view includes tyre_layout and renders diagram sections"""
        from fleet.models import Tyre
        Tyre.objects.create(serial_number="T-DETAIL-1", brand="MRF", current_vehicle=self.vehicle, current_position="FL")

        resp = self.client.get(reverse('vehicle-detail', args=[self.vehicle.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('tyre_layout', resp.context)
        content = resp.content.decode('utf-8')
        self.assertIn("Tyre Positions", content)
        self.assertIn("Horse", content)
        self.assertIn("Trailer", content)
        self.assertIn("T-DETAIL-1", content)



