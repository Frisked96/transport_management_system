"""
FASTag XLSX Statement Import Service.

Parses bank-specific FASTag statement files (AXIS, IDFC, LIVQUIK) for toll debits,
and the Boss Account Summary for wallet recharge credits.
Handles vehicle plate normalization, duplicate detection, and optional trip auto-linking.
"""
import re
from decimal import Decimal, InvalidOperation
from datetime import datetime, date as date_type
from dataclasses import dataclass, field

import openpyxl
from django.db import transaction
from django.utils import timezone

from ledger.models import TollAccount, TollRecharge, TollEntry
from fleet.models import Vehicle


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ImportSummary:
    """Result summary returned after importing a statement."""
    toll_entries_created: int = 0
    recharges_created: int = 0
    platform_fees_created: int = 0
    duplicate_entries_skipped: int = 0
    duplicate_recharges_skipped: int = 0
    unmatched_vehicles: list = field(default_factory=list)
    total_debit_amount: Decimal = Decimal('0.00')
    total_credit_amount: Decimal = Decimal('0.00')
    errors: list = field(default_factory=list)
    file_type: str = ''  # 'bank_statement' or 'boss_summary'
    detected_vendor: str = ''


# ---------------------------------------------------------------------------
# Constants & header signatures
# ---------------------------------------------------------------------------

# Bank statement header (AXIS, IDFC, LIVQUIK)
BANK_HEADER_SIGNATURE = {
    'Transaction Time', 'Nature (C/D)', 'Amount', 'Description',
    'Truck Number', 'Transaction ID', 'Vendor'
}

# Boss summary header
BOSS_HEADER_SIGNATURE = {
    'Transaction Time', 'Nature (C/D)', 'Amount', 'From/To',
    'Description', 'TransactionId'
}

