"""Thin routes shared by the old API server and standalone audio server."""
from pathlib import Path
import re
import shutil
from urllib.parse import quote
from .model import fields
from .audio_provider import AudioError


def route(h, audio, body):
    path=h.path
    if h.command=='GET' and path in ('/','/audio'):
        return h.send(200,Path(__file__).with_name('audio.html').read_bytes(),'text/html; charset=utf-8')
    if h.command=='POST' and path=='/audio/library':
        fields(body,['ids','action','title','favorite'],['ids','action'])
        return h.send(200,audio.library_update(**body))
    if h.command=='GET' and path=='/audio/library/revision': return h.send(200,audio.catalog_revision())
    if h.command=='POST' and path=='/audio/library/page':
        fields(body,['offset','limit','view','filter','search','sort','include_ids','task_offset'],[])
        return h.send(200,audio.catalog_page(**body))
    static={'/audio/ui/studio.css':('studio.css','text/css; charset=utf-8'),'/audio/ui/studio.js':('studio.js','text/javascript; charset=utf-8'),'/audio/ui/cover.png':('cover.png','image/png')}
    for name in ('common.js','health.js','tasks.js','listening.js','catalog.js'):
        static['/audio/ui/'+name]=(name,'text/javascript; charset=utf-8')
    if h.command=='GET' and path in static:
        name,mime=static[path]
        return h.send(200,(Path(__file__).parent/'ui'/name).read_bytes(),mime)
    if h.command=='GET' and path=='/audio/health': return h.send(200,audio.health.report(audio))
    if h.command=='POST' and path=='/audio/health/network':
        fields(body,[])
        return h.send(200,audio.health.network(audio))
    if h.command=='GET' and path=='/audio/config': return h.send(200,audio.config())
    if h.command=='GET' and path=='/audio/budget': return h.send(200,audio.budget())
    if h.command=='POST' and path=='/audio/budget':
        fields(body,['max_submissions'],['max_submissions'])
        return h.send(200,audio.set_budget(**body))
    if h.command=='GET' and path=='/audio/history': return h.send(200,audio.history())
    if h.command=='POST' and path=='/audio/analyze-lyrics':
        fields(body,['raw_text','lyrics','constraints','request_id'],['raw_text','lyrics','request_id'])
        return h.send(200,audio.analyze_lyrics(**body))
    if h.command=='POST' and path=='/audio/preview':
        fields(body,['raw_text','prompt_mode','constraints','creation_mode','lyrics','arrangement_summary','analysis_id'],['raw_text'])
        return h.send(200,audio.preview(**body))
    if h.command=='POST' and path=='/audio/tasks':
        fields(body,['raw_text','request_id','prompt_mode','constraints','creation_mode','lyrics','arrangement_summary','analysis_id','n','project_id','reviewed','source_audio_asset_id','title'],['raw_text','request_id'])
        return h.send(202,audio.create(**body))
    m=re.fullmatch(r'/audio/stems/([a-f0-9]{32})/midi(?:/([a-f0-9]{32}))?/download',path)
    if m and h.command=='GET':
        aid,fid=m.groups()
        p=audio.midi_file(aid,fid)
        suffix='mid' if fid else 'zip'
        data=p.read_bytes()
        h.send_response(200)
        h.send_header('Content-Type','audio/midi' if fid else 'application/zip')
        h.send_header('Content-Length',str(len(data)))
        h.send_header('X-Content-Type-Options','nosniff')
        h.send_header('Content-Disposition',f'attachment; filename="beatmate-midi-{aid}-{fid or "all"}.{suffix}"')
        h.end_headers(); h.wfile.write(data); return
    m=re.fullmatch(r'/audio/stems/([a-f0-9]{32})/midi/retry',path)
    if m and h.command=='POST':
        fields(body,[])
        return h.send(200,audio.retry_midi(m[1]))
    m=re.fullmatch(r'/audio/stems/([a-f0-9]{32})/retry',path)
    if m and h.command=='POST':
        fields(body,[])
        return h.send(200,audio.retry_stems(m[1]))
    m=re.fullmatch(r'/audio/tasks/([a-f0-9]{32})(/retry)?',path)
    if m:
        if h.command=='GET' and not m[2]: return h.send(200,audio.get(m[1]))
        if h.command=='POST' and m[2]:
            fields(body,[])
            return h.send(200,audio.retry(m[1]))
    m=re.fullmatch(r'/audio/projects/([a-f0-9]{32})/select',path)
    if m and h.command=='POST':
        fields(body,['asset_id'],['asset_id'])
        return h.send(200,audio.select(m[1],body['asset_id']))
    m=re.fullmatch(r'/audio/assets/([a-f0-9]{32})/notes',path)
    if m:
        if h.command=='GET': return h.send(200,audio.notes(m[1]))
        fields(body,['action','seconds','text','note_id'],['action'])
        return h.send(200,audio.update_note(m[1],**body))
    m=re.fullmatch(r'/audio/works/([a-f0-9]{32})/export',path)
    if m and h.command=='GET':
        with audio.export_bundle(m[1]) as (stream,name):
            stream.seek(0,2); size=stream.tell(); stream.seek(0)
            h.send_response(200)
            h.send_header('Content-Type','application/zip')
            h.send_header('Content-Length',str(size))
            h.send_header('Content-Disposition',"attachment; filename=beatmate-export.zip; filename*=UTF-8''"+quote(name,safe=''))
            h.send_header('X-Content-Type-Options','nosniff')
            h.end_headers(); shutil.copyfileobj(stream,h.wfile)
        return
    m=re.fullmatch(r'/audio/assets/([a-f0-9]{32})(/download)?',path)
    if m and h.command=='GET':
        p,a=audio.file(m[1]); data=p.read_bytes(); status=200; start=0; end=len(data)-1
        range_header=h.headers.get('Range')
        if range_header:
            match=re.fullmatch(r'bytes=(\d+)-(\d*)',range_header)
            if not match: return h.send(416,{'error':'Unsupported byte range'})
            start=int(match[1]); end=min(int(match[2]) if match[2] else end,end)
            if start>end: return h.send(416,{'error':'Invalid byte range'})
            status=206
        mime={'wav':'audio/wav','mp3':'audio/mpeg','flac':'audio/flac','m4a':'audio/mp4'}[a['format']]
        h.send_response(status); h.send_header('Content-Type',mime)
        h.send_header('Content-Length',str(end-start+1)); h.send_header('Accept-Ranges','bytes')
        h.send_header('X-Content-Type-Options','nosniff')
        if status==206: h.send_header('Content-Range',f'bytes {start}-{end}/{len(data)}')
        if m[2]: h.send_header('Content-Disposition',f'attachment; filename="beatmate-{a.get("track_type","original")}-{a["id"]}.{a["format"]}"')
        h.end_headers(); h.wfile.write(data[start:end+1]); return
    return h.send(404,{'error':'Audio route not found'})
