#!/usr/bin/env python
"""
Transport Management System - Setup Script

This script sets up the entire project including:
1. Creating and applying migrations
2. Creating user groups with permissions
3. Creating demo user accounts
4. Populating with sample data

Run this script after installing requirements:
    pip install -r requirements.txt
    python setup_project.py
"""

import os
import sys
import django
from datetime import datetime, timedelta
from decimal import Decimal

# Add the project to the Python path
project_path = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_path)

# Set up Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'transport_mgmt.settings')
django.setup()

from django.contrib.auth.models import User, Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

# Import our models
from trips.models import Trip
from fleet.models import Vehicle, MaintenanceLog
from ledger.models import FinancialRecord


def create_groups():
    """Create user groups with appropriate permissions"""
    print("Creating user groups...")
    
    # Define groups and their permissions
    groups_permissions = {
        'admin': {
            'description': 'System Owner - Full Access',
            'permissions': []
        },
        'manager': {
            'description': 'Operations Manager',
            'permissions': [
                # Trip permissions
                'add_trip', 'change_trip', 'view_trip',
                # Vehicle permissions
                'add_vehicle', 'change_vehicle', 'view_vehicle',
                # Maintenance log permissions
                'add_maintenancelog', 'change_maintenancelog', 'view_maintenancelog',
                'can_create_maintenance_log',
                # Financial record permissions
                'add_financialrecord', 'change_financialrecord', 'view_financialrecord',
                'can_view_financial_records', 'can_manage_financial_records',
                # Custom permissions
                'can_view_all_trips', 'can_view_all_vehicles',
                'can_view_manager_dashboard',
            ]
        },
        'supervisor': {
            'description': 'Team Supervisor',
            'permissions': [
                # Trip permissions (view only, status update)
                'view_trip', 'can_view_all_trips', 'can_update_trip_status',
                # Vehicle permissions (view only)
                'view_vehicle', 'can_view_all_vehicles',
                # Maintenance log permissions (create and view)
                'add_maintenancelog', 'view_maintenancelog', 'can_create_maintenance_log',
                # Financial record permissions (view only)
                'view_financialrecord', 'can_view_financial_records',
            ]
        },
        'driver': {
            'description': 'Employee/Driver',
            'permissions': [
                # Trip permissions (own trips only, status update)
                'view_trip', 'can_update_trip_status',
                'can_view_driver_dashboard',
                # Vehicle permissions (view only assigned)
                'view_vehicle',
            ]
        }
    }
    
    # Create groups and assign permissions
    for group_name, config in groups_permissions.items():
        group, created = Group.objects.get_or_create(name=group_name)
        if created:
            print(f"  Created group: {group_name}")
        else:
            print(f"  Group already exists: {group_name}")
        
        # Clear existing permissions
        group.permissions.clear()
        
        # Add permissions to group
        for perm_codename in config['permissions']:
            try:
                permission = Permission.objects.get(codename=perm_codename)
                group.permissions.add(permission)
                print(f"    Added permission: {perm_codename}")
            except Permission.DoesNotExist:
                print(f"    Warning: Permission '{perm_codename}' not found")
        
        group.save()
        print(f"  Group '{group_name}' configured successfully")
    
    print("✓ Groups created successfully\n")


def create_users():
    """Create demo user accounts"""
    print("Creating demo users...")
    
    users = [
        {'username': 'admin', 'password': 'admin123', 'group': 'admin', 'is_superuser': True, 'is_staff': True},
        {'username': 'manager', 'password': 'manager123', 'group': 'manager', 'is_superuser': False, 'is_staff': False},
        {'username': 'supervisor', 'password': 'supervisor123', 'group': 'supervisor', 'is_superuser': False, 'is_staff': False},
        {'username': 'driver', 'password': 'driver123', 'group': 'driver', 'is_superuser': False, 'is_staff': False},
    ]
    
    for user_data in users:
        # Check if user exists
        try:
            user = User.objects.get(username=user_data['username'])
            print(f"  User '{user_data['username']}' already exists")
        except User.DoesNotExist:
            # Create new user
            user = User.objects.create_user(
                username=user_data['username'],
                password=user_data['password']
            )
            print(f"  Created user: {user_data['username']}")
        
        # Update user properties
        user.is_superuser = user_data['is_superuser']
        user.is_staff = user_data['is_staff']
        user.save()
        
        # Add user to group
        try:
            group = Group.objects.get(name=user_data['group'])
            user.groups.clear()
            user.groups.add(group)
            print(f"    Assigned to group: {user_data['group']}")
        except Group.DoesNotExist:
            print(f"    Warning: Group '{user_data['group']}' not found")
    
    print("✓ Demo users created successfully\n")


