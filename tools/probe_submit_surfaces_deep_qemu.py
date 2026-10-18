"""QEMU-only observer: follow GPU submit descriptors and find live-view surfaces.

At the OpenVG-backend submit 0x1055D112 read the 0x68-byte command descriptor,
follow every plausible pointer in it one level (0x100 bytes) and report where
the live-view surface framebuffer addresses appear in the pointed objects.

QEMU-only; read-only guest.
"""
import argparse
import json
import socket
import sys
import time
from pathlib import Path


SUBMIT = 0x1055D112
SURFACES = {
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


def plausible(value):
    return 0xC0000000 <= value < 0xD6000000


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
            assert remote.command('Z1,{:x},2'.format(SUBMIT)) == 'OK'
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
                    assert remote.command('z1,{:x},2'.format(SUBMIT)) == 'OK'
                    if pc == SUBMIT:
                        desc = int(regs['r0'], 16)
                        rec = dict(n=len(records), thread=thread, lr=hex(int(regs['r14'], 16)),
                                   desc=hex(desc), hits=[])
                        if plausible(desc):
                            raw = remote.memory(desc, 0x68)
                            rec['desc_hex'] = raw.hex()
                            rec['type'] = hex(int.from_bytes(raw[0:4], 'little'))
                            for off in range(0, 0x68, 4):
                                value = int.from_bytes(raw[off:off + 4], 'little')
                                if value in SURFACES:
                                    rec['hits'].append(dict(where='desc+{:x}'.format(off),
                                                            value=hex(value),
                                                            name=SURFACES[value]))
                                elif plausible(value):
                                    try:
                                        obj = remote.memory(value, 0x100)
                                    except Exception:
                                        continue
                                    for k in range(0, 0x100, 4):
                                        word = int.from_bytes(obj[k:k + 4], 'little')
                                        if word in SURFACES:
                                            rec['hits'].append(dict(
                                                where='obj@desc+{:x}+{:x}'.format(off, k),
                                                value=hex(word), name=SURFACES[word],
                                                ptr=hex(value)))
                        records.append(rec)
                    assert remote.command('Hc' + thread) == 'OK'
                    remote.send('s')
                    remote.sock.settimeout(5)
                    self.packet = remote.receive()
                    assert remote.command('Hc-1') == 'OK'
                    assert remote.command('Z1,{:x},2'.format(SUBMIT)) == 'OK'
            finally:
                remote.command('z1,{:x},2'.format(SUBMIT))

    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    s = ObservedSession(str(out), device_env=DEVICE_ENV)
    try:
        start(s)
        refresh(s)
        s.run_for(0.2)
        state['armed'] = True
        s.run_for(a.seconds)
        hits = [rec for rec in records if rec['hits']]
        print('submits', len(records), 'with-surface-hits', len(hits))
        for rec in hits[:20]:
            print(rec['n'], rec['lr'], rec['type'], rec['hits'][:3])
        types = {}
        for rec in records:
            types[rec['type']] = types.get(rec['type'], 0) + 1
        print('types', types)
        (out / 'context.json').write_text(json.dumps(
            dict(probe='submit descriptor surface search deep', submit=hex(SUBMIT),
                 surfaces={hex(k): v for k, v in SURFACES.items()},
                 submits=len(records), records=records), indent=2) + '\n')
    finally:
        s.close()


if __name__ == '__main__':
    main()
