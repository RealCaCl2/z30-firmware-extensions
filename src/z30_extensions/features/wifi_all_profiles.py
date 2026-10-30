"""Opt-in US/-1 country requests for all valid Z30 C 1.20 profiles.

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


def add(plan):
    # Check the complete recovered table, including reserved records and the
    # identity fields that we will leave untouched. Plan also pins stock SHA.
    expected_table = b''.join(
        struct.pack('<4sII', b'AA\0\0' if i in VALID_PROFILES else bytes(4),
                    i if i in VALID_PROFILES else 0, i)
        for i in range(SLOT_COUNT)
    )
    if plan.image.read(TABLE_BASE, len(expected_table)) != expected_table:
        raise ValueError('Unexpected stock Wi-Fi profile table')
    for profile in VALID_PROFILES:
        plan.edit(TABLE_BASE + RECORD_SIZE * profile, REQUEST,
                  f'Wi-Fi profile {profile}: request US/-1; preserve original index',
                  struct.pack('<4sI', b'AA\0\0', profile))
    plan.features['wifi_5ghz'] = {
        'scope': 'all-valid-profiles',
        'profiles': list(VALID_PROFILES),
        'requested_country': 'US',
        'requested_revision': -1,
        'preserved_reserved_profiles': list(RESERVED_PROFILES),
        'profile_index_fields_preserved': True,
        'unset_index_fallback_affected': True,
        'persistent_region_write': False,
        'runtime_code_added': False,
        'limitations': [
            'All 22 valid profiles and the unset-index fallback request US/-1; no actual-device profile is inferred.',
            'Initialization paths bypassing the country table remain unchanged.',
            'Driver acceptance, effective channels, AP behavior and 2.4 GHz behavior require hardware verification.',
            'This does not establish all-channel support, throughput or negotiated bandwidth.',
            'Effective channels and power depend on the driver country configuration and must suit the place of use.',
        ],
    }
