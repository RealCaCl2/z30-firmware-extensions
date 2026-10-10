"""Decode supported stock containers using input-derived XOR masks.

Known-zero offsets are version-specific observations, not bundled firmware
bytes or copied XOR tables. The full input hash is checked before decoding.
"""
import binascii
import json
import struct
from importlib.resources import files

from .platform.z30_120 import StockImage as StockImageZ30, STOCK_SHA256 as Z30_EG_SHA256, STOCK_SIZE as Z30_EG_SIZE
from .platform.z7_380 import (StockImage as StockImageZ7, STOCK_SHA256 as Z7_STOCK_SHA256,
                              STOCK_SIZE as Z7_STOCK_SIZE, MAIN_PAYLOAD_SHA256 as Z7_MAIN_PAYLOAD_SHA256)
from .platform.z30_120 import sha256

# Z30 C 1.20 outer upgrade container.
CONTAINER_SHA256 = '1a5449ef505f1f347ed4e066a6a3f588ea542240680e83f4ebcae3b16da3434a'
CONTAINER_SIZE = 34496798
ZERO_ANCHOR = 0x3A00

# Z7 C 3.80 outer upgrade container.
Z7_CONTAINER_SHA256 = '07cf19513ada5b770caec4cfc3531a6734c48aede28dcec037f0f8374ef441a2'
Z7_CONTAINER_SIZE = 34472614
Z7_ZERO_ANCHOR = 0x3A00


def _load_samples(package, filename):
    return json.loads(files(package).joinpath(filename).read_text(encoding='utf-8'))


def recover_mask(encoded_body, samples=None, zero_anchor=None):
    """Recover the 64 KiB XOR mask from known-zero plaintext observations."""
    if samples is None:
        samples = _load_samples('z30_extensions.platform', 'z30_120_zero_samples.json')
    if zero_anchor is None:
        zero_anchor = ZERO_ANCHOR
    encoded_body = bytes(encoded_body)
    if len(encoded_body) <= max(list(samples) + [zero_anchor + 255]):
        raise ValueError('Body is too short for this version-specific decoder')
    # A known-zero 256-byte aligned span yields column mask differences.
    columns = bytes(value ^ encoded_body[zero_anchor]
                    for value in encoded_body[zero_anchor:zero_anchor + 256])
    # At known-zero offsets: encoded[i] ^ columns[i & 255] = row ^ block.
    # Normalize row[0] to zero. Samples form a spanning tree of 512 nodes.
    values = [None] * 512
    values[0] = 0
    for offset in samples:
        row = (offset >> 8) & 255
        block = 256 + ((offset >> 16) & 255)
        value = encoded_body[offset] ^ columns[offset & 255]
        if values[row] is not None:
            recovered = values[row] ^ value
            if values[block] is not None and values[block] != recovered:
                raise ValueError('Inconsistent known-zero mask samples')
            values[block] = recovered
        elif values[block] is not None:
            values[row] = values[block] ^ value
        else:
            raise ValueError('Disconnected mask sample ordering')
    if any(value is None for value in values):
        raise ValueError('Incomplete mask recovery')
    return columns, bytes(values[:256]), bytes(values[256:])


def decode_body(encoded_body, samples=None, zero_anchor=None):
    columns, rows, blocks = recover_mask(encoded_body, samples=samples, zero_anchor=zero_anchor)
    # Build 64 KiB column/row mask; the high-byte mask repeats every 16 MiB.
    pattern = b''.join(columns.translate(bytes(v ^ row for v in range(256))) for row in rows)
    body = bytearray(encoded_body)
    for start in range(0, len(body), 65536):
        high = blocks[(start >> 16) & 255]
        stop = min(start + 65536, len(body))
        low = max(start, 32)
        body[low:stop] = bytes(value ^ pattern[i - start] ^ high
                               for i, value in enumerate(encoded_body[low:stop], low))
    return bytes(body)


