"""QEMU-only observer: display-module dispatcher event order in live view.

Static analysis of the 0x104E8xxx-0x104E9Exx display module found several
compose/copy/overlay functions, all reached from the dispatcher region around
0x103BC0xx-0x103BC2xx (the same dispatcher thunk that reaches the UI renderer at
0x103BC4D0).  Break on those handler entry points to see which run, in what
order, once per display cycle.

QEMU-only; read-only guest (no guest memory writes).
"""
import argparse
import json
import socket
import sys
import time
from pathlib import Path


SITES = {
    0x103BC01A: 'disp-plane-check',
    0x103BC032: 'disp-D10-b',
    0x103BC28E: 'disp-compose-main',
    0x103BC29A: 'disp-D10-a',
    0x103BC2A8: 'disp-copy38',
    0x103BC2B2: 'disp-2copies',
    0x103BC2DC: 'disp-2copies-b',
    0x103BC4D0: 'ui-renderer',
}
CAP = 400


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
                        records.append(dict(
                            n=len(records), thread=thread, site=SITES[pc],
                            lr=regs['r14'],
                            args=[regs['r0'], regs['r1'], regs['r2'], regs['r3']]))
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
        order = [rec['site'] for rec in records]
        result = dict(probe='display dispatcher event order', sites=SITES,
                      hits=state['hits'], by_site=by_site, order=order,
                      records=records[:CAP],
                      notes='QEMU-only; handler entry order in live view')
        (out / 'context.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(dict(hits=state['hits'], by_site=by_site,
                              order=order[:80]), indent=2))
    finally:
        s.close()


if __name__ == '__main__':
    main()
