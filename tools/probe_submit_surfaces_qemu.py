"""QEMU-only observer: search GPU submit descriptors for surface framebuffers.

Every OpenVG-backend submit funnels through 0x1055D112 with a 0x68-byte command
descriptor in r0. Read the descriptor, follow the pointers it contains (and one
level of their fields) and report which submit refers to the live-view surfaces
(0xD045F200 UI canvas / 0xD0959600 image surface / 0xD2BFD600 third canvas) and
where that address sits in the object graph.

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
}
CAP = 60


def plausible(value):
    # guest object/pointer heap ranges seen so far
    return (0xC0000000 <= value < 0xD6000000)


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
                        rec = dict(n=len(records), thread=thread, desc=hex(desc),
                                   lr=hex(int(regs['r14'], 16)),
                                   r1=hex(int(regs['r1'], 16)),
                                   graph=[], hits=[])
                        if plausible(desc) or desc >= 0xC0000000:
                            raw = remote.memory(desc, 0x68)
                            rec['desc_hex'] = raw.hex()
                            pointers = []
                            for off in range(0, 0x68, 4):
                                value = int.from_bytes(raw[off:off + 4], 'little')
                                if value in SURFACES:
                                    rec['hits'].append(dict(
                                        where='desc+{:x}'.format(off),
                                        value=hex(value), name=SURFACES[value]))
                                elif plausible(value):
                                    pointers.append((off, value))
                            for off, value in pointers:
                                obj = remote.memory(value, 0x40)
                                node = dict(ptr=hex(value), from_off=hex(off),
                                            hex=obj.hex(), hits=[])
                                words = []
                                for k in range(0, 0x40, 4):
                                    word = int.from_bytes(obj[k:k + 4], 'little')
                                    words.append(word)
                                    if word in SURFACES:
                                        node['hits'].append(dict(
                                            where='+{:x}'.format(k),
                                            value=hex(word), name=SURFACES[word]))
                                node['words'] = [hex(w) for w in words]
                                # one more level for object pointers
                                for k, word in enumerate(words):
                                    if not plausible(word) or word == value:
                                        continue
                                    try:
                                        inner = remote.memory(word, 0x40)
                                    except Exception:
                                        continue
                                    iwords = [hex(int.from_bytes(inner[j:j + 4], 'little'))
                                              for j in range(0, 0x40, 4)]
                                    ihits = [(hex(int.from_bytes(inner[j:j + 4], 'little')),
                                              SURFACES[int.from_bytes(inner[j:j + 4], 'little')])
                                             for j in range(0, 0x40, 4)
                                             if int.from_bytes(inner[j:j + 4], 'little') in SURFACES]
                                    node.setdefault('inner', []).append(dict(
                                        at='+{:x}'.format(k), ptr=hex(word),
                                        words=iwords, hits=ihits))
                                if node['hits'] or any(i['hits'] for i in node.get('inner', [])):
                                    rec['graph'].append(node)
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
        hits = [rec for rec in records if rec['hits'] or rec['graph']]
        print('submits', len(records), 'with-surface-refs', len(hits))
        for rec in hits[:10]:
            print(rec['n'], rec['lr'], 'direct', rec['hits'],
                  'graph', [(g['from_off'], g['ptr'], g['hits'],
                             [(i['at'], i['ptr'], i['hits']) for i in g.get('inner', [])])
                            for g in rec['graph']][:3])
        (out / 'context.json').write_text(json.dumps(
            dict(probe='submit descriptor surface search', submit=hex(SUBMIT),
                 surfaces={hex(k): v for k, v in SURFACES.items()},
                 submits=len(records), records=records), indent=2) + '\n')
    finally:
        s.close()


if __name__ == '__main__':
    main()
