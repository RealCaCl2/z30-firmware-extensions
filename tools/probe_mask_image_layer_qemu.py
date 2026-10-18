"""QEMU-only diagnostic: insert the dim mask into the image layer.

Established facts:
  * the UI canvas (0xD045F200) is the top RGBA layer; status icons and focus
    brackets live there, so the mask must be composited below it;
  * the RGBA layer below the canvas is the image surface (0xD0959600), which is
    copied into the rotating overlay bank before the canvas copy (GEOMETRY log
    order: source=d0959600 then source=d045f200);
  * drawing the mask into the image surface therefore composites it above the
    video and below the UI elements.

This probe patches pixel data (not code) at the layer copy setup call site
(0x104E8FAE, i.e. the BL that leads into 0x104E8D34): the mask bands are written
into the image surface right before the image layer copy is prepared. A small
CPU stub in the worker stack performs the fill (0.5 MB, two full-width bands).
The erase phase fills the same bands with transparent black to prove the
baseline returns without a UI refresh.

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


POINTS = {
    'band_top': (350, 40),
    'band_bottom': (350, 450),
    'photo_a': (200, 240),
    'photo_b': (500, 300),
    'cam_icon': (40, 150),
    'info_icon': (690, 150),
    'card_icon': (24, 330),
    'bracket': (610, 455),
}

IMAGE_SURFACE = 0xD0959600
UI_CANVAS = 0xD045F200
IMAGE_LAYER_SITE = 0x104E8FAE     # BL into layer copy setup 0x104E8D34
OTHER_LAYER_SITE = 0x104E9CF8     # second call site of the same setup
COPY_SUBMIT = 0x1055D112

# Two full-width bands of a 720x480 RGBA surface: screen y[0,87) and y[393,480).
# Stored rows are mirrored (row = 479 - screen_y), so the same row ranges apply.
BAND_WORDS = 87 * 720
BAND_GAP_BYTES = (393 - 87) * 720 * 4

# Thumb stubs assembled offline with keystone (see
# build/research/mask-stub/make_stub.py). They write two 87-row bands of the
# image surface at 0xD0959600: push {r0-r3}; r3=base; r1=RGBA word; r2=count;
# two word loops; pop {r0-r3}; bx lr.
STUB_FILL = bytes.fromhex(
    '0fb449f20063cdf2950340f20001c4f200014ff2b04219600433013afbd1'
    '47f28022c0f20d0213444ff2b04219600433013afbd10fbc7047')
STUB_ERASE = bytes.fromhex(
    '0fb449f20063cdf2950340f20001c0f200014ff2b04219600433013afbd1'
    '47f28022c0f20d0213444ff2b04219600433013afbd10fbc7047')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--alpha', type=lambda s: int(s, 0), default=0x40)
    p.add_argument('--observe-seconds', type=float, default=0.6)
    p.add_argument('--dim-seconds', type=float, default=0.8)
    p.add_argument('--tail-seconds', type=float, default=0.5)
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from liveview_session import start, refresh, DEVICE_ENV
    from guest_call import call

    records = []
    phase = {'name': 'observe'}
    budget = {'fills': 0, 'erases': 0}
    hits = {'image_site': 0, 'other_site': 0, 'copy_submit': 0}
    site_log = []

    # Stub bytes are embedded above (assembled offline by
    # build/research/mask-stub/make_stub.py).
    stub_dim = STUB_FILL
    stub_erase = STUB_ERASE

    def set_register(remote, number, value):
        raw = struct.pack('<I', value).hex()
        assert remote.command(f'P{number:x}={raw}') == 'OK', 'register write failed'

    def sample(remote):
        out = {}
        for name, (x, y) in POINTS.items():
            address = IMAGE_SURFACE + ((479 - y) * 720 + x) * 4
            out[name] = remote.memory(address, 4).hex()
        return out

    def layer_args(remote):
        regs = remote.registers()
        state = int(regs['r4'], 16)
        try:
            fields = remote.memory(state + 0x94, 0x10)
            plane = struct.unpack('<I', fields[0:4])[0]
            fb = struct.unpack('<I', fields[4:8])[0]
        except Exception:
            plane = fb = None
        return dict(r4=hex(state), plane=hex(plane) if plane else None,
                    fb=hex(fb) if fb else None, lr=regs['r14'], sp=int(regs['r13'], 16))

    def install_stub(remote, sp, stub):
        address = (sp - 0x800) & ~0xF
        assert remote.command(f'M{address:x},{len(stub):x}:' + stub.hex()) == 'OK'
        return address

    class ObservedSession(Session):
        armed = False

        def run_for(self, seconds):
            if not self.armed:
                # Live-view startup: breakpoints must stay out of the startup
                # protocol, otherwise the native start API may never return.
                return super().run_for(seconds)
            remote = self.remote
            deadline = time.monotonic() + seconds
            breakpoints = [IMAGE_LAYER_SITE, OTHER_LAYER_SITE, COPY_SUBMIT]
            for address in breakpoints:
                assert remote.command(f'Z1,{address:x},2') == 'OK'
            try:
                seen = 0
                while time.monotonic() < deadline and seen < 600:
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
                    if pc == IMAGE_LAYER_SITE:
                        hits['image_site'] += 1
                        info = layer_args(remote)
                        site_log.append(dict(phase=phase['name'], site='image', thread=thread, **info))
                        fill = None
                        if phase['name'] == 'dim' and budget['fills'] < 3:
                            fill, budget['fills'] = stub_dim, budget['fills'] + 1
                        elif phase['name'] == 'erase' and budget['erases'] < 2:
                            fill, budget['erases'] = stub_erase, budget['erases'] + 1
                        if fill is not None:
                            assert remote.command(f'z1,{IMAGE_LAYER_SITE:x},2') == 'OK'
                            sp = info['sp']
                            stub_address = install_stub(remote, sp, fill)
                            set_register(remote, 0xe, IMAGE_LAYER_SITE)   # LR
                            set_register(remote, 0xf, stub_address)       # PC
                            records.append(dict(phase=phase['name'], thread=thread,
                                                stub=hex(stub_address), **info))
                            seen += 1
                            remote.send('c')
                            continue
                    elif pc == OTHER_LAYER_SITE:
                        hits['other_site'] += 1
                        site_log.append(dict(phase=phase['name'], site='other', thread=thread, **layer_args(remote)))
                    elif pc == COPY_SUBMIT:
                        hits['copy_submit'] += 1
                    seen += 1
                    assert remote.command(f'z1,{pc:x},2') == 'OK'
                    remote.command(f'Hg{thread}')
                    remote.send('s')
                    remote.sock.settimeout(5)
                    self.packet = remote.receive()
                    assert remote.command('Hc-1') == 'OK'
                    for address in breakpoints:
                        assert remote.command(f'Z1,{address:x},2') == 'OK'
            finally:
                for address in breakpoints:
                    remote.command(f'z1,{address:x},2')

    def grab(name):
        path = s.frame(name)
        width, height, pixels = read_ppm(s.graphics / 'gui-latest.ppm')
        def pixel(point):
            i = (point[1] * width + point[0]) * 3
            return [pixels[i], pixels[i + 1], pixels[i + 2]]
        return dict(name=name, png=str(path), points={k: pixel(v) for k, v in POINTS.items()})

    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    s = ObservedSession(str(out), device_env=DEVICE_ENV)
    try:
        start(s)
        s.armed = True
        frames = [grab('01-stock-liveview')]
        refresh(s)
        phase['name'] = 'observe'
        s.run_for(a.observe_seconds)
        frames.append(grab('02-baseline'))
        phase['name'] = 'dim'
        s.run_for(a.dim_seconds)
        frames.append(grab('03-image-layer-dim'))
        s.run_for(a.tail_seconds)
        frames.append(grab('04-image-layer-dim-later'))
        phase['name'] = 'erase'
        s.run_for(a.tail_seconds)
        phase['name'] = 'off'
        s.run_for(a.tail_seconds)
        frames.append(grab('05-after-erase'))
        result = dict(stock_code_unchanged=True, camera_pixels_verified=False,
                      target_surface=hex(IMAGE_SURFACE), patch_site=hex(IMAGE_LAYER_SITE),
                      screen_points={k: list(v) for k, v in POINTS.items()},
                      hits=hits, site_log=site_log, records=records, frames=frames,
                      notes='QEMU-only; CPU stub fills two 87-row bands of the image '
                            'surface right before the image layer copy; no guest code patched')
        (out / 'context.json').write_text(json.dumps(result, indent=2) + '\n')
        print('hits:', hits, 'records:', len(records), flush=True)
        for frame in frames:
            print(frame['name'], frame['points'], flush=True)
        for item in site_log[:8]:
            print(item, flush=True)
    finally:
        s.close()


if __name__ == '__main__':
    main()
