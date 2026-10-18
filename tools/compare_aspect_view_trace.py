"""Compare two v4 view logs; differences are evidence, not a root-cause verdict."""
import argparse
import hashlib
import json
from pathlib import Path
from read_aspect_trace import decode


def compare(before, after, profile=1):
    logs = [decode(data) for data in (before, after)]
    if any(log['version'] != 4 for log in logs):
        raise ValueError('Both logs must be view-diagnostic format 4')
    indexes = [{r['event']: r for r in log['records']
                if r['profile'] == profile and 5 <= r['event'] <= 12} for log in logs]
    rows, missing = [], []
    keys = ('user_area', 'derived_area', 'area', 'size', 'wh', 'caller_input_wh', 'view_cache')
    for event in range(5, 13):
        a, b = (index.get(event) for index in indexes)
        if a is None or b is None:
            missing.append(dict(event=event, missing_before=a is None, missing_after=b is None))
            continue
        fields = {key: dict(before=a.get(key), after=b.get(key))
                  for key in keys if a.get(key) != b.get(key)}
        rows.append(dict(event=event, before_sequence=a['sequence'], after_sequence=b['sequence'],
                         changed=bool(fields), differences=fields))
    return dict(profile=profile, sha256=[hashlib.sha256(data).hexdigest() for data in (before, after)],
                samples=rows, missing=missing,
                any_geometry_difference=any(row['changed'] for row in rows),
                limits=['State labels come from the operator, not the log.',
                        'Menu samples retain cached geometry; no measured LCD pixels or ISP register state.',
                        'A matching cache does not establish equal applied display transforms.',
                        'First-query slots are per RAM session, not per menu selection.'])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('before', type=Path)
    p.add_argument('after', type=Path)
    p.add_argument('--profile', type=int, choices=range(7), default=1)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    result = compare(a.before.read_bytes(), a.after.read_bytes(), a.profile)
    value = json.dumps(result, indent=2) + '\n'
    if a.output:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(value, encoding='utf-8')
    print(value)


if __name__ == '__main__':
    main()
