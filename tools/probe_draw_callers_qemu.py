"""QEMU-only observer: callers of the OpenVG draw entry points in live view.

Record LR/thread for vgDrawImage (0x1050B5F0), vgDrawPath (0x1050AE18) and the
queued path body (0x1050ACF6) so the live-view draw pipeline can be attributed.

QEMU-only; read-only guest.
"""
import argparse
import json
import socket
import sys
import time
from pathlib import Path


SITES = {
    0x1050B5F0: 'vgDrawImage',
    0x1050AE18: 'vgDrawPath',
    0x1050ACF6: 'vgDrawPath-body',
}
CAP = 200


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--seconds', type=float, default=1.5)
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from liveview_session import start, refresh, DEVICE_ENV

    records = []
    state = {'armed': False}

    class ObservedSession(Session):
        def run_for(self, seconds):
            if not state['armed'] or len(records) >= CAP:
                super().run_for(seconds)
                return
            remote = self.remote
            deadline = time.monotonic() + seconds
            for address in SITES:
                assert remote.command('Z1,{:x},2'.format(address)) == 'OK'
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
                    for address in SITES:
                        assert remote.command('z1,{:x},2'.format(address)) == 'OK'
                    if pc in SITES:
                        records.append(dict(n=len(records), site=SITES[pc],
                                            thread=thread,
                                            lr=hex(int(regs['r14'], 16)),
                                            r0=hex(int(regs['r0'], 16)),
                                            r1=hex(int(regs['r1'], 16)),
                                            r2=hex(int(regs['r2'], 16)),
                                            r3=hex(int(regs['r3'], 16))))
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
        by = {}
        for rec in records:
            key = (rec['site'], rec['lr'])
            by[key] = by.get(key, 0) + 1
        print('stops', len(records))
        for key in sorted(by, key=lambda k: -by[k]):
            print(by[key], key)
        (out / 'context.json').write_text(json.dumps(
            dict(probe='OpenVG draw entry callers', sites=SITES,
                 stops=len(records), by=[[k[0], k[1], v] for k, v in by.items()],
                 records=records), indent=2) + '\n')
    finally:
        s.close()


if __name__ == '__main__':
    main()
