"""Profile-7 Wi-Fi country request override for Z30 C 1.20."""
import struct

ENTRY = 0x114A3810
EXPECTED = struct.pack('<4sII', b'AA\0\0', 7, 7)
REPLACEMENT = struct.pack('<4siI', b'US\0\0', -1, 7)


def add(plan):
    plan.edit(ENTRY, REPLACEMENT,
              'Wi-Fi profile 7: request US/-1 instead of AA/7', EXPECTED)
    plan.features['wifi_5ghz'] = {
        'profile': 7,
        'requested_country': 'US',
        'requested_revision': -1,
        'unset_index_fallback_affected': True,
        'persistent_region_write': False,
        'limitations': [
            'Only profile 7 and its unset-index fallback are overridden.',
            'No 5 GHz camera hotspot, throughput improvement or negotiated bandwidth is established.',
            'Effective channels and power depend on the driver country configuration and must suit the place of use.',
        ],
    }
