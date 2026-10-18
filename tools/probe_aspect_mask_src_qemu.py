"""QEMU-only diagnostic: pretend mask fill with replace (SRC) blending.

The observed pre-copy vgFinish draws a translucent rectangle through the native
queued painter. The previous probe left the ambient blend mode (SRC_OVER), so
repeated frames accumulated. This probe:

1. reads the ambient OpenVG blend mode and scissor state at the stop,
2. switches to VG_BLEND_SRC (replace) while submitting the same rectangle,
3. restores both states before resuming the worker,

so repeated submissions must produce a constant pixel instead of a darker one.
It then submits an alpha=0 replace fill to test erasure of the region.

Guest code is never patched and no host pixels are written; the sibling
assisted QEMU harness and its colour-bar stimulus remain fixtures, not camera
or panel proof.
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


def sample(ppm, points):
    width, height, pixels = ppm
    out = {}
    for name, (x, y) in points.items():
        if not (0 <= x < width and 0 <= y < height):
            raise ValueError(f'Pixel out of frame: {name} ({x},{y})')
        i = (y * width + x) * 3
        out[name] = [pixels[i], pixels[i + 1], pixels[i + 2]]
    return out


# Stable assisted-session surfaces (from Z30_GPU_* / Z30_DMA_ZERO logs):
#   d045f200, d0959600, d2bfd600: 720x480 RGBA canvases (4 bytes per pixel)
#   ecca7000/ecee7000/ed127000: rotating overlay YYUV422A planes (2 bytes per
#   pixel), with 1-byte alpha plane at +0xA8C00.
CANVASES = [('canvas-d045f200', 0xD045F200), ('canvas-d0959600', 0xD0959600),
            ('canvas-d2bfd600', 0xD2BFD600)]
OVERLAYS = [('overlay-ecca7000', 0xECCA7000), ('overlay-ecee7000', 0xECEE7000),
            ('overlay-ed127000', 0xED127000)]
SAMPLE_POINTS = {'inside': (300, 80), 'outside': (300, 130),
                 'flip_inside': (300, 400), 'left_edge': (20, 240),
                 'right_edge': (700, 240), 'top_band': (300, 20),
                 'bottom_band': (300, 460)}


def memory_probes(points):
    probes = []
    for name, base in CANVASES:
        for label, (x, y) in points.items():
            probes.append(dict(surface=name, point=label, address=base + (y * 720 + x) * 4,
                               width=16))
    for name, base in OVERLAYS:
        for label, (x, y) in points.items():
            probes.append(dict(surface=name, point=label, address=base + (y * 720 + x) * 2,
                               width=8, plane='color'))
            probes.append(dict(surface=name, point=label,
                               address=base + 0xA8C00 + y * 720 + x, width=4, plane='alpha'))
    return probes


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--alpha', type=lambda s: int(s, 0), default=0x40)
    p.add_argument('--rect', type=lambda s: int(s, 0), default=0x122C3C)
    p.add_argument('--max-injections', type=int, default=24)
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from liveview_session import start, refresh, DEVICE_ENV
    from guest_call import call

    finish = 0x1050A306
    return_site = 0x10477D6D
    records = []
    phase = 'off'
    budget = {'injections': 0}

    def inject(remote, thread, packet):
        sp = int(remote.registers()['r13'], 16)
        scratch = (sp - 96) & ~7
        previous = remote.memory(scratch, 96)
        for point in (finish,):
            remote.command(f'z1,{point:x},2')

        def native(address, args):
            nonlocal packet
            result, packet = call(remote, packet, address, args, seconds=10,
                                  stack_pointer=scratch - 32)
            if not result['completed'] or not result['context_restored']:
                raise RuntimeError('Native call failed: ' + hex(address))
            return int(result['registers']['r0'], 16)

        record = dict(phase=phase, thread=thread, caller=hex(return_site))
        try:
            blend = native(0x1050A47C, [0x1104])
            scissoring = native(0x1050A47C, [0x1131])
            native(0x1050A4D0, [0x1106, 4, scratch])
            clip = remote.memory(scratch, 16)
            record.update(ambient_blend=hex(blend), ambient_scissoring=hex(scissoring),
                          ambient_clip=clip.hex())
            native(0x10476F92, [0])
            native(0x1050A406, [0x1104, 0x2000])  # VG_BLEND_SRC (replace)
            rgba = 0x00000000 if phase == 'erase' else (a.alpha & 0xFF)
            shape = struct.pack('<6I2B2x', 240, 360, 240, 60, rgba, 0, 0, 0)
            assert remote.command(f'M{scratch+32:x},{len(shape):x}:' + shape.hex()) == 'OK'
            native(0x1047743C, [scratch + 32])
            native(0x1050A406, [0x1104, blend])  # restore ambient blend
            assert remote.command(f'M{scratch:x},10:' + clip.hex()) == 'OK'
            native(0x1050A442, [0x1106, 4, scratch])
            native(0x1050A406, [0x1131, scissoring])
            record.update(submitted=True, rgba=hex(rgba), blend_used='0x2000')
        finally:
            remote.command(f'M{scratch:x},60:' + previous.hex())
            remote.command(f'Z1,{finish:x},2')
        records.append(record)

    def capture(remote, regs, stop_thread=None):
        pc = int(regs['r15'], 16)
        if pc == finish and int(regs['r14'], 16) == return_site and phase != 'off':
            if budget['injections'] < a.max_injections:
                if stop_thread is None:
                    raise RuntimeError('Injection needs the stop packet CPU')
                budget['injections'] += 1
                inject(remote, stop_thread, f'T05thread:{stop_thread};')
                return

    class ObservedSession(Session):
        def run_for(self, seconds):
            remote = self.remote
            deadline = time.monotonic() + seconds
            armed = {finish}
            seen = 0
            for point in armed:
                assert remote.command(f'Z1,{point:x},2') == 'OK'
            try:
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
                    capture(remote, regs, thread)
                    seen += 1
                    assert remote.command(f'z1,{finish:x},2') == 'OK'
                    assert remote.command('Hc' + thread) == 'OK'
                    remote.send('s')
                    remote.sock.settimeout(5)
                    self.packet = remote.receive()
                    assert remote.command('Hc-1') == 'OK'
                    if seen >= 256:
                        break
                    assert remote.command(f'Z1,{finish:x},2') == 'OK'
            finally:
                for point in armed:
                    remote.command(f'z1,{point:x},2')

    def grab(name):
        path = s.frame(name)
        return dict(name=name, png=str(path),
                    samples=sample(read_ppm(s.graphics / 'gui-latest.ppm'), SAMPLE_POINTS))

    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    s = ObservedSession(str(out), device_env=DEVICE_ENV)
    try:
        start(s)
        frames = [grab('01-stock-liveview')]
        refresh(s)
        s.run_for(.5)
        frames.append(grab('02-baseline'))
        probes = memory_probes(SAMPLE_POINTS)
        phase = 'dim'
        s.run_for(.4)
        frames.append(grab('03-dim-a'))
        s.run_for(.4)
        frames.append(grab('03-dim-b'))
        memory_before_erase = [dict(probe, hex=s.remote.memory(probe['address'], probe['width']).hex())
                               for probe in probes]
        phase = 'erase'
        s.run_for(.3)
        frames.append(grab('04-erase'))
        phase = 'off'
        refresh(s)
        s.run_for(.4)
        frames.append(grab('05-after-disable-refresh'))
        memory_after = [dict(probe, hex=s.remote.memory(probe['address'], probe['width']).hex())
                        for probe in probes]
        result = dict(stock_code_unchanged=True, mask_drawn=False,
                      camera_pixels_verified=False, startup='assisted',
                      rect=[240, 360, 240, 60], screen_hint='x[240,480) y[60,120) y-flip',
                      alpha=hex(a.alpha), blend_used='0x2000 (VG_BLEND_SRC replace)',
                      injections=records, frames=frames,
                      memory_around_erase=memory_before_erase, memory_after=memory_after,
                      notes='QEMU-only diagnostic; ambient state saved and restored')
        (out / 'context.json').write_text(json.dumps(result, indent=2) + '\n')
        print('Injections:', len(records), flush=True)
        for frame in frames:
            print(frame['name'], frame['samples'], flush=True)
    finally:
        s.close()


if __name__ == '__main__':
    main()
