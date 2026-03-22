import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'transport_mgmt.settings')
django.setup()

from django.contrib.auth import get_user_model

User = get_user_model()
username = 'admin'
password = 'admin123'
email = 'admin@example.com'

if not User.objects.filter(username=username).exists():
    User.objects.create_superuser(username, email, password)
    print(f"Superuser '{username}' created successfully.")
else:
    print(f"User '{username}' already exists.")
