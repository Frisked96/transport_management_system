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