# Known vendor mappings to TollAccount names/providers
VENDOR_TO_ACCOUNT = {
    'AXIS': ['AXIS', 'Axis'],
    'IDFC': ['IDFC', 'Idfc'],
    'LIVQ': ['LIVQUIK', 'LivQuik', 'Livquik', 'LIVQ'],
    'BLACKBUCK': ['BlackBuck', 'Blackbuck', 'BLACKBUCK'],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_plate(raw_plate):
    """
    Strip all non-alphanumeric characters and uppercase.
    e.g. 'CG04PJ8276' or 'CG-04-PJ-8276' or 'CG 04 PJ 8276' -> 'CG04PJ8276'
    """
    if not raw_plate:
        return ''
    return re.sub(r'[^A-Za-z0-9]', '', str(raw_plate)).upper()


def parse_timestamp(raw_value):
    """
    Parse a transaction timestamp from the XLSX file.
    Handles both string format ('21 Oct 26 07:09 AM') and native datetime objects.
    Returns a date object.
    """
    if isinstance(raw_value, datetime):
        return raw_value.date()
    if isinstance(raw_value, date_type):
        return raw_value
    if not raw_value or not str(raw_value).strip():
        return None
    raw_str = str(raw_value).strip()
    for fmt in ('%d %b %y %I:%M %p', '%d %b %Y %I:%M %p', '%Y-%m-%d', '%d/%m/%Y'):
        try:
            return datetime.strptime(raw_str, fmt).date()
        except ValueError:
            continue
    return None


def extract_toll_plaza(description, vendor=''):
    """
    Extract toll plaza name from description text.
    Bank statements: 'FasTag Toll Payment at Mathni - Maharashtra' -> 'Mathni - Maharashtra'
    IDFC: 'Issuer Debit Transaction for toll fare - 540015 - ARAMBHA TOLL PLAZA' -> 'ARAMBHA TOLL PLAZA'
    """
    if not description:
        return ''
    desc = str(description)

    # Pattern: "FasTag Toll Payment at <plaza>"
    match = re.search(r'FasTag Toll Payment at\s+(.+)', desc, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    # Pattern: "Issuer Debit Transaction for toll fare - <code> - <plaza>"
    match = re.search(r'toll fare\s*-\s*\d+\s*-\s*(.+)', desc, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    return ''


def find_header_row(sheet, max_rows=25):
    """
    Scan the first max_rows rows to find the header row.
    Returns (row_index, header_cells, file_type) or (None, None, None).
    """
    for idx, row in enumerate(sheet.iter_rows(min_row=1, max_row=max_rows, values_only=True), start=1):
        cells = [str(c).strip() if c is not None else '' for c in row]
        cells_set = set(c for c in cells if c)

        if BANK_HEADER_SIGNATURE.issubset(cells_set):
            return idx, cells, 'bank_statement'
        if BOSS_HEADER_SIGNATURE.issubset(cells_set):
            return idx, cells, 'boss_summary'

    return None, None, None


def build_vehicle_lookup():
    """
    Build a dict mapping normalized identifiers (registration plate and chassis number)
    -> Vehicle instance for the entire fleet.
    Registration plates take precedence over chassis numbers in case of collision.
    """
    lookup = {}
    # First pass: map chassis numbers (for new/temporary vehicles before plate assignment)
    for v in Vehicle.objects.exclude(chassis_number__isnull=True).exclude(chassis_number=''):
        norm_chassis = normalize_plate(v.chassis_number)
        if norm_chassis:
            lookup[norm_chassis] = v

    # Second pass: map registration plates (takes precedence)
    for v in Vehicle.objects.exclude(registration_plate__isnull=True).exclude(registration_plate=''):
        norm_plate = normalize_plate(v.registration_plate)
        if norm_plate:
            lookup[norm_plate] = v

    return lookup


def resolve_toll_account(vendor_hint, fallback_account=None):
    """
    Try to find a TollAccount matching the vendor hint string.
    If fallback_account is specified, it takes priority.
    If a master 'BlackBuck' account exists and is default, preference is given to BlackBuck.
    Otherwise, matches vendor or falls back to default account.
    """
    if fallback_account:
        return fallback_account

    # If BlackBuck is configured as the default master wallet
    blackbuck = TollAccount.objects.filter(name__iexact='BlackBuck', is_default=True).first()
    if blackbuck:
        return blackbuck

    if vendor_hint:
        hint_upper = vendor_hint.strip().upper()
        for vendor_key, names in VENDOR_TO_ACCOUNT.items():
            if hint_upper in [n.upper() for n in names] or hint_upper == vendor_key:
                account = TollAccount.objects.filter(
                    name__iexact=vendor_key
                ).first() or TollAccount.objects.filter(
                    provider__icontains=vendor_key
                ).first()
                if account:
                    return account

    return TollAccount.objects.filter(is_default=True).first() or TollAccount.objects.first()


# ---------------------------------------------------------------------------
# Main import functions
# ---------------------------------------------------------------------------

def import_bank_statement(file_obj, toll_account=None, auto_link_trips=True, user=None):
    """
    Import a bank FASTag statement (AXIS / IDFC / LIVQUIK).
    Processes DEBIT rows as TollEntry records.
    Credit rows in bank statements are internal auto-sweeps and are SKIPPED
    (real credits come from the Boss summary).

    Args:
        file_obj: An uploaded file object (InMemoryUploadedFile or file path).
        toll_account: Optional TollAccount to use. If None, auto-detected from Vendor column.
        auto_link_trips: If True, attempt to link toll entries to active trips.
        user: The User performing the import.

    Returns:
        ImportSummary with results.
    """
    summary = ImportSummary(file_type='bank_statement')

    try:
        wb = openpyxl.load_workbook(file_obj, data_only=True)
    except Exception as e:
        summary.errors.append(f"Failed to open file: {e}")
        return summary

    sheet = wb.active
    header_row_idx, header_cells, file_type = find_header_row(sheet)

    if not header_row_idx or file_type != 'bank_statement':
        summary.errors.append(
            "Could not detect a valid bank statement header. "
            "Expected columns: Transaction Time, Nature (C/D), Amount, Description, "
            "Truck Number, Transaction ID, Vendor."
        )
        wb.close()
        return summary

    # Build column index map
    col_map = {name: idx for idx, name in enumerate(header_cells) if name}

    # Build vehicle lookup
    vehicle_lookup = build_vehicle_lookup()

    # Collect existing transaction references for dedup
    existing_entry_refs = set()
    if toll_account:
        existing_entry_refs = set(
            TollEntry.objects.filter(toll_account=toll_account)
            .exclude(transaction_reference='')
            .values_list('transaction_reference', flat=True)
        )

    entries_to_create = []
    detected_vendor = ''
    account_refs_cache = {}  # vendor -> (TollAccount, existing_refs set)

    for row in sheet.iter_rows(min_row=header_row_idx + 1, values_only=True):
        cells = list(row)
        if not cells or all(c is None for c in cells):
            continue

        nature = str(cells[col_map.get('Nature (C/D)', 1)] or '').strip()
        if nature != 'Debit':
            continue

        raw_amount = cells[col_map.get('Amount', 2)]
        raw_date = cells[col_map.get('Transaction Time', 0)]
        description = str(cells[col_map.get('Description', 3)] or '')
        truck_number = str(cells[col_map.get('Truck Number', 4)] or '').strip()
        txn_id = str(cells[col_map.get('Transaction ID', 5)] or '').strip()
        vendor = str(cells[col_map.get('Vendor', 8)] or '').strip()

        if not detected_vendor and vendor:
            detected_vendor = vendor
            summary.detected_vendor = vendor

        # Resolve toll account for this vendor
        if toll_account:
            target_account = toll_account
        else:
            if vendor not in account_refs_cache:
                resolved = resolve_toll_account(vendor)
                refs = set(
                    TollEntry.objects.filter(toll_account=resolved)
                    .exclude(transaction_reference='')
                    .values_list('transaction_reference', flat=True)
                )
                account_refs_cache[vendor] = (resolved, refs)
            target_account, existing_entry_refs = account_refs_cache[vendor]

        # Parse amount
        try:
            amount = Decimal(str(raw_amount))
            if amount <= 0:
                continue
        except (InvalidOperation, TypeError, ValueError):
            continue

        # Parse date
        entry_date = parse_timestamp(raw_date)
        if not entry_date:
            entry_date = timezone.now().date()

        # Dedup check
        if txn_id and txn_id in existing_entry_refs:
            summary.duplicate_entries_skipped += 1
            continue

        # Vehicle lookup
        norm_plate = normalize_plate(truck_number)
        vehicle = vehicle_lookup.get(norm_plate) if norm_plate else None
        if not vehicle and norm_plate:
            summary.unmatched_vehicles.append(truck_number)
            continue
        if not vehicle:
            continue

        # Extract toll plaza
        toll_plaza = extract_toll_plaza(description, vendor)

        base_desc = f"Imported from {vendor} statement" if vendor else "Imported from FASTag statement"
        if norm_plate != normalize_plate(vehicle.registration_plate):
            entry_desc = f"{base_desc} (Tag Chassis: {truck_number})"
        else:
            entry_desc = base_desc

        entries_to_create.append(TollEntry(
            toll_account=target_account,
            vehicle=vehicle,
            date=entry_date,
            amount=amount,
            toll_plaza=toll_plaza,
            transaction_reference=txn_id,
            description=entry_desc,
            created_by=user,
        ))

        if txn_id:
            existing_entry_refs.add(txn_id)

        summary.total_debit_amount += amount

    wb.close()

    # Batch create in atomic transaction
    if entries_to_create:
        with transaction.atomic():
            TollEntry.objects.bulk_create(entries_to_create)

            # Refresh balances of affected accounts
            affected_accounts = set(e.toll_account_id for e in entries_to_create)
            for acc_id in affected_accounts:
                try:
                    TollAccount.objects.get(pk=acc_id).refresh_balance()
                except TollAccount.DoesNotExist:
                    pass

    summary.toll_entries_created = len(entries_to_create)
    # Deduplicate unmatched vehicles list
    summary.unmatched_vehicles = list(set(summary.unmatched_vehicles))
    return summary


def import_boss_summary(file_obj, toll_account=None, user=None):
    """
    Import the Boss Account Summary for recharge credits and BlackBuck platform fees.
    - CREDIT rows are imported as TollRecharge records with company_account=None (unallocated).
    - DEBIT rows with Description != 'FasTag Recharge' (GPS recovery, sensor orders, tag orders, repair fees)
      are automatically imported as TollEntry platform fee deductions to keep the wallet balance accurate.
    - DEBIT rows with Description == 'FasTag Recharge' are internal tag sweeps and are SKIPPED
      (actual vehicle toll plaza entries come from individual bank statements).

    Args:
        file_obj: An uploaded file object.
        toll_account: Optional TollAccount. If None, uses the default account.
        user: The User performing the import.

    Returns:
        ImportSummary with results.
    """
    summary = ImportSummary(file_type='boss_summary')

    try:
        wb = openpyxl.load_workbook(file_obj, data_only=True)
    except Exception as e:
        summary.errors.append(f"Failed to open file: {e}")
        return summary

    sheet = wb.active
    header_row_idx, header_cells, file_type = find_header_row(sheet)

    if not header_row_idx or file_type != 'boss_summary':
        summary.errors.append(
            "Could not detect a valid Boss Account Summary header. "
            "Expected columns: Transaction Time, Nature (C/D), Amount, From/To, "
            "Description, TransactionId."
        )
        wb.close()
        return summary

    # Build column index map
    col_map = {name: idx for idx, name in enumerate(header_cells) if name}

    # Resolve target toll account (defaults to BlackBuck if present, or system default)
    target_account = toll_account or TollAccount.objects.filter(name__iexact='BlackBuck').first() or TollAccount.objects.filter(is_default=True).first() or TollAccount.objects.first()
    if not target_account:
        summary.errors.append("No Toll Account found. Please create one first.")
        wb.close()
        return summary

    # Existing recharge and entry refs for dedup
    existing_recharge_refs = set(
        TollRecharge.objects.filter(toll_account=target_account)
        .exclude(reference_number='')
        .values_list('reference_number', flat=True)
    )
    existing_entry_refs = set(
        TollEntry.objects.filter(toll_account=target_account)
        .exclude(transaction_reference='')
        .values_list('transaction_reference', flat=True)
    )

    vehicle_lookup = build_vehicle_lookup()
    recharges_to_create = []
    platform_fees_to_create = []

    for row in sheet.iter_rows(min_row=header_row_idx + 1, values_only=True):
        cells = list(row)
        if not cells or all(c is None for c in cells):
            continue

        nature = str(cells[col_map.get('Nature (C/D)', 1)] or '').strip()
        raw_amount = cells[col_map.get('Amount', 2)]
        raw_date = cells[col_map.get('Transaction Time', 0)]
        from_to = str(cells[col_map.get('From/To', 3)] or '').strip()
        description = str(cells[col_map.get('Description', 4)] or '').strip()
        truck_col = str(cells[col_map.get('Truck Number', 5)] or '').strip()
        txn_id = str(cells[col_map.get('TransactionId', 6)] or '').strip()

        # Parse amount
        try:
            amount = Decimal(str(raw_amount))
            if amount <= 0:
                continue
        except (InvalidOperation, TypeError, ValueError):
            continue

        # Parse date
        entry_date = parse_timestamp(raw_date)
        if not entry_date:
            entry_date = timezone.now().date()

        # --- 1. CREDITS (Top-ups) ---
        if nature == 'Credit':
            # Dedup check
            if txn_id and txn_id in existing_recharge_refs:
                summary.duplicate_recharges_skipped += 1
                continue

            # Map payment mode from From/To column
            payment_mode = TollRecharge.MODE_OTHER
            from_to_lower = from_to.lower()
            if 'net banking' in from_to_lower:
                payment_mode = TollRecharge.MODE_NET_BANKING
            elif 'upi' in from_to_lower:
                payment_mode = TollRecharge.MODE_UPI
            elif 'debit card' in from_to_lower or 'card' in from_to_lower:
                payment_mode = TollRecharge.MODE_DEBIT_CARD
            elif 'neft' in from_to_lower or 'rtgs' in from_to_lower or 'bank transfer' in from_to_lower:
                payment_mode = TollRecharge.MODE_BANK_TRANSFER

            recharges_to_create.append(TollRecharge(
                toll_account=target_account,
                company_account=None,  # Unallocated - to be reconciled later
                date=entry_date,
                amount=amount,
                payment_mode=payment_mode,
                reference_number=txn_id,
                description=f"Imported from Boss Summary ({from_to}: {description})" if from_to else f"Imported: {description}",
                created_by=user,
            ))

            if txn_id:
                existing_recharge_refs.add(txn_id)

            summary.total_credit_amount += amount

        # --- 2. DEBITS (Platform & Service Fees) ---
        elif nature == 'Debit':
            # Skip internal FasTag card sweeps (toll plaza crossings come from bank files)
            if description == 'FasTag Recharge':
                continue

            # Dedup check
            if txn_id and txn_id in existing_entry_refs:
                summary.duplicate_entries_skipped += 1
                continue

            # Check if a vehicle is mentioned in truck column or in description
            matched_vehicle = None
            if truck_col:
                matched_vehicle = vehicle_lookup.get(normalize_plate(truck_col))
            if not matched_vehicle and description:
                plate_match = re.search(r'([A-Z]{2}\s*\d{2}\s*[A-Z]{1,3}\s*\d{4})', description, re.IGNORECASE)
                if plate_match:
                    matched_vehicle = vehicle_lookup.get(normalize_plate(plate_match.group(1)))
                if not matched_vehicle:
                    for token in re.findall(r'[A-Za-z0-9]+', description):
                        norm_token = normalize_plate(token)
                        if norm_token and len(norm_token) >= 6 and norm_token in vehicle_lookup:
                            matched_vehicle = vehicle_lookup[norm_token]
                            break

            platform_fees_to_create.append(TollEntry(
                toll_account=target_account,
                vehicle=matched_vehicle,
                date=entry_date,
                amount=amount,
                toll_plaza=f"BlackBuck: {description}"[:200],
                transaction_reference=txn_id,
                description=f"BlackBuck Fee: {description} ({from_to})" if from_to else f"BlackBuck Fee: {description}",
                created_by=user,
            ))

            if txn_id:
                existing_entry_refs.add(txn_id)

            summary.total_debit_amount += amount

    wb.close()

    # Batch create in atomic transaction
    with transaction.atomic():
        if recharges_to_create:
            TollRecharge.objects.bulk_create(recharges_to_create)
        if platform_fees_to_create:
            TollEntry.objects.bulk_create(platform_fees_to_create)
        if recharges_to_create or platform_fees_to_create:
            target_account.refresh_balance()

    summary.recharges_created = len(recharges_to_create)
    summary.platform_fees_created = len(platform_fees_to_create)
    return summary


def import_fasttag_statement(file_obj, toll_account=None, auto_link_trips=True, user=None):
    """
    Smart import that auto-detects the file type and delegates accordingly.

    Returns:
        ImportSummary with results.
    """
    # First pass: detect file type
    try:
        wb = openpyxl.load_workbook(file_obj, data_only=True)
    except Exception as e:
        summary = ImportSummary()
        summary.errors.append(f"Failed to open file: {e}")
        return summary

    sheet = wb.active
    _, _, file_type = find_header_row(sheet)
    wb.close()

    # Reset file position for re-reading
    if hasattr(file_obj, 'seek'):
        file_obj.seek(0)

    if file_type == 'bank_statement':
        return import_bank_statement(file_obj, toll_account, auto_link_trips, user)
    elif file_type == 'boss_summary':
        return import_boss_summary(file_obj, toll_account, user)
    else:
        summary = ImportSummary()
        summary.errors.append(
            "Could not detect the file type. Please upload a valid FASTag bank statement "
            "(AXIS / IDFC / LIVQUIK) or a Boss Account Summary file."
        )
        return summary
