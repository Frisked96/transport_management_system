"""
Services and configuration for Fleet Tyre Management
Provides layout mapping and standardized position definitions for 18-wheeler trucks + 1 Stepney.
"""
from typing import Dict, Any, List, Optional
from decimal import Decimal


# --- Standardized Tyre Positions for 18-Wheeler (Horse + Trailer) + 1 Stepney (19 Total) ---

TYRE_POSITIONS: List[Dict[str, Any]] = [
    # --- HORSE (CABIN / PRIME MOVER) - 6 TYRES ---
    # Axle 1: Front / Steer Axle (2 tyres)
    {
        'code': 'FL',
        'name': 'Front Left',
        'display': 'FL - Front Left (Steer)',
        'section': 'horse',
        'axle_num': 1,
        'axle_name': 'Axle 1 (Front Steer)',
        'side': 'left',
        'is_dual': False,
    },
    {
        'code': 'FR',
        'name': 'Front Right',
        'display': 'FR - Front Right (Steer)',
        'section': 'horse',
        'axle_num': 1,
        'axle_name': 'Axle 1 (Front Steer)',
        'side': 'right',
        'is_dual': False,
    },

    # Axle 2: Horse Drive Axle (4 tyres - duals on each side)
    {
        'code': 'DLO',
        'name': 'Drive Left Outer',
        'display': 'DLO - Drive Left Outer',
        'section': 'horse',
        'axle_num': 2,
        'axle_name': 'Axle 2 (Drive Duals)',
        'side': 'left_outer',
        'is_dual': True,
    },
    {
        'code': 'DLI',
        'name': 'Drive Left Inner',
        'display': 'DLI - Drive Left Inner',
        'section': 'horse',
        'axle_num': 2,
        'axle_name': 'Axle 2 (Drive Duals)',
        'side': 'left_inner',
        'is_dual': True,
    },
    {
        'code': 'DRI',
        'name': 'Drive Right Inner',
        'display': 'DRI - Drive Right Inner',
        'section': 'horse',
        'axle_num': 2,
        'axle_name': 'Axle 2 (Drive Duals)',
        'side': 'right_inner',
        'is_dual': True,
    },
    {
        'code': 'DRO',
        'name': 'Drive Right Outer',
        'display': 'DRO - Drive Right Outer',
        'section': 'horse',
        'axle_num': 2,
        'axle_name': 'Axle 2 (Drive Duals)',
        'side': 'right_outer',
        'is_dual': True,
    },

    # --- TRAILER - 12 TYRES (3 AXLES x 4 DUALS) ---
    # Axle 3: Trailer Axle 1 (4 tyres)
    {
        'code': 'T1_LO',
        'name': 'Trailer 1 Left Outer',
        'display': 'T1-LO - Trailer 1 Left Outer',
        'section': 'trailer',
        'axle_num': 3,
        'axle_name': 'Trailer Axle 1',
        'side': 'left_outer',
        'is_dual': True,
    },
    {
        'code': 'T1_LI',
        'name': 'Trailer 1 Left Inner',
        'display': 'T1-LI - Trailer 1 Left Inner',
        'section': 'trailer',
        'axle_num': 3,
        'axle_name': 'Trailer Axle 1',
        'side': 'left_inner',
        'is_dual': True,
    },
    {
        'code': 'T1_RI',
        'name': 'Trailer 1 Right Inner',
        'display': 'T1-RI - Trailer 1 Right Inner',
        'section': 'trailer',
        'axle_num': 3,
        'axle_name': 'Trailer Axle 1',
        'side': 'right_inner',
        'is_dual': True,
    },
    {
        'code': 'T1_RO',
        'name': 'Trailer 1 Right Outer',
        'display': 'T1-RO - Trailer 1 Right Outer',
        'section': 'trailer',
        'axle_num': 3,
        'axle_name': 'Trailer Axle 1',
        'side': 'right_outer',
        'is_dual': True,
    },

    # Axle 4: Trailer Axle 2 (4 tyres)
    {
        'code': 'T2_LO',
        'name': 'Trailer 2 Left Outer',
        'display': 'T2-LO - Trailer 2 Left Outer',
        'section': 'trailer',
        'axle_num': 4,
        'axle_name': 'Trailer Axle 2',
        'side': 'left_outer',
        'is_dual': True,
    },
    {
        'code': 'T2_LI',
        'name': 'Trailer 2 Left Inner',
        'display': 'T2-LI - Trailer 2 Left Inner',
        'section': 'trailer',
        'axle_num': 4,
        'axle_name': 'Trailer Axle 2',
        'side': 'left_inner',
        'is_dual': True,
    },
    {
        'code': 'T2_RI',
        'name': 'Trailer 2 Right Inner',
        'display': 'T2-RI - Trailer 2 Right Inner',
        'section': 'trailer',
        'axle_num': 4,
        'axle_name': 'Trailer Axle 2',
        'side': 'right_inner',
        'is_dual': True,
    },
    {
        'code': 'T2_RO',
        'name': 'Trailer 2 Right Outer',
        'display': 'T2-RO - Trailer 2 Right Outer',
        'section': 'trailer',
        'axle_num': 4,
        'axle_name': 'Trailer Axle 2',
        'side': 'right_outer',
        'is_dual': True,
    },

    # Axle 5: Trailer Axle 3 (4 tyres)
    {
        'code': 'T3_LO',
        'name': 'Trailer 3 Left Outer',
        'display': 'T3-LO - Trailer 3 Left Outer',
        'section': 'trailer',
        'axle_num': 5,
        'axle_name': 'Trailer Axle 3',
        'side': 'left_outer',
        'is_dual': True,
    },
    {
        'code': 'T3_LI',
        'name': 'Trailer 3 Left Inner',
        'display': 'T3-LI - Trailer 3 Left Inner',
        'section': 'trailer',
        'axle_num': 5,
        'axle_name': 'Trailer Axle 3',
        'side': 'left_inner',
        'is_dual': True,
    },
    {
        'code': 'T3_RI',
        'name': 'Trailer 3 Right Inner',
        'display': 'T3-RI - Trailer 3 Right Inner',
        'section': 'trailer',
        'axle_num': 5,
        'axle_name': 'Trailer Axle 3',
        'side': 'right_inner',
        'is_dual': True,
    },
    {
        'code': 'T3_RO',
        'name': 'Trailer 3 Right Outer',
        'display': 'T3-RO - Trailer 3 Right Outer',
        'section': 'trailer',
        'axle_num': 5,
        'axle_name': 'Trailer Axle 3',
        'side': 'right_outer',
        'is_dual': True,
    },

    # --- STEPNEY / SPARE (1 TYRE) ---
    {
        'code': 'STP',
        'name': 'Stepney (Spare)',
        'display': 'STP - Stepney (Spare Tyre)',
        'section': 'spare',
        'axle_num': None,
        'axle_name': 'Spare Carrier',
        'side': 'spare',
        'is_dual': False,
    },
]

