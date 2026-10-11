from django.db import migrations


def create_and_update_toll_roles(apps, schema_editor):
    from django.apps import apps as global_apps
    from django.contrib.contenttypes.management import create_contenttypes
    from django.contrib.auth.management import create_permissions

    db_alias = schema_editor.connection.alias

    # 1. Ensure ContentTypes and Permissions exist in test/live database
    for app_label in ['contenttypes', 'auth', 'fleet', 'trips', 'ledger']:
        try:
            app_config = global_apps.get_app_config(app_label)
            create_contenttypes(app_config, using=db_alias, verbosity=0)
            create_permissions(app_config, using=db_alias, verbosity=0)
        except Exception:
            pass

    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')

    def get_perm(app_label, codename):
        return Permission.objects.using(db_alias).filter(content_type__app_label=app_label, codename=codename).first()

    # 2. Toll Operator Role (Allows entry and viewing of toll data)
    operator_group, _ = Group.objects.using(db_alias).get_or_create(name='Toll Operator')
    operator_perms = [
        ('ledger', 'view_tollaccount'),
        ('ledger', 'add_tollentry'),
        ('ledger', 'change_tollentry'),
        ('fleet', 'view_vehicle'),
        ('fleet', 'can_view_all_vehicles'),
        ('trips', 'view_trip'),
    ]
    for app_label, codename in operator_perms:
        p = get_perm(app_label, codename)
        if p:
            operator_group.permissions.add(p)

    # 3. Toll Manager Role (Full control over toll recharges, entries, and accounts)
    manager_group, _ = Group.objects.using(db_alias).get_or_create(name='Toll Manager')
    manager_perms = [
        ('ledger', 'view_tollaccount'),
        ('ledger', 'add_tollentry'),
        ('ledger', 'change_tollentry'),
        ('ledger', 'delete_tollentry'),
        ('ledger', 'add_tollrecharge'),
        ('ledger', 'change_tollrecharge'),
        ('ledger', 'delete_tollrecharge'),
        ('ledger', 'add_tollaccount'),
        ('ledger', 'change_tollaccount'),
        ('ledger', 'delete_tollaccount'),
        ('fleet', 'view_vehicle'),
        ('fleet', 'can_view_all_vehicles'),
        ('trips', 'view_trip'),
    ]
    for app_label, codename in manager_perms:
        p = get_perm(app_label, codename)
        if p:
            manager_group.permissions.add(p)

    # 4. Update existing 'Office' role if present
    office_group = Group.objects.using(db_alias).filter(name='Office').first()
    if office_group:
        office_toll_perms = [
            ('ledger', 'view_tollaccount'),
            ('ledger', 'add_tollentry'),
            ('ledger', 'change_tollentry'),
            ('ledger', 'add_tollrecharge'),
        ]
        for app_label, codename in office_toll_perms:
            p = get_perm(app_label, codename)
            if p:
                office_group.permissions.add(p)


def remove_toll_roles(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    Group = apps.get_model('auth', 'Group')
    Group.objects.using(db_alias).filter(name__in=['Toll Operator', 'Toll Manager']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
        ('ledger', '0041_tollaccount_tollrecharge_and_more'),
        ('fleet', '0022_alter_tyrelog_position'),
        ('trips', '0017_alter_trip_date'),
        ('contenttypes', '0002_remove_content_type_name'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(create_and_update_toll_roles, reverse_code=remove_toll_roles),
    ]
