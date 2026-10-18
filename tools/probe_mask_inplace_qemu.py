"""QEMU-only experiment E25: dim the image surface in-place, force a display
compose with refresh(), then verify dim -> erase -> baseline recovery.

The image surface (0xD0959600, 720x480 RGBA) is composited onto the overlay bank
*below* the UI canvas, so darkening two bands there dims the video without
touching the camera status icons or the focus brackets.  This probe stops on
every GPU submit (0x1055D112) and CPU-fills the bands (alpha 64), then calls the
stock refresh entry to force a new compose so the LCD actually shows the result.

QEMU-only; the colour bars are the assisted preview fixture, not camera data.
"""
import argparse
import json
import socket
import sys
import time
from pathlib import Path

SUBMIT = 0x1055D112
IMAGE = 0xD0959600
KNOWN = {
    0xD045F200: 'ui-canvas',
    0xD0959600: 'image-surface',
    0xD2BFD600: 'canvas3',
    0xECCA7000: 'bank0',
    0xECEE7000: 'bank1',
    0xED127000: 'bank2',
    0xED733000: 'video-base',
}
W = 720
H = 480
# screen_y = 479 - row
TOP_BAND_ROWS = (393, 480)      # screen y [0,87)
BOTTOM_BAND_ROWS = (0, 87)      # screen y [393,480)
DIM = 0x40000000                # bytes 00 00 00 40 -> black, alpha 64
ERASE = 0x00000000
CAP = 400
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


def fill_rows(remote, rows, word):
    payload = (word.to_bytes(4, 'little') * W) * (rows[1] - rows[0])
    base = IMAGE + rows[0] * W * 4
    for off in range(0, len(payload), CHUNK):
        piece = payload[off:off + CHUNK]
        assert remote.command('M{:x},{:x}:{}'.format(
            base + off, len(piece), piece.hex())) == 'OK'


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
                    if pc == SUBMIT and state['mode']:
                        desc = int(regs['r0'], 16)
                        kind = None
                        fb = 0
                        if 0xC0000000 <= desc < 0xD6000000:
                            raw = remote.memory(desc, 0x20)
                            kind = int.from_bytes(raw[0:4], 'little')
                            child = int.from_bytes(raw[0x14:0x18], 'little')
                            if 0xC0000000 <= child < 0xD6000000:
                                fb = int.from_bytes(
                                    remote.memory(child + 0xB0, 4), 'little')
                        word = DIM if state['mode'] == 'dim' else ERASE
                        fill_rows(remote, TOP_BAND_ROWS, word)
                        fill_rows(remote, BOTTOM_BAND_ROWS, word)
                        records.append(dict(n=len(records), mode=state['mode'],
                                            kind=kind, fb=KNOWN.get(fb),
                                            lr=hex(int(regs['r14'], 16))))
                    assert remote.command('Hc' + thread) == 'OK'
                    remote.send('s')
                    remote.sock.settimeout(5)
                    self.packet = remote.receive()
                    assert remote.command('Hc-1') == 'OK'
                    assert remote.command('Z1,{:x},2'.format(SUBMIT)) == 'OK'
            finally:
                remote.command('z1,{:x},2'.format(SUBMIT))

    def grab(s, name):
        s.frame(name)
        pix = sample_pixels(s.graphics / 'gui-latest.ppm')
        print(name, pix, flush=True)
        return pix

    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    s = ObservedSession(str(out), device_env=DEVICE_ENV)
    frames = {}
    try:
        start(s)
        refresh(s)
        s.run_for(0.2)
        frames['01-baseline'] = grab(s, '01-baseline')

        state['armed'] = True
        state['mode'] = 'dim'
        s.run_for(a.seconds)
        state['mode'] = None
        refresh(s)
        frames['02-dim'] = grab(s, '02-dim')
        frames['03-dim-hold'] = grab(s, '03-dim-hold')

        state['mode'] = 'erase'
        s.run_for(a.seconds)
        state['mode'] = None
        refresh(s)
        frames['04-erase'] = grab(s, '04-erase')

        state['armed'] = False
        refresh(s)
        frames['05-after-disable'] = grab(s, '05-after-disable')

        counts = {}
        for rec in records:
            key = '{}:{}'.format(rec['mode'], rec['fb'])
            counts[key] = counts.get(key, 0) + 1
        print('fills', len(records), counts)
        (out / 'context.json').write_text(json.dumps(
            dict(probe='E25 image-surface in-place dim with refresh',
                 submit=hex(SUBMIT), frames=frames, records=records,
                 counts=counts), indent=2) + '\n')
    finally:
        s.close()


if __name__ == '__main__':
    main()
