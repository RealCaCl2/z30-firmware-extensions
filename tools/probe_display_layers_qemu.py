"""QEMU-only observer: which plane does each per-cycle display copy carry?

The live-view display cycle runs two compositing copies (dispatcher cases
0x103BC032 -> 0x104E9496 and 0x103BC29A -> 0x104E93CA, both calling the
plane-to-plane copy API 0x10509D10).  Identify the source/target planes and
framebuffer addresses of each so a dim layer can be inserted between them.

QEMU-only; read-only guest (no guest memory writes).
"""
import argparse
import json
import socket
import sys
import time
from pathlib import Path


SITES = {
    0x104E93CA: 'd10-a',
    0x104E9496: 'd10-b',
}
CAP = 40

KNOWN = {
    0xD045F200: 'ui-canvas',
    0xD0959600: 'image-surface',
    0xD2BFD600: 'canvas3',
    0xECCA7000: 'bank0',
    0xECEE7000: 'bank1',
    0xED127000: 'bank2',
    0xED733000: 'video-y',
    0xED787600: 'video-uv',
}


def name(value):
    return KNOWN.get(value, hex(value))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--seconds', type=float, default=1.6)
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
                        r0 = int(regs['r0'], 16)
                        r1 = int(regs['r1'], 16)
                        rec = dict(n=len(records), thread=thread, site=SITES[pc],
                                   r0=hex(r0), r1=hex(r1))
                        try:
                            st = remote.memory(r0, 0xB0)
                            src = int.from_bytes(st[0x94:0x98], 'little')
                            dst = int.from_bytes(st[0x98:0x9c], 'little')
                            rec['src_plane'] = hex(src)
                            rec['dst_plane'] = hex(dst)
                            for key, off in (('src_fb', 0x84), ('src_fb2', 0x88),
                                             ('dst_fb', 0x84), ('dst_fb2', 0x88)):
                                plane = src if key.startswith('src') else dst
                                if plane:
                                    raw = remote.memory(plane + off, 4)
                                    value = int.from_bytes(raw, 'little')
                                    rec[key] = hex(value)
                                    rec[key + '_name'] = name(value)
                            for key, off in (('rect_a8', 0xa8), ('rect_aa', 0xaa),
                                             ('rect_ac', 0xac), ('rect_ae', 0xae),
                                             ('field20', 0x20), ('field_c4', 0xc4)):
                                raw = remote.memory(r0 + off, 4)
                                rec[key] = hex(int.from_bytes(raw, 'little'))
                        except Exception as exc:
                            rec['error'] = str(exc)
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
        result = dict(probe='per-cycle display plane identity', sites=SITES,
                      hits=state['hits'], records=records,
                      notes='QEMU-only; state/plane identity at the two compositing copies')
        (out / 'context.json').write_text(json.dumps(result, indent=2) + '\n')
        for rec in records:
            print(json.dumps(rec))
        print('hits', state['hits'])
    finally:
        s.close()


if __name__ == '__main__':
    main()
