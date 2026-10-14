"""
Services and layout generation for Fleet Tyre Management.
Provides dynamic layout mapping and position normalization for 18-wheeler trucks + 1 Stepney.
"""
from typing import Dict, Any, Optional

from fleet.constants import (
    TYRE_POSITIONS,
    TYRE_POSITION_MAP,
    TYRE_POSITION_NAME_MAP,
    TYRE_POSITION_CHOICES,
    FLAT_TYRE_POSITION_CHOICES,
    POSITION_ALIASES,
)


def normalize_tyre_position(position_str: Optional[str]) -> str:
    """
    Normalizes a position string to its canonical position code.
    Example: 'Front Left' -> 'FL', 't1-lo' -> 'T1_LO', 'FL' -> 'FL'.
    """
    if not position_str:
        return ''
    cleaned = str(position_str).strip().upper()
    if cleaned in TYRE_POSITION_MAP:
        return cleaned
    cleaned_hyphen = cleaned.replace('-', '_')
    if cleaned_hyphen in TYRE_POSITION_MAP:
        return cleaned_hyphen
    if cleaned in POSITION_ALIASES:
        return POSITION_ALIASES[cleaned]
    return cleaned


def get_vehicle_tyre_layout(vehicle) -> Dict[str, Any]:
    """
    Constructs a structured 18+1 tyre layout data structure for a vehicle.
    Maps all currently mounted tyres to their wheel positions.
    """
    mounted_tyres = list(vehicle.tyres.select_related('vendor').all())
    
    # Map normalized position -> tyre
    position_to_tyre = {}
    unassigned_tyres = []

    for tyre in mounted_tyres:
        norm_pos = normalize_tyre_position(tyre.current_position)
        if norm_pos in TYRE_POSITION_MAP:
            position_to_tyre[norm_pos] = tyre
        else:
            unassigned_tyres.append(tyre)

    def _slot(code: str) -> Dict[str, Any]:
        meta = TYRE_POSITION_MAP[code]
        tyre = position_to_tyre.get(code)
        return {
            'code': code,
            'name': meta['name'],
            'display': meta['display'],
            'axle_name': meta['axle_name'],
            'side': meta['side'],
            'is_dual': meta['is_dual'],
            'tyre': tyre,
            'is_mounted': tyre is not None,
        }

    # 1. Horse Axles
    horse_axles = [
        {
            'axle_num': 1,
            'name': 'Axle 1 (Front Steer)',
            'type': 'single',
            'wheels': {
                'left': _slot('FL'),
                'right': _slot('FR'),
            }
        },
        {
            'axle_num': 2,
            'name': 'Axle 2 (Drive Duals)',
            'type': 'dual',
            'wheels': {
                'left_outer': _slot('DLO'),
                'left_inner': _slot('DLI'),
                'right_inner': _slot('DRI'),
                'right_outer': _slot('DRO'),
            }
        }
    ]

    # 2. Trailer Axles
    trailer_axles = [
        {
            'axle_num': 3,
            'name': 'Axle 3 (Trailer 1)',
            'type': 'dual',
            'wheels': {
                'left_outer': _slot('T1_LO'),
                'left_inner': _slot('T1_LI'),
                'right_inner': _slot('T1_RI'),
                'right_outer': _slot('T1_RO'),
            }
        },
        {
            'axle_num': 4,
            'name': 'Axle 4 (Trailer 2)',
            'type': 'dual',
            'wheels': {
                'left_outer': _slot('T2_LO'),
                'left_inner': _slot('T2_LI'),
                'right_inner': _slot('T2_RI'),
                'right_outer': _slot('T2_RO'),
            }
        },
        {
            'axle_num': 5,
            'name': 'Axle 5 (Trailer 3)',
            'type': 'dual',
            'wheels': {
                'left_outer': _slot('T3_LO'),
                'left_inner': _slot('T3_LI'),
                'right_inner': _slot('T3_RI'),
                'right_outer': _slot('T3_RO'),
            }
        }
    ]

    # 3. Stepney / Spare
    stepney_slot = _slot('STP')

    # Total counts
    total_positions = len(TYRE_POSITIONS)  # 19
    mounted_count = len(position_to_tyre)
    vacant_count = total_positions - mounted_count
    completion_pct = round((mounted_count / total_positions) * 100) if total_positions else 0

    return {
        'horse': {
            'title': 'Cabin / Horse (6 Wheels)',
            'axles': horse_axles,
        },
        'trailer': {
            'title': 'Trailer (12 Wheels)',
            'axles': trailer_axles,
        },
        'stepney': stepney_slot,
        'unassigned_tyres': unassigned_tyres,
        'stats': {
            'total_positions': total_positions,
            'total_mounted': mounted_count,
            'total_vacant': vacant_count,
            'completion_pct': completion_pct,
            'has_unassigned': len(unassigned_tyres) > 0,
        }
    }


__all__ = [
    'TYRE_POSITIONS',
    'TYRE_POSITION_MAP',
    'TYRE_POSITION_NAME_MAP',
    'TYRE_POSITION_CHOICES',
    'FLAT_TYRE_POSITION_CHOICES',
    'POSITION_ALIASES',
    'normalize_tyre_position',
    'get_vehicle_tyre_layout',
]
