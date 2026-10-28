"""Temporarily use a saved camera Wi-Fi profile, preflight, then reconnect.

Windows only. Never reads passwords or adds/deletes profiles. Default is a
plan. The original connected profile is recorded locally before switching.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

NETSH = str(Path(os.environ.get('WINDIR', r'C:\Windows')) / 'System32/netsh.exe')


def parse_link(text):
    result = {}
    keep = {'Name', 'State', 'Profile', 'Band', 'Channel', 'Radio type',
            'Receive rate (Mbps)', 'Transmit rate (Mbps)', 'Signal', 'Rssi'}
    for line in text.splitlines():
        key, separator, value = line.strip().partition(':')
        if separator and key.strip() in keep:
            key = key.strip()
            if key in result:
                raise ValueError('Expected exactly one wireless interface')
            result[key] = value.strip()
    if result.get('Name') != 'WLAN':
        raise ValueError('Expected the verified WLAN interface and English netsh field names')
    return result


class WindowsNetwork:
    def __init__(self):
        self.recovery_file = None

    def call(self, *arguments):
        result = subprocess.run([NETSH, 'wlan', *arguments], capture_output=True,
                                text=True, timeout=5, errors='replace')
        if result.returncode:
            raise RuntimeError('Windows wireless command failed')
        return result.stdout

    def link(self):
        return parse_link(self.call('show', 'interfaces'))

    def profiles(self):
        names = []
        for line in self.call('show', 'profiles').splitlines():
            key, separator, value = line.partition(':')
            if separator and key.strip() == 'All User Profile':
                names.append(value.strip())
        return names

    def connect(self, profile):
        self.call('connect', f'name={profile}', 'interface=WLAN')

    def wait(self, profile, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            link = self.link()
            if link.get('State') == 'connected' and link.get('Profile') == profile:
                return link
            time.sleep(0.5)
        raise TimeoutError('Timed out waiting for the selected saved Wi-Fi profile')

    def gateway(self):
        script = "(Get-NetIPConfiguration -InterfaceAlias 'WLAN').IPv4DefaultGateway.NextHop | ConvertTo-Json -Compress"
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                                capture_output=True, text=True, timeout=12)
        if result.returncode:
            raise RuntimeError('Unable to read the WLAN gateway')
        value = json.loads(result.stdout)
        if not isinstance(value, str):
            raise ValueError('Expected one camera hotspot IPv4 gateway')
        from camera_wireless_ptpip import Client
        Client(value)  # Validate local IPv4 only; this does not connect.
        return value

    def inspect(self, address, output):
        script = Path(__file__).with_name('camera_wireless_ptpip.py')
        result = subprocess.run([sys.executable, str(script), '--execute', '--address', address,
                                 '--check-service-gate', '--output', str(output)],
                                capture_output=True, text=True, timeout=60)
        return result.returncode


def run(network, camera_profile, output):
    # No mutation occurs until all local prerequisites and recovery metadata exist.
    before = network.link()
    original = before.get('Profile')
    if before.get('State') != 'connected' or not original:
        raise ValueError('An existing connected Wi-Fi profile is required for restoration')
    if camera_profile == original:
        raise ValueError('First reconnect the original internet Wi-Fi before using the switch workflow')
    if camera_profile not in network.profiles():
        raise ValueError('Camera Wi-Fi profile has not been saved in Windows')
    output.mkdir(parents=True, exist_ok=False)
    # Contains local profile names only; keep this ignored directory private.
    (output / 'network-recovery.json').write_text(json.dumps(
        dict(interface='WLAN', original_profile=original, camera_profile=camera_profile), indent=2) + '\n')
    report = dict(state='prepared', radio_change_sent=False)
    try:
        report['state'] = 'camera_connection_requested'
        network.connect(camera_profile)
        connected = network.wait(camera_profile, 20)
        report['camera_link'] = {k: v for k, v in connected.items() if k not in ('Name', 'Profile')}
        address = network.gateway()
        report['preflight_exit_code'] = network.inspect(address, output / 'ptpip.json')
        report['state'] = 'preflight_returned'
    except Exception as exc:
        report.update(state='preflight_failed', error=str(exc))
    finally:
        try:
            network.connect(original)
            network.wait(original, 25)
            report['original_wifi_profile_reconnected'] = True
        except Exception as exc:
            report['original_wifi_profile_reconnected'] = False
            report['restoration_error'] = str(exc)
        (output / 'workflow.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--camera-profile')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not args.execute:
        print('Plan: require a saved camera profile and current connected WLAN profile; '
              'record recovery; switch, run bounded read-only preflight; reconnect original profile in finally.')
        return
    if not args.camera_profile or args.output is None:
        parser.error('--execute requires --camera-profile and a new --output directory')
    result = run(WindowsNetwork(), args.camera_profile, args.output)
    print(json.dumps(result, indent=2))
    if result.get('error') or result.get('preflight_exit_code') or result.get('restoration_error'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
