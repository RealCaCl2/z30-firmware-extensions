"""Read-only Z30 USB capability inventory via the existing Windows WPD driver.

Requires comtypes (the project's timing extra). Does not read photographs,
set properties, enter service mode, or send firmware/vendor write commands.
Device serial numbers and Windows device IDs are deliberately omitted.
"""
import argparse
import json
import struct
from datetime import datetime, timezone
from pathlib import Path


class Reader:
    def __init__(self, data):
        self.data, self.offset = data, 0

    def number(self, fmt):
        value, = struct.unpack_from('<' + fmt, self.data, self.offset)
        self.offset += struct.calcsize('<' + fmt)
        return value

    def string(self):
        size = self.number('B') * 2
        end = self.offset + size
        if end > len(self.data):
            raise ValueError('Truncated PTP string')
        value = self.data[self.offset:end].decode('utf-16le').rstrip('\0')
        self.offset = end
        return value

    def array(self):
        count = self.number('I')
        if count > (len(self.data) - self.offset) // 2:
            raise ValueError('Truncated PTP array')
        return [self.number('H') for _ in range(count)]


def device_info(data):
    r = Reader(data)
    out = dict(standard_version=r.number('H'), vendor_extension_id=r.number('I'),
               vendor_extension_version=r.number('H'), vendor_extension=r.string(),
               functional_mode=r.number('H'))
    for name in ('operations', 'events', 'properties', 'capture_formats', 'image_formats'):
        out[name] = [f'0x{v:04x}' for v in r.array()]
    for name in ('manufacturer', 'model', 'firmware'):
        out[name] = r.string()
    r.string()  # Serial: intentionally discard, including in the saved report.
    if r.offset != len(data):
        raise ValueError('Unexpected trailing DeviceInfo bytes')
    return out


def scalar_descriptor(data):
    r = Reader(data)
    code, datatype, writable = r.number('H'), r.number('H'), r.number('B')
    fmt = {1: 'b', 2: 'B', 3: 'h', 4: 'H', 5: 'i', 6: 'I', 7: 'q', 8: 'Q'}.get(datatype)
    if fmt is None:
        raise ValueError(f'Unexpected datatype {datatype:#x} for diagnostic scalar')
    out = {'property': f'0x{code:04x}', 'datatype': f'0x{datatype:04x}',
           'writable': bool(writable), 'default': r.number(fmt), 'current': r.number(fmt)}
    form = r.number('B')
    if form == 1:
        out['range'] = [r.number(fmt) for _ in range(3)]
    elif form == 2:
        count = r.number('H')
        out['allowed'] = [r.number(fmt) for _ in range(count)]
    elif form != 0:
        raise ValueError(f'Unknown property form {form}')
    if r.offset != len(data):
        raise ValueError('Unexpected property descriptor bytes')
    return out


