"""QEMU-only experiment E27: fill the live-view image surface from the UI
renderer hook point (0x10477D68, the pre-copy vgFinish call), discovering the
image surface through the display-state slot at 0xC18200C0.

This is the planned production timing for the soft aspect mask: the renderer
runs at UI event rate, the image surface holds the current live-view frame, and
the surface is composited under the UI canvas, so dimming it keeps the stock
grid, status icons and focus brackets untouched.

QEMU-only; the colour bars are the assisted preview fixture, not camera data.
"""
import argparse
import json
import socket
import sys
import time
from pathlib import Path

HOOK = 0x10477D68
IMAGE_SLOT = 0xC18200C0
W = 720
H = 480
# screen_y = 479 - row
TOP_BAND_ROWS = (393, 480)      # screen y [0,87)
BOTTOM_BAND_ROWS = (0, 87)      # screen y [393,480)
DIM = 0x40000000                # bytes 00 00 00 40 -> black, alpha 64
ERASE = 0x00000000
CAP = 200
CHUNK = 2048
SAMPLES = [
    ('band_top', 350, 40),
    ('band_bottom', 350, 450),
    ('photo', 350, 240),
    ('cam_icon', 40, 150),
    ('info_icon', 690, 150),
    ('card_icon', 24, 330),
    ('bracket_left', 610, 455),
    ('bracket_right', 668, 455),
]


def fill_rows(remote, base, rows, word):
    payload = (word.to_bytes(4, 'little') * W) * (rows[1] - rows[0])
    address = base + rows[0] * W * 4
    for off in range(0, len(payload), CHUNK):
        piece = payload[off:off + CHUNK]
        assert remote.command('M{:x},{:x}:{}'.format(
            address + off, len(piece), piece.hex())) == 'OK'


def read_ppm_pixels(path):
    data = Path(path).read_bytes()
    magic, dimensions, maximum, pixels = data.split(b'\n', 3)
    width, height = map(int, dimensions.split())
    if magic != b'P6' or maximum != b'255' or len(pixels) != width * height * 3:
        raise ValueError('Invalid PPM')
    return width, pixels


def sample_pixels(path):
    width, pixels = read_ppm_pixels(path)
    out = {}
    for label, x, y in SAMPLES:
        idx = (y * width + x) * 3
        out[label] = list(pixels[idx:idx + 3])
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--seconds', type=float, default=0.8)
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from liveview_session import start, refresh, DEVICE_ENV

    records = []
    state = {'armed': False, 'mode': None}

    class ObservedSession(Session):
        def run_for(self, seconds):
            if not state['armed']:
                super().run_for(seconds)
                return
            remote = self.remote
            deadline = time.monotonic() + seconds
            assert remote.command('Z1,{:x},2'.format(HOOK)) == 'OK'
            try:
                while time.monotonic() < deadline and len(records) < CAP:
                    remote.send('c')
                    remote.sock.settimeout(max(.01, deadline - time.monotonic()))
                    try:
                        packet = remote.receive()
                    except socket.timeout:
                        remote.sock.settimeout(2)
                        remote.sock.sendall(b'\x03')
                        remote.sock.settimeout(5)
                        self.packet = remote.receive()
                        break
                    self.packet = packet
                    thread = packet.split('thread:')[1].split(';')[0]
                    assert remote.command('Hg' + thread) == 'OK'
                    regs = remote.registers()
                    pc = int(regs['r15'], 16)
                    assert remote.command('z1,{:x},2'.format(HOOK)) == 'OK'
                    if pc == HOOK and state['mode']:
                        image = int.from_bytes(remote.memory(IMAGE_SLOT, 4), 'little')
                        word = DIM if state['mode'] == 'dim' else ERASE
                        fill_rows(remote, image, TOP_BAND_ROWS, word)
                        fill_rows(remote, image, BOTTOM_BAND_ROWS, word)
                        records.append(dict(n=len(records), mode=state['mode'],
                                            image=hex(image),
                                            lr=hex(int(regs['r14'], 16))))
                    assert remote.command('Hc' + thread) == 'OK'
                    remote.send('s')
                    remote.sock.settimeout(5)
                    self.packet = remote.receive()
                    assert remote.command('Hc-1') == 'OK'
                    assert remote.command('Z1,{:x},2'.format(HOOK)) == 'OK'
            finally:
                remote.command('z1,{:x},2'.format(HOOK))

    s = ObservedSession(str(a.output_dir.resolve()), device_env=DEVICE_ENV)
    (a.output_dir.resolve()).mkdir(parents=True, exist_ok=True)
    samples = {}
    try:
        start(s)
        refresh(s)
        s.frame('01-baseline')
        samples['01-baseline'] = sample_pixels(s.graphics / 'gui-latest.ppm')
        state['armed'] = True
        state['mode'] = 'dim'
        s.run_for(a.seconds)
        refresh(s)
        s.run_for(.2)
        refresh(s)
        s.frame('02-dim')
        samples['02-dim'] = sample_pixels(s.graphics / 'gui-latest.ppm')
        state['mode'] = 'erase'
        s.run_for(a.seconds)
        refresh(s)
        s.run_for(.2)
        refresh(s)
        s.frame('03-erase')
        samples['03-erase'] = sample_pixels(s.graphics / 'gui-latest.ppm')
        state['mode'] = None
        s.run_for(.2)
        refresh(s)
        s.frame('04-after-disable')
        samples['04-after-disable'] = sample_pixels(s.graphics / 'gui-latest.ppm')
    finally:
        result = dict(records=records, samples=samples)
        (a.output_dir / 'context.json').write_text(json.dumps(result, indent=1))
        s.close()
    print('records', len(records))
    for label, values in samples.items():
        print(label, values)
    print('images', sorted({r['image'] for r in records}),
          'modes', {mode: sum(1 for r in records if r['mode'] == mode)
                    for mode in ('dim', 'erase')})


if __name__ == '__main__':
    main()
