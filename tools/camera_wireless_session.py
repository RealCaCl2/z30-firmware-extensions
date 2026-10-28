"""Prepare or run one bounded Z30 C1.20 USB diagnostic service session.

Default is a plan with no device access. --check-transport only repeats the
known region-read rejection. --execute changes service state and invokes the
stock same-value setting write and exit: it is NOT an entirely read-only run.
No region/power modification, arbitrary command, firmware or restore upload is
implemented. Entry/exit remains experimental; response success alone does not
prove physical recovery. Optional memory reads are restricted to six ranges.
"""
import argparse
import ctypes
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from inspect_camera_usb import device_info

SIZE = 0x1FFFC
MEMORY_READS = (
    ('host_rom', 0x10020000, 16),
    ('network_vectors', 0xC1000000, 64),
    ('network_state_country', 0xC12007EC, 36),
    ('bandwidth_cache', 0xC1201F2C, 8),
    ('interface_nvram', 0xC12020D4, 8),
    ('radio_firmware_slots', 0xC1202164, 12),
)
PLAN = {
    'target': 'Nikon Z 30 V1.20 only',
    'steps': [
        'Read DeviceInfo and require the exact model and firmware version.',
        'Require ordinary-mode F006 rejection A004 with five readable response parameters.',
        'Enter service mode once: 94FD(F001,0,0,0,0).',
        'Read block 8 twice: 94FF(F02D,0,131068,0,0); save and compare both copies.',
        'Read region: 94FF(F006,0,0,0,0); retain only its first two assigned bytes.',
        'If backed-up byte 0x351 is 0 or 1, use F064 with that same value to snapshot the stock exit cache.',
        'Read block 8 again after the same-value write; require equality to the backup before requesting exit.',
        'Request F002 exit once, then check ordinary DeviceInfo and require F006 to be rejected again.',
    ],
    'known_effects': [
        'F001/F002 request real service-mode transitions through a communication backend.',
        'F064 and F002 invoke block-8 setting writes/notifications even when the byte value is unchanged.',
        'Successful F002 response does not prove hardware recovery.',
        'If no consistent backup exists, or byte 0x351 is outside 0/1, automated cleanup stops rather than overwriting an unknown value.',
        'Failure can leave service state unknown or open and require physical investigation; power-cycle recovery is not established.',
    ],
    'excluded': ['F004 region write', 'F02E restoration upload', 'power changes', 'firmware upload', 'arbitrary commands'],
    'optional_memory_snapshot': [dict(name=name, address=hex(address), length=length)
                                 for name, address, length in MEMORY_READS],
    'optional_memory_scope': 'Requires --read-wireless-memory and --source; service-mode caches are not effective RF settings.',
    'status': 'experimental_bounded_sessions_observed_no_radio_validation',
}


def validate_request(op, params):
    if op == 0x1001 and params == ():
        return False, None
    if len(params) != 5 or params[3:] != (0, 0):
        raise ValueError('Only fixed diagnostic request shapes are allowed')
    command = params[0]
    if op == 0x94FD and command in (0xF001, 0xF002) and params[1:3] == (0, 0):
        return False, None
    if op == 0x94FD and command == 0xF064 and params[1] in (0, 1) and params[2] == 0:
        return False, None
    if op == 0x94FF and command == 0xF006 and params[1:3] == (0, 0):
        return True, 4
    if op == 0x94FF and command == 0xF02D and params[1:3] == (0, SIZE):
        return True, SIZE
    if op == 0x94FF and command == 0xF032 and params[1:3] in {(a, n) for _, a, n in MEMORY_READS}:
        return True, params[2]
    raise ValueError('Command is outside the diagnostic allowlist')


