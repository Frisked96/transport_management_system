"""
General ledger maintenance and resequencing service.
"""
from django.db import transaction


class LedgerService:
    """
    Handles general ledger maintenance and sequencing.
    """
    
    @staticmethod
    def resequence_financial_records(from_date=None, from_created_at=None):
        """
        Resequence entry numbers to maintain gapless numbering without loading the full table.
        """
        from ledger.models import FinancialRecord, Sequence
        from django.db.models import Max, Q
        
        with transaction.atomic():
            if from_date and from_created_at:
                prior_filter = Q(date__lt=from_date) | Q(date=from_date, created_at__lt=from_created_at)
                start_index = FinancialRecord.objects.filter(prior_filter).count() + 1
                subsequent_filter = Q(date__gt=from_date) | Q(date=from_date, created_at__gte=from_created_at)
                records = list(FinancialRecord.objects.filter(subsequent_filter).only('id', 'entry_number').order_by('date', 'created_at'))
            else:
                start_index = 1
                records = list(FinancialRecord.objects.all().only('id', 'entry_number').order_by('date', 'created_at'))
            
            records_to_update = []
            current_index = start_index
            for record in records:
                if record.entry_number != current_index:
                    record._new_entry_number = current_index
                    records_to_update.append(record)
                current_index += 1
            
            if records_to_update:
                max_val = FinancialRecord.objects.aggregate(max_val=Max('entry_number'))['max_val'] or 0
                offset = max_val + 1000

                # Step A: Temporary high numbers to avoid unique constraint collision
                for record in records_to_update:
                    record.entry_number = offset + record.pk
                FinancialRecord.objects.bulk_update(records_to_update, ['entry_number'])

                # Step B: Final numbers
                for record in records_to_update:
                    record.entry_number = record._new_entry_number
                FinancialRecord.objects.bulk_update(records_to_update, ['entry_number'])
                
            total_records = FinancialRecord.objects.count()
            Sequence.objects.filter(key='financial_record_entry_number').update(value=total_records)
