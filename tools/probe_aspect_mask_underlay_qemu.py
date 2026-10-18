"""QEMU-only diagnostic: mask underlay below the stock UI elements.

E5 proved the UI canvas is the frame buffer that carries the status icons and
the focus brackets, and that the renderer clears it before drawing elements.
A production mask therefore has to be painted after that clear but before the
element draws, so icons and brackets keep painting on top of it.

This probe stops the stock renderer at 0x104785D8 (the instruction right after
the full-canvas vgClear at 0x104785D4, before the vgDrawImage/vgDrawPath
element draws) and replaces two 2.35:1 letterbox bands with SRC fills. It then
captures frames over time to check:

* the bands stay a constant alpha (SRC replace is idempotent),
* the photo area is untouched,
* status icons and the focus brackets stay bright (they draw after the fill),
* what remains after the injections stop and a stock refresh runs.

QEMU-only; the colour bars are a fixture. No guest code is patched.
"""
import argparse
import json
import socket
import struct
import sys
import time
import zlib
from pathlib import Path


def read_ppm(path):
    data = Path(path).read_bytes()
    magic, dimensions, maximum, pixels = data.split(b'\n', 3)
    width, height = map(int, dimensions.split())
    if magic != b'P6' or maximum != b'255' or len(pixels) != width * height * 3:
        raise ValueError('Invalid PPM: ' + str(path))
    return width, height, pixels


def png_rgba(path, width, height, rgba):
    raw = b''.join(b'\x00' + bytes(rgba[y * width * 4:(y + 1) * width * 4])
                   for y in range(height))

    def chunk(tag, payload):
        body = tag + payload
        return (struct.pack('>I', len(payload)) + body
                + struct.pack('>I', zlib.crc32(body) & 0xFFFFFFFF))

    header = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)
    data = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header)
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))
    Path(path).write_bytes(data)


# Renderer: stop after the full-canvas clear, before the element draws.
CLEAR_RETURN = 0x104785D8
CANVAS = 0xD045F200
CANVAS_WIDTH = 720
CANVAS_HEIGHT = 480
# 2.35:1 letterbox in true screen space: photo rows [87,393).
# OpenVG/canvas row = 480 - screen_y - height (proved by the 240x60 probe).
TOP_BAND = (0, 393, 720, 87)      # screen y [0,87)
BOTTOM_BAND = (0, 0, 720, 87)     # screen y [393,480)
MASK_ALPHA = 0x40

SCREEN_POINTS = {
    'top_band': (300, 20),
    'bottom_band': (300, 455),
    'photo_area': (300, 240),
    'cam_icon': (40, 150),
    'card_icon': (24, 330),
    'info_icon': (690, 150),
    'bracket_left': (610, 455),
    'bracket_right': (668, 455),
    'white_box': (350, 240),
}


