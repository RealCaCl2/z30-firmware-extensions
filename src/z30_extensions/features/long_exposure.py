"""Enable native extended M-mode times through a narrow flag-read hook."""
import struct
from ..thumb import Thumb
from ..platform import z30_120 as z30_platform
from ..platform import z7_380 as z7_platform

SPECS = {
    'z30': {'platform': z30_platform, 'getter': 0x100F303C, 'key': 0x11A3},
    'z7': {'platform': z7_platform, 'getter': 0x10070234, 'key': 0x11A3},
}
EXPECTED_PREFIX = bytes.fromhex('38b50c4600212160')


def read_hook(address, key, continuation):
    a = Thumb(address)
    a.load(3, 'extended_m_key', key)
    a.emit('9842')
    a.branch(1, 'original')
    a.emit('0120 0860 7047')
    a.label('original')
    a.emit(EXPECTED_PREFIX.hex())
    a.load(3, 'continuation', continuation | 1)
    a.emit('1847')
    return a.finish()


def add(plan, model='z30'):
    spec = SPECS[model]
    getter = spec['getter']
    address = plan.alloc(read_hook(plan.address, spec['key'], getter + 8))
    jump = bytes.fromhex('004b1847') + struct.pack('<I', address | 1)
    plan.edit(getter, jump, 'Read extended M flag as enabled', EXPECTED_PREFIX)
    plan.features['m900'] = {
        'read_hook': hex(address | 1), 'property_key': hex(spec['key']),
        'maximum_seconds': 900, 'stored_setting_unchanged': True,
        'enabled': 'always-on read override; native M-mode gating remains',
        'physical_900_second_capture_verified': False,
    }

# Backward-compatible Z30 aliases used by the original verification tooling.
GETTER = SPECS['z30']['getter']
KEY = SPECS['z30']['key']
