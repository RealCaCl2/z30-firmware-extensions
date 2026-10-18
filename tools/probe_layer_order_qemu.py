"""QEMU-only layer probe: does the image surface composite under the UI canvas?

The per-cycle overlay build performs two GEOMETRY copies into the rotating
overlay bank: first from the image surface (0xD0959600), then from the UI canvas
(0xD045F200). If the image surface is composited below the canvas, a mask
painted into it would dim the video while status icons and focus brackets (in
the canvas) stay above it.

This probe writes solid test patches into each surface through the debugger
once, while the stock renderer is stopped, then watches the scanout frames:

* image surface rows [420,440) x[100,200): opaque blue
* image surface rows [393,480) x[300,420): black alpha 64 (letterbox band)
* UI canvas rows [200,220) x[100,200): opaque red

Screen mapping for a surface pixel row: screen_y = 479 - row.

QEMU-only; no guest code is patched.
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


SURFACES = {
    'image': 0xD0959600,
    'canvas': 0xD045F200,
}
TEST_STOP = 0x10477D68      # renderer pre-copy vgFinish call
RETURN_SITE = 0x10477D6D


def write_rows(remote, base, rows, x0, x1, rgba):
    payload = bytes(rgba) * (x1 - x0)
    for row in rows:
        address = base + (row * 720 + x0) * 4
        assert remote.command(f'M{address:x},{len(payload):x}:' + payload.hex()) == 'OK'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--alpha', type=lambda s: int(s, 0), default=0x40)
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from liveview_session import start, refresh, DEVICE_ENV

    patches = [
        dict(name='A-image-blue', surface='image', rows=range(420, 440),
             x=(100, 200), rgba=(0, 0, 255, 255)),
        dict(name='B-canvas-red', surface='canvas', rows=range(200, 220),
             x=(100, 200), rgba=(255, 0, 0, 255)),
        dict(name='C-image-letterbox', surface='image',
             rows=range(393, 480), x=(300, 420), rgba=(0, 0, 0, a.alpha)),
    ]
    state = {'written': False}

    class ObservedSession(Session):
        def run_for(self, seconds):
            remote = self.remote
            deadline = time.monotonic() + seconds
            assert remote.command(f'Z1,{TEST_STOP:x},2') == 'OK'
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
                    lr = int(regs['r14'], 16)
                    assert remote.command(f'z1,{TEST_STOP:x},2') == 'OK'
                    if pc == TEST_STOP and lr == RETURN_SITE and not state['written']:
                        for patch in patches:
                            write_rows(remote, SURFACES[patch['surface']],
                                       patch['rows'], patch['x'][0], patch['x'][1],
                                       patch['rgba'])
                        state['written'] = True
                        print('wrote patches', flush=True)
                    assert remote.command('Hc' + thread) == 'OK'
                    remote.send('s')
                    remote.sock.settimeout(5)
                    self.packet = remote.receive()
                    assert remote.command('Hc-1') == 'OK'
                    assert remote.command(f'Z1,{TEST_STOP:x},2') == 'OK'
            finally:
                remote.command(f'z1,{TEST_STOP:x},2')

    def grab(name):
        path = s.frame(name)
        width, height, pixels = read_ppm(s.graphics / 'gui-latest.ppm')

        def pixel(x, y):
            i = (y * width + x) * 3
            return [pixels[i], pixels[i + 1], pixels[i + 2]]

        return dict(name=name, png=str(path),
                    image_patch=pixel(150, 50),     # image rows 420..439
                    canvas_patch=pixel(150, 270),   # canvas rows 200..219
                    letterbox_top=pixel(350, 30),   # image rows 449..479
                    letterbox_mid=pixel(350, 60),   # image rows 393..419
                    control=pixel(350, 240))

    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    s = ObservedSession(str(out), device_env=DEVICE_ENV)
    try:
        start(s)
        refresh(s)
        frames = [grab('01-baseline')]
        s.run_for(.8)
        frames.append(grab('02-after-patches'))
        s.run_for(.8)
        frames.append(grab('03-patches-later'))
        result = dict(probe='layer probe: image surface vs UI canvas',
                      alpha=hex(a.alpha), patches=patches, frames=frames,
                      mapping='surface row == 479 - screen_y',
                      notes='QEMU-only; patches written once through the debugger')
        (out / 'context.json').write_text(json.dumps(result, indent=2) + '\n')
        for frame in frames:
            print(frame['name'], {k: frame[k] for k in
                                  ('image_patch', 'canvas_patch', 'letterbox_top',
                                   'letterbox_mid', 'control')}, flush=True)
    finally:
        s.close()


if __name__ == '__main__':
    main()
