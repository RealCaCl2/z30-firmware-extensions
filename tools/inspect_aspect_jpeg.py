"""Inspect primary and embedded JPEG dimensions without changing a photo.

Candidate streams must contain a valid marker sequence through SOF. This is
an inventory, not proof that a particular stream is used by camera playback.
"""
import argparse
import json
from pathlib import Path


def dimensions(data, start):
    pos = start + 2
    while pos + 4 <= len(data):
        if data[pos] != 0xff:
            return None
        while pos < len(data) and data[pos] == 0xff:
            pos += 1
        if pos >= len(data):
            return None
        marker = data[pos]
        pos += 1
        if marker in (0xd9, 0xda, 0x00):
            return None
        if marker == 1 or 0xd0 <= marker <= 0xd7:
            continue
        length = int.from_bytes(data[pos:pos + 2], 'big')
        if length < 2 or pos + length > len(data):
            return None
        if marker in (0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7,
                      0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf):
            if length < 8:
                return None
            height = int.from_bytes(data[pos + 3:pos + 5], 'big')
            width = int.from_bytes(data[pos + 5:pos + 7], 'big')
            components = data[pos + 7]
            if not width or not height or not components or length != 8 + components * 3:
                return None
            return {'offset': start, 'width': width, 'height': height,
                    'ratio': round(width / height, 6), 'sof_marker': hex(marker)}
        pos += length
    return None


def inspect(data):
    streams = []
    offset = data.find(b'\xff\xd8')
    while offset >= 0:
        result = dimensions(data, offset)
        if result:
            streams.append(result)
        offset = data.find(b'\xff\xd8', offset + 2)
    return streams


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('photo', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = {'file': args.photo.name, 'jpeg_stream_candidates': inspect(args.photo.read_bytes()),
              'limit': 'Dimensions only; playback stream identity requires further evidence.'}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + '\n', encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
