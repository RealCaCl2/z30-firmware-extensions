"""Bounded read-only PTP/IP preflight for a Z30 C1.20 camera hotspot.

Default prints a plan. Only DeviceInfo, ordinary session open/close and the
fixed F006 gate query are implemented. No service entry, memory read, capture,
property write, file retrieval or radio change is sent.

Packet layout reference: libgphoto2 camlibs/ptp2/ptpip.c (upstream). This is an
independent small Python implementation, not a copied libgphoto2 transport.
"""
import argparse
import ipaddress
import json
import select
import socket
import struct
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from inspect_camera_usb import device_info

MAX_PACKET = 65536 + 20
LOCAL_NETWORKS = tuple(ipaddress.ip_network(s) for s in
                       ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16',
                        '169.254.0.0/16', '127.0.0.0/8'))
PLAN = dict(target='Nikon Z 30 V1.20 on a user-selected local hotspot address',
            operations=['GetDeviceInfo', 'OpenSession(1)', 'optional 94FF(F006,0,0,0,0)', 'CloseSession'],
            service_mode_entry=False, memory_read=False, radio_change=False,
            limitations=['Requires Wi-Fi association prepared outside this tool.',
                         'Opening a normal PTP session may occupy the camera connection.',
                         'No real PTP/IP camera connection has yet been verified.'])


def packet(kind, payload=b''):
    return struct.pack('<2I', len(payload) + 8, kind) + payload


def validate_request(op, params):
    if (op, params) in ((0x1001, ()), (0x1002, (1,)), (0x1003, ())):
        return 65536 if op == 0x1001 else 0
    if op == 0x94FF and params == (0xF006, 0, 0, 0, 0):
        return 4
    raise ValueError('Outside the fixed read-only PTP/IP preflight operations')


