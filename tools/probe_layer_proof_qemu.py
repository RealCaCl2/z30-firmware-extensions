"""QEMU-only layer-order proof: patch the image surface and UI canvas right
before their copies are submitted, then see which one wins.

The display module builds the rotating overlay bank from two sources per cycle:
first the image surface (0xD0959600, SRC_OVER), then the UI canvas (0xD045F200,
SRC_OVER). This probe stops on every GPU copy submit (0x1055D112) and writes:

  * image surface  rows [320,352) x[100,200): opaque blue
  * UI canvas      rows [300,332) x[100,200): opaque red
    (both map to overlapping screen pixels: screen_y = 479 - row)

so the region screen y[148,160) x[100,200) is covered by both patches. Whichever
colour shows there is the higher layer.

A second patch pair tests under-UI dimming: image surface rows [0,20) x[300,420)
black alpha 64 (letterbox band), while a white-on-canvas patch sits in the same
band at x[480,500) - if the canvas is above, that white patch stays bright.

QEMU-only; patches go through the debugger, no guest code is modified.
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


IMAGE = 0xD0959600
CANVAS = 0xD045F200
COPY_SUBMIT = 0x1055D112
PATCHES = [
    dict(name='image-blue', base=IMAGE, rows=range(320, 352), x=(100, 200),
         rgba=(0, 0, 255, 255)),
    dict(name='canvas-red', base=CANVAS, rows=range(300, 332), x=(100, 200),
         rgba=(255, 0, 0, 255)),
    dict(name='image-band', base=IMAGE, rows=range(0, 20), x=(300, 420),
         rgba=(0, 0, 0, 0x40)),
    dict(name='canvas-white', base=CANVAS, rows=range(0, 20), x=(480, 500),
         rgba=(255, 255, 255, 255)),
]


def write_rows(remote, base, rows, x0, x1, rgba):
    payload = bytes(rgba) * (x1 - x0)
    for row in rows:
        address = base + (row * 720 + x0) * 4
        assert remote.command(f'M{address:x},{len(payload):x}:' + payload.hex()) == 'OK'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--seconds', type=float, default=1.2)
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from liveview_session import start, refresh, DEVICE_ENV

    stats = {'stops': 0, 'patched': 0}

    class ObservedSession(Session):
        def run_for(self, seconds):
            remote = self.remote
            deadline = time.monotonic() + seconds
            assert remote.command(f'Z1,{COPY_SUBMIT:x},2') == 'OK'
            try:
                while time.monotonic() < deadline:
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
                    stats['stops'] += 1
                    assert remote.command(f'z1,{COPY_SUBMIT:x},2') == 'OK'
                    if pc == COPY_SUBMIT:
                        for patch in PATCHES:
                            write_rows(remote, patch['base'], patch['rows'],
                                       patch['x'][0], patch['x'][1], patch['rgba'])
                        stats['patched'] += 1
                    assert remote.command('Hc' + thread) == 'OK'
                    remote.send('s')
                    remote.sock.settimeout(5)
                    self.packet = remote.receive()
                    assert remote.command('Hc-1') == 'OK'
                    assert remote.command(f'Z1,{COPY_SUBMIT:x},2') == 'OK'
            finally:
                remote.command(f'z1,{COPY_SUBMIT:x},2')

    def grab(name):
        path = s.frame(name)
        width, height, pixels = read_ppm(s.graphics / 'gui-latest.ppm')

        def pixel(x, y):
            i = (y * width + x) * 3
            return [pixels[i], pixels[i + 1], pixels[i + 2]]

        return dict(name=name, png=str(path),
                    overlap=pixel(150, 154),      # image rows 320..351 vs canvas 300..331 -> screen 148..160
                    band=pixel(350, 470),         # image rows 0..19 -> screen 460..479
                    canvas_white=pixel(490, 470),  # canvas rows 0..19 at x480..500
                    bracket=pixel(610, 455),
                    info_icon=pixel(690, 150),
                    control=pixel(350, 240))

    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    s = ObservedSession(str(out), device_env=DEVICE_ENV)
    try:
        start(s)
        refresh(s)
        frames = [grab('01-baseline')]
        s.run_for(a.seconds)
        frames.append(grab('02-after-patches'))
        s.run_for(.6)
        frames.append(grab('03-later'))
        result = dict(probe='layer order proof via copy-submit patches',
                      copy_submit=hex(COPY_SUBMIT), stats=stats,
                      patches=[dict(name=p['name'], base=hex(p['base']),
                                    rows=[p['rows'][0], p['rows'][-1]],
                                    x=list(p['x']), rgba=list(p['rgba']))
                               for p in PATCHES],
                      frames=frames,
                      mapping='surface row == 479 - screen_y',
                      notes='image surface under canvas expected: overlap shows canvas red; '
                            'under-UI dim band shows dark while canvas white patch stays bright')
        (out / 'context.json').write_text(json.dumps(result, indent=2) + '\n')
        for frame in frames:
            print(frame['name'], {k: frame[k] for k in
                                  ('overlap', 'band', 'canvas_white', 'bracket',
                                   'info_icon', 'control')}, flush=True)
    finally:
        s.close()


if __name__ == '__main__':
    main()
