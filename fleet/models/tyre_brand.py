"""
TyreBrand model for Fleet application.
"""
from django.db import models


class TyreBrand(models.Model):
    """
    Pre-defined tyre brands/makes with suggestive pricing.
    Used as reference data for tyre inventory management.
    """
    name = models.CharField(
        max_length=100,
        unique=True,
        verbose_name='Brand / Make'
    )
    suggestive_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name='Suggestive Price',
        help_text='Suggested purchase price for tyres of this brand'
    )
    vendor = models.ForeignKey(
        'ledger.Party',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        limit_choices_to={'party_type__in': ['Creditor', 'Both']},
        verbose_name='Default Vendor',
        help_text='Vendor to automatically bill when a tyre of this brand is created'
    )

    class Meta:
        verbose_name = 'Tyre Brand'
        verbose_name_plural = 'Tyre Brands'
        ordering = ['name']

    def __str__(self):
        return self.name
