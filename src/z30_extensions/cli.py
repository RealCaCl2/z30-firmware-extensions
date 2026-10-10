"""Local firmware build CLI; never connects to a camera or writes an SD card."""
import argparse
import hashlib
import json
from pathlib import Path
from .build import build
from .container import extract_eg, detect_model
from .platform import z30_120 as z30_platform
from .platform import z7_380 as z7_platform

PLATFORMS = {'z30': z30_platform, 'z7': z7_platform}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('feature', nargs='?', default='full',
                        choices=['full', 'liveview-dual', 'm900', 'shutter-sound'])
    parser.add_argument('--source', type=Path, required=True,
                        help='Original Z_30_0120.bin / Z_7_0380.bin, SHA-256 checked before decoding')
    parser.add_argument('--extracted-eg', action='store_true',
                        help='Developer mode: input is an extracted stock EG/MAIN module')
    parser.add_argument('--model', choices=['z30', 'z7'],
                        help='Override auto-detected camera model')
    parser.add_argument('--output', type=Path, required=True,
                        help='New output path; existing files are refused')
    args = parser.parse_args(argv)
    metadata = args.output.with_suffix('.json')
    if args.output.resolve() == metadata.resolve():
        parser.error('Output path must not have a .json suffix')
    if args.output.exists() or metadata.exists():
        parser.error('Output or report already exists; choose fresh paths')
    try:
        supplied = args.source.read_bytes()
        model = args.model or detect_model(supplied, extracted=args.extracted_eg)
        source = supplied if args.extracted_eg else extract_eg(supplied)
        output, report = build(source, args.feature, model=model)
        report['input_kind'] = 'extracted_eg' if args.extracted_eg else 'stock_container'
        report['source_model'] = model
        report['source_sha256'] = hashlib.sha256(supplied).hexdigest()
        if not args.extracted_eg:
            report['container_sha256'] = hashlib.sha256(supplied).hexdigest()
        if model == 'z7':
            platform = PLATFORMS[model]
            report['main_payload_size'] = len(output) - 2
            report['main_payload_sha256'] = hashlib.sha256(output[:-2]).hexdigest()
            report['qemu_payload_note'] = ('The primary output is a complete EG module with its '
                                           'two-byte CRC. For Z7 QEMU, supply output[:-2] as the '
                                           'MAIN payload.')
        # Create exclusively; never overwrite an existing image or report.
        with metadata.open('x', encoding='utf-8', newline='\n') as f:
            f.write(json.dumps(report, indent=2) + '\n')
        try:
            with args.output.open('xb') as f:
                f.write(output)
        except OSError:
            metadata.unlink()
            raise
    except (ValueError, OSError) as error:
        parser.exit(1, f'Build failed: {error}\n')
    print(f'Built {args.output}: {report["sha256"]}')
    print(f'Report: {metadata}')
    if model == 'z7':
        print('Z7 QEMU MAIN payload: strip the final two CRC bytes from the output.')
    return 0
