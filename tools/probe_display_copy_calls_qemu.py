"""QEMU-only observer: which display-module copy path actually runs in live view.

Break at every call site of the three plane-to-plane copy APIs in the display
module and record the source/target plane framebuffer addresses (+0xB0).

QEMU-only; read-only guest.
"""
import argparse
import json
import socket
import sys
import time
from pathlib import Path


SITES = {
    0x104E8C5E: 'copyD10',
    0x104E8D9C: 'copyD10',
    0x104E941A: 'copyD10',
    0x104E94F0: 'copyD10',
    0x104E901C: 'copy62',
    0x104E9758: 'copy62',
    0x104E9D8C: 'copy62',
    0x104E9264: 'copy38',
    0x104E9630: 'copy38',
    0x104E9746: 'copy38',
    0x104E9D7C: 'copy38',
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
                        rec = dict(n=len(records), thread=thread, site=SITES[pc],
                                   at=hex(pc), r0=hex(int(regs['r0'], 16)),
                                   r1=hex(int(regs['r1'], 16)),
                                   r2=hex(int(regs['r2'], 16)),
                                   r3=hex(int(regs['r3'], 16)),
                                   r4=hex(int(regs['r4'], 16)))
                        for key in ('r0', 'r1'):
                            value = int(regs[key], 16)
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
        by_site = {}
        for rec in records:
            by_site[rec['site']] = by_site.get(rec['site'], 0) + 1
        result = dict(probe='display-module copy call sites in live view',
                      sites=SITES, hits=state['hits'], by_site=by_site,
                      records=records,
                      notes='r0/r1 are the plane args; _fb mirrors them (the '
                            'copy APIs read the framebuffer address from +0xB0 '
                            'inside, so the call-site value is not the fb)')
        (out / 'context.json').write_text(json.dumps(result, indent=2) + '\n')
        print('hits', state['hits'], 'by_site', by_site)
        seen = set()
        for rec in records:
            key = (rec['site'], rec['r0'], rec['r1'])
            if key in seen:
                continue
            seen.add(key)
            print(json.dumps({k: rec[k] for k in
                              ('n', 'thread', 'site', 'at', 'r0', 'r1', 'r2', 'r3', 'r4')}))
    finally:
        s.close()


if __name__ == '__main__':
    main()