def read_service_region(ptp, dev):
    """One fixed, statically recovered read request; never opens service mode."""
    v = ptp.command(ptp.DATA_TO_READ)
    v.SetUnsignedIntegerValue(ptp.OPCODE, 0x94FF)
    coll = ptp.cc.CreateObject(ptp.typ.PortableDevicePropVariantCollection,
                               interface=ptp.api.IPortableDevicePropVariantCollection)
    for value in (0xF006, 0, 0, 0, 0):
        pv = ptp.api.tag_inner_PROPVARIANT()
        pv.vt = 19
        ptp.ctypes.c_ulong.from_address(ptp.ctypes.addressof(pv) + 8).value = value
        coll.Add(ptp.ctypes.pointer(pv))
    v.SetIPortableDevicePropVariantCollectionValue(ptp.PARAMS, coll)
    out = {'operation': '0x94ff', 'parameters': ['0xf006', 0, 0, 0, 0],
           'service_mode_entry_sent': False, 'write_command_sent': False}
    context, total = None, None
    try:
        r = ptp.send(dev, v)
        try:
            code = r.GetUnsignedIntegerValue(ptp.RESPONSE)
        except ptp.comtypes.COMError:
            code = None
        if code is not None:
            out['initial_response'] = f'0x{code:04x}'
            if code != 0x2001:
                return out
        context = r.GetStringValue(ptp.CONTEXT)
        total = r.GetUnsignedLargeIntegerValue(ptp.TOTAL)
        out['announced_bytes'] = total
        if total not in (4, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF):
            out['error'] = 'Unexpected region-read size; transfer ended without reading payload'
            return out
        buffer = (ptp.ctypes.c_ubyte * 16)()
        v = ptp.command(ptp.READ_DATA)
        v.SetStringValue(ptp.CONTEXT, context)
        v.SetUnsignedIntegerValue(ptp.TO_READ, 16 if total != 4 else 4)
        v.SetBufferValue(ptp.DATA, buffer, len(buffer))
        r = ptp.send(dev, v)
        pointer, size = r.GetBufferValue(ptp.DATA)
        try:
            count = r.GetUnsignedIntegerValue(ptp.NUM_READ)
            if count > size or count > 16:
                raise ValueError('Invalid region-read byte count')
            payload = ptp.ctypes.string_at(pointer, count)
        finally:
            ptp.ctypes.windll.ole32.CoTaskMemFree(pointer)
        out['received_bytes'] = len(payload)
        # Only these two bytes are explicitly populated by the stock handler.
        if len(payload) == 4:
            out['region_byte'], out['profile_byte'] = payload[:2]
    except (OSError, ValueError, ptp.comtypes.COMError) as exc:
        out['error'] = str(exc)
    finally:
        if context is not None:
            try:
                final = ptp.end_transfer(dev, context)
                out['final_response'] = f'0x{final:04x}'
                if final != 0x2001:
                    out['status'] = 'camera_rejected_request'
                    if total == 0:
                        out.pop('error', None)
                else:
                    out['status'] = 'read_complete' if out.get('received_bytes') == 4 else 'unexpected_payload'
            except (OSError, ptp.comtypes.COMError) as exc:
                out['end_transfer_error'] = str(exc)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--vendor-properties', action='store_true',
                   help='Also request advertised Nikon GetVendorPropCodes (read only).')
    p.add_argument('--connection-properties', action='store_true',
                   help='Read advertised battery, USB speed and connection-path descriptors only.')
    p.add_argument('--region-read', action='store_true',
                   help='Try fixed C1.20 service-region read; does not enter service mode.')
    args = p.parse_args()
    import ptp_timing as ptp
    ptp.comtypes.CoInitialize()
    dev = None
    try:
        found = [d for d in ptp.devices() if 'vid_04b0' in d.lower()]
        if len(found) != 1:
            raise RuntimeError(f'Expected one Nikon USB device, found {len(found)}')
        dev = ptp.cc.CreateObject(ptp.api.PortableDevice, interface=ptp.api.IPortableDevice)
        dev.Open(found[0], ptp.values())
        code, data, seconds = ptp.mtp(dev, 0x1001)
        if code != 0x2001:
            raise RuntimeError(f'GetDeviceInfo response 0x{code:04x}')
        report = {'timestamp_utc': datetime.now(timezone.utc).isoformat(),
                  'transport': 'physical camera USB via Windows WPD',
                  'request': 'GetDeviceInfo (0x1001)', 'seconds': seconds,
                  'device': device_info(data),
                  'limitations': 'Capability inventory only; no radio state or throughput measured.'}
        if args.region_read:
            if report['device']['model'] != 'Z 30' or report['device']['firmware'] != 'V1.20':
                raise RuntimeError('Service-region read is restricted to Z30 V1.20')
            report['service_region_read'] = read_service_region(ptp, dev)
        if args.vendor_properties and '0x90ca' in report['device']['operations']:
            code, data, seconds = ptp.mtp(dev, 0x90CA)
            vendor = {'request': 'GetVendorPropCodes (0x90ca)',
                      'response': f'0x{code:04x}', 'seconds': seconds}
            if code == 0x2001:
                r = Reader(data)
                vendor['properties'] = [f'0x{v:04x}' for v in r.array()]
                if r.offset != len(data):
                    raise ValueError('Unexpected vendor property list format')
            report['vendor_properties'] = vendor
        if args.connection_properties:
            advertised = set(report['device']['properties'])
            advertised.update(report.get('vendor_properties', {}).get('properties', []))
            report['connection_properties'] = {}
            for prop, name in ((0x5001, 'BatteryLevel'), (0xD10C, 'USBSpeed'),
                               (0xD12E, 'ConnectionPath')):
                if f'0x{prop:04x}' not in advertised:
                    continue
                code, data, seconds = ptp.mtp(dev, 0x1014, (prop,))
                item = {'response': f'0x{code:04x}', 'seconds': seconds}
                if code == 0x2001:
                    item.update(scalar_descriptor(data))
                report['connection_properties'][name] = item
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        print(json.dumps(report, indent=2, ensure_ascii=False))
    finally:
        if dev is not None:
            dev.Close()
        ptp.comtypes.CoUninitialize()


if __name__ == '__main__':
    main()
