# Transport Management System (TMS)

## Project Overview
This project is an enterprise Transport Management System built with Django. It manages end-to-end fleet operations, trip logistics, tyre lifecycle tracking, FASTag/toll accounting, driver pocket ledgers, and double-entry accrual financial accounting with invoicing and GST compliance.

## Directory Structure & Modular Architecture
All apps follow a modular package structure (`models/`, `views/`, `forms/`, `services/`, `signals/`):
*   **`transport_mgmt/`**: Project root configuration, URL routing, settings, and storage bridge.
*   **`trips/`**: Trip lifecycle, single-leg routing, market/vendor hire rates, and route analytics dashboards.
*   **`fleet/`**: Vehicle assets, maintenance schedules, and 18+1 tyre layout positioning and tracking.
*   **`ledger/`**: Double-entry financial engine, party balances, billing handoffs, and FASTag toll management.
*   **`drivers/`**: Driver profiles and personal financial pocket ledgers (salary, allowances, advances, loans).
*   **`documents/`**: Centralized compliance document tracking, base documents, renewals, and expiry alerts.
*   **`accounts/`**: Custom "no-admin" user management, RBAC role permissions, and active user tracking.
*   **`templates/`**: Responsive Tailwind CSS frontend templates (mobile drawer, bottom bar, desktop nav).
*   **`static/`**: Fonts and custom assets.

## High-Level Models Overview
*   **`trips`**: 
    *   `Trip`: Single-leg transport operations. Status is derived from payment. Caches revenue, GST, and balances. Supports company-owned vs market/attached vehicles (`revenue_type`, `vendor_hire_amount`).
    *   `Route`: Predefined routes (`pickup_location`, `delivery_location`, `default_rate`) with analytics dashboards.
*   **`fleet`**: 
    *   `Vehicle`: Central fleet asset entity (`ownership`: Owned, Attached, Market; linked `vendor` and `company_account`).
    *   `MaintenanceRecord`: Tracks scheduled maintenance (by interval km/days) and completed service logs.
    *   `Tyre`, `TyreBrand`, `TyreLog`: Manages 18-wheeler + 1 Stepney layout, positioning, status (Mounted, Scrap, Retreaded, Spare), and movement logs.
*   **`ledger`**:
    *   `CompanyAccount`: Operating firm bank/cash accounts with custom invoice/CN/DN sequence formatting.
    *   `Party`: Clients (Debtors) and Vendors/Suppliers (Creditors) with distinct balance calculation logic.
    *   `FinancialRecord`: Core double-entry system mapping Income, Expense, and Invoice accruals. Supports explicit foreign keys to trips, bills, tyres, document renewals, and toll recharges.
    *   `Bill` & `BillTrip`: Invoices, Credit Notes, Debit Notes, and Creditor Bills. Supports trip grouping, discounts, roundoff, and reverse charge.
    *   `TripAllocation` & `BillAllocation`: Explicit payment-to-trip and payment-to-bill allocation tracking.
    *   `TollAccount`, `TollRecharge`, `TollEntry`: FASTag wallet management, bank-funded recharges, vehicle toll slips/charges, statement imports, and reconciliation.
*   **`drivers`**:
    *   `Driver`: Core driver profile extending the default User with cached pocket balances.
    *   `DriverTransaction`: Driver wallet ledger (Salary, Advance, Allowance, Loan, Repayment).
*   **`documents`**:
    *   `Document`: Vehicle and driver compliance documents. Base vehicle documents (Fitness, Permits, Insurance, Tax, RC, PUCC, VLTD) cannot be deleted.
    *   `DocumentRenewal` & `DocumentFile`: Historical renewals, receipt files, and cloud-backed file storage.
*   **`accounts`**:
    *   `UserProfile`: Extends User with `last_seen` timestamp tracked via `ActiveUserMiddleware`.
    *   Role & Permission Management: Custom front-end CRUD for Django `Group` and `Permission` management.

## Core Financial Logic (Ledger, Billing & Tolls)
*   **Accrual-Based Revenue & Expenses**: Trips and Bills generate `RECORD_TYPE_INVOICE` financial records as soon as created. This represents earned revenue or accrued vendor liability before cash is exchanged.
*   **Ledger Hand-off**: Trip payment exists in the ledger as an individual unbilled trip entry OR as part of a consolidated Bill entry. When a customer Bill is created, individual trip invoice records are deleted and replaced by a single consolidated Bill record.
*   **Creditor Billing for Attached Vehicles**: Trips using attached vehicles with `vendor_hire_amount > 0` automatically generate linked Creditor Bills (`CR-` prefix) for the vehicle vendor upon customer billing. Customer Credit Notes auto-mirror as vendor Debit Notes for transit shortage deductions.
*   **Snapshot Principle**: Bills snapshot Firm details (Name, GSTIN, Address, Bank) from `CompanyAccount` at creation time to preserve historical integrity.
*   **Balance Calculation Invariants**:
    *   `CompanyAccount` Balance = `Opening Balance + Total Received - Total Paid Out` (actual cash/bank flow; `INVOICE` accruals and non-cash deductions/notes are excluded).
    *   Debtor `Party` Balance = `Opening Balance + Invoices + Expenses - Receipts - Deductions/TDS/Shortages - Credit Notes`.
    *   Creditor `Party` Balance = `Opening Balance + Invoices/Lorry Hire - Payments Out - TDS - Deductions - Debit Notes`.
*   **FASTag & Toll Flow**: Toll recharges funded from a `CompanyAccount` reduce bank balance and increase the `TollAccount` wallet. Toll charges deduct strictly from the `TollAccount` and map dynamically to trips within operational vehicle windows (from trip date to next trip date for company-owned vehicles).

## UI & Application Philosophy
*   **IGNORE DJANGO ADMIN**: Business users, operators, and managers should NEVER use the default Django Admin interface. All operational tasks (Trips, Ledger, Vehicles, Tyres, Tolls, Documents, Roles, Users) have stylized front-end views in the main application. Django Admin is strictly for developer debugging.
*   **Form UX**: Global double-submit prevention on all POST forms, Select2 searchable dropdowns, and arrow-key grid navigation in tabular interfaces.
*   **Currency Formatting**: All monetary values use the custom `indian_comma` filter (`1,45,140.00`).

## Performance & Storage Guidelines
*   **On-Demand Storage Access**: Document and tyre URLs are generated on-demand via proxy views (`document-view`, `renewal-file-view`, `tyre-photo-serve`) with presigned Cloudflare R2 URLs. Never invoke `.url` inside template loops.
*   **Automated Tests**: During automated tests, Django switches automatically to local `FileSystemStorage` and `MD5PasswordHasher` for fast execution without external network calls.
*   **Database Aggregations**: Compute counts, balances, and totals at the database level using `Sum`, `Count`, `Case`, `When`, and `Coalesce` rather than Python-side loops. Always use `select_related` and `prefetch_related`.
