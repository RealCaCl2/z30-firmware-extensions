"""US/-1 Wi-Fi country requests for all valid Z30 C 1.20 profiles.

Only the country and revision fields change. The record's final index word is
also consumed outside Wi-Fi initialization and must retain its original value.
This is a country-request override, not proof of effective radio channels.
"""
import struct

TABLE_BASE = 0x114A37BC
SLOT_COUNT = 34
RECORD_SIZE = 12
VALID_PROFILES = tuple(range(1, 11)) + tuple(range(14, 26))
RESERVED_PROFILES = tuple(i for i in range(SLOT_COUNT) if i not in VALID_PROFILES)
REQUEST = struct.pack('<4si', b'US\0\0', -1)
EXPECTED_TABLE = b''.join(
    struct.pack('<4sII', b'AA\0\0' if i in VALID_PROFILES else bytes(4),
                i if i in VALID_PROFILES else 0, i)
    for i in range(SLOT_COUNT)
)


def add(plan):
    # Check the reserved records and index fields we leave untouched as well.
    if plan.image.read(TABLE_BASE, len(EXPECTED_TABLE)) != EXPECTED_TABLE:
        raise ValueError('Unexpected stock Wi-Fi profile table')
    for profile in VALID_PROFILES:
        plan.edit(TABLE_BASE + RECORD_SIZE * profile, REQUEST,
                  f'Wi-Fi profile {profile}: request US/-1 instead of AA/{profile}',
                  struct.pack('<4sI', b'AA\0\0', profile))
    plan.features['wifi_5ghz'] = {
        'profiles': list(VALID_PROFILES),
        'requested_country': 'US',
        'requested_revision': -1,
        'preserved_reserved_profiles': list(RESERVED_PROFILES),
        'profile_index_fields_preserved': True,
        'unset_index_fallback_affected': True,
        'persistent_region_write': False,
        'limitations': [
            'All 22 valid profiles and the unset-index fallback request US/-1; the profile of a given body is not read.',
            'Initialization paths bypassing the country table remain unchanged.',
            '2.4 GHz channels 12 and 13 may become unavailable on bodies that previously allowed them; this is inferred, not measured.',
            'No 5 GHz camera hotspot, throughput improvement or negotiated bandwidth is established.',
            'Effective channels and power depend on the driver country configuration and must suit the place of use.',
        ],
    }
