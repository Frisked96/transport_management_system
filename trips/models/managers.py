"""
Trip model managers and querysets.
"""
from django.db import models
from django.db.models import Case, When, Value, F, CharField, Exists, OuterRef, Q


class TripQuerySet(models.QuerySet):
    def with_payment_info(self):
        """
        Ultra-lightweight payment info using cached fields.
        Backward compatible with previous annotation names.
        """
        return self.annotate(
            annotated_revenue=F('revenue_cached'),
            annotated_gst_amount=F('gst_amount_cached'),
            annotated_total_revenue=F('total_revenue_cached'),
            annotated_received=F('amount_received_cached'),
            annotated_outstanding=F('outstanding_balance_cached'),
            annotated_status=F('payment_status_cached')
        )

    def with_billing_info(self):
        """Annotate queryset with billing status and GST type"""
        # Import internally to avoid circular dependency
        from ledger.models import Bill

        return self.annotate(
            annotated_is_billed=Exists(
                Bill.objects.filter(trips=OuterRef('pk'), customer_bill__isnull=True).exclude(category__name='Lorry Hire').filter(
                    Q(party=OuterRef('party')) | ~Q(party__party_type='Creditor')
                )
            ),
            annotated_is_creditor_billed=Exists(
                Bill.objects.filter(trips=OuterRef('pk')).filter(
                    Q(customer_bill__isnull=False) | Q(category__name='Lorry Hire') | Q(party__party_type='Creditor')
                )
            ),
            annotated_gst_type=Case(
                When(gst_type_snapshot__gt='', then=F('gst_type_snapshot')),
                When(route__route_type='intra', then=Value('IGST')),
                When(route__route_type='none', then=Value('NONE')),
                default=Value('GST'),
                output_field=CharField()
            )
        )


class TripManager(models.Manager):
    def get_queryset(self):
        return TripQuerySet(self.model, using=self._db)
    
    def with_payment_info(self):
        return self.get_queryset().with_payment_info()

    def with_billing_info(self):
        return self.get_queryset().with_billing_info()
