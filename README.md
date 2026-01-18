# Transport Management System

A complete, production-ready internal web application for managing transport business operations, built with Django and SQLite.

## Project Overview

The **Transport Management System** is designed to streamline logistics operations, offering robust management for trips, fleet, drivers, and financial records. It features role-based access control (Admin, Manager, Supervisor, Driver) and provides intuitive dashboards. The user interface has recently undergone a significant overhaul, now utilizing **Bootstrap 5** for a modern, responsive design with a custom "Slate & Teal" theme, ensuring a professional and visually cohesive experience across all modules.

## Features

- **User Authentication & Authorization**: Role-based access control with four user types (Admin, Manager, Supervisor, Driver)
- **Trip Management**: Single-leg trip structure with daily logbook view. Create, assign, and track transport trips by date.
- **Fleet Management**: Manage vehicles and maintenance logs
- **Driver Management**: Comprehensive driver profiles, history, and financial pocket (allowances, salary, loans)
- **Financial Ledger**: Track income and expenses with trip association
- **Dashboard Views**: Role-specific dashboards for drivers and managers
- **Permission System**: Granular permissions enforced on all views

## Technology Stack

- **Backend**: Django 5.1.4 (LTS)
- **Database**: SQLite3
- **Frontend**: Django Templates (HTML) enhanced with **Bootstrap 5** for responsive design and a custom "Slate & Teal" theme. The UI emphasizes clarity, contrast, and usability, with improved button visibility, larger icons, and a consistent visual style across all pages.
- **Server**: Django Development Server

## User Roles & Permissions

### Admin (admin/admin123)
- Full system access
- User and group management
- Django admin interface access
- Complete CRUD on all data

### Manager (manager/manager123)
- Create, view, update trips, vehicles, and financial records
- Cannot delete records (except by admin)
- Access to manager dashboard with system overview
- Cannot modify user accounts or system settings

### Supervisor (supervisor/supervisor123)
- View all trips and vehicles
- Update trip statuses
- Create maintenance logs
- Read-only access to financial records

### Driver (driver/driver123)
- View only their assigned trips
- Update status of their own trips
- Read-only access to their assigned vehicle details
- No access to financial records or reports

## Installation & Setup

### Prerequisites
- Python 3.8+
- pip (Python package manager)

### Installation Steps

1. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Database Setup**
   ```bash
   python manage.py migrate
   python manage.py createsuperuser
   ```
   
   *Note: `setup_project.py` is currently unavailable. You will need to manually set up groups and permissions via the Django Admin interface.*

3. **Start Development Server**
   ```bash
   python manage.py runserver
   ```

4. **Access the Application**
   Open your browser to: http://127.0.0.1:8000

### Demo Accounts
- **Admin**: admin / admin123
- **Manager**: manager / manager123
- **Supervisor**: supervisor / supervisor123
- **Driver**: driver / driver123

## Project Structure

```
transport_management_system/
├── transport_mgmt/          # Main Django project
│   ├── settings.py          # Project configuration
│   ├── urls.py             # Main URL configuration
│   └── wsgi.py             # WSGI configuration
├── trips/                  # Trip management app
│   ├── models.py           # Trip model (Single Leg)
│   ├── views.py            # Trip views with permissions
│   ├── forms.py            # Trip forms
│   └── urls.py             # Trip URLs
├── fleet/                  # Fleet management app
│   ├── models.py           # Vehicle and MaintenanceLog models
│   ├── views.py            # Fleet views with permissions
│   ├── forms.py            # Fleet forms
│   └── urls.py             # Fleet URLs
├── ledger/                 # Financial management app
│   ├── models.py           # FinancialRecord model
│   ├── views.py            # Ledger views with permissions
│   ├── forms.py            # Ledger forms
│   └── urls.py             # Ledger URLs
├── drivers/                # Driver management app
│   ├── models.py           # Driver and DriverTransaction models
│   ├── views.py            # Driver management views
│   ├── forms.py            # Driver forms
│   └── urls.py             # Driver URLs
├── templates/              # HTML templates
│   ├── base.html           # Base template
│   ├── registration/       # Login template
│   ├── trips/              # Trip templates
│   ├── fleet/              # Fleet templates
│   └── ledger/             # Ledger templates
├── static/                 # Static files (CSS, JS, images)
├── media/                  # Uploaded files
├── manage.py               # Django management script
└── requirements.txt        # Python dependencies
```

