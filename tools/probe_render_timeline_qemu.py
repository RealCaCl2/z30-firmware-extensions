"""QEMU-only diagnostic: timeline of the live-view UI renderer, its canvas
clears, and the canvas->overlay copy submits.

Static analysis (build/research/render-context/pipeline-notes.md) placed the
renderer under dispatcher event 0x4201001A, the canvas clears at the end of the
renderer, and the copy submit in the OpenVG/display module. This probe records
the natural order and cadence of those stops and samples the UI canvas and the
three overlay alpha planes at a fixed screen point at every stop.

QEMU-only; colour bars are a fixture, no guest code is patched.
"""
import argparse
import json
import socket
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


POINT = (530, 230)
CONTROL = (530, 150)
CANVASES = [0xD045F200, 0xD0959600, 0xD2BFD600]
OVERLAYS = [0xECCA7000, 0xECEE7000, 0xED127000]
ALPHA_PLANE = 0xA8C00
POINTS = {0x10477C9C: 'renderer', 0x1047858C: 'clear-band-a', 0x104785A2: 'clear-band-b',
          0x104785BA: 'clear-band-c', 0x104785D4: 'clear-band-d',
          0x1055D112: 'copy-submit', 0x10581A4A: 'gpu-trigger',
          0x103BC4D0: 'dispatch-call'}


def canvas_offset(base, x, y):
    return base + ((480 - y) * 720 + x) * 4


def sample(remote):
    out = {}
    for index, base in enumerate(CANVASES):
        out[f'canvas{index}'] = remote.memory(canvas_offset(base, *POINT) + 3, 1)[0]
    out['canvas_ctrl'] = remote.memory(canvas_offset(CANVASES[0], *CONTROL) + 3, 1)[0]
    for index, base in enumerate(OVERLAYS):
        out[f'bank{index}'] = remote.memory(base + ALPHA_PLANE + POINT[1] * 720 + POINT[0], 1)[0]
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--seconds', type=float, default=.8)
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from liveview_session import start, refresh, DEVICE_ENV

    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    series = []

    class ObservedSession(Session):
        def run_for_multi(self, seconds):
            remote = self.remote
            deadline = time.monotonic() + seconds
            for point in POINTS:
                assert remote.command(f'Z1,{point:x},2') == 'OK'
            try:
                seq = 0
                while time.monotonic() < deadline and seq < 400:
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
                    if pc not in POINTS:
                        raise RuntimeError('Unexpected stop: ' + packet)
                    seq += 1
                    series.append(dict(seq=seq, stop=POINTS[pc], pc=hex(pc), thread=thread,
                                       registers={key: regs[key] for key in
                                                  ('r0', 'r1', 'r2', 'r3', 'r14')},
                                       **sample(remote)))
                    assert remote.command(f'z1,{pc:x},2') == 'OK'
                    assert remote.command('Hc' + thread) == 'OK'
                    remote.send('s')
                    remote.sock.settimeout(5)
                    self.packet = remote.receive()
                    assert remote.command('Hc-1') == 'OK'
                    assert remote.command(f'Z1,{pc:x},2') == 'OK'
            finally:
                for point in POINTS:
                    remote.command(f'z1,{point:x},2')

        def frame(self, name):
            return Session.frame(self, name)

    def grab(name):
        path = s.frame(name)
        width, height, pixels = read_ppm(s.graphics / 'gui-latest.ppm')
        def pixel(point):
            i = (point[1] * width + point[0]) * 3
            return [pixels[i], pixels[i + 1], pixels[i + 2]]
        return dict(name=name, png=str(path), fill=pixel(POINT), control=pixel(CONTROL))

    s = ObservedSession(str(out), device_env=DEVICE_ENV)
    try:
        start(s)
        refresh(s)
        s.run_for(.4)
        frames = [grab('01-baseline')]
        s.run_for_multi(a.seconds)
        frames.append(grab('02-after-timeline'))
        result = dict(stock_code_unchanged=True, camera_pixels_verified=False, startup='assisted',
                      point=list(POINT), control=list(CONTROL), points={hex(k): v for k, v in POINTS.items()},
                      frames=frames, series=series)
        (out / 'context.json').write_text(json.dumps(result, indent=2) + '\n')
        counts = {}
        for item in series:
            counts[item['stop']] = counts.get(item['stop'], 0) + 1
        print('counts:', counts, flush=True)
        for item in series[:25]:
            print(item['seq'], item['stop'], item['thread'],
                  'canvases=', item['canvas0'], item['canvas1'], item['canvas2'],
                  'ctrl=', item['canvas_ctrl'],
                  'banks=', item['bank0'], item['bank1'], item['bank2'], flush=True)
        print('...', len(series), 'stops', flush=True)
    finally:
        s.close()


if __name__ == '__main__':
    main()
