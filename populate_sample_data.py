import os
import django
import datetime

# Set up Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'transport_mgmt.settings')
django.setup()

from ledger.models import CompanyAccount, Party
from fleet.models import Vehicle

def create_sample_data():
    print("Creating sample firm accounts (CompanyAccount)...")
    firms = [
        {
            "name": "Global Logistics Pvt Ltd",
            "address": "123 Business Hub, Mumbai, Maharashtra",
            "phone_number": "9876543210",
            "gstin": "27AAACG1234A1Z5",
            "pan": "AAACG1234A",
            "bank_name": "HDFC Bank",
            "account_number": "50100123456789",
            "ifsc_code": "HDFC0000123",
            "account_holder_name": "Global Logistics Pvt Ltd",
            "authorized_signatory": "Rajesh Kumar",
            "opening_balance": 500000
        },
        {
            "name": "Skyline Transports",
            "address": "45 Industrial Area, Gurgaon, Haryana",
            "phone_number": "9988776655",
            "gstin": "06AADCS5678B1Z2",
            "pan": "AADCS5678B",
            "bank_name": "ICICI Bank",
            "account_number": "000405001234",
            "ifsc_code": "ICIC0000004",
            "account_holder_name": "Skyline Transports",
            "authorized_signatory": "Anita Sharma",
            "opening_balance": 250000
        }
    ]

    for firm_data in firms:
        firm, created = CompanyAccount.objects.get_or_create(
            name=firm_data["name"],
            defaults=firm_data
        )
        if created:
            print(f"  Created firm: {firm.name}")
        else:
            print(f"  Firm already exists: {firm.name}")

    print("\nCreating sample parties...")
    parties = [
        {
            "name": "Tata Steel Ltd",
            "phone_number": "9123456789",
            "state": "Jharkhand",
            "address": "Jamshedpur, Jharkhand",
            "gstin": "20AAACT1234B1Z1",
            "bank_name": "State Bank of India",
            "account_number": "33445566778",
            "ifsc_code": "SBIN0000001",
            "account_holder_name": "Tata Steel Ltd"
        },
        {
            "name": "Reliance Industries",
            "phone_number": "9234567890",
            "state": "Gujarat",
            "address": "Jamnagar, Gujarat",
            "gstin": "24AAACR4321C1Z0",
            "bank_name": "Axis Bank",
            "account_number": "910010012345678",
            "ifsc_code": "UTIB0000005",
            "account_holder_name": "Reliance Industries"
        },
        {
            "name": "Amazon Seller Services",
            "phone_number": "9345678901",
            "state": "Karnataka",
            "address": "Bangalore, Karnataka",
            "gstin": "29AAACA9876D1Z9",
            "bank_name": "Citibank",
            "account_number": "12345678",
            "ifsc_code": "CITI0000001",
            "account_holder_name": "Amazon India"
        }
    ]

    for party_data in parties:
        party, created = Party.objects.get_or_create(
            name=party_data["name"],
            defaults=party_data
        )
        if created:
            print(f"  Created party: {party.name}")
        else:
            print(f"  Party already exists: {party.name}")

    print("\nCreating sample vehicles...")
    vehicles = [
        {
            "registration_plate": "MH 01 AB 1234",
            "make_model": "Tata Prima 4923.S",
            "purchase_date": datetime.date(2022, 5, 15),
            "current_odometer": 45000,
            "status": "Active"
        },
        {
            "registration_plate": "HR 55 CD 5678",
            "make_model": "Ashok Leyland 3520",
            "purchase_date": datetime.date(2023, 1, 10),
            "current_odometer": 28000,
            "status": "Active"
        },
        {
            "registration_plate": "MH 43 EF 9012",
            "make_model": "BharatBenz 2823R",
            "purchase_date": datetime.date(2021, 11, 20),
            "current_odometer": 62000,
            "status": "Maintenance"
        },
        {
            "registration_plate": "KA 01 GH 3456",
            "make_model": "Mahindra Blazo X 49",
            "purchase_date": datetime.date(2023, 8, 5),
            "current_odometer": 12000,
            "status": "Active"
        }
    ]

    for vehicle_data in vehicles:
        vehicle, created = Vehicle.objects.get_or_create(
            registration_plate=vehicle_data["registration_plate"],
            defaults=vehicle_data
        )
        if created:
            print(f"  Created vehicle: {vehicle.registration_plate}")
        else:
            print(f"  Vehicle already exists: {vehicle.registration_plate}")

if __name__ == "__main__":
    create_sample_data()
    print("\nSample data creation completed!")
