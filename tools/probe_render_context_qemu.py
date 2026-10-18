"""Observe the stock live-view UI renderer entry and factory clear calls.

Read-only QEMU diagnostic: records renderer argument structures, call sites and
the native vgClear call sites/rectangles that the stock worker submits. It does
not draw, patch guest code, or write into guest framebuffers; the sibling
assisted QEMU harness and its colour-bar stimulus are fixtures, not camera or
panel proof.
"""
import argparse
import hashlib
import json
import socket
import struct
import sys
import time
from pathlib import Path


def read_ppm(path):
    data = Path(path).read_bytes()
    magic, dimensions, maximum, pixels = data.split(b'\n', 3)
    width, height = map(int, dimensions.split())
    if magic != b'P6' or maximum != b'255' or len(pixels) != width * height * 3:
        raise ValueError('Invalid PPM: ' + str(path))
    return width, height, pixels


def sample(ppm, points):
    width, height, pixels = ppm
    out = {}
    for name, (x, y) in points.items():
        if not (0 <= x < width and 0 <= y < height):
            raise ValueError(f'Pixel out of frame: {name} ({x},{y})')
        i = (y * width + x) * 3
        out[name] = [pixels[i], pixels[i + 1], pixels[i + 2]]
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--seconds', type=float, default=0.8)
    p.add_argument('--max-stops', type=int, default=400)
    p.add_argument('--render-entry', type=lambda s: int(s, 0), default=0x10477C9C)
    p.add_argument('--clear', type=lambda s: int(s, 0), default=0x1050AAD6)
    p.add_argument('--clear-args', type=int, default=4)
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from liveview_session import start, refresh, DEVICE_ENV

    points = {a.render_entry: 'render_entry', a.clear: 'vgClear'}
    captures = {name: [] for name in points.values()}
    arg_hashes = {}
    callsites = {}
    order = []

    def capture(remote, regs, stop_thread=None):
        pc = int(regs['r15'], 16)
        name = points[pc]
        lr = int(regs['r14'], 16)
        item = dict(api=name, pc=hex(pc), lr=regs['r14'], thread=stop_thread,
                    r0=regs['r0'], r1=regs['r1'], r2=regs['r2'], r3=regs['r3'])
        if name == 'render_entry':
            r0 = int(regs['r0'], 16)
            if r0:
                raw = remote.memory(r0, 0x60)
                item['arg'] = raw.hex()
                digest = hashlib.sha256(raw).hexdigest()[:16]
                item['arg_sha'] = digest
                arg_hashes[digest] = arg_hashes.get(digest, 0) + 1
            else:
                item['arg'] = None
        else:
            raw = remote.memory(int(regs['r13'], 16), 32)
            item['stack_top'] = raw.hex()
            try:
                item['args'] = [int(regs['r0'], 16), int(regs['r1'], 16),
                                int(regs['r2'], 16), int(regs['r3'], 16)]
            except ValueError:
                item['args'] = None
        key = (name, lr)
        callsites[key] = callsites.get(key, 0) + 1
        if len(captures[name]) < 120:
            captures[name].append(item)
        order.append((name, lr))
        return item

    class ObservedSession(Session):
        def run_for(self, seconds):
            remote = self.remote
            deadline = time.monotonic() + seconds
            armed = set(points)
            seen = 0
            for point in armed:
                assert remote.command(f'Z1,{point:x},2') == 'OK'
            try:
                while time.monotonic() < deadline and seen < a.max_stops:
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
                    if pc not in armed:
                        raise RuntimeError('Unexpected guest stop: ' + packet)
                    capture(remote, regs, thread)
                    seen += 1
                    assert remote.command(f'z1,{pc:x},2') == 'OK'
                    assert remote.command('Hc' + thread) == 'OK'
                    remote.send('s')
                    remote.sock.settimeout(5)
                    self.packet = remote.receive()
                    assert remote.command('Hc-1') == 'OK'
                    if seen >= a.max_stops:
                        break
                    assert remote.command(f'Z1,{pc:x},2') == 'OK'
            finally:
                for point in armed:
                    remote.command(f'z1,{point:x},2')

    samples = {'points': {'internal': (300, 80), 'internal2': (300, 130),
                          'probe_rect': (300, 80), 'top_band': (300, 20),
                          'bottom_band': (300, 460), 'left_edge': (20, 240),
                          'right_edge': (700, 240)}}
    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    s = ObservedSession(str(out), device_env=DEVICE_ENV)
    try:
        start(s)
        frames = []
        frames.append(str(s.frame('01-stock-liveview')))
        refresh(s)
        # Only observe naturally scheduled workers: no injection and no nested
        # native calls while this checkpoint loop is active.
        s.run_for(a.seconds)
        frames.append(str(s.frame('02-after-observe')))
        ppm = read_ppm(s.graphics / 'gui-latest.ppm')
        before = sample(ppm, samples['points'])
        result = dict(
            stock_code_unchanged=True, mask_drawn=False, camera_pixels_verified=False,
            startup='assisted', sensor_source='model colour bars when descriptors permit',
            renderer_entry=hex(a.render_entry), clear_api=hex(a.clear),
            stops=len(order), max_stops=a.max_stops,
            arg_struct_variants={k: v for k, v in sorted(arg_hashes.items(), key=lambda kv: -kv[1])},
            callsites={f'{name}<-{hex(lr)}': n for (name, lr), n in sorted(callsites.items())},
            captures=captures, frames=frames, samples_before=before)
        (out / 'context.json').write_text(json.dumps(result, indent=2) + '\n')
        print('Stops:', len(order), flush=True)
        print('Renderer arg variants:', len(arg_hashes), flush=True)
        print('Callsites:', result['callsites'], flush=True)
    finally:
        s.close()


if __name__ == '__main__':
    main()