def canvas_offset(x, y):
    # Canvas memory row == OpenVG y (row 480 - screen_y - 1 for a pixel).
    return CANVAS + ((480 - y - 1) * 720 + x) * 4


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--alpha', type=lambda s: int(s, 0), default=MASK_ALPHA)
    p.add_argument('--injections', type=int, default=40)
    p.add_argument('--observe-seconds', type=float, default=1.2)
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from liveview_session import start, refresh, DEVICE_ENV
    from guest_call import call

    records = []
    phase = {'name': 'off'}
    budget = {'used': 0}
    series = []
    tick = {'n': 0}

    def inject(remote, thread, packet):
        sp = int(remote.registers()['r13'], 16)
        scratch = (sp - 128) & ~7
        previous = remote.memory(scratch, 128)
        remote.command(f'z1,{CLEAR_RETURN:x},2')

        def native(address, args):
            nonlocal packet
            result, packet = call(remote, packet, address, args, seconds=10,
                                  stack_pointer=scratch - 32)
            if not result['completed'] or not result['context_restored']:
                raise RuntimeError('Native call failed: ' + hex(address))
            return int(result['registers']['r0'], 16)

        record = dict(phase=phase['name'], thread=thread,
                      caller=hex(CLEAR_RETURN), bands=[])
        try:
            blend = native(0x1050A47C, [0x1104])
            scissoring = native(0x1050A47C, [0x1131])
            native(0x1050A4D0, [0x1106, 4, scratch])
            clip = remote.memory(scratch, 16)
            native(0x10476F92, [0])
            native(0x1050A406, [0x1104, 0x2000])  # VG_BLEND_SRC (replace)
            rgba = 0 if phase['name'] == 'erase' else (a.alpha & 0xFF)
            for index, band in enumerate((TOP_BAND, BOTTOM_BAND)):
                shape = struct.pack('<6I2B2x', *band, rgba, 0, 0, 0)
                assert remote.command(f'M{scratch+32+index*32:x},{len(shape):x}:'
                                      + shape.hex()) == 'OK'
                native(0x1047743C, [scratch + 32 + index * 32])
                record['bands'].append(band)
            native(0x1050A406, [0x1104, blend])
            assert remote.command(f'M{scratch:x},10:' + clip.hex()) == 'OK'
            native(0x1050A442, [0x1106, 4, scratch])
            native(0x1050A406, [0x1131, scissoring])
            record.update(submitted=True, rgba=hex(rgba), ambient_blend=hex(blend),
                          ambient_scissoring=hex(scissoring), ambient_clip=clip.hex())
        finally:
            remote.command(f'M{scratch:x},80:' + previous.hex())
            remote.command(f'Z1,{CLEAR_RETURN:x},2')
        records.append(record)
        budget['used'] += 1

    class ObservedSession(Session):
        def run_for(self, seconds):
            remote = self.remote
            deadline = time.monotonic() + seconds
            assert remote.command(f'Z1,{CLEAR_RETURN:x},2') == 'OK'
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
                    if int(regs['r15'], 16) != CLEAR_RETURN:
                        raise RuntimeError('Unexpected guest stop: ' + packet)
                    if phase['name'] != 'off' and budget['used'] < a.injections:
                        inject(remote, thread, f'T05thread:{thread};')
                    if phase['name'] in ('dim', 'observe'):
                        tick['n'] += 1
                        series.append(dict(stop=tick['n'], thread=thread,
                                           **samples(remote)))
                    seen += 1
                    assert remote.command(f'z1,{CLEAR_RETURN:x},2') == 'OK'
                    assert remote.command('Hc' + thread) == 'OK'
                    remote.send('s')
                    remote.sock.settimeout(5)
                    self.packet = remote.receive()
                    assert remote.command('Hc-1') == 'OK'
                    assert remote.command(f'Z1,{CLEAR_RETURN:x},2') == 'OK'
            finally:
                remote.command(f'z1,{CLEAR_RETURN:x},2')

    def canvas_alpha(remote, x, y):
        return remote.memory(canvas_offset(x, y) + 3, 1)[0]

    def samples(remote):
        return {name: canvas_alpha(remote, *point)
                for name, point in SCREEN_POINTS.items()}

    def grab(name):
        path = s.frame(name)
        width, height, pixels = read_ppm(s.graphics / 'gui-latest.ppm')

        def pixel(point):
            i = (point[1] * width + point[0]) * 3
            return [pixels[i], pixels[i + 1], pixels[i + 2]]

        return dict(name=name, png=str(path),
                    **{key: pixel(point) for key, point in SCREEN_POINTS.items()})

    def dump_canvas(name):
        rgba = bytearray()
        for row in range(CANVAS_HEIGHT):
            for start in range(0, CANVAS_WIDTH, 90):
                rgba += s.remote.memory(CANVAS + (row * 720 + start) * 4,
                                        90 * 4)
        png_rgba(out / name, CANVAS_WIDTH, CANVAS_HEIGHT, bytes(rgba))

    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    s = ObservedSession(str(out), device_env=DEVICE_ENV)
    try:
        start(s)
        frames = [grab('01-stock-liveview')]
        refresh(s)
        s.run_for(.3)
        frames.append(grab('02-baseline'))
        phase['name'] = 'dim'
        s.run_for(a.observe_seconds)
        frames.append(grab('03-dim-under-ui'))
        phase['name'] = 'observe'
        s.run_for(.5)
        frames.append(grab('04-dim-stable'))
        dump_canvas('05-canvas-during-dim.png')
        phase['name'] = 'erase'
        budget['used'] = 0
        s.run_for(.4)
        phase['name'] = 'off'
        frames.append(grab('06-after-erase'))
        refresh(s)
        s.run_for(.4)
        frames.append(grab('07-after-refresh'))
        dump_canvas('08-canvas-after.png')
        result = dict(stock_code_unchanged=True, camera_pixels_verified=False,
                      startup='assisted', alpha=hex(a.alpha), blend_used='0x2000',
                      stop_site=hex(CLEAR_RETURN),
                      bands=dict(top=TOP_BAND, bottom=BOTTOM_BAND),
                      screen_mapping='screen_y = 480 - openvg_y - height',
                      points={k: list(v) for k, v in SCREEN_POINTS.items()},
                      injections=records, frames=frames, series=series,
                      notes='QEMU-only mask-underlay diagnostic; SRC replace after the stock clear')
        (out / 'context.json').write_text(json.dumps(result, indent=2) + '\n')
        print('Injections:', len(records), 'phases:', [r['phase'] for r in records],
              flush=True)
        for frame in frames:
            print(frame['name'], {k: frame[k] for k in ('top_band', 'bottom_band',
                                                        'photo_area', 'bracket_left',
                                                        'cam_icon')}, flush=True)
        print('series samples:', len(series), flush=True)
        for item in series[:8]:
            print(item, flush=True)
    finally:
        s.close()


if __name__ == '__main__':
    main()
