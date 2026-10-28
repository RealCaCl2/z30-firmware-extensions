"""Replay an exact region trial into QEMU and open native firmware pages.

Assisted task calls, not a natural boot or SD update acceptance test. No camera.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu-root', type=Path, required=True)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--stock-control', action='store_true')
    args = parser.parse_args()
    sys.path.insert(0, str(args.qemu_root.resolve() / 'tools'))
    from display_session import Session, png_from_ppm
    report = json.loads(args.report.read_text())
    candidate = args.image.read_bytes()
    stock_path = args.qemu_root / '.local/firmware/eg2090_0120a0.bi'
    stock = stock_path.read_bytes()
    sha = lambda data: hashlib.sha256(data).hexdigest()
    assert sha(candidate) == report['candidate_sha256']
    assert sha(stock) == report['source_sha256']
    assert len(candidate) == len(stock)
    image = stock if args.stock_control else candidate
    differences = [i for i, (a, b) in enumerate(zip(stock, image)) if a != b]
    allowed = {len(stock)-2, len(stock)-1}
    for edit in report['edits']:
        offset = int(edit['offset'], 16)
        old, new = bytes.fromhex(edit['expected_hex']), bytes.fromhex(edit['replacement_hex'])
        assert stock[offset:offset+len(old)] == old
        assert candidate[offset:offset+len(new)] == new
        allowed.update(range(offset, offset+len(new)))
    assert set(differences) <= allowed
    output = args.output_dir.resolve()
    s = Session(str(output), firmware_image=stock_path.resolve(), fresh=True)
    result = dict(candidate_sha256=sha(candidate), runtime_image_sha256=sha(image),
                  stock_control=args.stock_control, applied_differing_bytes=len(differences),
                  checks=[], limitations=[
                      'Assisted QEMU task-checkpoint calls; natural cold boot is not demonstrated.',
                      'Version and confirmation pages are explicitly requested through native navigation API.',
                      'Confirmation-page rendering does not prove SD discovery or updater eligibility.',
                      'No update confirmation, flash writer, real NOR, camera or RF operation is invoked.',
                  ])
    try:
        for offset in differences:
            reply = s.remote.command(f'M{0x10020000+offset:x},1:{image[offset:offset+1].hex()}')
            assert reply == 'OK'
        # Re-read the entire image to prove that no unlisted runtime byte edit
        # was required to render the firmware pages.
        readback = bytearray()
        for offset in range(0, len(image), 4096):
            readback.extend(s.remote.memory(0x10020000+offset, min(4096, len(image)-offset)))
        assert sha(readback) == sha(image)
        result['full_rom_readback_sha256'] = sha(readback)
        (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        s.input(0x102355EE, [0xC182E688, 1])
        s.run_for(2)
        s.input(0x10465990, [0x1400, 3])
        s.input(0x10452CC0, [1])

        def page(index, label):
            s.input(0x1045F732, [2, index], seconds=20)
            s.run_for(2)
            active = s.input(0x1046109A, [])
            pointer = int(active['registers']['r0'], 16)
            assert pointer
            actual = int.from_bytes(s.remote.memory(pointer+2, 2), 'little')
            assert actual == index, (index, actual)
            samples = []
            for _ in range(6):
                s.run_for(.25)
                raw = (s.graphics / 'gui-latest.ppm').read_bytes()
                pixels = raw.split(b'\n', 3)[3]
                score = sum(pixels[i:i+3] != b'\0\0\0' for i in range(0, len(pixels), 3))
                samples.append((score, len(samples), raw))
            score, _, raw = max(samples, key=lambda x: x[:2])
            assert score > 1000
            (output / (label+'.png')).write_bytes(png_from_ppm(raw))
            result['checks'].append(dict(page=hex(index), active_page=hex(actual),
                                         nonblack_pixels=score, frame=label+'.png'))
            (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')

        page(397, '01-setup')
        page(0x265, '02-firmware-version')
        page(0x266, '03-update-confirmation-direct')
        page(0x265, '04-firmware-version-return')
        result['page_checks_passed'] = True
        print(json.dumps(result, indent=2), flush=True)
    except Exception as exc:
        result['page_checks_passed'] = False
        result['error'] = str(exc)
        result['failed_step'] = 'first native page request not present in checks'
        raise
    finally:
        (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        s.close()


if __name__ == '__main__':
    main()
