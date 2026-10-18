"""QEMU-only: depth-limited pointer graph walk from a copy submit descriptor.

At 0x1055D112, walk the 0x68-byte descriptor, following pointer-looking words up
to two levels (0x100 bytes each), and report where the known live-view surface
addresses appear.  Read-only.
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
CAP = 8
WINDOW = 0x100


def read_bytes(remote, address, length):
    reply = remote.command(f'm{address:x},{length:x}').strip()
    try:
        return bytes.fromhex(reply)
    except ValueError:
        return None


def words_of(data):
    return [int.from_bytes(data[i:i + 4], 'little') for i in range(0, len(data) - 3, 4)]


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
                        root = int(regs['r0'], 16)
                        record = dict(thread=thread, lr=regs['r14'], desc=hex(root), hits=[])
                        seen = {root}
                        frontier = [('desc', root)]
                        for depth in range(2):
                            next_frontier = []
                            for path, pointer in frontier:
                                data = read_bytes(remote, pointer, WINDOW)
                                if not data:
                                    continue
                                values = words_of(data)
                                for index, word in enumerate(values):
                                    if word in KNOWN:
                                        record['hits'].append(dict(path=f'{path}+{index * 4:#x}',
                                                                   value=hex(word), name=KNOWN[word]))
                                    if (0xC0000000 <= word < 0xE0000000 and word not in seen
                                            and word % 4 == 0 and len(seen) < 48):
                                        seen.add(word)
                                        next_frontier.append((f'{path}+{index * 4:#x}', word))
                            frontier = next_frontier
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
        print(json.dumps(dict(probe='submit descriptor pointer graph', submit=hex(SUBMIT),
                              known={hex(k): v for k, v in KNOWN.items()}, records=records), indent=2),
              flush=True)
    finally:
        s.close()


if __name__ == '__main__':
    main()
