"""Diagnostic workflow fault checks. No COM import or camera connection."""
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import camera_wireless_session as session


class FakeCamera:
    def __init__(self, fault=None, saved_byte=1):
        self.calls, self.fault = [], fault
        self.block = bytearray(session.SIZE)
        self.block[0x351] = saved_byte
        state = bytearray(36)
        state[28:32] = b'XT\0\0'
        struct.pack_into('<i', state, 32, 82)
        raw = (b'\x11' * 16, b'\x22' * 64, bytes(state), struct.pack('<2I', 1, 1),
               struct.pack('<2I', 2, 0), struct.pack('<3I', 0xC10E7974, 0, 0xC116C608))
        self.memory = {(a, n): value for (_, a, n), value in zip(session.MEMORY_READS, raw)}
        self.expectations = dict(host_rom=raw[0], network_vectors=raw[1])

    def __call__(self, op, params):
        session.validate_request(op, params)
        self.calls.append((op, params))
        code, response, data = 0x2001, [0x2001, 0, 0, 0, 0], b''
        cmd = params[0] if params else None
        if op == 0x1001:
            return code, [], b'identity fixture'
        if cmd == 0xF006:
            entered = (sum(p[0] == 0xF001 for _, p in self.calls if p)
                       > sum(p[0] == 0xF002 for _, p in self.calls if p))
            if self.fault == 'exit_gate_still_open' and any(p and p[0] == 0xF002 for _, p in self.calls):
                entered = True
            if not entered:
                return 0xA004, ([] if self.fault == 'missing_parameters' else [0] * 5), b''
            return code, response, b'\x02\x07\xaa\xbb'
        if cmd == 0xF02D:
            data = bytes(self.block)
            if self.fault == 'short_backup':
                data = data[:-1]
            if self.fault == 'mismatched_backup' and sum(p[0] == cmd for _, p in self.calls if p) == 2:
                data = b'\x01' + data[1:]
            if self.fault == 'post_cache_changed' and sum(p[0] == cmd for _, p in self.calls if p) == 3:
                data = b'\x01' + data[1:]
        if cmd == 0xF032:
            if self.fault == 'memory_timeout':
                raise TimeoutError('fixture memory timeout')
            data = self.memory[params[1:3]]
            name = next(name for name, a, n in session.MEMORY_READS if (a, n) == params[1:3])
            if self.fault == f'{name}_mismatch':
                data = bytes((data[0] ^ 1,)) + data[1:]
            if self.fault == 'memory_short':
                data = data[:-1]
        if cmd == 0xF064 and self.fault == 'cache_write_failed':
            response[0] = 0xA001
        if cmd == 0xF002 and self.fault == 'exit_timeout':
            raise TimeoutError('fixture exit timeout')
        return code, response, data


