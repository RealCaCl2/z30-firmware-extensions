"""Check saved guest LCD frames for unchanged native UI above the soft mask.

Consumes verify_mask_menu_qemu evidence; does not render or alter images.
The QEMU PNG writer uses RGB8 scanlines with filter zero.
"""
import argparse
import hashlib
import json
import struct
import zlib
from pathlib import Path


def pixels(path):
    blob = path.read_bytes()
    assert blob[:8] == b'\x89PNG\r\n\x1a\n'
    offset = 8
    compressed = bytearray()
    while offset < len(blob):
        size = int.from_bytes(blob[offset:offset + 4], 'big')
        tag = blob[offset + 4:offset + 8]
        data = blob[offset + 8:offset + 8 + size]
        if tag == b'IHDR':
            assert struct.unpack('>IIBBBBB', data) == (720, 480, 8, 2, 0, 0, 0)
        elif tag == b'IDAT':
            compressed.extend(data)
        offset += size + 12
    raw = zlib.decompress(compressed)
    stride = 720 * 3 + 1
    assert len(raw) == stride * 480
    assert all(raw[y * stride] == 0 for y in range(480))
    return lambda x, y: list(raw[y * stride + 1 + x * 3:y * stride + 4 + x * 3])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--profiles', type=int, nargs='+', choices=range(7), default=[1, 2, 3, 4, 5, 6, 0])
    args = parser.parse_args()
    observations = json.loads((args.evidence / 'mask-observations.json').read_text())
    assert [item['live_profile'] for item in observations] == args.profiles
    assert all(item['checks_passed'] for item in observations)
    digest = hashlib.sha256(args.image.read_bytes()).hexdigest()
    session = json.loads((args.evidence / 'session.json').read_text())
    assert any(item.get('candidate_sha256') == digest for item in session['audit'])
    assert not any(item.get('stock_submit_control') for item in session['audit'])
    # Opaque camera/info glyphs and a white focus bracket on the blue bar.
    # A translucent tile background legitimately follows the dimmed image;
    # comparing that background to its unmasked value would be a false failure.
    points = [(30, 145), (680, 145), (598, 452)]
    baseline = pixels(args.evidence / '00-live-baseline.png')
    checks = []
    failures = []
    for profile in args.profiles:
        frame = args.evidence / f'04-live-{profile}.png'
        current = pixels(frame)
        samples = []
        for x, y in points:
            expected, actual = baseline(x, y), current(x, y)
            assert max(expected) >= 40
            if actual != expected:
                failures.append(dict(profile=profile, point=[x, y], actual=actual, expected=expected))
            samples.append(dict(point=[x, y], baseline=expected, actual=actual))
        checks.append(dict(profile=profile, frame_sha256=hashlib.sha256(frame.read_bytes()).hexdigest(),
                           unchanged_ui=samples))
    result = dict(image_sha256=digest, checks=checks, native_ui_samples_preserved=not failures,
                  failures=failures,
                  scope='Saved guest LCD pixels; explicit sensorless viewport/source fixture; '
                        'native grid hardware pixels and physical camera not verified')
    (args.evidence / 'ui-frame-checks.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    assert not failures, failures
    print(f'PASS {len(points) * len(checks)} bright native UI samples in {len(checks)} guest LCD frames')


if __name__ == '__main__':
    main()
