
import django
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from django.contrib.auth import get_user_model
User = get_user_model()
u, created = User.objects.get_or_create(username='tester')
u.set_password('tester123')
u.is_staff = True
u.is_superuser = True
u.is_active = True
u.save()
print(f"User 'tester' set with password 'tester123'. Created: {created}")
