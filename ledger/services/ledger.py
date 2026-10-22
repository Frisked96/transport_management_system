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
        Resequence entry numbers to maintain gapless numbering.
        """
        from ledger.models import FinancialRecord, Sequence
        from django.db.models import Max
        
        with transaction.atomic():
            records = list(FinancialRecord.objects.all().only('id', 'entry_number').order_by('date', 'created_at', 'id'))
            
            records_to_update = []
            for current_index, record in enumerate(records, start=1):
                if record.entry_number != current_index:
                    record._new_entry_number = current_index
                    records_to_update.append(record)
            
            if records_to_update:
                max_val = FinancialRecord.objects.aggregate(max_val=Max('entry_number'))['max_val'] or 0
                offset = max_val + 1000

                # Step A: Temporary high numbers to avoid unique constraint collision
                for record in records_to_update:
                    record.entry_number = offset + record.pk
                FinancialRecord.objects.bulk_update(records_to_update, ['entry_number'], batch_size=1000)

                # Step B: Final numbers
                for record in records_to_update:
                    record.entry_number = record._new_entry_number
                FinancialRecord.objects.bulk_update(records_to_update, ['entry_number'], batch_size=1000)
                
            total_records = FinancialRecord.objects.count()
            Sequence.objects.filter(key='financial_record_entry_number').update(value=total_records)