# Quick code lookup
TYRE_POSITION_MAP = {p['code']: p for p in TYRE_POSITIONS}
TYRE_POSITION_NAME_MAP = {p['code']: p['name'] for p in TYRE_POSITIONS}

# Grouped choices for HTML <select> with <optgroup>
TYRE_POSITION_CHOICES = [
    ('Horse (Cabin) - 6 Wheels', [
        ('FL', 'FL - Front Left (Steer)'),
        ('FR', 'FR - Front Right (Steer)'),
        ('DLO', 'DLO - Drive Left Outer'),
        ('DLI', 'DLI - Drive Left Inner'),
        ('DRI', 'DRI - Drive Right Inner'),
        ('DRO', 'DRO - Drive Right Outer'),
    ]),
    ('Trailer - 12 Wheels', [
        ('T1_LO', 'T1-LO - Trailer 1 Left Outer'),
        ('T1_LI', 'T1-LI - Trailer 1 Left Inner'),
        ('T1_RI', 'T1-RI - Trailer 1 Right Inner'),
        ('T1_RO', 'T1-RO - Trailer 1 Right Outer'),
        ('T2_LO', 'T2-LO - Trailer 2 Left Outer'),
        ('T2_LI', 'T2-LI - Trailer 2 Left Inner'),
        ('T2_RI', 'T2-RI - Trailer 2 Right Inner'),
        ('T2_RO', 'T2-RO - Trailer 2 Right Outer'),
        ('T3_LO', 'T3-LO - Trailer 3 Left Outer'),
        ('T3_LI', 'T3-LI - Trailer 3 Left Inner'),
        ('T3_RI', 'T3-RI - Trailer 3 Right Inner'),
        ('T3_RO', 'T3-RO - Trailer 3 Right Outer'),
    ]),
    ('Spare Tyre', [
        ('STP', 'STP - Stepney (Spare Tyre)'),
    ]),
]

# Flat choices for Model field choices
FLAT_TYRE_POSITION_CHOICES = [(p['code'], p['display']) for p in TYRE_POSITIONS]

# Alias map for backwards compatibility / legacy string formats
POSITION_ALIASES = {
    'FRONT LEFT': 'FL',
    'FRONT-LEFT': 'FL',
    'STEER LEFT': 'FL',
    'FRONT RIGHT': 'FR',
    'FRONT-RIGHT': 'FR',
    'STEER RIGHT': 'FR',
    'DRIVE LEFT OUTER': 'DLO',
    'DRIVE LEFT INNER': 'DLI',
    'DRIVE RIGHT INNER': 'DRI',
    'DRIVE RIGHT OUTER': 'DRO',
    'REAR LEFT': 'DLO',
    'REAR RIGHT': 'DRO',
    'REAR LEFT 1': 'DLO',
    'REAR RIGHT 1': 'DRO',
    'TRAILER 1 LEFT OUTER': 'T1_LO',
    'T1-LO': 'T1_LO',
    'T1-LI': 'T1_LI',
    'T1-RI': 'T1_RI',
    'T1-RO': 'T1_RO',
    'T2-LO': 'T2_LO',
    'T2-LI': 'T2_LI',
    'T2-RI': 'T2_RI',
    'T2-RO': 'T2_RO',
    'T3-LO': 'T3_LO',
    'T3-LI': 'T3_LI',
    'T3-RI': 'T3_RI',
    'T3-RO': 'T3_RO',
    'STEPNEY': 'STP',
    'STEPP': 'STP',
    'SPARE': 'STP',
    'SPARE TYRE': 'STP',
}


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
