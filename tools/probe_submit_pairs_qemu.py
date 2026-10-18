"""QEMU-only observer: map every GPU submit to its source/target surfaces.

At 0x1055D112 read the 0x68-byte descriptor, dereference the child objects at
the slots used by the copy wrappers (desc+0x10, desc+0x14, desc+0x1c, ...) and
read their plane framebuffer fields (+0xB0, +0x84, +0x88). The resulting
(type, source fb, target fb) table shows exactly which submit performs
"image surface -> overlay bank" and "UI canvas -> overlay bank".

QEMU-only; read-only guest.
"""
import argparse
import json
import socket
import sys
import time
from pathlib import Path


SUBMIT = 0x1055D112
CAP = 120
CODE_LO, CODE_HI = 0x10020000, 0x12240000
SLOTS = (0x10, 0x14, 0x18, 0x1c, 0x20, 0x24, 0x28, 0x2c, 0x30, 0x34, 0x38)

KNOWN = {
    0xD045F200: 'ui-canvas',
    0xD0959600: 'image-surface',
    0xD2BFD600: 'canvas3',
    0xECCA7000: 'bank0',
    0xECEE7000: 'bank1',
    0xED127000: 'bank2',
    0xED733000: 'video-base',
    0xED787600: 'video-uv',
}


def name(value):
    return KNOWN.get(value, '')


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
                        sp = int(regs['r13'], 16)
                        rec = dict(n=len(records), thread=thread,
                                   lr=hex(int(regs['r14'], 16)),
                                   r1=hex(int(regs['r1'], 16)),
                                   desc=hex(desc), children=[], fbs=[])
                        if plausible(desc):
                            raw = remote.memory(desc, 0x68)
                            rec['type'] = hex(int.from_bytes(raw[0:4], 'little'))
                            rec['desc_hex'] = raw.hex()
                            for off in SLOTS:
                                ptr = int.from_bytes(raw[off:off + 4], 'little')
                                if not plausible(ptr):
                                    continue
                                try:
                                    obj = remote.memory(ptr, 0x100)
                                except Exception:
                                    continue
                                node = dict(off=hex(off), ptr=hex(ptr))
                                for field in (0x84, 0x88, 0x8C, 0x90, 0xB0, 0xB4):
                                    if field + 4 <= 0x100:
                                        value = int.from_bytes(
                                            obj[field:field + 4], 'little')
                                        node['f{:x}'.format(field)] = hex(value)
                                        if value in KNOWN:
                                            node['fb'+hex(field)] = KNOWN[value]
                                            rec['fbs'].append(dict(
                                                where='desc+{:x}[+{:x}]'.format(off, field),
                                                value=hex(value), name=KNOWN[value]))
                                rec['children'].append(node)
                            stack = remote.memory(sp, 0x80)
                            words = [int.from_bytes(stack[i:i + 4], 'little')
                                     for i in range(0, 0x80, 4)]
                            rec['code'] = [hex(w) for w in words
                                           if CODE_LO <= w < CODE_HI]
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
        print('submits', len(records))
        for rec in records:
            if rec['fbs'] or rec.get('type') in ('0xa01', '0xa03'):
                print(rec['n'], rec.get('type'), rec['lr'], rec['fbs'][:4],
                      rec['code'][:5])
        (out / 'context.json').write_text(json.dumps(
            dict(probe='submit source/target plane mapping', submit=hex(SUBMIT),
                 records=records), indent=2) + '\n')
    finally:
        s.close()


if __name__ == '__main__':
    main()
