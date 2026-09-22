import http.client
import io
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
import uuid
import zipfile
from unittest.mock import patch, MagicMock
from beatmate.audio import AudioService
from beatmate.audio_provider import AudioError, MurekaAdapter
from beatmate.audio_health import RuntimeHealth, DEFAULT_PORT
from beatmate.audio_progress import task_progress
from beatmate.api import server


class ImprovementsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.s=AudioService(self.tmp.name,env={});self.addCleanup(self.s.close)

    def generate(self,song=True):
        kw=dict(creation_mode='song_stems',lyrics='测试歌词',reviewed=True) if song else {}
        task=self.s.create('原始描述',uuid.uuid4().hex,title='午夜 / 电台',**kw)
        for _ in range(4):self.s.tick()
        h=self.s.history();return next(a for a in h['assets'] if a['source_audio_asset_id']==a['id'])

    def test_health_is_local_and_detects_source_and_configuration_changes(self):
        with patch('beatmate.audio_health.socket.create_connection') as connect:
            report=self.s.health.report(self.s)
            self.assertFalse(report['restart_required']);connect.assert_not_called()
            self.assertEqual(report['audio_dir'],str(Path(self.tmp.name).resolve()))
        with patch('beatmate.audio_health.source_revision',return_value='changed'):
            self.assertTrue(self.s.health.report(self.s)['restart_required'])
        p=Path(self.tmp.name)/'config';p.write_text('MUREKA_API_KEY=private-test')
        self.s.health.config_path=p
        report=self.s.health.report(self.s)
        self.assertTrue(report['restart_required']);self.assertNotIn('private-test',json.dumps(report))

    def test_network_diagnostic_never_sends_http_or_credentials(self):
        self.s.adapter=MurekaAdapter({'MUREKA_API_KEY':'private-test'})
        raw=MagicMock();secured=MagicMock();context=MagicMock();context.wrap_socket.return_value=secured
        with patch('beatmate.audio_health.public_target',return_value=(MagicMock(hostname='api.mureka.ai'),'8.8.8.8')),patch('beatmate.audio_health.socket.create_connection',return_value=raw),patch('beatmate.audio_health.ssl.create_default_context',return_value=context),patch('http.client.HTTPSConnection.request') as request:
            report=self.s.health.network(self.s)
            self.assertEqual(report['status'],'ok');self.assertFalse(report['http_sent']);request.assert_not_called()
            context.wrap_socket.assert_called_once_with(raw,server_hostname='api.mureka.ai');secured.close.assert_called_once()
        with patch('beatmate.audio_health.public_target',side_effect=OSError('private-test')):
            result=self.s.health.network(self.s)
            self.assertEqual(result['stage'],'dns');self.assertNotIn('private-test',json.dumps(result))
        self.assertEqual(self.s.history()['tasks'],[])
        self.assertEqual(self.s.budget()['used_submissions'],0)

    def test_progress_does_not_offer_resubmission_for_uncertain(self):
        t=self.s.create('描述',uuid.uuid4().hex)
        t.update(status='uncertain',error='连接断开')
        self.assertFalse(task_progress(t)['can_resume'])
        t.update(status='generating',provider_task_id='confirmed')
        self.assertTrue(task_progress(t)['can_resume'])
        self.assertIn('已返回',task_progress(t)['last_success'])
        self.s.tick();self.assertIsNotNone(self.s.get(t['id'])['progress']['last_attempt_at'])

    def test_notes_survive_restart_and_validate_scope(self):
        a=self.generate();aid=a['id']
        notes=self.s.update_note(aid,'add',1.2,'副歌前留白')
        again=AudioService(self.tmp.name,env={});self.addCleanup(again.close)
        self.assertEqual(again.notes(aid),notes)
        for seconds in (-1,100,float('nan'),True):
            with self.assertRaises(ValueError):self.s.update_note(aid,'add',seconds,'test')
        with self.assertRaises(ValueError):self.s.update_note(aid,'add',1,' '*3)
        other=next(x for x in self.s.history()['assets'] if x['id']!=aid)
        with self.assertRaises(KeyError):self.s.update_note(other['id'],'delete',note_id=notes[0]['id'])
        self.assertEqual(len(self.s.notes(aid)),1)
        self.s.library_update([aid],'trash');self.s.library_update([aid],'restore')
        self.assertEqual(len(self.s.notes(aid)),1)
        self.s.update_note(aid,'delete',note_id=notes[0]['id']);self.assertEqual(self.s.notes(aid),[])

    def test_bundle_has_original_tracks_midi_notes_and_safe_names_without_network(self):
        a=self.generate();self.s.update_note(a['id'],'add',2,'调整鼓组')
        before=self.s.budget()
        with patch.object(self.s.adapter,'download',side_effect=AssertionError('network')),patch.object(self.s.adapter,'separate',side_effect=AssertionError('paid')):
            with self.s.export_bundle(a['id']) as (f,name):data=f.read()
        self.assertNotIn('/',name)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names=z.namelist();self.assertFalse(any('..' in n.split('/') for n in names))
            manifest=json.loads(z.read(next(n for n in names if n.endswith('/manifest.json'))))
            self.assertEqual({f['kind'] for f in manifest['files']},{'mix','instrumental','vocals','midi','midi_archive'})
            self.assertEqual(manifest['notes'][0]['text'],'调整鼓组');self.assertEqual(manifest['unavailable'],[])
            for file in manifest['files']:
                import hashlib
                self.assertEqual(hashlib.sha256(z.read(names[0].split('/')[0]+'/'+file['path'])).hexdigest(),file['sha256'])
        self.assertEqual(before,self.s.budget())

    def test_partial_export_explains_missing_audio_and_purge_blocks_notes_export(self):
        a=self.generate();stem=next(x for x in self.s.history()['assets'] if x['track_type']=='vocals')
        self.s.file(stem['id'])[0].unlink()
        with self.s.export_bundle(a['id']) as (f,_):
            with zipfile.ZipFile(f) as z:manifest=json.loads(z.read(next(n for n in z.namelist() if n.endswith('/manifest.json'))))
        self.assertIn('vocals',[x['kind'] for x in manifest['unavailable']])
        self.s.update_note(a['id'],'add',1,'test')
        self.s.library_update([a['id']],'trash');self.s.library_update([a['id']],'purge')
        with self.assertRaises(AudioError):
            with self.s.export_bundle(a['id']):pass
        with self.assertRaises(AudioError):self.s.notes(a['id'])
        with self.s.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM audio_notes').fetchone()[0],0)

    def test_cached_history_rechecks_changed_files_and_download_always_validates(self):
        a=self.generate(song=False);path,_=self.s.file(a['id']);self.s.history()
        with patch('beatmate.audio.sha',side_effect=AssertionError('unnecessary read')):
            self.assertTrue(self.s.history()['assets'][0]['available'])
        path.chmod(0o644);data=path.read_bytes();path.write_bytes(b'X'+data[1:])
        self.assertFalse(self.s.history()['assets'][0]['available'])
        with self.assertRaises(AudioError):self.s.file(a['id'])

    def test_http_health_notes_export_same_origin_and_module_routes(self):
        a=self.generate();httpd=server(None,0,audio=self.s,background_audio=False)
        t=threading.Thread(target=httpd.serve_forever,daemon=True);t.start()
        def request(method,path,body=None,origin=None):
            c=http.client.HTTPConnection('127.0.0.1',httpd.server_port,timeout=5)
            headers={'Content-Type':'application/json'}
            if origin:headers['Origin']=origin
            c.request(method,path,json.dumps(body) if body is not None else None,headers)
            r=c.getresponse();data=r.read();status=r.status;c.close();return status,data
        try:
            for path in ['/audio/health','/audio/ui/common.js','/audio/ui/tasks.js','/audio/ui/listening.js','/audio/ui/health.js']:
                self.assertEqual(request('GET',path)[0],200)
            self.assertEqual(request('POST','/audio/health/network',{})[0],200)
            self.assertEqual(request('POST','/audio/health/network',{},'https://evil.example')[0],403)
            path='/audio/assets/'+a['id']+'/notes'
            status,data=request('POST',path,dict(action='add',seconds=1,text='测试'));self.assertEqual(status,200)
            self.assertEqual(len(json.loads(request('GET',path)[1])),1)
            status,data=request('GET','/audio/works/'+a['id']+'/export');self.assertEqual(status,200);self.assertTrue(zipfile.is_zipfile(io.BytesIO(data)))
            self.assertEqual(request('GET','/audio/works/'+a['id']+'/export',origin='https://evil.example')[0],403)
        finally:httpd.shutdown();httpd.server_close();t.join()

    def test_port_conflict_reports_actionable_error_without_worker_start(self):
        from beatmate.audio_cli import run
        from types import SimpleNamespace
        occupied=socket.socket();occupied.bind(('127.0.0.1',0));occupied.listen()
        try:
            with patch('beatmate.audio_cli.load_audio_env',return_value={}),patch('beatmate.audio.AudioService.start') as start:
                with self.assertRaisesRegex(ValueError,'端口'):
                    run(SimpleNamespace(command='audio-serve',audio_dir=self.tmp.name,port=occupied.getsockname()[1]))
                start.assert_not_called()
        finally:occupied.close()
        self.assertEqual(DEFAULT_PORT,8767)
