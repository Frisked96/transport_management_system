# Attached / Market Vehicles Architecture Plan

This document outlines the architectural and workflow changes required to support "Attached" or "Market" trucks within the Transport Management System.

## 1. Core Objective
The system currently assumes all trucks are owned by the firm. We need a way to distinguish between "Owned Fleet" and "Market/Attached Trucks" and ensure that market truck owners (Vendors) are automatically credited for their work via the double-entry ledger.

## 2. Database Model Changes

### A. `Vehicle` Model Update
We will update the existing `Vehicle` model in `fleet/models.py` to include:
*   **Ownership Type:** A choice field toggling between `Owned (Company Fleet)` and `Attached (Market Vehicle)`. Default will be `Owned`.
*   **Vendor / Owner Link:** A `ForeignKey` to the `Party` model. This will only be required if the Ownership Type is set to `Attached`. The field will be restricted to parties categorized as `Creditor` (Suppliers).

### B. `Trip` Model Update
When a Trip uses an Attached vehicle, the company acts as a broker. The company charges the Customer (Freight Revenue) but owes the Truck Owner (Hire Cost). We will add:
*   **Vendor Hire Amount:** A `DecimalField` in `trips/models.py` to record exactly how much the broker agreed to pay the truck owner for that specific trip.

## 3. Automated Accounting Engine (Ledger Sync)

Currently, the `TripFinancialService.sync_trip_accrual()` method automatically creates an `Invoice` entry on the Customer's ledger when a trip is saved. We will upgrade this engine:

*   **Trigger:** If the trip uses an `Attached` vehicle, the engine will generate a *second* financial entry behind the scenes.
*   **Action:** It will create a **"Lorry Hire Expense"** record (as a General Expense).
*   **Result:** This expense will automatically credit the linked Vendor's ledger. Without any double data entry, the Customer ledger shows they owe you money, and the Vendor ledger accurately reflects that you owe *them* the hire amount.

## 4. Operational Workflow & UI

### Advances & Settlements
*   **Payment Out:** When an advance is paid to the market truck (e.g., at the loading point), the operator simply records a standard **"Payment Out"** to that Vendor in the Ledger module.
*   **Real-time Balance:** The system will deduct the payment from the Vendor's total accrued Lorry Hire balance, providing a real-time, exact view of outstanding payables to every market truck owner.

### Profitability Tracking
*   **Gross Margin Calculation:** Because both the Customer Revenue and the Vendor Hire Cost are tied to the same trip, the system can calculate the **Brokerage Margin** (Revenue minus Hire Cost).
*   **Dashboard Updates:** The manager dashboard and trip detail views will be updated to display this profit margin for all attached vehicle trips.