class WpdTransport:
    def __init__(self):
        import ptp_timing as ptp
        self.ptp = ptp
        self.dev = None
        ptp.comtypes.CoInitialize()
        try:
            found = [d for d in ptp.devices() if 'vid_04b0' in d.lower()]
            if len(found) != 1:
                raise RuntimeError(f'Expected one Nikon device; found {len(found)}')
            self.dev = ptp.cc.CreateObject(ptp.api.PortableDevice, interface=ptp.api.IPortableDevice)
            self.dev.Open(found[0], ptp.values())
        except BaseException:
            self.close()
            raise

    def close(self):
        try:
            if self.dev is not None:
                self.dev.Close()
                self.dev = None
        finally:
            self.ptp.comtypes.CoUninitialize()

    def response(self, r):
        ptp = self.ptp
        code = r.GetUnsignedIntegerValue(ptp.RESPONSE)
        params = []
        try:
            coll = r.GetIPortableDevicePropVariantCollectionValue(ptp.RESPONSE_PARAMS)
            count_out = ctypes.c_ulong()
            coll.GetCount(ctypes.byref(count_out))
            count = count_out.value
            if count > 5:
                raise ValueError('Too many PTP response parameters')
            for i in range(count):
                value = ptp.api.tag_inner_PROPVARIANT()
                coll.GetAt(i, ctypes.byref(value))
                if value.vt not in (3, 19):
                    raise ValueError('Unexpected PTP response parameter type')
                params.append(ctypes.c_uint32.from_address(ctypes.addressof(value) + 8).value)
        except ptp.comtypes.COMError:
            pass  # Service preflight requires all five, so this fails closed.
        return code, params

    def __call__(self, op, params=()):
        params = tuple(params)
        read, expected = validate_request(op, params)
        ptp = self.ptp
        if op == 0x1001:
            code, data, _ = ptp.mtp(self.dev, op)
            return code, [], data
        v = ptp.command(ptp.DATA_TO_READ if read else ptp.NO_DATA)
        v.SetUnsignedIntegerValue(ptp.OPCODE, op)
        coll = ptp.cc.CreateObject(ptp.typ.PortableDevicePropVariantCollection,
                                  interface=ptp.api.IPortableDevicePropVariantCollection)
        for parameter in params:
            pv = ptp.api.tag_inner_PROPVARIANT()
            pv.vt = 19
            ctypes.c_ulong.from_address(ctypes.addressof(pv) + 8).value = parameter
            coll.Add(ctypes.pointer(pv))
        v.SetIPortableDevicePropVariantCollectionValue(ptp.PARAMS, coll)
        r = ptp.send(self.dev, v)
        if not read:
            code, response = self.response(r)
            return code, response, b''
        context = r.GetStringValue(ptp.CONTEXT)
        parts, got = [], 0
        try:
            total = r.GetUnsignedLargeIntegerValue(ptp.TOTAL)
            if total not in (0, expected, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF):
                raise ValueError(f'Unexpected diagnostic payload size {total}')
            maximum = expected + int(total in (0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF))
            while total != 0 and got < maximum:
                length = min(maximum - got, 65536)
                buffer = (ctypes.c_ubyte * length)()
                v = ptp.command(ptp.READ_DATA)
                v.SetStringValue(ptp.CONTEXT, context)
                v.SetUnsignedIntegerValue(ptp.TO_READ, length)
                v.SetBufferValue(ptp.DATA, buffer, length)
                result = ptp.send(self.dev, v)
                pointer, size = result.GetBufferValue(ptp.DATA)
                try:
                    count = result.GetUnsignedIntegerValue(ptp.NUM_READ)
                    if count > size or count > length:
                        raise ValueError('Invalid diagnostic byte count')
                    parts.append(ctypes.string_at(pointer, count))
                finally:
                    ctypes.windll.ole32.CoTaskMemFree(pointer)
                got += count
                if count < length:
                    break
            if got > expected:
                raise ValueError('Diagnostic payload exceeds the expected size')
        finally:
            end = ptp.command(ptp.END_TRANSFER)
            end.SetStringValue(ptp.CONTEXT, context)
            final = ptp.send(self.dev, end)
        code, response = self.response(final)
        return code, response, b''.join(parts)


