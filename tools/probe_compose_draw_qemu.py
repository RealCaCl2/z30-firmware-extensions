"""QEMU-only diagnostic: identify the guest path that composites display layers.

Stops on the native draw entry (0x1050AE18) and on the candidate composition
function (0x10478E60), dumping the OpenVG context/paint structures and the three
live-view surfaces found in the display state table, so the layer draw can be
recognised from the image pointer it consumes.

Guest code is not patched and no fill is injected.  The assisted QEMU colour
bars are a fixture, not camera footage.
"""
import argparse
import json
import socket
import sys
import time
from pathlib import Path

DRAW = 0x1050AE18
COMPOSE = 0x10478E60
VG_CTX = 0xC1D6E1FC
DISPLAY_STATE = 0xC1820000
KNOWN = {
    0xC960E000: 'tex-32x32',
    0xD045F200: 'ui-canvas',
    0xD0959600: 'image-surface',
    0xD2BFD600: 'canvas3',
    0xECCA7000: 'bank0',
    0xECEE7000: 'bank1',
    0xED127000: 'bank2',
    0xED733000: 'video-base',
    0xED787600: 'video-uv',
}
CAP = 80


def words(raw, base):
    return {hex(base + i): hex(int.from_bytes(raw[i:i + 4], 'little'))
            for i in range(0, len(raw), 4)}


def known_words(raw, base):
    out = []
    for i in range(0, len(raw) - 3, 4):
        value = int.from_bytes(raw[i:i + 4], 'little')
        if value in KNOWN:
            out.append((hex(base + i), KNOWN[value], value))
    return out


def read(remote, address, length):
    data = bytearray()
    while len(data) < length:
        step = min(0x1000, length - len(data))
        data += remote.memory(address + len(data), step)
    return bytes(data)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--seconds', type=float, default=1.5)
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from liveview_session import start, refresh, DEVICE_ENV

    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    records = []
    state = {'armed': False}

    class ObservedSession(Session):
        def run_for(self, seconds):
            if not state['armed']:
                super().run_for(seconds)
                return
            remote = self.remote
            deadline = time.monotonic() + seconds
            assert remote.command('Z1,{:x},2'.format(DRAW)) == 'OK'
            assert remote.command('Z1,{:x},2'.format(COMPOSE)) == 'OK'
            try:
                while time.monotonic() < deadline and len(records) < CAP:
                    remote.send('c')
                    remote.sock.settimeout(max(.01, deadline - time.monotonic()))
                    try:
                        packet = remote.receive()
                    except socket.timeout:
                        remote.sock.sendall(b'\x03')
                        remote.sock.settimeout(5)
                        self.packet = remote.receive()
                        break
                    self.packet = packet
                    thread = packet.split('thread:')[1].split(';')[0]
                    assert remote.command('Hg' + thread) == 'OK'
                    regs = remote.registers()
                    pc = int(regs['r15'], 16)
                    assert remote.command('z1,{:x},2'.format(DRAW)) == 'OK'
                    assert remote.command('z1,{:x},2'.format(COMPOSE)) == 'OK'
                    regs = {k: (int(v, 16) if isinstance(v, str) else v)
                            for k, v in regs.items()}
                    record = dict(n=len(records), site=hex(pc),
                                  lr=hex(regs['r14']), thread=thread,
                                  r0=hex(regs['r0']), r1=hex(regs['r1']),
                                  r2=hex(regs['r2']), r3=hex(regs['r3']))
                    if pc == DRAW:
                        ctx = read(remote, VG_CTX, 0x80)
                        record['ctx'] = words(ctx, VG_CTX)
                        record['ctx_known'] = known_words(ctx, VG_CTX)
                        paint = int.from_bytes(ctx[0x18:0x1C], 'little')
                        record['paint'] = hex(paint)
                        if 0xC0000000 <= paint < 0xD8000000:
                            praw = read(remote, paint, 0x60)
                            record['paint_words'] = words(praw, paint)
                            record['paint_known'] = known_words(praw, paint)
                        handle = regs['r0']
                        if 0xC0000000 <= handle < 0xD8000000:
                            hraw = read(remote, handle, 0x60)
                            record['r0_words'] = words(hraw, handle)
                            record['r0_known'] = known_words(hraw, handle)
                    else:
                        arg = regs['r0']
                        if 0xC0000000 <= arg < 0xD8000000:
                            araw = read(remote, arg, 0x80)
                            record['arg_words'] = words(araw, arg)
                            record['arg_known'] = known_words(araw, arg)
                    records.append(record)
                    assert remote.command('Hc' + thread) == 'OK'
                    remote.send('s')
                    remote.sock.settimeout(5)
                    self.packet = remote.receive()
                    assert remote.command('Hc-1') == 'OK'
                    assert remote.command('Z1,{:x},2'.format(DRAW)) == 'OK'
                    assert remote.command('Z1,{:x},2'.format(COMPOSE)) == 'OK'
            finally:
                remote.command('z1,{:x},2'.format(DRAW))
                remote.command('z1,{:x},2'.format(COMPOSE))

    s = ObservedSession(str(out), device_env=DEVICE_ENV)
    try:
        start(s)
        refresh(s)
        s.run_for(.4)
        state_before = words(read(s.remote, DISPLAY_STATE, 0x200), DISPLAY_STATE)
        state_known = known_words(read(s.remote, DISPLAY_STATE, 0x200), DISPLAY_STATE)
        state['armed'] = True
        s.run_for(a.seconds)
        s.frame('01-compose')
        refresh(s)
        s.frame('02-after-refresh')
    finally:
        result = dict(records=records, display_state_words=state_before,
                      display_state_known=state_known)
        (out / 'context.json').write_text(json.dumps(result, indent=1))
        s.close()
    print('records', len(records))
    for item in records[:40]:
        print(json.dumps(item, sort_keys=True))
    print('display_state_known', state_known)


if __name__ == '__main__':
    main()