class Client:
    def __init__(self, address, port=15740, timeout=12):
        ip = ipaddress.IPv4Address(address)
        if not any(ip in network for network in LOCAL_NETWORKS):
            raise ValueError('An explicit local IPv4 address is required; no DNS or discovery')
        self.address, self.port, self.timeout = str(ip), port, timeout
        self.command = self.event = None
        self.transaction = 0
        self.healthy = True

    def close(self):
        for stream in (self.event, self.command):
            if stream is not None:
                stream.close()
        self.command = self.event = None

    def _exact(self, stream, size, deadline):
        chunks = bytearray()
        while len(chunks) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('PTP/IP deadline expired')
            stream.settimeout(remaining)
            data = stream.recv(size - len(chunks))
            if not data:
                raise EOFError('PTP/IP peer disconnected')
            chunks.extend(data)
        return bytes(chunks)

    def _packet(self, stream, deadline):
        length, kind = struct.unpack('<2I', self._exact(stream, 8, deadline))
        if not 8 <= length <= MAX_PACKET:
            raise ValueError('PTP/IP packet length exceeds the bounded preflight size')
        return kind, self._exact(stream, length - 8, deadline)

    def connect(self):
        deadline = time.monotonic() + self.timeout
        try:
            self.command = socket.create_connection((self.address, self.port), timeout=self.timeout)
            self.command.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            hello = uuid.uuid4().bytes + 'WL Wireless Probe\0'.encode('utf-16le') + struct.pack('<I', 0x10000)
            self.command.sendall(packet(1, hello))
            kind, data = self._packet(self.command, deadline)
            if kind == 5:
                raise RuntimeError('Camera rejected the PTP/IP handshake')
            if kind != 2 or len(data) < 26 or len(data) % 2:
                raise ValueError('Malformed PTP/IP command handshake')
            name = data[20:-4].decode('utf-16le')
            if not name.endswith('\0') or struct.unpack('<I', data[-4:])[0] != 0x10000:
                raise ValueError('Unsupported PTP/IP handshake name or version')
            connection, = struct.unpack_from('<I', data)
            # Do not persist the camera GUID/name or this host's random GUID.
            remaining = max(0.001, deadline - time.monotonic())
            self.event = socket.create_connection((self.address, self.port), timeout=remaining)
            self.event.sendall(packet(3, struct.pack('<I', connection)))
            kind, data = self._packet(self.event, deadline)
            if kind != 4 or data:
                raise ValueError('Malformed PTP/IP event handshake')
        except BaseException:
            self.healthy = False
            self.close()
            raise

    def _next_command_packet(self, deadline):
        for _ in range(64):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('PTP/IP transaction deadline expired')
            ready, _, _ = select.select([self.command, self.event], [], [], remaining)
            if not ready:
                raise TimeoutError('PTP/IP transaction deadline expired')
            for stream in (self.event, self.command):
                if stream not in ready:
                    continue
                kind, data = self._packet(stream, deadline)
                if kind == 13 and not data:
                    stream.sendall(packet(14))
                elif stream is self.event:
                    if kind != 8 or not 6 <= len(data) <= 18 or (len(data) - 6) % 4:
                        raise ValueError('Unexpected PTP/IP event packet')
                else:
                    return kind, data
        raise ValueError('Too many unrelated PTP/IP packets')

    def request(self, op, params=()):
        params = tuple(params)
        maximum = validate_request(op, params)
        if not self.healthy or self.command is None:
            raise RuntimeError('PTP/IP stream is unavailable or unsynchronized')
        tid = 0 if op == 0x1002 else self.transaction
        self.transaction = tid + 1
        phase = struct.pack('<IHI', 1, op, tid) + struct.pack('<' + 'I' * len(params), *params)
        data, declared, ended = bytearray(), None, False
        deadline = time.monotonic() + self.timeout
        try:
            self.command.settimeout(self.timeout)
            self.command.sendall(packet(6, phase))
            for _ in range(128):
                kind, body = self._next_command_packet(deadline)
                if kind == 7:
                    if not 6 <= len(body) <= 26 or (len(body) - 6) % 4:
                        raise ValueError('Malformed PTP/IP operation response')
                    code, response_tid = struct.unpack_from('<HI', body)
                    if response_tid != tid:
                        raise ValueError('PTP/IP response transaction mismatch')
                    if declared is not None and (not ended or len(data) != declared):
                        raise ValueError('PTP/IP data phase was incomplete')
                    if code == 0x2001 and op == 0x1001 and declared is None:
                        raise ValueError('DeviceInfo response has no data')
                    values = list(struct.unpack('<' + 'I' * ((len(body) - 6) // 4), body[6:]))
                    return code, values, bytes(data)
                if kind == 9:
                    if declared is not None or len(body) != 12:
                        raise ValueError('Malformed or repeated PTP/IP start-data packet')
                    response_tid, declared = struct.unpack('<IQ', body)
                    if response_tid != tid or declared > maximum:
                        raise ValueError('Unexpected PTP/IP data transaction or length')
                elif kind in (10, 12):
                    if declared is None or ended or len(body) < 4:
                        raise ValueError('Unexpected PTP/IP data packet ordering')
                    if struct.unpack_from('<I', body)[0] != tid:
                        raise ValueError('PTP/IP data transaction mismatch')
                    data.extend(body[4:])
                    if len(data) > declared:
                        raise ValueError('PTP/IP payload exceeds declared length')
                    ended = kind == 12
                else:
                    raise ValueError('Unexpected PTP/IP command packet')
            raise ValueError('Too many PTP/IP data packets')
        except BaseException:
            self.healthy = False  # Do not reuse a stream after framing/timeout failure.
            raise


def inspect(client, *, check_gate=False):
    report = dict(timestamp_utc=datetime.now(timezone.utc).isoformat(), steps=[],
                  service_mode_entry_sent=False, memory_read_sent=False, radio_change_sent=False)
    opened = False

    def run(op, params=()):
        row = dict(operation=hex(op), parameters=[hex(p) for p in params], status='pending')
        report['steps'].append(row)
        code, values, data = client.request(op, params)
        row.update(status='responded', code=hex(code), response_parameters=[hex(p) for p in values],
                   payload_bytes=len(data))
        return code, values, data

    try:
        client.connect()
        code, _, data = run(0x1001)
        if code != 0x2001:
            raise RuntimeError('DeviceInfo failed')
        identity = device_info(data)
        report['device'] = identity
        if identity['model'] != 'Z 30' or identity['firmware'] != 'V1.20':
            raise RuntimeError('Expected exactly Z 30 V1.20; no session opened')
        code, _, _ = run(0x1002, (1,))
        if code != 0x2001:
            raise RuntimeError('Normal PTP session was not opened; existing sessions are left alone')
        opened = True
        if check_gate:
            code, values, data = run(0x94FF, (0xF006, 0, 0, 0, 0))
            if code == 0xA004 and values == [0] * 5 and not data:
                report['service_gate'] = 'closed_as_expected'
            elif code == 0x2001 and len(values) == 5 and values[0] == 0x2001 and len(data) == 4:
                report['service_gate'] = 'unexpectedly_open_no_further_service_operations'
            else:
                report['service_gate'] = 'not_confirmed'
        report['state'] = 'read_only_preflight_completed'
    except Exception as exc:
        report.update(state='preflight_failed', error=str(exc))
    finally:
        if opened and client.healthy:
            try:
                code, _, _ = run(0x1003)
                report['normal_session_close_confirmed'] = code == 0x2001
                if code != 0x2001:
                    report['cleanup_error'] = 'Normal PTP session close was not confirmed'
            except Exception as exc:
                report['cleanup_error'] = str(exc)
        elif opened:
            report['normal_session_close_confirmed'] = False
        client.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--address', help='Explicit camera hotspot IPv4; no discovery or scanning')
    parser.add_argument('--check-service-gate', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps(PLAN, indent=2))
        return
    if not args.address or args.output is None:
        parser.error('--execute requires --address and a new --output file')
    # Reserve the output before connecting, avoiding an unrecorded overwrite.
    with args.output.open('x', encoding='utf-8') as stream:
        result = inspect(Client(args.address), check_gate=args.check_service_gate)
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(result, indent=2))
    if result.get('error') or result.get('cleanup_error'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
