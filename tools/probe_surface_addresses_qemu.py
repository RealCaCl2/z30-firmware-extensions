"""QEMU-only: find where the live-view surface addresses are stored in guest RAM.

Pauses once at a copy submit entry (0x1055D112) and scans a few likely heap /
state regions for the known surface base addresses (image surface d0959600, UI
canvas d045f200, canvas3 d2bfd600).  Every hit tells us which descriptor field or
table entry carries that surface address - the field a redirection patch would
rewrite.  Read-only.
"""
import argparse
import json
import socket
import sys
import time
from pathlib import Path


SUBMIT = 0x1055D112
TARGETS = [0xD045F200, 0xD0959600, 0xD2BFD600]
REGIONS = [
    (0xC1800000, 0x100000),
    (0xC1D00000, 0x100000),
    (0xC2A00000, 0x200000),
    (0xCD000000, 0x200000),
]
CHUNK = 0x8000
CAP = 3


def read_bytes(remote, address, length):
    reply = remote.command(f'm{address:x},{length:x}').strip()
    try:
        return bytes.fromhex(reply)
    except ValueError:
        return None


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
                    if pc == SUBMIT and not records:
                        record = dict(thread=thread, lr=regs['r14'], desc=regs['r0'], hits=[], chunks=0)
                        for start, length in REGIONS:
                            offset = 0
                            while offset < length:
                                size = min(CHUNK, length - offset)
                                data = read_bytes(remote, start + offset, size)
                                record['chunks'] += 1
                                if data:
                                    for index in range(0, len(data) - 3, 4):
                                        word = int.from_bytes(data[index:index + 4], 'little')
                                        if word in TARGETS:
                                            record['hits'].append(dict(
                                                address=hex(start + offset + index),
                                                value=hex(word), name=hex(word)))
                                offset += size
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
        print(json.dumps(dict(probe='surface address search', targets=[hex(t) for t in TARGETS],
                              regions=[[hex(s0), hex(length)] for s0, length in REGIONS],
                              records=records), indent=2), flush=True)
    finally:
        s.close()


if __name__ == '__main__':
    main()