def inspect_identity(tx):
    code, _, data = tx(0x1001, ())
    if code != 0x2001:
        raise RuntimeError('DeviceInfo failed')
    identity = device_info(data)
    if (identity['model'], identity['firmware']) != ('Z 30', 'V1.20'):
        raise RuntimeError('This diagnostic is restricted to Z30 V1.20')
    return {k: identity[k] for k in ('manufacturer', 'model', 'firmware')}


def check_transport(tx):
    identity = inspect_identity(tx)
    code, params, data = tx(0x94FF, (0xF006, 0, 0, 0, 0))
    valid = code == 0xA004 and len(params) == 5 and not data
    return dict(device=identity, response=hex(code), response_parameters=[hex(p) for p in params],
                payload_bytes=len(data), ready_for_review=valid,
                service_mode_entry_sent=False, write_command_sent=False)


def capture_wireless_memory(command, output, expectations, report):
    """Read only fixed ranges; do not follow pointers or request driver changes."""
    import hashlib
    import struct
    captured = {}
    report.update(scope='RAM snapshot during service mode; not an active-radio readback', reads=[])
    for name, address, length in MEMORY_READS:
        raw = command(0x94FF, (0xF032, address, length, 0, 0), size=length)
        (output / f'{name}.bin').write_bytes(raw)
        captured[name] = raw
        item = dict(name=name, address=hex(address), length=length,
                    sha256=hashlib.sha256(raw).hexdigest())
        report['reads'].append(item)
        if name in ('host_rom', 'network_vectors'):
            item['matches_stock_prefix'] = raw == expectations[name]
            if not item['matches_stock_prefix']:
                raise RuntimeError(f'{name} does not match the stock prefix; stopped further memory reads')
    state = captured['network_state_country']
    country = state[28:32].split(b'\0')[0]
    interface, alternate = struct.unpack('<2I', captured['interface_nvram'])
    slots = struct.unpack('<3I', captured['radio_firmware_slots'])
    report['decoded'] = dict(
        cached_vht_flag=state[0], internal_state_word=struct.unpack_from('<I', state, 4)[0],
        cached_country_hex=state[28:32].hex(),
        cached_country_ascii=country.decode('ascii') if all(32 <= c <= 126 for c in country) else None,
        cached_country_revision=struct.unpack_from('<i', state, 32)[0],
        cached_bandwidth_band_mask=list(struct.unpack('<2I', captured['bandwidth_cache'])),
        interface_version=interface, alternate_nvram_pointer=hex(alternate),
        radio_firmware_slot_pointers={'1': hex(slots[0]), '3': hex(slots[1]), '2': hex(slots[2])})
    report['limitations'] = [
        'Stock prefixes identify sampled bytes, not the complete running firmware.',
        'Service entry may stop or change wireless activity; values may be uninitialized or stale.',
        'Cached country/bandwidth/VHT do not prove effective driver or RF configuration.',
        'A zero alternate pointer does not exclude earlier uploads or lower-level OTP overrides.',
        'No band/chanspec/qtxpower driver query is issued; no power or 5 GHz change is made.',
    ]


