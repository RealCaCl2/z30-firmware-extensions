"""QEMU-only observer: who binds framebuffer addresses into plane objects?

0x1052F12A(plane, address) stores address into [plane+0x84] and 0x1052F14E does
the same for the second plane at [plane+0x88]. If the live-view image surface
(d0959600) is installed through this function, a hook here can substitute a
mask surface address and put a dim layer under the UI canvas copy.

QEMU-only; read-only guest.
"""
import argparse
import json
import socket
import sys
import time
from pathlib import Path


BIND_MAIN = 0x1052F12A
BIND_SECOND = 0x1052F14E
CAP = 240

WATCH = {
    0xD045F200: 'ui-canvas-d045f200',
    0xD0959600: 'image-surface-d0959600',
    0xD2BFD600: 'canvas3-d2bfd600',
    0xECCA7000: 'bank0-ecca7000',
    0xECEE7000: 'bank1-ecee7000',
    0xED127000: 'bank2-ed127000',
    0xED733000: 'video-base-y',
    0xED787600: 'video-base-uv',
}


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
    state = {'hits': 0}

    class ObservedSession(Session):
        def run_for(self, seconds):
            remote = self.remote
            deadline = time.monotonic() + seconds
            for address in (BIND_MAIN, BIND_SECOND):
                assert remote.command(f'Z1,{address:x},2') == 'OK'
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
                    for address in (BIND_MAIN, BIND_SECOND):
                        assert remote.command(f'z1,{address:x},2') == 'OK'
                    if pc in (BIND_MAIN, BIND_SECOND):
                        state['hits'] += 1
                        plane = int(regs['r0'], 16)
                        value = int(regs['r1'], 16)
                        frame = None
                        try:
                            # plane object: current main/second framebuffer fields
                            raw = remote.memory(plane + 0x84, 4)
                            old = int.from_bytes(raw, 'little')
                            frame = WATCH.get(old)
                        except Exception:
                            old = None
                        records.append(dict(
                            thread=thread, lr=regs['r14'], entry=hex(pc),
                            plane=hex(plane), value=hex(value), old=hex(old) if old is not None else None,
                            old_name=frame, value_name=WATCH.get(value)))
                    assert remote.command('Hc' + thread) == 'OK'
                    remote.send('s')
                    remote.sock.settimeout(5)
                    self.packet = remote.receive()
                    assert remote.command('Hc-1') == 'OK'
                    for address in (BIND_MAIN, BIND_SECOND):
                        assert remote.command(f'Z1,{address:x},2') == 'OK'
            finally:
                for address in (BIND_MAIN, BIND_SECOND):
                    remote.command(f'z1,{address:x},2')

    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    s = ObservedSession(str(out), device_env=DEVICE_ENV)
    try:
        start(s)
        refresh(s)
        s.run_for(a.seconds)
        by_value = {}
        for rec in records:
            by_value.setdefault(rec['value_name'] or 'other', 0)
            by_value[rec['value_name'] or 'other'] += 1
        result = dict(probe='plane framebuffer bind observers', main=hex(BIND_MAIN),
                      second=hex(BIND_SECOND), hits=state['hits'],
                      by_value=by_value, records=records[:CAP],
                      notes='QEMU-only; logs (plane, address) at the two plane bind entry points')
        (out / 'context.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(dict(hits=state['hits'],
                              values=dict(sorted(((k, v) for k, v in by_value.items()),
                                                 key=lambda kv: -kv[1]))), indent=2))
    finally:
        s.close()


if __name__ == '__main__':
    main()