def _extract_eg_z30(encoded):
    encoded = bytes(encoded)
    # No source inspection, decode, patching or file output precedes this guard.
    if len(encoded) != CONTAINER_SIZE or sha256(encoded) != CONTAINER_SHA256:
        raise ValueError('Original Z_30_0120.bin SHA-256 mismatch; only stock Z30 C 1.20 is supported')
    body = decode_body(encoded[32:])
    if body[:32] != b' ' * 32 or struct.unpack_from('>4I', body, 32) != (3, 144, 0, 0):
        raise ValueError('Unexpected decoded stock header')
    if body[-14:] != bytes(14) or binascii.crc_hqx(body[:-16], 0) != int.from_bytes(body[-16:-14], 'big'):
        raise ValueError('Decoded container CRC mismatch')
    cursor = 144
    main = None
    names = set()
    for index in range(3):
        raw_name, start, size, reserved0, reserved1 = struct.unpack_from('>16s4I', body, 48 + index * 32)
        name = raw_name.rstrip(b'\0')
        if name in names or reserved0 or reserved1 or start != cursor or size < 2 or start + size > len(body) - 16:
            raise ValueError('Unexpected decoded module bounds')
        names.add(name)
        module = body[start:start + size]
        if binascii.crc_hqx(module, 0):
            raise ValueError('Decoded module CRC mismatch')
        if name == b'eg2090_0120a0.bi':
            main = module
        cursor += size
    if cursor != len(body) - 16 or main is None:
        raise ValueError('Missing EG module or incomplete body coverage')
    # A second cryptographic identity guard confirms the entire recovered EG.
    return StockImageZ30(main).data


def _extract_eg_z7(encoded):
    samples = _load_samples('z30_extensions.platform', 'z7_380_zero_samples.json')
    encoded = bytes(encoded)
    if len(encoded) != Z7_CONTAINER_SIZE or sha256(encoded) != Z7_CONTAINER_SHA256:
        raise ValueError('Original Z_7_0380.bin SHA-256 mismatch; only stock Z7 C 3.80 is supported')
    body = decode_body(encoded[32:], samples=samples, zero_anchor=Z7_ZERO_ANCHOR)
    if body[:32] != b' ' * 32 or struct.unpack_from('>4I', body, 32) != (4, 176, 0, 0):
        raise ValueError('Unexpected decoded Z7 stock header')
    cursor = 176
    main = None
    names = set()
    for index in range(4):
        raw_name, start, size, reserved0, reserved1 = struct.unpack_from('>16s4I', body, 48 + index * 32)
        name = raw_name.rstrip(b'\0')
        if name in names or reserved0 or reserved1 or start != cursor or size < 2 or start + size > len(body) - 16:
            raise ValueError('Unexpected decoded Z7 module bounds')
        names.add(name)
        if name == b'eg1610_038000.bi':
            main = body[start:start + size]
        cursor += size
    if cursor != len(body) - 16 or main is None or len(main) < 2:
        raise ValueError('Missing Z7 EG module or incomplete body coverage')
    if sha256(main[:-2]) != Z7_MAIN_PAYLOAD_SHA256:
        raise ValueError('Recovered Z7 MAIN payload SHA-256 mismatch')
    # StockImageZ7 verifies the full module CRC and fingerprint before returning.
    return StockImageZ7(main).data


def extract_eg(encoded):
    """Extract the executable EG/MAIN module from a supported stock container."""
    encoded = bytes(encoded)
    if len(encoded) == CONTAINER_SIZE and sha256(encoded) == CONTAINER_SHA256:
        return _extract_eg_z30(encoded)
    if len(encoded) == Z7_CONTAINER_SIZE and sha256(encoded) == Z7_CONTAINER_SHA256:
        return _extract_eg_z7(encoded)
    raise ValueError('Original firmware SHA-256 mismatch; only stock Z30 C 1.20 or Z7 C 3.80 is supported')


def detect_model(encoded, extracted=False):
    """Return the model key for a stock container or extracted module.

    The check is intentionally cryptographic: a different firmware version or a
    modified image must be rejected before any patch plan is built.
    """
    encoded = bytes(encoded)
    if extracted:
        if len(encoded) == Z30_EG_SIZE and sha256(encoded) == Z30_EG_SHA256:
            return 'z30'
        if len(encoded) == Z7_STOCK_SIZE and sha256(encoded) == Z7_STOCK_SHA256:
            return 'z7'
        raise ValueError('Unsupported extracted firmware; expected stock Z30 C 1.20 EG or Z7 C 3.80 MAIN module')
    if len(encoded) == CONTAINER_SIZE and sha256(encoded) == CONTAINER_SHA256:
        return 'z30'
    if len(encoded) == Z7_CONTAINER_SIZE and sha256(encoded) == Z7_CONTAINER_SHA256:
        return 'z7'
    raise ValueError('Original firmware SHA-256 mismatch; only stock Z30 C 1.20 or Z7 C 3.80 is supported')