def acquire(tx, output, *, memory_expectations=None):
    """Experimental state-changing workflow. Caller must obtain authorization."""
    output.mkdir(parents=True, exist_ok=False)
    report = dict(timestamp_utc=datetime.now(timezone.utc).isoformat(), steps=[],
                  state='not_entered', radio_unlock_attempted=False,
                  limitations=PLAN['known_effects'])
    entered, baseline = False, None

    def save():
        (output / 'session.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')

    def command(op, params, *, reject=False, size=None):
        validate_request(op, params)
        report['steps'].append(dict(operation=hex(op), parameters=[hex(p) for p in params], status='pending'))
        save()
        code, values, data = tx(op, params)
        report['steps'][-1].update(response=hex(code), response_parameters=[hex(p) for p in values],
                                   bytes_received=len(data), status='responded')
        save()
        expected = 0xA004 if reject else 0x2001
        if code != expected or len(values) != 5 or (not reject and values[0] != 0x2001):
            raise RuntimeError('Service response did not confirm the requested step')
        if size is not None and len(data) != size:
            raise RuntimeError('Diagnostic transfer length mismatch')
        return data

    try:
        report['device'] = inspect_identity(tx)
        command(0x94FF, (0xF006, 0, 0, 0, 0), reject=True, size=0)
        report['state'] = 'entry_outcome_unknown'
        save()
        command(0x94FD, (0xF001, 0, 0, 0, 0))
        entered = True
        report['state'] = 'service_entered'
        first = command(0x94FF, (0xF02D, 0, SIZE, 0, 0), size=SIZE)
        (output / 'block8-first.bin').write_bytes(first)
        second = command(0x94FF, (0xF02D, 0, SIZE, 0, 0), size=SIZE)
        (output / 'block8-second.bin').write_bytes(second)
        if first != second:
            raise RuntimeError('Block-8 reads differ; no consistent recovery baseline')
        baseline = first
        import hashlib
        report['block8_sha256'] = hashlib.sha256(first).hexdigest()
        report['block8_offset_351'] = first[0x351]
        region = command(0x94FF, (0xF006, 0, 0, 0, 0), size=4)
        report['region_byte'], report['profile_byte'] = region[:2]
        if memory_expectations is not None:
            report['wireless_memory'] = {}
            capture_wireless_memory(command, output, memory_expectations, report['wireless_memory'])
    except Exception as exc:
        report['error'] = str(exc)
    finally:
        if entered and baseline is not None and baseline[0x351] in (0, 1):
            try:
                # Stock F064 first snapshots the existing byte to the cache
                # later used by F002. Set the same value, never a new setting.
                command(0x94FD, (0xF064, baseline[0x351], 0, 0, 0))
                verified = command(0x94FF, (0xF02D, 0, SIZE, 0, 0), size=SIZE)
                (output / 'block8-after-cache.bin').write_bytes(verified)
                if verified != baseline:
                    raise RuntimeError('Block 8 differs after the same-value cache operation; stopped before exit')
                command(0x94FD, (0xF002, 0, 0, 0, 0))
                report['state'] = 'exit_reported_success_hardware_recovery_unverified'
                report['post_exit_device'] = inspect_identity(tx)
                command(0x94FF, (0xF006, 0, 0, 0, 0), reject=True, size=0)
                report['post_exit_service_access'] = 'rejected_again'
            except Exception as exc:
                report['cleanup_error'] = str(exc)
                report['state'] = 'recovery_required_state_unknown'
        elif entered:
            report['state'] = 'recovery_required_service_left_open'
            report['cleanup_error'] = 'No consistent supported setting baseline; did not invoke potentially destructive default cleanup'
        save()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--check-transport', action='store_true')
    modes.add_argument('--execute', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--read-wireless-memory', action='store_true',
                        help='During the session, read six exact audited ROM/RAM ranges via F032')
    parser.add_argument('--source', type=Path,
                        help='Matching stock C1.20 container, required to verify memory prefixes')
    args = parser.parse_args()
    expectations = None
    if args.read_wireless_memory:
        if not args.execute or args.source is None:
            parser.error('--read-wireless-memory requires --execute and --source')
        from z30_extensions.container import extract_eg
        from z30_extensions.platform.z30_120 import BASE, StockImage
        from probe_wireless_config import SOURCE
        data = extract_eg(args.source.read_bytes())
        StockImage(data)
        expectations = dict(host_rom=data[:16], network_vectors=data[SOURCE - BASE:SOURCE - BASE + 64])
    if not args.check_transport and not args.execute:
        print(json.dumps(PLAN, indent=2))
        return
    if args.execute and args.output is None:
        parser.error('--execute requires a new --output directory')
    tx = WpdTransport()
    try:
        result = (check_transport(tx) if args.check_transport else
                  acquire(tx, args.output, memory_expectations=expectations))
        print(json.dumps(result, indent=2))
        if args.execute and ('error' in result or 'cleanup_error' in result):
            raise SystemExit(1)
    finally:
        tx.close()


if __name__ == '__main__':
    main()
