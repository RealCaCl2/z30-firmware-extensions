"""Build a stock C1.20 profile-7 country trial EG. Does not access a camera."""
import argparse
import binascii
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.plan import Plan
from z30_extensions.platform.z30_120 import BASE, StockImage, sha256
from probe_wireless_band import machine
from probe_wireless_config import DATA, STACK, STOP, runtime

ENTRY = 0x114A3810
BEFORE = struct.pack('<4sII', b'AA\0\0', 7, 7)
TARGETS = {'CN': 0, 'US': -1}


def verify(stock, candidate, target_country, target_revision):
    from native_runtime import Context, RAM
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                   UC_ARM_REG_R3, UC_ARM_REG_PC, UC_ARM_REG_LR,
                                   UC_ARM_REG_SP)
    regs = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)

    def ret(u, value=0):
        u.reg_write(UC_ARM_REG_R0, value & 0xFFFFFFFF)
        u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))

    cases = []
    # Prove this table edit is consumed by the original getter and request
    # constructor, and does not change other IDs or the non-country mode.
    for image_name, image in (('stock', stock), ('candidate', candidate)):
        for index, mode in ((7, 0), (0xFFFFFFFF, 0), (3, 0), (7, 1)):
            context = Context(stock, image)
            u = context.u
            context.map(0xC18CA000, 0x1000)
            u.mem_write(0xC18807C0, struct.pack('<I', index))
            requests = []

            def host_hook(em, address, size, unused):
                r0, r1, r2, r3 = [em.reg_read(r) for r in regs]
                if address == 0x10392F00:
                    fields = struct.unpack('<6I', em.mem_read(r0, 24))
                    country = (bytes(em.mem_read(fields[3], 4)).split(b'\0')[0].decode('ascii')
                               if fields[3] else None)
                    requests.append(dict(country=country, revision=fields[4],
                                         mode=fields[2] & 255))
                    ret(em, 1)
                else:
                    ret(em, 1 if address == 0x101F4E06 else 0)

            for a in (0x10392E8E, 0x10392ED0, 0x101F4E06, 0x10392F00):
                u.hook_add(UC_HOOK_CODE, host_hook, begin=a, end=a)
            assert context.call(0x103A72C8, (mode,), limit=4000) == 1
            assert len(requests) == 1
            expected = (None, 0xFFFFFFFF) if mode else (
                ('AA', 3) if index == 3 else (target_country, target_revision & 0xFFFFFFFF)
                if image_name == 'candidate' else ('AA', 7))
            assert (requests[0]['country'], requests[0]['revision']) == expected
            cases.append(dict(image=image_name, synthetic_saved_index=index,
                              caller_mode=mode, request=requests[0]))

    # Explicit handoff of captured country/revision to the original callback.
    # This executes the same callback image: the trial only edits a host table.
    u = machine(candidate)
    u.mem_write(DATA, struct.pack('<I4si', 0, target_country.encode('ascii'), target_revision))
    u.reg_write(UC_ARM_REG_R1, DATA)
    notifications = []

    def callback_hook(em, address, size, unused):
        r0, r1, r2, r3 = [em.reg_read(r) for r in regs]
        if address == STOP:
            em.emu_stop()
        elif address == runtime(0x11D6CFDC):
            left = bytes(em.mem_read(r0, 32)).split(b'\0')[0]
            right = bytes(em.mem_read(r1, 32)).split(b'\0')[0]
            ret(em, (left > right) - (left < right))
        elif address == runtime(0x11D6CA88):
            em.mem_write(r0, bytes(em.mem_read(r1, 32)).split(b'\0')[0] + b'\0')
            ret(em, r0)
        else:
            pointer, length = struct.unpack('<2I', em.mem_read(r3, 8))
            notifications.append(struct.unpack('<H', em.mem_read(pointer, 2))[0])
            ret(em)

    for a in (STOP,) + tuple(map(runtime, (0x11D6CFDC, 0x11D6CA88, 0x11D1791E))):
        u.hook_add(UC_HOOK_CODE, callback_hook, begin=a, end=a)
    u.emu_start(runtime(0x11D106DC) | 1, STOP + 2, count=4000)
    assert u.reg_read(UC_ARM_REG_PC) == STOP and u.reg_read(UC_ARM_REG_SP) == STACK
    country = bytes(u.mem_read(0xC1200808, 4)).split(b'\0')[0].decode('ascii')
    revision = struct.unpack('<i', u.mem_read(0xC120080C, 4))[0]
    bandwidth = struct.unpack('<2I', u.mem_read(0xC1201F2C, 8))
    vht = bytes(u.mem_read(0xC12007EC, 1))[0]
    assert (country, revision, bandwidth, vht, notifications) == (target_country, target_revision, (1, 7), 1, [0])
    return dict(host_cases=cases, network_callback=dict(country=country, revision=revision,
                bandwidth_band_mask=list(bandwidth), cached_vht=vht, notification_status=notifications))


def build_trial(stock, target_country='CN'):
    StockImage(stock)
    target_revision = TARGETS[target_country]
    replacement = struct.pack('<4siI', target_country.encode('ascii'), target_revision, 7)
    plan = Plan(stock)
    plan.edit(ENTRY, replacement,
              f'Profile 7: request {target_country}/{target_revision} instead of internal AA/7', BEFORE)
    candidate, report = plan.finish()
    assert len(candidate) == len(stock) and binascii.crc_hqx(candidate, 0) == 0
    allowed = set(range(ENTRY - BASE, ENTRY - BASE + len(BEFORE))) | {len(stock)-2, len(stock)-1}
    changes = [i for i, (a, b) in enumerate(zip(stock, candidate)) if a != b]
    assert set(changes) <= allowed
    verification = verify(stock, candidate, target_country, target_revision)
    report.update(trial=f'wifi-sta-profile7-{target_country.lower()}{target_revision}',
                  requested_country=target_country, requested_revision=target_revision,
                  status='offline_candidate_not_flashability_verified',
                  source_sha256=sha256(stock), candidate_sha256=sha256(candidate),
                  changed_byte_count=len(changes), verification=verification,
                  limitations=[
                      'Standalone EG only; not a complete Nikon update or verified flashing/recovery path.',
                      'No camera, persistent region write, F004, F02E, power or calibration changes performed.',
                      'Profile 7 and unset-index fallback both change; all callers of that profile are affected.',
                      'Other saved profile IDs and country-free initialization mode are unchanged.',
                      'Requested country is a diagnostic configuration, not a proven sales-region conversion or unlock.',
                      'US/-1 is not yet bound to the exact OEM Americas profile or verified for local RF use.',
                      'Country callback caches are not driver readback; channel 157 permission is unverified.',
                      'Persistent loader, dispatch, strings and notifications use explicit stubs.',
                      'No actual 5 GHz scan, association, phone interoperability, throughput or rollback proof.',
                  ])
    return candidate, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--country', choices=tuple(TARGETS), default='CN')
    args = parser.parse_args()
    report_path = args.output.with_suffix('.json')
    if args.output.exists() or report_path.exists():
        parser.error('Output and report must be new files')
    candidate, report = build_trial(extract_eg(args.source.read_bytes()), args.country)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('xb') as stream:
        stream.write(candidate)
    with report_path.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    print(f"Built {args.country}/{TARGETS[args.country]} trial: {report['changed_byte_count']} changed bytes; "
          '8 host request cases, country callback and EG CRC passed; no camera access')


if __name__ == '__main__':
    main()
