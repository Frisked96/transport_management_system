# Transport Management System (TMS)

## Project Overview
This project is a comprehensive Transport Management System built with Django. It is designed to manage fleet operations, trip logistics, driver assignments, and financial accounting. The system provides tools for tracking vehicles, monitoring trip expenses and revenues, managing driver ledgers, and generating invoices and financial reports.

## Directory Structure

*   **`transport_mgmt/`**: The main project configuration directory, containing settings, URL routing, and WSGI/ASGI application entry points.
*   **`trips/`**: Handles core trip management logic, including trip creation, route details, revenue calculation, and expense tracking.
*   **`fleet/`**: Manages vehicle profiles, maintenance logs, fuel tracking, and tyre inventory.
*   **`drivers/`**: Contains driver profiles, license information, and personal financial ledgers (salary, allowances).
*   **`ledger/`**: The financial engine of the application, handling double-entry accounting, invoicing, payment tracking, and financial reporting.
*   **`documents/`**: A centralized module for uploading and managing documents (e.g., permits, licenses) with expiry tracking features.
*   **`templates/`**: Global HTML templates that define the layout and structure of the application's frontend.
*   **`static/`**: Static assets such as CSS stylesheets, JavaScript files, and images used across the application.
*   **`manage.py`**: Django's command-line utility for administrative tasks like running the server, making migrations, and creating superusers.

## High-Level Models Overview
To assist in understanding the application architecture:
*   **`trips`**: 
    *   `Trip`: Uses a single-leg structure to track end-to-end transport operations. Supports `fixed` and `per_ton` revenue models.
    *   `TripExpense`: Dynamically tracks custom expenses (e.g., Diesel, Toll) associated with a specific trip.
*   **`fleet`**: 
    *   `Vehicle`: Central entity for fleet assets.
    *   `MaintenanceTask` & `MaintenanceLog`: Decouples maintenance into recurring requirements (Tasks) and historical events (Logs).
    *   `Tyre` & `TyreLog`: Manages tyre inventory, including statuses (Mounted, Scrap, etc.), automatic movement tracking, and KM usage calculations based on completed trips.
    *   `FuelLog`: Tracks fueling events, synchronized with trip diesel entries.
*   **`drivers`**:
    *   `Driver`: Core driver profile extending the default User.
    *   `DriverTransaction`: Manages a driver's "Pocket/Wallet" balance (e.g., Salary, Allowance, Loan, Repayment).
*   **`ledger`**:
    *   `CompanyAccount`: Represents the firm(s) operating the system (issued from).
    *   `Party`: Represents clients/customers (billed to).
    *   `FinancialRecord`: The core of the double-entry system mapping Income/Expense transactions.
    *   `Bill`: Represents an Invoice, either aggregated from multiple `Trip`s or as a standalone Standard item.
*   **`documents`**:
    *   `Document`: A unified model for tracking expirations and storing scanned copies linked to Vehicles or Drivers.

## Core Financial Logic (Ledger & Billing)

*   **Accrual-Based Revenue**: Revenue is recorded as an "Invoice" type entry in the ledger as soon as a Trip is created or a Bill is generated. This represents earned income before cash is received.
*   **Ledger Hand-off**: Trip Payment exists in the ledger as either an individual entry (if unbilled) OR as part of a consolidated Bill entry (if billed). When a Bill is created, individual trip ledger entries are deleted, and a single consolidated entry for the Bill is created.
*   **Consolidated Billing & GST**: "Final" status Bills consolidate revenue and include the GST component in a single "Trip Payment" ledger entry. "Draft" status Bills show only the subtotal without GST in the ledger.
*   **Snapshot Principle**: Bill/Invoice models snapshot Firm details (Name, GSTIN, Address, Bank) from the `CompanyAccount` at the time of creation. This ensures historical invoices remain accurate even if the Firm's current details change.
*   **Balance Calculation**: `CompanyAccount` and `Party` balances are calculated as: `Opening Balance + Total Received - Total Expenses`. "Invoice" type records are excluded from balance calculations as they represent accruals, not actual cash flow.

## Performance Optimizations

*   **On-Demand Storage Access**: To prevent slow page loads when using Google Drive storage, document URLs are generated via a proxy view (`document-view`) only when clicked. Avoid calling `.url` on many file fields within a single template loop.
*   **Database Annotations**: Document list counts (Total, Expired, Expiring) are calculated using SQL-level `Count` and `Q` filters in `get_queryset` to avoid N+1 issues and Python-side loop overhead.
*   **Query Prefetching**: Always use `select_related` and `prefetch_related` for nested attributes (e.g., `driver__user`, `documents`) especially in global context processors like `document_alerts`.