def create_vehicles():
    """Create sample vehicles"""
    print("Creating sample vehicles...")
    
    vehicles = [
        {
            'registration_plate': 'TRK-001',
            'make_model': 'Ford F-150',
            'purchase_date': datetime(2022, 3, 15).date(),
            'status': Vehicle.STATUS_ACTIVE
        },
        {
            'registration_plate': 'TRK-002',
            'make_model': 'Chevrolet Silverado 2500',
            'purchase_date': datetime(2021, 8, 20).date(),
            'status': Vehicle.STATUS_ACTIVE
        },
        {
            'registration_plate': 'VAN-001',
            'make_model': 'Mercedes Sprinter',
            'purchase_date': datetime(2023, 1, 10).date(),
            'status': Vehicle.STATUS_ACTIVE
        },
        {
            'registration_plate': 'TRK-003',
            'make_model': 'Volvo FH16',
            'purchase_date': datetime(2020, 5, 25).date(),
            'status': Vehicle.STATUS_MAINTENANCE
        },
    ]
    
    created_vehicles = []
    for vehicle_data in vehicles:
        vehicle, created = Vehicle.objects.get_or_create(
            registration_plate=vehicle_data['registration_plate'],
            defaults=vehicle_data
        )
        if created:
            print(f"  Created vehicle: {vehicle.registration_plate}")
        else:
            print(f"  Vehicle already exists: {vehicle.registration_plate}")
        created_vehicles.append(vehicle)
    
    print("✓ Sample vehicles created successfully\n")
    return created_vehicles


def create_trips(vehicles, users):
    """Create sample trips"""
    print("Creating sample trips...")
    
    # Get driver users
    driver_user = User.objects.get(username='driver')
    
    trips = [
        {
            'trip_number': 'TRP-001',
            'driver': driver_user,
            'vehicle': vehicles[0],  # TRK-001
            'client_name': 'ABC Manufacturing Ltd.',
            'pickup_location': '123 Industrial Ave, Chicago, IL',
            'delivery_location': '456 Factory Rd, Milwaukee, WI',
            'scheduled_datetime': timezone.now() + timedelta(days=2),
            'status': Trip.STATUS_SCHEDULED,
            'notes': 'Fragile cargo, handle with care.',
            'created_by': User.objects.get(username='manager')
        },
        {
            'trip_number': 'TRP-002',
            'driver': driver_user,
            'vehicle': vehicles[1],  # TRK-002
            'client_name': 'XYZ Distribution Inc.',
            'pickup_location': '789 Warehouse Blvd, Detroit, MI',
            'delivery_location': '321 Distribution Center, Cleveland, OH',
            'scheduled_datetime': timezone.now() - timedelta(days=1),
            'status': Trip.STATUS_IN_PROGRESS,
            'notes': 'Urgent delivery required.',
            'created_by': User.objects.get(username='manager')
        },
        {
            'trip_number': 'TRP-003',
            'driver': driver_user,
            'vehicle': vehicles[2],  # VAN-001
            'client_name': 'Quick Delivery Service',
            'pickup_location': '555 Express Lane, Indianapolis, IN',
            'delivery_location': '777 Speedy Blvd, Columbus, OH',
            'scheduled_datetime': timezone.now() - timedelta(days=3),
            'status': Trip.STATUS_COMPLETED,
            'notes': 'Express delivery completed successfully.',
            'created_by': User.objects.get(username='manager')
        },
        {
            'trip_number': 'TRP-004',
            'driver': driver_user,
            'vehicle': vehicles[0],  # TRK-001
            'client_name': 'Mega Retail Corp.',
            'pickup_location': '888 Supermarket Dr, St. Louis, MO',
            'delivery_location': '999 Store Chain Ave, Kansas City, KS',
            'scheduled_datetime': timezone.now() + timedelta(days=5),
            'status': Trip.STATUS_SCHEDULED,
            'notes': 'Large shipment, confirm loading dock availability.',
            'created_by': User.objects.get(username='manager')
        },
        {
            'trip_number': 'TRP-005',
            'driver': driver_user,
            'vehicle': vehicles[1],  # TRK-002
            'client_name': 'Fresh Produce Co.',
            'pickup_location': '111 Farm Market, Lexington, KY',
            'delivery_location': '222 Grocery Outlet, Nashville, TN',
            'scheduled_datetime': timezone.now() - timedelta(days=5),
            'status': Trip.STATUS_COMPLETED,
            'notes': 'Temperature-sensitive cargo.',
            'created_by': User.objects.get(username='manager')
        },
        {
            'trip_number': 'TRP-006',
            'driver': driver_user,
            'vehicle': vehicles[2],  # VAN-001
            'client_name': 'Tech Solutions LLC',
            'pickup_location': '333 Innovation Park, Austin, TX',
            'delivery_location': '444 Tech Hub, Dallas, TX',
            'scheduled_datetime': timezone.now() + timedelta(days=1),
            'status': Trip.STATUS_SCHEDULED,
            'notes': 'High-value electronics, secure transport required.',
            'created_by': User.objects.get(username='manager')
        },
    ]
    
    created_trips = []
    for trip_data in trips:
        trip, created = Trip.objects.get_or_create(
            trip_number=trip_data['trip_number'],
            defaults=trip_data
        )
        if created:
            print(f"  Created trip: {trip.trip_number}")
        else:
            print(f"  Trip already exists: {trip.trip_number}")
        created_trips.append(trip)
    
    print("✓ Sample trips created successfully\n")
    return created_trips


