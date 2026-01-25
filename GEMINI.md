# Application State: Transport Management System

## Core Apps & Features

### 1. Trip Management (`trips`)
*   **Trip Lifecycle:** Create, Update, Status Tracking (In Progress, Completed, Cancelled).
*   **Fields:** Trip Number (Auto-generated `PLATE-SEQ/MM/YYYY`), Vehicle, Driver, Party, Route (Pickup/Delivery), Weight, Rate per Ton.
*   **Financials:**
    *   **Revenue:** Auto-calculated (`Weight * Rate`).
    *   **Expense Management:** dedicated interface for bulk adding/editing expenses with dynamic rows.
    *   **Payment Status:** Real-time tracking (Unpaid, Partial, Paid) based on Ledger records.
    *   **Profit:** Net profit calculation per trip.
*   **Mobile Optimizations:** Responsive views for details and expense management; Auto-save for draft expenses.
*   **Autocomplete:** AJAX-based suggestions for locations and expense names.

### 2. Fleet Management (`fleet`)
*   **Vehicles:**
    *   **Profile:** Registration, Model, Odometer, Status (Active, Maintenance, Retired).
    *   **Dashboard:** Central view for Maintenance, Fuel, Documents, and Tyre history per vehicle.
*   **Maintenance:**
    *   **Logs:** Service Date, Type (Oil Change, Tyre Work, Repair, etc.), Cost, Provider.
    *   **Reminders:** Tracking via Date or Odometer (Mileage-based due checking).
*   **Fuel Tracking:**
    *   **Logs:** Date, Odometer, Liters, Rate, Total Cost.
    *   **Trip Link:** Option to associate fuel entries with specific trips.
*   **Tyre Inventory (NEW):**
    *   **Inventory:** Individual Tyre tracking (Serial #, Brand, Size, Purchase Cost).
    *   **Lifecycle:** Log actions: Mount (assign to Vehicle + Position), Dismount, Repair, Scrap.
    *   **History:** Complete audit trail of every tyre's movement.
    *   **Autocomplete:** Smart suggestions for Brands and Sizes.

### 3. Drivers (`drivers`)
*   **Profiles:** Employee ID, License Info, Contact Details.
*   **User Account:** Linked to Django Auth User for login/permissions.
*   **Financial Ledger (Pocket):**
    *   **Transactions:** Salary, Allowances, Loans, Payments, Repayments.
    *   **Balance:** Real-time calculation of "Company owes Driver" vs "Driver owes Company".

### 4. Financial Ledger (`ledger`)
*   **Double-Entry-Like System:**
    *   **Financial Records:** Income/Expense entries linked to Accounts, Parties, Drivers, or Trips.
    *   **Categories:** Dynamic types (e.g., "Freight Income", "Diesel Expense").
    *   **Accounts:** Company Bank/Cash accounts with opening balance and real-time running balance.
    *   **Parties:** Client/Vendor management with ledger history.
*   **Trip Payment Linking:**
    *   **Direct:** Record income directly against a Trip.
    *   **Allocation:** Split one large payment across multiple trips (Batch Payments).
*   **Reports:** Monthly Income/Expense/Net Profit summary.

### 5. Document Management (`documents`)
*   **Centralized Storage:** Upload and manage scanned files (PDF/Images).
*   **Entities:**
    *   **Vehicle Docs:** Insurance, Permit, Fitness, Tax, Pollution.
    *   **Driver Docs:** License, ID Proofs.
*   **Expiry Tracking:** Dates tracked with "Expiring Soon" ( < 30 days) and "Expired" visual alerts.

## Technical Stack
*   **Backend:** Django 5.1.4, SQLite.
*   **Frontend:** Django Templates, Bootstrap 5 (Responsive), jQuery (AJAX), Select2 (Searchable Dropdowns).
*   **Environment:** Python 3.12+, `python-decouple` for `.env`.
*   **Utilities:** Custom Sequence generator for gap-less numbering (Trip #, Receipt #).

## Current Infrastructure
*   **Network:** Configured for Local LAN Access (`ALLOWED_HOSTS` includes local IP).
*   **Static:** Served locally.
*   **Permissions:** Granular Django Groups (Manager, Supervisor, Driver).