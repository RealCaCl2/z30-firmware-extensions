"""QEMU-only diagnostic: dump each display layer as a PNG.

The renderer and copy pipeline reference several 720x480 surfaces that are not
interchangeable: the UI canvas is 32-bit RGBA, the image surface is also 32-bit
RGBA, and the scanout overlay bank is YYUV422A with a separate alpha plane.
This probe reads each surface once at baseline and writes RGB PNGs so the
layering question ("which elements live above the mask, which below") can be
answered by looking at the layers, not by reasoning about the copy log.

Guest code is not patched and no fill is injected.  The assisted QEMU colour
bars remain a fixture, not camera footage.
"""
import argparse
import json
import struct
import sys
import zlib
from pathlib import Path

CANVASES = [('ui-canvas-d045f200', 0xD045F200), ('image-surface-d0959600', 0xD0959600),
            ('canvas3-d2bfd600', 0xD2BFD600)]
BANK = 0xECCE7000
ALPHA_PLANE = 0xA8C00
WIDTH, HEIGHT = 720, 480


def png_from_rgb(width, height, rgb, path):
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload))
    rows = b''.join(b'\0' + rgb[y * width * 3:(y + 1) * width * 3] for y in range(height))
    path.write_bytes(b'\x89PNG\r\n\x1a\n'
                     + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
                     + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


def read_ppm(path):
    data = Path(path).read_bytes()
    magic, dimensions, maximum, pixels = data.split(b'\n', 3)
    width, height = map(int, dimensions.split())
    if magic != b'P6' or maximum != b'255' or len(pixels) != width * height * 3:
        raise ValueError('Invalid PPM: ' + str(path))
    return width, height, pixels


def read_guest(remote, address, length):
    data = bytearray()
    while len(data) < length:
        step = min(0x8000, length - len(data))
        data += remote.memory(address + len(data), step)
    return bytes(data)


def rgba_to_rgb(raw):
    return bytes(value for i in range(0, len(raw), 4) for value in raw[i:i + 3])


def rgba_alpha(raw):
    return bytes(value for i in range(3, len(raw), 4) for value in (raw[i], raw[i], raw[i]))


def yuyv_to_rgb(raw):
    out = bytearray()
    for i in range(0, len(raw), 4):
        y0, u, y1, v = raw[i:i + 4]
        for y in (y0, y1):
            c = y - 16
            r = (298 * c + 409 * (v - 128) + 128) >> 8
            g = (298 * c - 100 * (u - 128) - 208 * (v - 128) + 128) >> 8
            b = (298 * c + 516 * (u - 128) + 128) >> 8
            out += bytes((max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))))
    return bytes(out)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from liveview_session import start, refresh, DEVICE_ENV

    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    s = Session(str(out), device_env=DEVICE_ENV)
    try:
        start(s)
        refresh(s)
        s.run_for(.6)
        path = s.frame('screen-baseline')
        width, height, pixels = read_ppm(s.graphics / 'gui-latest.ppm')
        report = dict(screen=str(path), camera_pixels_verified=False, startup='assisted',
                      layers={}, screen_samples={})
        for name, base in CANVASES:
            raw = read_guest(s.remote, base, WIDTH * HEIGHT * 4)
            png_from_rgb(WIDTH, HEIGHT, rgba_to_rgb(raw), out / (name + '-rgb.png'))
            png_from_rgb(WIDTH, HEIGHT, rgba_alpha(raw), out / (name + '-alpha.png'))
            nonzero = sum(1 for value in raw[3::4] if value)
            report['layers'][name] = dict(rgb_png=str(out / (name + '-rgb.png')),
                                          alpha_png=str(out / (name + '-alpha.png')),
                                          alpha_nonzero=nonzero)
        for bank_name, bank_base in [('bank0-ecca7000', 0xECCA7000), ('bank1-ecce7000', 0xECCE7000),
                                     ('bank2-ed127000', 0xED127000)]:
            color = read_guest(s.remote, bank_base, WIDTH * HEIGHT * 2)
            alpha = read_guest(s.remote, bank_base + ALPHA_PLANE, WIDTH * HEIGHT)
            png_from_rgb(WIDTH, HEIGHT, yuyv_to_rgb(color), out / (bank_name + '-rgb.png'))
            png_from_rgb(WIDTH, HEIGHT, bytes(value for v in alpha for value in (v, v, v)),
                         out / (bank_name + '-alpha.png'))
            report['layers'][bank_name] = dict(
                rgb_png=str(out / (bank_name + '-rgb.png')),
                alpha_png=str(out / (bank_name + '-alpha.png')),
                alpha_nonzero=sum(1 for value in alpha if value))
        for label, (x, y) in {'bars_in': (300, 80), 'left_band': (40, 240), 'cam_icon': (40, 150),
                              'info_icon': (690, 150), 'center': (350, 240)}.items():
            index = (y * width + x) * 3
            report['screen_samples'][label] = list(pixels[index:index + 3])
        (out / 'context.json').write_text(json.dumps(report, indent=2) + '\n')
        print('baseline screen', report['screen_samples'], flush=True)
        for name, layer in report['layers'].items():
            print(name, 'alpha_nonzero=', layer['alpha_nonzero'], flush=True)
    finally:
        s.close()


if __name__ == '__main__':
    main()