def create_maintenance_logs(vehicles, users):
    """Create sample maintenance logs"""
    print("Creating sample maintenance logs...")
    
    maintenance_logs = [
        {
            'vehicle': vehicles[0],  # TRK-001
            'date': (timezone.now() - timedelta(days=30)).date(),
            'type': MaintenanceLog.TYPE_ROUTINE,
            'description': 'Oil change, filter replacement, brake inspection. All systems normal.',
            'cost': Decimal('250.00'),
            'service_provider': 'Quick Lube Express',
            'next_service_due': (timezone.now() + timedelta(days=90)).date(),
            'logged_by': users['manager']
        },
        {
            'vehicle': vehicles[1],  # TRK-002
            'date': (timezone.now() - timedelta(days=45)).date(),
            'type': MaintenanceLog.TYPE_REPAIR,
            'description': 'Transmission fluid leak fixed. Replaced seals and gaskets.',
            'cost': Decimal('850.00'),
            'service_provider': 'Heavy Duty Truck Repair',
            'next_service_due': (timezone.now() + timedelta(days=120)).date(),
            'logged_by': users['manager']
        },
        {
            'vehicle': vehicles[2],  # VAN-001
            'date': (timezone.now() - timedelta(days=15)).date(),
            'type': MaintenanceLog.TYPE_ROUTINE,
            'description': 'Tire rotation, alignment check, cabin air filter replacement.',
            'cost': Decimal('180.00'),
            'service_provider': 'Fleet Service Center',
            'next_service_due': (timezone.now() + timedelta(days=60)).date(),
            'logged_by': users['supervisor']
        },
        {
            'vehicle': vehicles[3],  # TRK-003 (in maintenance)
            'date': (timezone.now() - timedelta(days=5)).date(),
            'type': MaintenanceLog.TYPE_REPAIR,
            'description': 'Engine overhaul in progress. Replacing piston rings and gaskets.',
            'cost': Decimal('4500.00'),
            'service_provider': 'Volvo Authorized Service',
            'next_service_due': (timezone.now() + timedelta(days=180)).date(),
            'logged_by': users['manager']
        },
    ]
    
    for log_data in maintenance_logs:
        log, created = MaintenanceLog.objects.get_or_create(
            vehicle=log_data['vehicle'],
            date=log_data['date'],
            type=log_data['type'],
            defaults=log_data
        )
        if created:
            print(f"  Created maintenance log: {log.vehicle.registration_plate} - {log.date}")
        else:
            print(f"  Maintenance log already exists: {log.vehicle.registration_plate} - {log.date}")
    
    print("✓ Sample maintenance logs created successfully\n")


