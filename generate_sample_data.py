import os
import django
import random
from decimal import Decimal
from datetime import timedelta
from faker import Faker

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "transport_mgmt.settings")
django.setup()

from django.contrib.auth.models import User
from fleet.models import Vehicle, Tyre, MaintenanceRecord
from drivers.models import Driver, DriverTransaction
from ledger.models import Party, CompanyAccount, TransactionCategory, FinancialRecord, Bill
from trips.models import Route, Trip
from documents.models import Document
from django.utils import timezone
from django.db import transaction

fake = Faker('en_IN')

@transaction.atomic
def run():
    print("Clearing old data...")
    # Temporarily disconnect some signals if they cause issues, but atomic block usually helps
    Document.objects.all().delete()
    FinancialRecord.objects.all().delete()
    Bill.objects.all().delete()
    Trip.objects.all().delete()
    DriverTransaction.objects.all().delete()
    Tyre.objects.all().delete()
    MaintenanceRecord.objects.all().delete()
    Route.objects.all().delete()
    Driver.objects.all().delete()
    Vehicle.objects.all().delete()
    Party.objects.all().delete()
    CompanyAccount.objects.all().delete()

    print("Creating Categories...")
    income_cats = ['Freight Revenue', 'Other Income', 'Credit Note']
    expense_cats = ['Fuel', 'Toll', 'Maintenance', 'Driver Salary', 'Driver Allowance', 'Payment Out', 'Deductions', 'TDS', 'Debit Note']
    for cat in income_cats:
        TransactionCategory.objects.get_or_create(name=cat, defaults={'type': TransactionCategory.TYPE_INCOME})
    for cat in expense_cats:
        TransactionCategory.objects.get_or_create(name=cat, defaults={'type': TransactionCategory.TYPE_EXPENSE})

    print("Creating Company Accounts...")
    firm1 = CompanyAccount.objects.create(
        name=fake.company() + " Transport Ltd",
        address=fake.address(),
        phone_number=fake.phone_number(),
        gstin=fake.bothify(text='??#####????#?#?').upper(),
        pan=fake.bothify(text='?????####?').upper(),
        bank_name="HDFC Bank",
        bank_branch="Main Branch",
        account_number=fake.bban(),
        ifsc_code="HDFC0001234",
        authorized_signatory=fake.name()
    )

    print("Creating Parties...")
    parties = []
    for _ in range(15):
        party = Party.objects.create(
            name=fake.company(),
            party_type=Party.TYPE_DEBTOR if random.random() > 0.3 else Party.TYPE_CREDITOR,
            phone_number=fake.phone_number(),
            state=fake.state(),
            address=fake.address(),
            gstin=fake.bothify(text='??#####????#?#?').upper()
        )
        parties.append(party)
    debtors = [p for p in parties if p.party_type == Party.TYPE_DEBTOR]

    print("Creating Vehicles...")
    vehicles = []
    for _ in range(10):
        vehicle = Vehicle.objects.create(
            registration_plate=fake.bothify(text='??-##-??-####').upper(),
            make_model=random.choice(["Tata Prima", "Ashok Leyland", "BharatBenz", "Mahindra Blazo"]),
            purchase_date=fake.date_between(start_date='-5y', end_date='-1y'),
            current_odometer=random.randint(50000, 300000),
            status=Vehicle.STATUS_ACTIVE
        )
        vehicles.append(vehicle)
        
        # Add some tyres
        for i in range(6):
            Tyre.objects.create(
                serial_number=fake.bothify(text='TYRE-#######').upper(),
                brand=random.choice(["MRF", "Apollo", "CEAT", "Michelin"]),
                size="295/90R20",
                current_vehicle=vehicle,
                current_position=f"Pos-{i+1}"
            )
            
        # Add maintenance
        MaintenanceRecord.objects.create(
            vehicle=vehicle,
            name="General Service",
            is_completed=True,
            completion_date=timezone.now().date() - timedelta(days=random.randint(10, 100)),
            completion_km=vehicle.current_odometer - random.randint(1000, 5000),
            cost=Decimal(random.randint(5000, 20000)),
            service_provider=fake.company()
        )

    print("Creating Drivers...")
    drivers = []
    for i in range(12):
        username = f"driver_{fake.user_name()}_{i}"
        user, _ = User.objects.get_or_create(username=username, defaults={'first_name': fake.first_name(), 'last_name': fake.last_name()})
        user.set_password('password123')
        user.save()
        
        driver = Driver.objects.create(
            user=user,
            employee_id=f"EMP{1000+i}",
            license_number=fake.bothify(text='??## ########').upper(),
            phone_number=fake.phone_number(),
            address=fake.address(),
            joined_date=fake.date_between(start_date='-3y', end_date='today')
        )
        drivers.append(driver)

    print("Creating Routes...")
    routes = []
    cities = ["Mumbai", "Delhi", "Bangalore", "Chennai", "Kolkata", "Ahmedabad", "Pune", "Jaipur", "Surat", "Lucknow", "Nagpur", "Indore"]
    for _ in range(25):
        pickup = random.choice(cities)
        delivery = random.choice([c for c in cities if c != pickup])
        route, _ = Route.objects.get_or_create(
            pickup_location=pickup,
            delivery_location=delivery,
            route_type=random.choice([Route.ROUTE_TYPE_LOCAL, Route.ROUTE_TYPE_INTRA, Route.ROUTE_TYPE_NONE]),
            defaults={'default_rate': Decimal(random.randint(1500, 5000))}
        )
        routes.append(route)

    print("Creating Trips...")
    admin_user = User.objects.filter(is_superuser=True).first()
    if not admin_user:
        admin_user = User.objects.create_superuser('admin', 'admin@example.com', 'admin')

    for _ in range(80):
        vehicle = random.choice(vehicles)
        driver = random.choice(drivers)
        party = random.choice(debtors) if debtors else random.choice(parties)
        route = random.choice(routes)
        
        trip = Trip(
            lr_no=fake.bothify(text='LR-####'),
            revenue_type=random.choice([Trip.REVENUE_PER_TON, Trip.REVENUE_FIXED]),
            driver=driver,
            vehicle=vehicle,
            date=fake.date_time_between(start_date='-6m', end_date='now', tzinfo=timezone.get_current_timezone()),
            party=party,
            route=route,
            weight=Decimal(random.randint(10, 40)) if random.random() > 0.2 else None,
            rate_per_ton=route.default_rate,
            created_by=admin_user
        )
        if trip.revenue_type == Trip.REVENUE_FIXED:
            trip.rate_per_ton = Decimal(random.randint(15000, 50000))
            trip.weight = None
        else:
            if not trip.weight: trip.weight = Decimal('25')
        
        trip.save()

    print("Database successfully populated with random sample data!")

if __name__ == '__main__':
    run()
