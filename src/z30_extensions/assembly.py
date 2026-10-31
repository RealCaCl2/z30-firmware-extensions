"""Checked assembly helpers for optional Keystone-backed target features."""
import gzip
import hashlib
import struct
import sys
from importlib.resources import files

CACHE_NAME = 'asm_cache.gz'
_cache = None
recorder = None  # tools/build_asm_cache.py collects assembled code here.


def constant(register, value):
    return f'movw {register}, #{value & 65535}; movt {register}, #{value >> 16};'


def invoke(address, thumb=True):
    return constant('r12', address | 1 if thumb else address & ~1) + ' blx r12;'


def jump(site, target, length=8):
    if site % 4 == 0:
        code = bytes.fromhex('dff800f0') + struct.pack('<I', target | 1)
    else:
        code = bytes.fromhex('dff804f0 00bf') + struct.pack('<I', target | 1)
    if length < len(code) or (length - len(code)) % 2:
        raise ValueError('Jump does not fit the displaced instruction window')
    return code + bytes.fromhex('00bf') * ((length - len(code)) // 2)


def cache_key(source, address):
    return hashlib.sha256(f'{address}:{source}'.encode()).hexdigest()


def load_cache():
    """Read the optional pre-assembled code; source checkouts normally have none."""
    resource = files('z30_extensions').joinpath(CACHE_NAME)
    if not resource.is_file():
        return {}
    lines = gzip.decompress(resource.read_bytes()).decode('ascii').splitlines()
    return {key: bytes.fromhex(code) for key, code in (line.split(' ') for line in lines)}


def keystone_assemble(source, address):
    try:
        from keystone import KS_ARCH_ARM, KS_MODE_THUMB, KS_MODE_V8, Ks
    except ImportError as error:
        if getattr(sys, 'frozen', False):
            message = 'This feature combination is missing from the pre-assembled data of this program.'
        elif isinstance(error, ModuleNotFoundError) and error.name == 'keystone':
            message = 'Keystone is not installed; run python -m pip install ".[sound]".'
        else:
            message = ('Keystone is installed but failed to load; check its native library '
                       f'and Python compatibility (see README). Original error: {error}')
        raise ValueError(message) from error
    raw, _ = Ks(KS_ARCH_ARM, KS_MODE_THUMB | KS_MODE_V8).asm(source, addr=address, as_bytes=True)
    return None if raw is None else bytes(raw)


def assemble(source, address, capacity):
    global _cache
    if _cache is None:
        _cache = load_cache()
    # The key covers the exact input, so an outdated entry is simply never hit.
    key = cache_key(source, address)
    raw = _cache.get(key)
    if raw is None:
        raw = keystone_assemble(source, address)
    if raw is None or len(raw) > capacity:
        raise ValueError(f'Assembled function exceeds its {capacity}-byte slot')
    if recorder is not None:
        recorder[key] = raw
    return raw
