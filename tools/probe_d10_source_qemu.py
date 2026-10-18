"""QEMU-only observer: source framebuffer of each per-cycle display copy.

Break at the two 0x10509D10 call sites inside the live-view handlers
(0x104E941A = D10-a, 0x104E94F0 = D10-b) and read the plane-to-plane copy
arguments: r0 = source plane (framebuffer address at +0xB0), r1 = target plane,
r2 = rect/format block.

QEMU-only; read-only guest (no guest memory writes).
"""
import argparse
import json
import socket
import sys
import time
from pathlib import Path


SITES = {
    0x104E941A: 'd10-a-call',
    0x104E94F0: 'd10-b-call',
}
CAP = 24

KNOWN = {
    0xD045F200: 'ui-canvas',
    0xD0959600: 'image-surface',
    0xD2BFD600: 'canvas3',
    0xECCA7000: 'bank0',
    0xECEE7000: 'bank1',
    0xED127000: 'bank2',
    0xED733000: 'video-y',
    0xED787600: 'video-uv',
    0xED733000: 'video-y',
}


def name(value):
    return KNOWN.get(value, hex(value))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--seconds', type=float, default=2.0)
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from liveview_session import start, refresh, DEVICE_ENV

    records = []
    state = {'hits': 0, 'armed': False}

    class ObservedSession(Session):
        def run_for(self, seconds):
            if not state['armed'] or state['hits'] >= CAP:
                super().run_for(seconds)
                return
            remote = self.remote
            deadline = time.monotonic() + seconds
            for address in SITES:
                assert remote.command('Z1,{:x},2'.format(address)) == 'OK'
            try:
                while time.monotonic() < deadline and state['hits'] < CAP:
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
                    for address in SITES:
                        assert remote.command('z1,{:x},2'.format(address)) == 'OK'
                    if pc in SITES:
                        state['hits'] += 1
                        src = int(regs['r0'], 16)
                        dst = int(regs['r1'], 16)
                        rec = dict(n=len(records), thread=thread, site=SITES[pc],
                                   src_plane=hex(src), dst_plane=hex(dst),
                                   r4=hex(int(regs['r4'], 16)),
                                   sp=hex(int(regs['r13'], 16)))
                        for key, plane in (('src', src), ('dst', dst)):
                            if not plane:
                                continue
                            raw = remote.memory(plane, 0x40)
                            for i in range(0, 0x40, 4):
                                value = int.from_bytes(raw[i:i + 4], 'little')
                                rec['{}_w{:02x}'.format(key, i)] = hex(value)
                            raw = remote.memory(plane + 0xB0, 4)
                            value = int.from_bytes(raw, 'little')
                            rec[key + '_fb'] = hex(value)
                            rec[key + '_fb_name'] = name(value)
                        records.append(rec)
                    assert remote.command('Hc' + thread) == 'OK'
                    remote.send('s')
                    remote.sock.settimeout(5)
                    self.packet = remote.receive()
                    assert remote.command('Hc-1') == 'OK'
                    for address in SITES:
                        assert remote.command('Z1,{:x},2'.format(address)) == 'OK'
            finally:
                for address in SITES:
                    remote.command('z1,{:x},2'.format(address))

    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    s = ObservedSession(str(out), device_env=DEVICE_ENV)
    try:
        start(s)
        refresh(s)
        s.run_for(0.2)
        state['armed'] = True
        s.run_for(a.seconds)
        result = dict(probe='per-cycle copy source framebuffer', sites=SITES,
                      hits=state['hits'], records=records,
                      notes='QEMU-only; plane fb fields at the two compositing copies')
        (out / 'context.json').write_text(json.dumps(result, indent=2) + '\n')
        for rec in records:
            slim = {k: v for k, v in rec.items() if k.endswith('_fb') or k.endswith('_fb_name') or k in ('n', 'thread', 'site')}
            print(json.dumps(slim))
        print('hits', state['hits'])
    finally:
        s.close()


if __name__ == '__main__':
    main()
