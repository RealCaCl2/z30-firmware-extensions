"""QEMU-only observer: who runs the display compositing copy/fill helpers?

Direct-BL xref found no caller for the plane-to-plane copy API (0x10509C38),
the copy helpers 0x10509C62/0x10509D10, or the plane framebuffer binder
0x1052F12A, because this firmware calls across modules through
`movw/movt r12, #addr; blx r12` (PIC invoke).  So find the runtime callers by
breaking on the callees and recording LR plus the first arguments.

Also observers the native rectangle fill body 0x1047743C, which the display
module reaches directly from 0x104E9238.

QEMU-only; read-only guest (no guest memory writes).
"""
import argparse
import json
import socket
import sys
import time
from pathlib import Path


SITES = {
    0x10509C38: 'copy-api-38',
    0x10509C62: 'copy-api-62',
    0x10509D10: 'copy-api-d10',
    0x1047743C: 'fill-body',
    0x1052F12A: 'bind-main',
    0x1052F3C4: 'plane-check',
}
CAP = 400

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
    return KNOWN.get(value, 'other')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--seconds', type=float, default=1.2)
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
                        args = [int(regs['r0'], 16), int(regs['r1'], 16),
                                int(regs['r2'], 16), int(regs['r3'], 16)]
                        records.append(dict(
                            thread=thread, site=name(pc), pc=hex(pc),
                            lr=regs['r14'], args=[hex(v) for v in args],
                            arg_names=[name(v) for v in args]))
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
        by_site = {}
        by_lr = {}
        for rec in records:
            by_site[rec['site']] = by_site.get(rec['site'], 0) + 1
            key = '{}<-{}'.format(rec['site'], rec['lr'])
            by_lr[key] = by_lr.get(key, 0) + 1
        result = dict(probe='display copy/fill runtime callers',
                      sites={hex(k): v for k, v in SITES.items()},
                      hits=state['hits'], by_site=by_site, by_lr=by_lr,
                      records=records[:CAP],
                      notes='QEMU-only; LR and r0-r3 at compositing helper entries')
        (out / 'context.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(dict(hits=state['hits'], by_site=by_site,
                              by_lr=dict(sorted(by_lr.items(), key=lambda kv: -kv[1]))),
                         indent=2))
    finally:
        s.close()


if __name__ == '__main__':
    main()
