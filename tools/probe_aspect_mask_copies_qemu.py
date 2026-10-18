"""QEMU-only diagnostic: copy cadence and alpha accumulation of the UI canvas.

The SRC probe proved a replace fill of the UI canvas is idempotent on the
canvas itself. The remaining question is how that canvas reaches the overlay
planes: how often, into which rotating bank, and whether repeated copies
accumulate the canvas alpha.

This probe injects one replace fill into an empty region, then samples, at
every stock renderer call, the alpha channel of the three overlay planes and of
the UI canvas at the same screen point. The resulting series shows when a copy
happens, which bank received it, and whether later copies stack.

QEMU-only; the colour bars are a fixture. No guest code is patched.
"""
import argparse
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


POINT = (530, 230)          # screen point inside the fill, empty colour-bar area
CONTROL = (530, 150)        # outside the fill, same column
FILL_OPENVG = (500, 220, 60, 60)   # screen x[500,560) y[200,260)
CANVAS = 0xD045F200
OVERLAYS = [0xECCA7000, 0xECEE7000, 0xED127000]
ALPHA_PLANE = 0xA8C00


def canvas_offset(x, y):
    return CANVAS + ((480 - y) * 720 + x) * 4


def overlay_offset(base, x, y):
    return base + ALPHA_PLANE + y * 720 + x


def samples(remote):
    out = {'canvas': remote.memory(canvas_offset(*POINT) + 3, 1)[0],
           'canvas_control': remote.memory(canvas_offset(*CONTROL) + 3, 1)[0]}
    for index, base in enumerate(OVERLAYS):
        out[f'bank{index}'] = remote.memory(overlay_offset(base, *POINT), 1)[0]
        out[f'bank{index}_ctrl'] = remote.memory(overlay_offset(base, *CONTROL), 1)[0]
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--alpha', type=lambda s: int(s, 0), default=0x40)
    p.add_argument('--observe-seconds', type=float, default=1.2)
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from liveview_session import start, refresh, DEVICE_ENV
    from guest_call import call

    finish = 0x1050A306
    return_site = 0x10477D6D
    records = []
    phase = {'name': 'off'}
    budget = {'injections': 0}
    series = []
    tick = {'n': 0}

    def inject(remote, thread, packet):
        sp = int(remote.registers()['r13'], 16)
        scratch = (sp - 96) & ~7
        previous = remote.memory(scratch, 96)
        remote.command(f'z1,{finish:x},2')

        def native(address, args):
            nonlocal packet
            result, packet = call(remote, packet, address, args, seconds=10,
                                  stack_pointer=scratch - 32)
            if not result['completed'] or not result['context_restored']:
                raise RuntimeError('Native call failed: ' + hex(address))
            return int(result['registers']['r0'], 16)

        record = dict(phase=phase['name'], thread=thread, caller=hex(return_site))
        try:
            blend = native(0x1050A47C, [0x1104])
            scissoring = native(0x1050A47C, [0x1131])
            native(0x1050A4D0, [0x1106, 4, scratch])
            clip = remote.memory(scratch, 16)
            native(0x10476F92, [0])
            native(0x1050A406, [0x1104, 0x2000])  # VG_BLEND_SRC (replace)
            rgba = 0 if phase['name'] == 'erase' else (a.alpha & 0xFF)
            shape = struct.pack('<6I2B2x', *FILL_OPENVG, rgba, 0, 0, 0)
            assert remote.command(f'M{scratch+32:x},{len(shape):x}:' + shape.hex()) == 'OK'
            native(0x1047743C, [scratch + 32])
            native(0x1050A406, [0x1104, blend])
            assert remote.command(f'M{scratch:x},10:' + clip.hex()) == 'OK'
            native(0x1050A442, [0x1106, 4, scratch])
            native(0x1050A406, [0x1131, scissoring])
            record.update(submitted=True, rgba=hex(rgba), ambient_blend=hex(blend),
                          ambient_scissoring=hex(scissoring), ambient_clip=clip.hex())
        finally:
            remote.command(f'M{scratch:x},60:' + previous.hex())
            remote.command(f'Z1,{finish:x},2')
        records.append(record)

    class ObservedSession(Session):
        def run_for(self, seconds):
            remote = self.remote
            deadline = time.monotonic() + seconds
            assert remote.command(f'Z1,{finish:x},2') == 'OK'
            try:
                seen = 0
                while time.monotonic() < deadline and seen < 400:
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
                    if int(regs['r15'], 16) != finish:
                        raise RuntimeError('Unexpected guest stop: ' + packet)
                    if phase['name'] != 'off' and budget['injections'] < 1:
                        budget['injections'] += 1
                        inject(remote, thread, f'T05thread:{thread};')
                    if phase['name'] == 'observe' or phase['name'] == 'dim':
                        tick['n'] += 1
                        series.append(dict(stop=tick['n'], thread=thread, **samples(remote)))
                    seen += 1
                    assert remote.command(f'z1,{finish:x},2') == 'OK'
                    assert remote.command('Hc' + thread) == 'OK'
                    remote.send('s')
                    remote.sock.settimeout(5)
                    self.packet = remote.receive()
                    assert remote.command('Hc-1') == 'OK'
                    assert remote.command(f'Z1,{finish:x},2') == 'OK'
            finally:
                remote.command(f'z1,{finish:x},2')

    def grab(name):
        path = s.frame(name)
        width, height, pixels = read_ppm(s.graphics / 'gui-latest.ppm')
        def pixel(point):
            i = (point[1] * width + point[0]) * 3
            return [pixels[i], pixels[i + 1], pixels[i + 2]]
        return dict(name=name, png=str(path), fill_point=pixel(POINT),
                    control_point=pixel(CONTROL))

    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    s = ObservedSession(str(out), device_env=DEVICE_ENV)
    try:
        start(s)
        frames = [grab('01-stock-liveview')]
        refresh(s)
        s.run_for(.4)
        frames.append(grab('02-baseline'))
        phase['name'] = 'dim'          # first renderer stop injects one fill, rest observed
        s.run_for(a.observe_seconds)
        phase['name'] = 'observe'
        frames.append(grab('03-after-copies'))
        phase['name'] = 'erase'
        budget['injections'] = 0
        s.run_for(.3)
        phase['name'] = 'off'
        refresh(s)
        s.run_for(.4)
        frames.append(grab('04-after-erase-refresh'))
        result = dict(stock_code_unchanged=True, camera_pixels_verified=False,
                      startup='assisted', alpha=hex(a.alpha),
                      blend_used='0x2000 (VG_BLEND_SRC replace)',
                      screen_point=list(POINT), control=list(CONTROL),
                      fill_openvg=list(FILL_OPENVG),
                      fill_screen=[FILL_OPENVG[0], 480 - FILL_OPENVG[1] - FILL_OPENVG[3],
                                   FILL_OPENVG[0] + FILL_OPENVG[2], 480 - FILL_OPENVG[1]],
                      injections=records, frames=frames, series=series,
                      notes='QEMU-only copy-cadence diagnostic; alpha sampled per renderer stop')
        (out / 'context.json').write_text(json.dumps(result, indent=2) + '\n')
        print('Injections:', len(records), flush=True)
        for frame in frames:
            print(frame['name'], frame['fill_point'], frame['control_point'], flush=True)
        for item in series[:12]:
            print(item, flush=True)
        print('...', len(series), 'samples', flush=True)
    finally:
        s.close()


if __name__ == '__main__':
    main()
