"""QEMU-only: locate the plane/surface address fields used by copy submits.

At the common submit entry 0x1055D112, dump the 0x68-byte descriptor at r0 and
follow every pointer-looking word one level deep, scanning 0x100 bytes for the
known live-view surface addresses (image surface d0959600, UI canvas d045f200,
overlay banks, video base).  This shows which field would have to be rewritten to
redirect a copy source.  Read-only.
"""
import argparse
import json
import socket
import sys
import time
from pathlib import Path


SUBMIT = 0x1055D112
KNOWN = {
    0xD045F200: 'ui-canvas', 0xD0959600: 'image-surface', 0xD2BFD600: 'canvas3',
    0xECCA7000: 'bank0', 0xECEE7000: 'bank1', 0xED127000: 'bank2',
    0xED733000: 'video-base-y', 0xED787600: 'video-base-uv',
}
CAP = 12
DESC_WORDS = 26
CHILD_BYTES = 0x100


def read_bytes(remote, address, length):
    reply = remote.command(f'm{address:x},{length:x}').strip()
    try:
        return bytes.fromhex(reply)
    except ValueError:
        return None


def words_of(data, count):
    return [int.from_bytes(data[i:i + 4], 'little') for i in range(0, min(len(data), count * 4) - 3, 4)]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--seconds', type=float, default=1.0)
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from liveview_session import start, refresh, DEVICE_ENV

    records = []

    class ObservedSession(Session):
        def run_for(self, seconds):
            remote = self.remote
            assert remote.command(f'Z1,{SUBMIT:x},2') == 'OK'
            deadline = time.monotonic() + seconds
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
                    assert remote.command(f'z1,{SUBMIT:x},2') == 'OK'
                    if pc == SUBMIT:
                        desc = int(regs['r0'], 16)
                        record = dict(thread=thread, lr=regs['r14'], desc=hex(desc), hits=[], raw_desc=None,
                                      children=[])
                        data = read_bytes(remote, desc, DESC_WORDS * 4)
                        if data:
                            record['raw_desc'] = [hex(w) for w in words_of(data, DESC_WORDS)]
                            for index, word in enumerate(words_of(data, DESC_WORDS)):
                                if word in KNOWN:
                                    record['hits'].append(dict(path=f'desc+{index * 4:#x}', value=hex(word),
                                                               name=KNOWN[word]))
                                if 0xC0000000 <= word < 0xE0000000:
                                    child = read_bytes(remote, word, CHILD_BYTES)
                                    if not child:
                                        continue
                                    found = []
                                    for child_index, child_word in enumerate(words_of(child, CHILD_BYTES // 4)):
                                        if child_word in KNOWN:
                                            found.append(dict(offset=child_index * 4,
                                                              value=hex(child_word), name=KNOWN[child_word]))
                                    record['children'].append(dict(
                                        desc_offset=index * 4, pointer=hex(word),
                                        hits=found,
                                        words=[hex(w) for w in words_of(child, 16)]))
                        records.append(record)
                    assert remote.command('Hc' + thread) == 'OK'
                    remote.send('s')
                    remote.sock.settimeout(5)
                    self.packet = remote.receive()
                    assert remote.command('Hc-1') == 'OK'
                    assert remote.command(f'Z1,{SUBMIT:x},2') == 'OK'
            finally:
                remote.command(f'z1,{SUBMIT:x},2')

    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    s = ObservedSession(str(out), device_env=DEVICE_ENV)
    try:
        start(s)
        refresh(s)
        s.run_for(a.seconds)
        print(json.dumps(dict(probe='copy submit descriptor scan', submit=hex(SUBMIT),
                              known={hex(k): v for k, v in KNOWN.items()}, records=records), indent=2),
              flush=True)
    finally:
        s.close()


if __name__ == '__main__':
    main()