## Application Features

### Trip Management
- Create and assign trips to vehicles
- Date-based trip log view
- Track trip status (Scheduled, In Progress, Completed, Cancelled)
- Automatic completion timestamp when status changes to Completed
- Trip search and filtering
- Trip-specific financial records and profitability analysis

### Fleet Management
- Vehicle registration and status tracking
- Maintenance logging with cost tracking
- Next service due date tracking
- Vehicle availability checking

### Driver Management
- Create and manage driver profiles linked to user accounts
- Track driver history: vehicles driven and trips taken
- Calculate profitability per driver (Revenue generated vs Expenses)
- Manage "Driver Pocket": Track salaries, allowances, loans, and repayments
- View driver financial balance

### Financial Ledger
- Income and expense tracking
- Trip-specific financial records
- Category-based classification
- File upload support for receipts/documents
- Financial summary reports

### Dashboards
- **Driver Dashboard**: Shows current and recent trips with status update capability
- **Manager Dashboard**: System overview with key metrics and recent activity

## Security Features

- Role-based access control using Django Groups
- Login required for all views
- Permission-based view access
- CSRF protection on all forms
- Secure password handling
- File upload validation

## Configuration

### Database
SQLite is configured as the default database in `settings.py`:
```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}
```

### Security Settings
For production deployment (closed network), set:
```python
DEBUG = False
ALLOWED_HOSTS = ['your.internal.ip', 'localhost']
SECRET_KEY = 'your-secret-key-here'
```

### Static and Media Files
- Static files: `/static/`
- Media files (uploads): `/media/`
- Both are served automatically in development

## Usage Guide

### For Admins
1. Login with admin credentials
2. Access Django admin at `/admin/` for full system management
3. Manage users, groups, and all data
4. Create new trips, vehicles, and financial records

### For Managers
1. Login with manager credentials
2. Access Manager Dashboard for system overview
3. Create and manage trips, vehicles, and financial records
4. View financial summaries and reports
5. Cannot delete records or manage users

### For Supervisors
1. Login with supervisor credentials
2. View all trips and vehicles
3. Update trip statuses
4. Create maintenance logs
5. View (but not modify) financial records

### For Drivers
1. Login with driver credentials
2. Access Driver Dashboard showing your assigned trips
3. View trip details and update status
4. View assigned vehicle information
5. No access to financial data or other drivers' trips

## Customization

### Adding New Models
1. Create model in app's `models.py`
2. Run `python manage.py makemigrations`
3. Run `python manage.py migrate`
4. Register in app's `admin.py` (optional)
5. Create views in `views.py` with permission checks
6. Add URLs in `urls.py`
7. Create templates

### Modifying Permissions
1. Use Django admin to manage permissions manually
2. Access the Admin interface at `/admin/`
3. Navigate to 'Groups' to manage role-based permissions

## Production Deployment

For production deployment in a closed network:

1. Set `DEBUG = False` in settings.py
2. Set appropriate `ALLOWED_HOSTS`
3. Change the `SECRET_KEY` to a secure random value
4. Enable security headers in settings.py
5. Use a production web server (e.g., Gunicorn) behind a reverse proxy
6. Set up proper file permissions
7. Configure regular database backups

## Troubleshooting

### Common Issues

1. **"No module named 'django'"**
   - Run `pip install -r requirements.txt`

2. **"Database errors"**
   - Run `python manage.py migrate`
   - Check database file permissions

3. **"Permission denied" errors**
   - Ensure user is in correct group
   - Check group permissions in Django admin

4. **"Static files not loading"**
   - Run `python manage.py collectstatic` (for production)
   - Ensure DEBUG = True (for development)

## Support

This is a complete, production-ready system designed for internal use on closed networks. The code is fully documented and structured for easy maintenance and extension.

For additional features or customizations, modify the respective models, views, and templates following Django best practices.
