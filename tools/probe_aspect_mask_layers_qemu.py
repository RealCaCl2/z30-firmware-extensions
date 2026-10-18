"""QEMU-only diagnostic: where do mask fills land and what do they cover?

The SRC probe showed the injection paints into the UI canvas (d045f200) with
replace blending, that repeated fills keep a constant alpha, and that its fill
is composited under nothing that redraws it. This probe answers the layering
question the soft mask depends on:

* which screen elements live in the UI canvas and which in another layer;
* whether a replace fill over such an element erases it (canvas content is
  below the fill) or leaves it bright (separate higher layer);
* whether "erase" fills plus a factory refresh restore the canvas to baseline.

It dumps the UI canvas alpha channel before and after the fills so we can see
the static elements the fill would clobber. Guest code is not patched; the
assisted QEMU colour bars remain a fixture, not camera evidence.
"""
import argparse
import json
import socket
import struct
import sys
import time
import zlib
from pathlib import Path


def png_from_rgb(width, height, rgb):
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload))
    rows = b''.join(b'\0' + rgb[y * width * 3:(y + 1) * width * 3] for y in range(height))
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


def read_ppm(path):
    data = Path(path).read_bytes()
    magic, dimensions, maximum, pixels = data.split(b'\n', 3)
    width, height = map(int, dimensions.split())
    if magic != b'P6' or maximum != b'255' or len(pixels) != width * height * 3:
        raise ValueError('Invalid PPM: ' + str(path))
    return width, height, pixels


SAMPLE_POINTS = {'cam_icon': (40, 150), 'card_icon': (24, 330), 'info_icon': (690, 150),
                 'bracket_l': (610, 455), 'bracket_r': (668, 455),
                 'center_box': (350, 240), 'center_box2': (175, 240), 'left_band': (40, 240),
                 'bars_in': (300, 80), 'bars_out': (300, 130),
                 'bar_yellow': (110, 240), 'bar_cyan': (200, 240)}

# Screen rects are half-open [x0,x1) / [y0,y1); OpenVG y = 480 - screen y0 - h.
ICON_RECTS = [('cam', 8, 292, 64, 76), ('info', 648, 292, 64, 76),
              ('center', 280, 140, 140, 200)]
ERASE_RECTS = ICON_RECTS
CANVAS = 0xD045F200
CANVAS_WIDTH, CANVAS_HEIGHT = 720, 480


def screen_to_canvas_row(y):
    return CANVAS_HEIGHT - y


def canvas_point_offset(x, y):
    return (screen_to_canvas_row(y) * CANVAS_WIDTH + x) * 4


def read_canvas(remote):
    data = bytearray()
    while len(data) < CANVAS_WIDTH * CANVAS_HEIGHT * 4:
        remaining = CANVAS_WIDTH * CANVAS_HEIGHT * 4 - len(data)
        step = min(0x4000, remaining)
        data += remote.memory(CANVAS + len(data), step)
    return bytes(data)


