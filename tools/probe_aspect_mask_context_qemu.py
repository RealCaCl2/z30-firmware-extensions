"""Observe stock live-view graphics calls, with optional QEMU drawing probes.

Uses the sibling assisted QEMU harness. Startup reports and the processed
colour-bar stimulus are explicit fixtures, not a sensor or camera-screen proof.
Optional probes call native painters; they never install guest code patches.
"""
import argparse
import json
import socket
import struct
import sys
import time
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--draw-probe', action='store_true',
                   help='Use native queued painter for one clearable translucent rectangle; QEMU only')
    p.add_argument('--at-copy', action='store_true',
                   help='Diagnostic native drawing at the observed pre-copy vgFinish, not an installable hook')
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from liveview_session import start, refresh, DEVICE_ENV
    from guest_call import call

    points = {0x1050AAD6: 'vgClear', 0x1050ACF6: 'vgDrawPath',
              0x10559C34: 'eglSwapBuffers', 0x1050A306: 'vgFinish'}
    observations = []
    injections = []
    inject_enabled = False

    def capture(remote, regs, stop_thread=None):
        pc = int(regs['r15'], 16)
        item = dict(api=points.get(pc, hex(pc)), registers=regs,
                    stack=remote.memory(int(regs['r13'], 16), 64).hex())
        observations.append(item)
        if (a.at_copy and inject_enabled and pc == 0x1050A306
                and int(regs['r14'],16) == 0x10477D6D):
            # A QEMU-only paused-worker diagnostic, not an installed callback.
            # Retain scissor state and both CPU/scratch contexts. No host-side
            # pixels are drawn or written into the guest's framebuffers.
            # Use the actual stop packet's CPU, not qC (which need not match
            # the Hg-selected CPU in this SMP stub).
            if stop_thread is None:
                raise RuntimeError('Worker injection requires the stop packet CPU')
            thread = stop_thread
            packet = f'T05thread:{thread};'
            sp = int(regs['r13'],16)
            scratch = (sp-96)&~7
            previous = remote.memory(scratch,96)
            for point in points:
                remote.command(f'z1,{point:x},2')
            def native(address,args):
                nonlocal packet
                result, packet = call(remote,packet,address,args,seconds=10,
                                      stack_pointer=scratch-32)
                if not result['completed'] or not result['context_restored']:
                    s.audit.append(dict(pre_copy_native_failure=hex(address),result=result))
                    s.write_audit()
                    raise RuntimeError('Pre-copy native call failed: '+hex(address))
                return int(result['registers']['r0'],16)
            try:
                enabled = native(0x1050A47C,[0x1131])
                native(0x1050A4D0,[0x1106,4,scratch])
                clip = remote.memory(scratch,16)
                native(0x10476F92,[0])
                shape = struct.pack('<6I2B2x',240,360,240,60,0x00000040,0,0,0)
                assert remote.command(f'M{scratch+32:x},{len(shape):x}:'+shape.hex())=='OK'
                native(0x1047743C,[scratch+32])
                assert remote.command(f'M{scratch:x},10:'+clip.hex())=='OK'
                native(0x1050A442,[0x1106,4,scratch])
                native(0x1050A406,[0x1131,enabled])
                injections.append(dict(caller='0x10477d68',original_clip=clip.hex(),
                                       scissoring_restored=enabled))
            finally:
                remote.command(f'M{scratch:x},60:'+previous.hex())
                for point in points:
                    remote.command(f'Z1,{point:x},2')
        return dict(mask_context_observation=item)

    class ObservedSession(Session):
        def input(self, function, args, **kwargs):
            kwargs.update(observe=tuple(points), capture=capture)
            return super().input(function, args, **kwargs)

        def run_for(self, seconds):
            # Observe the scheduled workers too, not just the explicit API.
            remote = self.remote
            deadline = time.monotonic() + seconds
            armed = set(points)
            seen = 0
            for point in armed:
                assert remote.command(f'Z1,{point:x},2') == 'OK'
            try:
                while time.monotonic() < deadline:
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
                    if pc not in armed:
                        raise RuntimeError('Unexpected guest stop: ' + packet)
                    capture(remote, regs, thread)
                    seen += 1
                    assert remote.command(f'z1,{pc:x},2') == 'OK'
                    assert remote.command('Hc' + thread) == 'OK'
                    remote.send('s')
                    remote.sock.settimeout(5)
                    self.packet = remote.receive()
                    assert remote.command('Hc-1') == 'OK'
                    if seen >= 256:
                        break
                    assert remote.command(f'Z1,{pc:x},2') == 'OK'
            finally:
                for point in armed:
                    remote.command(f'z1,{point:x},2')

    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    s = ObservedSession(str(out), device_env=DEVICE_ENV)
    try:
        start(s)
        frames = [str(s.frame('01-stock-liveview'))]
        for i in range(2):
            refresh(s)
            s.run_for(1)
            frames.append(str(s.frame(f'02-refresh-{i}')))
        if a.at_copy:
            refresh(s)
            # Only inject while observing naturally scheduled workers. Nested
            # diagnostic calls during input() would collide with its separate
            # InitTask checkpoint/sentinel on the other CPU.
            inject_enabled = True
            s.run_for(1)
            frames.append(str(s.frame('03-pre-copy-native-alpha')))
            inject_enabled = False
            refresh(s)
            s.run_for(.5)
            frames.append(str(s.frame('04-with-injection-disabled')))
        probe = None
        if a.draw_probe:
            # Original queued RGBA rectangle wrapper and its queued clear
            # counterpart. No ROM hook or direct framebuffer write is used.
            # Native OpenVG coordinates are investigated, not assumed LCD ones.
            # Diagnostic only: queued native full-canvas scissor. The native
            # refresh below rebuilds widget clipping; production must save and
            # restore the caller's clip rather than use this broad reset.
            s.input(0x10476FF4,[0])
            rectangle = struct.pack('<6I2B2x', 240,360,240,60,0x00000040,0,0,0)
            draw = s.input(0x10477528,[('scratch',0)],payload=rectangle)
            assert s.remote.memory(0x10476ED2,2) == bytes.fromhex('10b5')
            s.input(0x10476ED2,[])  # Queued native vgFinish, not a host flush.
            s.run_for(.25)
            frames.append(str(s.frame('03-native-alpha-rectangle')))
            clear = s.input(0x104779F4,[('scratch',0)],payload=rectangle)
            s.input(0x10476ED2,[])
            s.run_for(.25)
            frames.append(str(s.frame('04-native-clear')))
            refresh(s)
            s.run_for(.25)
            frames.append(str(s.frame('05-after-native-refresh')))
            probe = dict(rectangle=[240,360,240,60],rgba='0x00000040',
                         draw_api='0x10477528',clear_api='0x104779f4',
                         draw_returned=draw['completed'],clear_returned=clear['completed'],
                         scissor='diagnostic native full-canvas reset; not a production lifecycle',
                         physical_context_verified=False)
        result = dict(stock_code_unchanged=True, mask_drawn=False,
                      single_rectangle_probe=probe,
                      pre_copy_diagnostic_injections=injections,
                      camera_pixels_verified=False, startup='assisted',
                      sensor_source='model colour bars when descriptors permit',
                      observed_calls=observations, frames=frames,
                      liveview_descriptor_first_word=hex(int.from_bytes(s.remote.memory(0xc1821d44,4),'little')))
        (out/'context.json').write_text(json.dumps(result, indent=2)+'\n')
        print('Observed graphics calls:', len(observations), flush=True)
        print('No production mask installed; frames are native guest output.', flush=True)
    finally:
        s.close()


if __name__ == '__main__':
    main()
