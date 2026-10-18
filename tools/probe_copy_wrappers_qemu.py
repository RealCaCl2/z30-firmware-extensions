"""QEMU-only observation of the two overlay copy-submission wrappers.

The copy-submit entry (0x1055D112) is reached through small wrappers in
0x10514xxx. Two of them build an 0x0a03 command (0x10514CFC) and an 0x0a01
command (0x10514D3E). This probe stops at those wrapper entries and records the
caller LR and the incoming arguments, so the display-module call sites that
feed the per-cycle overlay copies can be identified.

QEMU-only; no guest code is patched and nothing is injected.
"""
import argparse
import json
import socket
import sys
import time
from pathlib import Path


SITES = {
    0x10514CFC: 'copy-0a03',
    0x10514D3E: 'copy-0a01',
    0x10514D8C: 'copy-rect',
}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--seconds', type=float, default=1.5)
    p.add_argument('--limit', type=int, default=200)
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from liveview_session import start, refresh, DEVICE_ENV

    records = []
    pending = {'site': None, 'thread': None}

    class ObservedSession(Session):
        def run_for(self, seconds):
            remote = self.remote
            deadline = time.monotonic() + seconds
            for site in SITES:
                assert remote.command(f'Z1,{site:x},2') == 'OK'
            try:
                while time.monotonic() < deadline and len(records) < a.limit:
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
                    if pc not in SITES:
                        raise RuntimeError('Unexpected guest stop: ' + packet)
                    sp = int(regs['r13'], 16)
                    words = remote.memory(sp, 24)
                    record = dict(n=len(records), site=SITES[pc], pc=hex(pc),
                                  thread=thread, caller=hex(int(regs['r14'], 16)),
                                  r0=hex(int(regs['r0'], 16)),
                                  r1=hex(int(regs['r1'], 16)),
                                  r2=hex(int(regs['r2'], 16)),
                                  r3=hex(int(regs['r3'], 16)),
                                  stack=words.hex(),
                                  stack_words=[hex(int.from_bytes(words[i:i + 4], 'little'))
                                               for i in range(0, 24, 4)])
                    records.append(record)
                    for site in SITES:
                        assert remote.command(f'z1,{site:x},2') == 'OK'
                    assert remote.command('Hc' + thread) == 'OK'
                    remote.send('s')
                    remote.sock.settimeout(5)
                    self.packet = remote.receive()
                    assert remote.command('Hc-1') == 'OK'
                    for site in SITES:
                        assert remote.command(f'Z1,{site:x},2') == 'OK'
            finally:
                for site in SITES:
                    remote.command(f'z1,{site:x},2')

    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    s = ObservedSession(str(out), device_env=DEVICE_ENV)
    try:
        start(s)
        refresh(s)
        s.run_for(.3)
        s.run_for(a.seconds)
        (out / 'context.json').write_text(json.dumps(
            dict(probe='copy wrapper callers', sites={hex(k): v for k, v in SITES.items()},
                 records=records), indent=2) + '\n')
        print('records:', len(records), flush=True)
        for record in records:
            print(record['n'], record['site'], record['thread'],
                  'caller', record['caller'], 'args',
                  record['r0'], record['r1'], record['r2'], record['r3'],
                  'stack', record['stack_words'], flush=True)
    finally:
        s.close()


if __name__ == '__main__':
    main()
