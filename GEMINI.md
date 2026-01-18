# Project Context: Transport Management System

## Project Overview
The **Transport Management System** is a production-ready internal web application designed to manage transport business operations. It is built using **Django 5.1.4** and **SQLite**. The system facilitates the management of trips, fleet/vehicles, drivers, and financial records.

**Key Features:**
*   **Role-Based Access Control:** Admin, Manager, Supervisor.
*   **Trip Management:** Scheduling, assigning, and tracking trips.
*   **Fleet Management:** Vehicle registration, maintenance logs, and tracking.
*   **Driver Management:** Profiles, history, and financial "pockets" (salary, loans, allowances). Drivers do not have a dedicated login interface.
*   **Financial Ledger:** Income and expense tracking, associated with trips.
*   **Dashboards:** Role-specific views for Managers.

## Architecture & Technology
*   **Backend:** Python (Django)
*   **Database:** SQLite3 (default configuration)
*   **Configuration:** `transport_mgmt/settings.py` uses `python-decouple` for environment variables (`.env`).
*   **Frontend:** Server-side rendered Django Templates (HTML).

### Key Directories
*   `transport_mgmt/`: Main project configuration settings and URLs.
*   `drivers/`: App for driver profiles and financial transactions.
*   `fleet/`: App for vehicle management and maintenance logs.
*   `ledger/`: App for financial records (income/expenses).
*   `trips/`: App for trip management (creation, assignment, tracking).
*   `templates/`: Global and app-specific HTML templates.

## Building and Running

### Prerequisites
*   Python 3.8+
*   pip

### Setup Instructions
1.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

2.  **Environment Setup:**
    *   Copy `.env.example` to `.env` (if it exists, otherwise create one based on `settings.py`).
    *   Configure `SECRET_KEY` and `DEBUG` in `.env`.

3.  **Database Setup:**
    *   Run migrations to set up the SQLite database:
        ```bash
        python manage.py migrate
        ```

    > **Note:** The `README.md` references a `setup_project.py` script for initializing data/permissions, but this file appears to be **missing** from the root directory. You may need to manually create a superuser and set up groups/permissions via the Django Admin.
    >
    > To create a superuser:
    > ```bash
    > python manage.py createsuperuser
    > ```

4.  **Run Development Server:**
    ```bash
    python manage.py runserver
    ```
    Access the application at `http://127.0.0.1:8000`.

### Testing
*   Run tests using the standard Django test runner:
    ```bash
    python manage.py test
    ```

## Development Conventions
*   **Code Style:** Follows standard PEP 8 Python guidelines.
*   **Languages:** python, html, css(minimum), JavaScript(wherever needed).
*   **Permissions:** Views heavily rely on Django's permission system (Groups). Ensure new views enforce appropriate checks.
*   **Templates:** Located in the `templates/` directory, organized by app name.
*   **Static Files:** Served from `static/` (development) or `staticfiles/` (production).

## Common Tasks
*   **New Migration:** `python manage.py makemigrations`
*   **Apply Migration:** `python manage.py migrate`
*   **Create Superuser:** `python manage.py createsuperuser`