def create_financial_records(trips, users):
    """Create sample financial records"""
    print("Creating sample financial records...")
    
    financial_records = [
        # Income records
        {
            'date': (timezone.now() - timedelta(days=3)).date(),
            'associated_trip': trips[2],  # TRP-003 (completed)
            'category': FinancialRecord.CATEGORY_FREIGHT_INCOME,
            'amount': Decimal('2500.00'),
            'description': 'Payment received for express delivery service.',
            'recorded_by': users['manager']
        },
        {
            'date': (timezone.now() - timedelta(days=5)).date(),
            'associated_trip': trips[4],  # TRP-005 (completed)
            'category': FinancialRecord.CATEGORY_FREIGHT_INCOME,
            'amount': Decimal('1800.00'),
            'description': 'Payment for fresh produce delivery.',
            'recorded_by': users['manager']
        },
        {
            'date': (timezone.now() - timedelta(days=30)).date(),
            'associated_trip': None,
            'category': FinancialRecord.CATEGORY_FREIGHT_INCOME,
            'amount': Decimal('3200.00'),
            'description': 'Monthly contract payment from ABC Manufacturing.',
            'recorded_by': users['manager']
        },
        
        # Expense records
        {
            'date': (timezone.now() - timedelta(days=2)).date(),
            'associated_trip': trips[1],  # TRP-002 (in progress)
            'category': FinancialRecord.CATEGORY_FUEL_EXPENSE,
            'amount': Decimal('450.00'),
            'description': 'Fuel for Detroit to Cleveland route.',
            'recorded_by': users['supervisor']
        },
        {
            'date': (timezone.now() - timedelta(days=10)).date(),
            'associated_trip': None,
            'category': FinancialRecord.CATEGORY_MAINTENANCE_EXPENSE,
            'amount': Decimal('250.00'),
            'description': 'Oil change and routine service for TRK-001.',
            'recorded_by': users['manager']
        },
        {
            'date': (timezone.now() - timedelta(days=15)).date(),
            'associated_trip': None,
            'category': FinancialRecord.CATEGORY_DRIVER_PAYMENT,
            'amount': Decimal('1200.00'),
            'description': 'Weekly driver salary payment.',
            'recorded_by': users['manager']
        },
        {
            'date': (timezone.now() - timedelta(days=45)).date(),
            'associated_trip': None,
            'category': FinancialRecord.CATEGORY_MAINTENANCE_EXPENSE,
            'amount': Decimal('850.00'),
            'description': 'Transmission repair for TRK-002.',
            'recorded_by': users['manager']
        },
        {
            'date': (timezone.now() - timedelta(days=3)).date(),
            'associated_trip': None,
            'category': FinancialRecord.CATEGORY_OTHER,
            'amount': Decimal('150.00'),
            'description': 'Office supplies and administrative expenses.',
            'recorded_by': users['manager']
        },
    ]
    
    for record_data in financial_records:
        # Use date and amount as unique constraint for get_or_create
        record, created = FinancialRecord.objects.get_or_create(
            date=record_data['date'],
            amount=record_data['amount'],
            category=record_data['category'],
            defaults=record_data
        )
        if created:
            print(f"  Created financial record: {record.category} - ${record.amount}")
        else:
            print(f"  Financial record already exists: {record.category} - ${record.amount}")
    
    print("✓ Sample financial records created successfully\n")


def main():
    """Main setup function"""
    print("=" * 60)
    print("Transport Management System - Setup Script")
    print("=" * 60)
    print()
    
    try:
        with transaction.atomic():
            # Create groups
            create_groups()
            
            # Create users
            create_users()
            
            # Get user references
            users = {
                'admin': User.objects.get(username='admin'),
                'manager': User.objects.get(username='manager'),
                'supervisor': User.objects.get(username='supervisor'),
                'driver': User.objects.get(username='driver'),
            }
            
            # Create vehicles
            vehicles = create_vehicles()
            
            # Create trips
            trips = create_trips(vehicles, users)
            
            # Create maintenance logs
            create_maintenance_logs(vehicles, users)
            
            # Create financial records
            create_financial_records(trips, users)
            
            print("=" * 60)
            print("SETUP COMPLETED SUCCESSFULLY!")
            print("=" * 60)
            print()
            print("Demo accounts created:")
            print("  - admin / admin123 (Full system access)")
            print("  - manager / manager123 (Operations manager)")
            print("  - supervisor / supervisor123 (Team supervisor)")
            print("  - driver / driver123 (Employee/Driver)")
            print()
            print("To start the development server:")
            print("  python manage.py runserver")
            print()
            print("Access the system at: http://127.0.0.1:8000")
            print()
            
    except Exception as e:
        print(f"Error during setup: {e}")
        print("Setup failed. Please check the error above.")
        sys.exit(1)


if __name__ == '__main__':
    main()