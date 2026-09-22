"""Opt-in, one persistent paid submission (n=1). Re-running resumes the SAME request."""
import argparse
import json
import os
from pathlib import Path
import time
import uuid
from .audio import AudioService


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument('--run',action='store_true')
    p.add_argument('--audio-dir',default='output/mureka-smoke')
    p.add_argument('--text',default='伤感、emo、克制，给低声旋律Rap留空间。')
    p.add_argument('--wait-seconds',type=int,default=300)
    args=p.parse_args(argv)
    if not args.run or not os.environ.get('MUREKA_API_KEY'):
        print(json.dumps({'status':'SKIPPED','reason':'Requires --run and backend MUREKA_API_KEY; no paid call made'})); return 0
    env=dict(os.environ,BEATMATE_AUDIO_PROVIDER='mureka',BEATMATE_AUDIO_MAX_CANDIDATES='1',BEATMATE_AUDIO_MAX_SUBMISSIONS='1')
    s=AudioService(args.audio_dir,env=env)
    if s.adapter.errors():
        print(json.dumps({'status':'SKIPPED','reason':'; '.join(s.adapter.errors())}));return 0
    manifest=Path(args.audio_dir)/'smoke-request.json'
    # Exclusive creation before submission. The retained request is always reused.
    try:
        with manifest.open('x') as f:json.dump({'request_id':uuid.uuid4().hex,'raw_text':args.text},f,ensure_ascii=False)
    except FileExistsError:pass
    saved=json.loads(manifest.read_text())
    t=s.create(saved['raw_text'],saved['request_id'],n=1)
    until=time.monotonic()+max(0,min(args.wait_seconds,1800))
    while time.monotonic()<until:
        s.tick();t=s.get(t['id'])
        if t['status'] in ('ready','failed','uncertain'):break
        time.sleep(2)
    t=s.get(t['id'])
    status='PASS' if t['status']=='ready' else ('SKIPPED' if t.get('provider_http_status') in (401,402,403,429) else 'FAIL') if t['status']=='failed' else 'PENDING'
    report={'status':status,'task':t,'assets':s.history()['assets'],'human_listening':'PENDING_HUMAN_REVIEW',
            'reason':'Account/model/credits rejection; inspect provider account' if t['status']=='failed' else
                     'Re-run with the same audio-dir to query/download only; uncertain will never resend',
            'submission_limit':1,'candidate_count':1}
    (Path(args.audio_dir)/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2));return 0 if status in ('PASS','SKIPPED','PENDING') else 1


if __name__=='__main__':raise SystemExit(main())
