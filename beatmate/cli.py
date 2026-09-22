import argparse
import json
import os
from pathlib import Path
from .api import server
from .service import Service
from .render import midi_bytes, preview_bytes
from .config import create_planner


def main():
    parser = argparse.ArgumentParser(description='BeatMate local native-MIDI agent (permanent Mock mode)')
    parser.add_argument('--db', default='.beatmate/state.sqlite3')
    sub = parser.add_subparsers(dest='command', required=True)
    serve = sub.add_parser('serve')
    serve.add_argument('--planner', choices=['mock', 'deepseek', 'openai'], default=None,
                       help='Overrides BEATMATE_PLANNER (default: mock)')
    serve.add_argument('--port', type=int, default=8765)
    demo = sub.add_parser('demo')
    demo.add_argument('--output', default='output')
    creative = sub.add_parser('creative', help='Create a schema-v2 creative project')
    creative.add_argument('--text', required=True)
    creative.add_argument('--planner', choices=['mock','deepseek','openai'], default=None)
    creative.add_argument('--reference-project')
    creative.add_argument('--reference-version')
    creative.add_argument('--output', required=True, help='New artifact directory; never overwritten')
    planning = sub.add_parser('plan-edit', help='Plan against trusted saved state; never commits')
    planning.add_argument('--project', required=True)
    planning.add_argument('--text', required=True)
    planning.add_argument('--base-version')
    planning.add_argument('--track')
    planning.add_argument('--start-bar', type=int)
    planning.add_argument('--end-bar', type=int)
    planning.add_argument('--planner', choices=['mock','deepseek','openai'], default=None)
    planning.add_argument('--output', required=True)
    apply = sub.add_parser('apply-edit', help='Explicitly commit a saved ready proposal')
    apply.add_argument('--project', required=True)
    apply.add_argument('--proposal', required=True)
    apply.add_argument('--output', required=True)
    creative_demo = sub.add_parser('creative-demo', help='Offline A/B/C; isolated temporary database')
    creative_demo.add_argument('--output', required=True)
    from .audio_cli import register, run
    register(sub)
    args = parser.parse_args()
    if args.command.startswith('audio-'):
        try:
            run(args)
        except (ValueError, KeyError) as error:
            parser.error(str(error))
        return
    if args.command == 'creative-demo':
        from .creative_demo import generate_demos
        print(generate_demos(args.output).resolve())
        return
    runtime = args.command in ('serve','creative','plan-edit')
    selected = (args.planner if args.planner is not None else os.environ.get('BEATMATE_PLANNER', 'mock')) if runtime else 'mock'
    try:
        planner = create_planner(selected)
    except ValueError as error:
        parser.error(str(error))
    service = Service(args.db, planner)
    if args.command in ('creative','plan-edit','apply-edit'):
        from .creative_demo import export_version
        out = Path(args.output)
        if out.exists():
            parser.error('Output already exists; choose a new path')
        if args.command == 'creative':
            v = service.create_creative_project(args.text, reference_project_id=args.reference_project,
                                                reference_version=args.reference_version)
        elif args.command == 'plan-edit':
            proposal = service.plan_project_edit(args.project,args.text,args.base_version,args.track,args.start_bar,args.end_bar)
            out.parent.mkdir(parents=True,exist_ok=True)
            with out.open('x') as f:
                json.dump(proposal,f,ensure_ascii=False,indent=2)
            print(json.dumps(proposal,ensure_ascii=False,indent=2))
            return
        else:
            proposal = json.loads(Path(args.proposal).read_text())
            if proposal.get('status') != 'ready':
                parser.error('Suggestions cannot be applied; resolve the scope first')
            v = service.edit_project(args.project,proposal['base_version'],proposal['plan'],proposal['protected_tracks'])
        export_version(v,out)
        print(json.dumps(dict(project_id=v['project_id'],version_id=v['version_id'],output=str(out.resolve())),indent=2))
        return
    if args.command == 'serve':
        print(f'BeatMate {selected} API: http://127.0.0.1:{args.port}', flush=True)
        from .audio import AudioService
        audio = AudioService()
        httpd = server(service, args.port, audio=audio)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()
            audio.close()
    else:
        out = Path(args.output)
        out.mkdir(parents=True, exist_ok=True)
        v1 = service.create_project(text='90 BPM boom bap, 8小节 C minor seed 7', protected_tracks=['kick', 'snare', 'bass', 'chords'])
        plan = service.planner.plan_edit('加密32', 'hihat', 3, 4)
        v2 = service.edit_project(v1['project_id'], v1['version_id'], plan)
        for name, v in [('v1', v1), ('v2', v2)]:
            (out / f'{name}.json').write_text(json.dumps(v, ensure_ascii=False, indent=2))
            (out / f'{name}.mid').write_bytes(midi_bytes(v))
            (out / f'{name}.wav').write_bytes(preview_bytes(v))
        print(json.dumps(dict(project_id=v1['project_id'], before=v1['version_id'], after=v2['version_id'], output=str(out.resolve())), indent=2))


if __name__ == '__main__':
    main()
