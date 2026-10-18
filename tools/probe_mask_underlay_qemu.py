"""QEMU-only proof: a one-shot fill of the image surface becomes a persistent
soft-mask layer under the UI canvas.

Layer order established by probe_layer_proof_qemu.py: the display module builds
the overlay bank per cycle as image surface copy (SRC_OVER) then UI canvas copy
(SRC_OVER), so the image surface sits *below* the icons/brackets and *above* the
video.

This probe arms patching only for its own run: the first GPU copy submit stop
gets
  * image surface rows [0,20) x[300,420)  black alpha 0x40   (bottom band)
  * image surface rows [420,440) x[300,420) black alpha 0x40  (top band)
  * canvas rows [0,20) x[480,500) opaque white (control: must stay bright)
and then never patches again. Later frames tell us:
  * whether the one-shot fill survives (image surface not cleared per cycle),
  * whether the dim band stays under the bright canvas control.

QEMU-only; no guest code is modified.
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
    dict(name='image-band-bottom', base=IMAGE, rows=range(0, 20), x=(300, 420),
         rgba=(0, 0, 0, 0x40)),
    dict(name='image-band-top', base=IMAGE, rows=range(420, 440), x=(300, 420),
         rgba=(0, 0, 0, 0x40)),
    dict(name='canvas-white', base=CANVAS, rows=range(0, 20), x=(480, 500),
         rgba=(255, 255, 255, 255)),
]
SAMPLE_WORD = IMAGE + (10 * 720 + 350) * 4   # inside image-band-bottom


def write_rows(remote, base, rows, x0, x1, rgba):
    payload = bytes(rgba) * (x1 - x0)
    for row in rows:
        address = base + (row * 720 + x0) * 4
        assert remote.command(f'M{address:x},{len(payload):x}:' + payload.hex()) == 'OK'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from liveview_session import start, refresh, DEVICE_ENV

    stats = {'armed': False, 'patched': 0, 'stops': 0, 'word_trace': []}

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
                    assert remote.command(f'z1,{COPY_SUBMIT:x},2') == 'OK'
                    if pc == COPY_SUBMIT:
                        stats['stops'] += 1
                        st = remote.command(f'm{SAMPLE_WORD:x},4')
                        stats['word_trace'].append(st.replace('\n', ' ').strip())
                        if stats['armed'] and not stats['patched']:
                            for patch in PATCHES:
                                write_rows(remote, patch['base'], patch['rows'],
                                           patch['x'][0], patch['x'][1], patch['rgba'])
                            stats['patched'] = 1
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
                    band_bottom=pixel(350, 470),   # image rows 0..19 -> screen 460..479
                    band_top=pixel(350, 50),       # image rows 420..439 -> screen 40..59
                    canvas_white=pixel(490, 470),  # canvas rows 0..19 at x480..500
                    bracket=pixel(610, 455),
                    info_icon=pixel(690, 150),
                    cam_icon=pixel(40, 150),
                    control=pixel(350, 240))

    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    s = ObservedSession(str(out), device_env=DEVICE_ENV)
    try:
        start(s)
        refresh(s)
        frames = [grab('01-baseline')]
        stats['armed'] = True
        s.run_for(.6)
        frames.append(grab('02-after-one-shot'))
        s.run_for(1.0)
        frames.append(grab('03-one-second-later'))
        s.run_for(2.0)
        frames.append(grab('04-two-seconds-later'))
        result = dict(probe='one-shot image-surface mask persistence',
                      copy_submit=hex(COPY_SUBMIT),
                      patches=[dict(name=x['name'], base=hex(x['base']),
                                    rows=[x['rows'][0], x['rows'][-1]],
                                    x=list(x['x']), rgba=list(x['rgba'])) for x in PATCHES],
                      stats=dict(armed=stats['armed'], patched=stats['patched'],
                                 stops=stats['stops']),
                      word_trace=stats['word_trace'][:80],
                      frames=frames,
                      mapping='surface row == 479 - screen_y',
                      notes='QEMU-only; a single image-surface fill should stay under the '
                            'bright canvas control across frames if the surface is not cleared')
        (out / 'context.json').write_text(json.dumps(result, indent=2) + '\n')
        for frame in frames:
            print(frame['name'], {k: frame[k] for k in
                                  ('band_bottom', 'band_top', 'canvas_white', 'bracket',
                                   'info_icon', 'cam_icon', 'control')}, flush=True)
    finally:
        s.close()


if __name__ == '__main__':
    main()
