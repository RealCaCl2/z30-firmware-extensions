"""Fragmented transport/failure checks; no camera or wireless connection."""
import json
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import camera_wireless_ptpip as probe


def frame(kind, payload=b''):
    return struct.pack('<II', len(payload) + 8, kind) + payload


def response(code, tid, params=()):
    return frame(7, struct.pack('<HI', code, tid) + struct.pack('<' + 'I' * len(params), *params))


def data_phase(tid, data, declared=None):
    n = len(data) if declared is None else declared
    return (frame(9, struct.pack('<IQ', tid, n))
            + frame(10, struct.pack('<I', tid) + data[:3])
            + frame(12, struct.pack('<I', tid) + data[3:]))


def ptp_string(value):
    return bytes((len(value) + 1,)) + (value + '\0').encode('utf-16le')


def identity(model='Z 30'):
    raw = struct.pack('<HIH', 100, 10, 100) + ptp_string('fixture') + b'\0\0'
    for values in ((0x1001, 0x1002, 0x1003, 0x94FF), (), (), (), ()):
        raw += struct.pack('<I', len(values)) + struct.pack('<' + 'H' * len(values), *values)
    for value in ('Nikon Corporation', model, 'V1.20', 'PRIVATE-SERIAL-FIXTURE'):
        raw += ptp_string(value)
    return raw


class Stream:
    def __init__(self, data):
        self.data = bytearray(data)
        self.sent, self.closed = [], False

    def recv(self, n):
        count = min(n, 3, len(self.data))  # Deliberately fragment every header/body.
        result = bytes(self.data[:count])
        del self.data[:count]
        return result

    def sendall(self, data):
        self.sent.append(data)

    def settimeout(self, value):
        pass

    def setsockopt(self, *args):
        pass

    def close(self):
        self.closed = True


class PtpIpTests(unittest.TestCase):
    def run_camera(self, *, model='Z 30', open_code=0x2001, close_code=0x2001,
                   gate_code=0xA004, gate_data=b'', wrong_tid=False, declared=None):
        hello = frame(2, struct.pack('<I', 99) + b'\x99' * 16
                      + 'PRIVATE-CAMERA-NAME\0'.encode('utf-16le') + struct.pack('<I', 0x10000))
        rx = hello + data_phase(0, identity(model)) + response(0x2001, 99 if wrong_tid else 0)
        rx += response(open_code, 0)
        if gate_data:
            rx += data_phase(1, gate_data, declared)
        rx += response(gate_code, 1, (0, 0, 0, 0, 0) if gate_code == 0xA004 else (0x2001, 0, 0, 0, 0))
        rx += response(close_code, 2)
        command = Stream(rx)
        event = Stream(frame(4) + frame(13) + frame(8, struct.pack('<HI', 0x4002, 0)))

        def ready(streams, *args):
            return [s for s in streams if s.data], [], []

        with patch.object(probe.socket, 'create_connection', side_effect=[command, event]), \
                patch.object(probe.select, 'select', side_effect=ready):
            result = probe.inspect(probe.Client('192.168.1.1'), check_gate=True)
        self.assertTrue(command.closed and event.closed)
        return result, command, event

    def test_fragmented_session_gate_and_close(self):
        result, command, event = self.run_camera()
        self.assertEqual(result['state'], 'read_only_preflight_completed')
        self.assertEqual(result['service_gate'], 'closed_as_expected')
        self.assertTrue(result['normal_session_close_confirmed'])
        self.assertFalse(result['service_mode_entry_sent'])
        self.assertNotIn('PRIVATE-', json.dumps(result))
        requests = [p for p in command.sent if struct.unpack_from('<I', p, 4)[0] == 6]
        decoded = [struct.unpack_from('<IHI', p, 8) for p in requests]
        self.assertEqual(decoded, [(1, 0x1001, 0), (1, 0x1002, 0),
                                   (1, 0x94FF, 1), (1, 0x1003, 2)])
        self.assertIn(frame(14), event.sent)

    def test_wrong_transaction_poisoned_stream_not_reused(self):
        result, command, _ = self.run_camera(wrong_tid=True)
        self.assertIn('transaction mismatch', result['error'])
        self.assertEqual(len(result['steps']), 1)

    def test_wrong_model_never_opens_session(self):
        result, _, _ = self.run_camera(model='OTHER')
        self.assertIn('Expected exactly', result['error'])
        self.assertEqual(len(result['steps']), 1)

    def test_existing_session_not_closed(self):
        result, _, _ = self.run_camera(open_code=0x201E)
        self.assertIn('not opened', result['error'])
        self.assertEqual([r['operation'] for r in result['steps']], ['0x1001', '0x1002'])

    def test_service_already_open_does_not_trigger_memory_or_mode_commands(self):
        result, _, _ = self.run_camera(gate_code=0x2001, gate_data=b'\x07\x07\0\0')
        self.assertEqual(result['service_gate'], 'unexpectedly_open_no_further_service_operations')
        self.assertEqual(len(result['steps']), 4)
        self.assertFalse(result['memory_read_sent'])

    def test_bad_declared_size_or_incomplete_data_stops_stream(self):
        for n in (3, 5):
            with self.subTest(declared=n):
                result, _, _ = self.run_camera(gate_code=0x2001, gate_data=b'abcd', declared=n)
                self.assertIn('error', result)
                self.assertFalse(result['normal_session_close_confirmed'])
                self.assertEqual(len(result['steps']), 3)

    def test_close_failure_is_visible(self):
        result, _, _ = self.run_camera(close_code=0x2002)
        self.assertIn('cleanup_error', result)
        self.assertFalse(result['normal_session_close_confirmed'])

    def test_write_memory_capture_and_arbitrary_operations_rejected(self):
        client = probe.Client('127.0.0.1')
        for op, params in ((0x94FD, (0xF001, 0, 0, 0, 0)),
                           (0x94FF, (0xF032, 0xC1000000, 64, 0, 0)),
                           (0x100E, ()), (0x1016, (0xD10C,)), (0x1002, (2,))):
            with self.subTest(op=op, params=params):
                with self.assertRaises(ValueError):
                    client.request(op, params)

    def test_no_public_host_or_dns(self):
        for address in ('8.8.8.8', 'camera.local', '0.0.0.0', '224.0.0.1'):
            with self.subTest(address=address):
                with self.assertRaises(ValueError):
                    probe.Client(address)

    def test_packet_bound_checked_before_body_read(self):
        client = probe.Client('127.0.0.1')
        stream = Stream(struct.pack('<II', 0x40000000, 9))
        with self.assertRaises(ValueError):
            client._packet(stream, probe.time.monotonic() + 1)

    def test_handshake_rejection_closes_transport(self):
        command = Stream(frame(5, struct.pack('<I', 1)))
        with patch.object(probe.socket, 'create_connection', return_value=command):
            client = probe.Client('127.0.0.1')
            with self.assertRaises(RuntimeError):
                client.connect()
        self.assertTrue(command.closed)


if __name__ == '__main__':
    unittest.main()
