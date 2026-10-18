"""Check continuous exterior mask edges in saved, unmodified guest LCD frames.

Uses the assisted colour-bar source. Columns avoid native UI foreground and the
animated marker; this is display-model evidence, not a camera screen test.
"""
import argparse
import hashlib
import json
from pathlib import Path
from verify_aspect_mask_frames import pixels


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('image','report','evidence'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    report=json.loads(args.report.read_text());digest=hashlib.sha256(args.image.read_bytes()).hexdigest()
    assert digest==report['sha256']
    observations=json.loads((args.evidence/'mask-observations.json').read_text())
    assert [item['live_profile'] for item in observations]==[1,2,3,4,5,6,0]
    assert all(item['checks_passed'] for item in observations)
    session=json.loads((args.evidence/'session.json').read_text())
    assert any(item.get('candidate_sha256')==digest for item in session['audit'])
    base=pixels(args.evidence/'00-live-baseline.png')
    factor=1-report['features']['aspect_profiles']['soft_mask']['alpha']/255
    checks=[]
    for item in observations:
        profile=item['live_profile'];frame=pixels(args.evidence/f'04-live-{profile}.png')
        points=[]
        if profile in (1,2):
            # Native camera/card widgets start at x=2; x=0 is exposed image.
            # At x=0 the animated white marker cannot change the white bar.
            points=[(0,y) for y in range(480)]
            assert all(rect[1]==0 and rect[3]==480 for rect in item['expected_regions'])
        elif profile:
            top,bottom=item['expected_regions']
            assert top[:3]==[0,0,720] and bottom[0]==0 and bottom[2:]==[720,480]
            points=[(x,y) for x in (20,100,360,500)
                    for y in (*range(top[3]),*range(bottom[1],480))]
        else:
            points=[(x,y) for x in (20,100,360,500) for y in (0,479)]
        bright=0
        for x,y in points:
            baseline,actual=base(x,y),frame(x,y)
            expected=[round(value*(factor if profile else 1)) for value in baseline]
            assert all(abs(a-b)<=3 for a,b in zip(actual,expected)),(profile,x,y,actual,expected)
            bright+=int(max(baseline)>=40)
        assert bright
        checks.append(dict(profile=profile,checked_pixels=len(points),bright_pixels=bright,
                           contiguous_edges_verified=True))
    result=dict(image_sha256=digest,checks=checks,scope=__doc__,camera_verified=False)
    (args.evidence/'edge-continuity.json').write_text(json.dumps(result,indent=2)+'\n')
    print('PASS',sum(item['checked_pixels'] for item in checks),'LCD edge pixel checks across seven profiles')


if __name__=='__main__':main()