class SessionTests(unittest.TestCase):
    identity = {'manufacturer': 'Nikon Corporation', 'model': 'Z 30', 'firmware': 'V1.20'}

    def run_fixture(self, camera, *, memory=False):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'new-session'
            with patch.object(session, 'device_info', return_value=self.identity):
                result = session.acquire(camera, path,
                    memory_expectations=camera.expectations if memory else None)
            self.assertEqual(json.loads((path / 'session.json').read_text()), result)
            return result

    def test_success_is_not_hardware_recovery_claim(self):
        camera = FakeCamera()
        result = self.run_fixture(camera)
        self.assertEqual(result['state'], 'exit_reported_success_hardware_recovery_unverified')
        self.assertEqual((result['region_byte'], result['profile_byte']), (2, 7))
        commands = [p[0] for _, p in camera.calls if p]
        self.assertEqual(commands, [0xF006, 0xF001, 0xF02D, 0xF02D, 0xF006, 0xF064, 0xF02D, 0xF002, 0xF006])
        self.assertEqual(result['post_exit_service_access'], 'rejected_again')
        self.assertIn((0x94FD, (0xF064, 1, 0, 0, 0)), camera.calls)

    def test_missing_parameters_prevents_entry(self):
        camera = FakeCamera('missing_parameters')
        result = self.run_fixture(camera)
        self.assertIn('error', result)
        self.assertFalse(any(p and p[0] == 0xF001 for _, p in camera.calls))

    def test_bad_backups_do_not_trigger_unsafe_cleanup(self):
        for fault in ('short_backup', 'mismatched_backup'):
            with self.subTest(fault=fault):
                camera = FakeCamera(fault)
                result = self.run_fixture(camera)
                self.assertEqual(result['state'], 'recovery_required_service_left_open')
                self.assertFalse(any(p and p[0] in (0xF064, 0xF002) for _, p in camera.calls))

    def test_unsupported_saved_byte_is_not_overwritten(self):
        camera = FakeCamera(saved_byte=2)
        result = self.run_fixture(camera)
        self.assertEqual(result['state'], 'recovery_required_service_left_open')
        self.assertFalse(any(p and p[0] == 0xF064 for _, p in camera.calls))

    def test_failed_cache_prime_does_not_run_exit(self):
        camera = FakeCamera('cache_write_failed')
        result = self.run_fixture(camera)
        self.assertEqual(result['state'], 'recovery_required_state_unknown')
        self.assertFalse(any(p and p[0] == 0xF002 for _, p in camera.calls))

    def test_exit_timeout_is_recorded_without_retry(self):
        camera = FakeCamera('exit_timeout')
        result = self.run_fixture(camera)
        self.assertEqual(result['state'], 'recovery_required_state_unknown')
        self.assertEqual(sum(p[0] == 0xF002 for _, p in camera.calls if p), 1)

    def test_post_cache_difference_stops_before_exit(self):
        camera = FakeCamera('post_cache_changed')
        result = self.run_fixture(camera)
        self.assertEqual(result['state'], 'recovery_required_state_unknown')
        self.assertFalse(any(p and p[0] == 0xF002 for _, p in camera.calls))

    def test_reported_exit_does_not_hide_open_gate(self):
        result = self.run_fixture(FakeCamera('exit_gate_still_open'))
        self.assertIn('cleanup_error', result)
        self.assertEqual(result['state'], 'recovery_required_state_unknown')

    def test_region_power_and_firmware_writes_are_rejected(self):
        for op, params in ((0x94FD, (0xF004, 0, 0, 0, 0)),
                           (0x94FE, (0xF02E, 0, session.SIZE, 0, 0)),
                           (0x1016, (0,)), (0x100D, ())):
            with self.subTest(op=op, params=params):
                with self.assertRaises(ValueError):
                    session.validate_request(op, params)

    def test_fixed_memory_snapshot_is_not_active_radio_proof(self):
        camera = FakeCamera()
        result = self.run_fixture(camera, memory=True)
        self.assertNotIn('error', result)
        self.assertEqual(result['post_exit_service_access'], 'rejected_again')
        snapshot = result['wireless_memory']
        self.assertEqual(len(snapshot['reads']), 6)
        self.assertEqual(snapshot['decoded']['interface_version'], 2)
        self.assertEqual(snapshot['decoded']['cached_country_ascii'], 'XT')
        self.assertEqual(snapshot['decoded']['cached_country_revision'], 82)
        self.assertEqual(snapshot['decoded']['radio_firmware_slot_pointers']['2'], '0xc116c608')
        self.assertIn('not an active-radio readback', snapshot['scope'])

    def test_bad_memory_fingerprint_stops_reads_but_still_exits(self):
        for fault, expected_reads in (('host_rom_mismatch', 1), ('network_vectors_mismatch', 2)):
            with self.subTest(fault=fault):
                camera = FakeCamera(fault)
                result = self.run_fixture(camera, memory=True)
                self.assertIn('does not match', result['error'])
                self.assertEqual(sum(p[0] == 0xF032 for _, p in camera.calls if p), expected_reads)
                self.assertNotIn('decoded', result['wireless_memory'])
                self.assertEqual(result['post_exit_service_access'], 'rejected_again')

    def test_memory_transfer_failure_still_attempts_supported_cleanup_once(self):
        for fault in ('memory_timeout', 'memory_short'):
            with self.subTest(fault=fault):
                camera = FakeCamera(fault)
                result = self.run_fixture(camera, memory=True)
                self.assertIn('error', result)
                self.assertEqual(sum(p[0] == 0xF002 for _, p in camera.calls if p), 1)
                self.assertEqual(result['post_exit_service_access'], 'rejected_again')

    def test_arbitrary_memory_read_or_write_is_rejected(self):
        for op, params in ((0x94FF, (0xF032, 0xC12020D4, 4, 0, 0)),
                           (0x94FF, (0xF032, 0x50000000, 16, 0, 0)),
                           (0x94FE, (0xF032, 0xC12020D4, 8, 0, 0)),
                           (0x94FF, (0xF032, 0xC12020D4, 8, 1, 0))):
            with self.subTest(op=op, params=params):
                with self.assertRaises(ValueError):
                    session.validate_request(op, params)


if __name__ == '__main__':
    unittest.main()
