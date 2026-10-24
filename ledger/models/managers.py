"""
Managers and querysets for Ledger models.
"""
from django.db import models
from django.db.models import F


class BillQuerySet(models.QuerySet):
    def with_payment_info(self):
        """
        Ultra-lightweight payment info using cached fields.
        Backward compatible with previous annotation names.
        """
        return self.annotate(
            annotated_subtotal=F('subtotal_cached'),
            annotated_gst_amount=F('gst_amount_cached'),
            annotated_total_amount=F('total_amount_cached'),
            annotated_received=F('amount_received_cached'),
            annotated_outstanding=F('outstanding_balance_cached'),
            annotated_status=F('payment_status_cached')
        )


class BillManager(models.Manager):
    def get_queryset(self):
        return BillQuerySet(self.model, using=self._db)
    
    def with_payment_info(self):
        return self.get_queryset().with_payment_info()
