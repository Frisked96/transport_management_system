from django.db import migrations


def consolidate_duplicate_routes(apps, schema_editor):
    Trip = apps.get_model('trips', 'Trip')
    Route = apps.get_model('trips', 'Route')

    # 1. Backfill any missing gst_type_snapshot values from route_type
    for trip in Trip.objects.filter(gst_type_snapshot__in=['', None]):
        route = trip.route
        if route:
            if route.route_type == 'intra':
                trip.gst_type_snapshot = 'IGST'
            elif route.route_type == 'none':
                trip.gst_type_snapshot = 'NONE'
            else:
                trip.gst_type_snapshot = 'GST'
        else:
            trip.gst_type_snapshot = 'GST'
        trip.save(update_fields=['gst_type_snapshot'])

    # 2. Consolidate duplicate routes
    all_routes = list(Route.objects.all())
    grouped_routes = {}
    for r in all_routes:
        key = (r.pickup_location.strip().lower(), r.delivery_location.strip().lower())
        grouped_routes.setdefault(key, []).append(r)

    for key, routes in grouped_routes.items():
        if len(routes) > 1:
            # Sort to pick the best canonical route:
            # 1. Most trips attached
            # 2. Highest default_rate
            # 3. Lowest ID (earliest created)
            routes_with_counts = []
            for r in routes:
                trip_count = Trip.objects.filter(route=r).count()
                rate = float(r.default_rate or 0)
                routes_with_counts.append((trip_count, rate, -r.id, r))

            routes_with_counts.sort(reverse=True)
            canonical = routes_with_counts[0][3]

            for _, _, _, dup in routes_with_counts[1:]:
                # Re-assign all trips from duplicate route to canonical route
                Trip.objects.filter(route=dup).update(route=canonical)
                dup.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('trips', '0017_alter_trip_date'),
    ]

    operations = [
        migrations.RunPython(consolidate_duplicate_routes, migrations.RunPython.noop),
        migrations.AlterUniqueTogether(
            name='route',
            unique_together={('pickup_location', 'delivery_location')},
        ),
    ]
