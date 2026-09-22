"""Audio commands never create a Planner or read its keys."""
import json
import os
from pathlib import Path
import shutil
import time
import uuid
from .audio import AudioService
from .api import server
from .audio_env import load_audio_env
from .audio_health import DEFAULT_PORT, startup_summary


def register(sub):
    for name in ('audio-serve','audio-preview','audio-create','audio-history','audio-task','audio-work','audio-select','audio-download','audio-demo','audio-budget','audio-doctor'):
        p=sub.add_parser(name)
        p.add_argument('--audio-dir',default='.beatmate/audio')
        if name in ('audio-preview','audio-create'):
            p.add_argument('--text',required=True); p.add_argument('--mode',choices=['direct','template'])
            p.add_argument('--constraints',default='{}',help='JSON explicit choices only')
        if name=='audio-create':
            p.add_argument('--request-id',required=True,help='32 lowercase hex; reuse on network retries')
            p.add_argument('--project'); p.add_argument('--source-asset'); p.add_argument('--n',type=int,default=1)
            p.add_argument('--reviewed',action='store_true')
        if name in ('audio-task',): p.add_argument('--task',required=True)
        if name=='audio-budget': p.add_argument('--limit',type=int,help='Save cumulative real-request limit (positive integer); omit to view')
        if name=='audio-serve': p.add_argument('--port',type=int,default=DEFAULT_PORT)
        if name=='audio-doctor': p.add_argument('--network',action='store_true',help='Check DNS/TCP/TLS only; no HTTP or paid request')
        if name=='audio-select': p.add_argument('--project',required=True); p.add_argument('--asset',required=True)
        if name=='audio-download': p.add_argument('--asset',required=True); p.add_argument('--output',required=True)
        if name=='audio-work': p.add_argument('--once',action='store_true')


def run(args):
    if args.command!='audio-demo':
        loaded=load_audio_env()
        # TLS uses the process environment; explicit exports retain precedence.
        for key in ('SSL_CERT_FILE','SSL_CERT_DIR'):
            if key in loaded: os.environ.setdefault(key,loaded[key])
    service=AudioService(args.audio_dir,env={} if args.command=='audio-demo' else loaded)
    command=args.command
    if command=='audio-serve':
        try: httpd=server(None,args.port,audio=service)
        except OSError:
            service.close()
            raise ValueError(f'无法使用端口 {args.port}。如果 BeatMate 已运行，请打开 http://127.0.0.1:{args.port}；更新代码后请在原终端 Ctrl+C 再启动。其他程序占用时可用 --port 指定新端口。') from None
        print(startup_summary(service),flush=True)
        print(f'BeatMate Audio: http://127.0.0.1:{httpd.server_port}',flush=True)
        try: httpd.serve_forever()
        except KeyboardInterrupt: pass
        finally: httpd.server_close(); service.close()
        return
    if command=='audio-doctor':
        result=service.health.report(service)
        if args.network: result['network']=service.health.network(service)
    elif command=='audio-preview': result=service.preview(args.text,args.mode,json.loads(args.constraints))
    elif command=='audio-create': result=service.create(args.text,args.request_id,args.mode,json.loads(args.constraints),args.n,args.project,args.reviewed,args.source_asset)
    elif command=='audio-budget': result=service.budget() if args.limit is None else service.set_budget(args.limit)
    elif command=='audio-history': result=service.history()
    elif command=='audio-task': result=service.get(args.task)
    elif command=='audio-select': result=service.select(args.project,args.asset)
    elif command=='audio-download':
        path,a=service.file(args.asset); output=Path(args.output)
        with output.open('xb') as f: f.write(path.read_bytes())
        result={'file':str(output.resolve()),'format':a['format'],'sha256':a['sha256']}
    elif command=='audio-work':
        if args.once: service.tick(); result=service.history()
        else:
            service.start()
            try:
                while True: time.sleep(1)
            except KeyboardInterrupt: service.close()
            return
    else:
        task=service.create('伤感、emo、克制，给低声旋律Rap留空间。',uuid.uuid4().hex)
        for _ in range(4): service.tick()
        result=service.history()
        asset=next(a for a in result['assets'] if a['task_id']==task['id'])
        service.select(task['project_id'],asset['id'])
        result={'status':service.get(task['id'])['status'],'provider':'mock','quality':'NOT_EVALUATED',
                'task':service.get(task['id']),'asset':asset,'file':str(service.file(asset['id'])[0])}
    print(json.dumps(result,ensure_ascii=False,indent=2))