def canvas_report(raw, tag):
    alpha = raw[3::4]
    rgb = b''.join(bytes((a, a, a)) for a in alpha)
    points = {}
    for name, (x, y) in SAMPLE_POINTS.items():
        i = canvas_point_offset(x, y)
        points[name] = dict(rgba=list(raw[i:i + 4]), alpha=raw[i + 3])
    nonzero = sum(1 for value in alpha if value)
    bbox = None
    rows = []
    for y in range(CANVAS_HEIGHT):
        row = alpha[y * CANVAS_WIDTH:(y + 1) * CANVAS_WIDTH]
        xs = [x for x, value in enumerate(row) if value]
        rows.append(len(xs))
        if xs:
            box = [xs[0], y, xs[-1] + 1, y + 1]
            bbox = box if bbox is None else [min(bbox[0], box[0]), min(bbox[1], box[1]),
                                             max(bbox[2], box[2]), max(bbox[3], box[3])]
    blocks = []
    for by in range(0, CANVAS_HEIGHT, 32):
        for bx in range(0, CANVAS_WIDTH, 32):
            count = 0
            for y in range(by, min(by + 32, CANVAS_HEIGHT)):
                row = alpha[y * CANVAS_WIDTH:(y + 1) * CANVAS_WIDTH]
                count += sum(1 for x in range(bx, min(bx + 32, CANVAS_WIDTH)) if row[x])
            if count > 256:
                blocks.append(dict(x=bx, y=by, nonzero=count))
    blocks.sort(key=lambda item: -item['nonzero'])
    return dict(tag=tag, nonzero=nonzero, bbox=bbox, dense_32px_blocks=blocks[:8],
                sample_points=points, alpha_png=str(Path(tag + '.png')), alpha_rows_nonzero=rows)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--alpha', type=lambda s: int(s, 0), default=0x40)
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

        record = dict(phase=phase['name'], thread=thread, caller=hex(return_site), fills=[])
        try:
            blend = native(0x1050A47C, [0x1104])
            scissoring = native(0x1050A47C, [0x1131])
            native(0x1050A4D0, [0x1106, 4, scratch])
            clip = remote.memory(scratch, 16)
            record.update(ambient_blend=hex(blend), ambient_scissoring=hex(scissoring),
                          ambient_clip=clip.hex())
            native(0x10476F92, [0])
            native(0x1050A406, [0x1104, 0x2000])  # VG_BLEND_SRC (replace)
            rects = ERASE_RECTS if phase['name'] == 'erase' else ICON_RECTS
            for name, x, y, w, h in rects:
                rgba = 0 if phase['name'] == 'erase' else (a.alpha & 0xFF)
                shape = struct.pack('<6I2B2x', x, y, w, h, rgba, 0, 0, 0)
                assert remote.command(f'M{scratch+32:x},{len(shape):x}:' + shape.hex()) == 'OK'
                native(0x1047743C, [scratch + 32])
                record['fills'].append(dict(name=name, screen=[x, 480 - y - h, x + w, 480 - y],
                                            rgba=hex(rgba)))
            native(0x1050A406, [0x1104, blend])
            assert remote.command(f'M{scratch:x},10:' + clip.hex()) == 'OK'
            native(0x1050A442, [0x1106, 4, scratch])
            native(0x1050A406, [0x1131, scissoring])
            record['submitted'] = True
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
                while time.monotonic() < deadline and seen < 256:
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
                    if phase['name'] != 'off' and budget['injections'] < 24:
                        budget['injections'] += 1
                        inject(remote, thread, f'T05thread:{thread};')
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
        samples = {}
        for label, (x, y) in SAMPLE_POINTS.items():
            i = (y * width + x) * 3
            samples[label] = [pixels[i], pixels[i + 1], pixels[i + 2]]
        return dict(name=name, png=str(path), samples=samples)

    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    s = ObservedSession(str(out), device_env=DEVICE_ENV)
    try:
        start(s)
        frames = [grab('01-stock-liveview')]
        refresh(s)
        s.run_for(.4)
        frames.append(grab('02-baseline'))
        canvas = [canvas_report(read_canvas(s.remote), 'canvas-baseline')]
        (out / 'canvas-baseline.png').write_bytes(
            png_from_rgb(CANVAS_WIDTH, CANVAS_HEIGHT,
                         b''.join(bytes((v, v, v)) for v in read_canvas(s.remote)[3::4])))
        phase['name'] = 'dim'
        s.run_for(.5)
        frames.append(grab('03-mask-over-canvas-elements'))
        phase['name'] = 'off'
        refresh(s)
        s.run_for(.3)
        frames.append(grab('04-disable-refresh'))
        phase['name'] = 'erase'
        s.run_for(.3)
        phase['name'] = 'off'
        refresh(s)
        s.run_for(.3)
        frames.append(grab('05-erase-refresh'))
        raw = read_canvas(s.remote)
        (out / 'canvas-after.png').write_bytes(
            png_from_rgb(CANVAS_WIDTH, CANVAS_HEIGHT, b''.join(bytes((v, v, v)) for v in raw[3::4])))
        canvas.append(canvas_report(raw, 'canvas-after'))
        result = dict(stock_code_unchanged=True, camera_pixels_verified=False,
                      startup='assisted', alpha=hex(a.alpha),
                      blend_used='0x2000 (VG_BLEND_SRC replace)',
                      icon_rects=[dict(name=name, openvg=[x, y, w, h],
                                       screen=[x, 480 - y - h, x + w, 480 - y])
                                  for name, x, y, w, h in ICON_RECTS],
                      injections=records, frames=frames, canvas=canvas,
                      notes='QEMU-only layering diagnostic; canvas d045f200 alpha channel dumped')
        (out / 'context.json').write_text(json.dumps(result, indent=2) + '\n')
        print('Injections:', len(records), flush=True)
        for frame in frames:
            print(frame['name'], frame['samples'], flush=True)
        for item in canvas:
            print(item['tag'], 'nonzero=', item['nonzero'], 'bbox=', item['bbox'], flush=True)
    finally:
        s.close()


if __name__ == '__main__':
    main()
