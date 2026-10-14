"""
General ledger maintenance and resequencing service.
"""
from django.db import transaction


class LedgerService:
    """
    Handles general ledger maintenance and sequencing.
    """
    
    @staticmethod
    def resequence_financial_records():
        """
        Resequence all entry numbers to remove gaps.
        """
        from ledger.models import FinancialRecord, Sequence
        from django.db.models import Max
        
        with transaction.atomic():
            records = list(FinancialRecord.objects.all().order_by('date', 'created_at'))
            records_to_update = []
            
            for i, record in enumerate(records, start=1):
                if record.entry_number != i:
                    record._new_entry_number = i
                    records_to_update.append(record)
            
            if records_to_update:
                max_val = FinancialRecord.objects.aggregate(max_val=Max('entry_number'))['max_val'] or 0
                offset = max_val + 1000

                # Step A: Temporary high numbers
                for record in records_to_update:
                    record.entry_number = offset + record.pk
                FinancialRecord.objects.bulk_update(records_to_update, ['entry_number'])

                # Step B: Final numbers
                for record in records_to_update:
                    record.entry_number = record._new_entry_number
                FinancialRecord.objects.bulk_update(records_to_update, ['entry_number'])
                
            Sequence.objects.filter(key='financial_record_entry_number').update(value=len(records))
