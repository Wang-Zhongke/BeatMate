"""Offline workflow and recovery checks; never invokes paid generation or analysis."""
import io
import http.client
import json
from pathlib import Path
import stat
import tempfile
import threading
import unittest
import uuid
import zipfile
from unittest.mock import patch
from beatmate.audio import AudioService
from beatmate.audio_input import prepare
from beatmate.audio_analysis import analyze
from beatmate.audio_provider import MockAudioAdapter, MurekaAdapter, AudioError, Rejected, BeforeSubmissionError
from beatmate.audio_stems import split_archive
from beatmate.llm import PlannerError
from beatmate.service import ConflictError
from beatmate.api import server


class CountingAdapter(MockAudioAdapter):
    def __init__(self):
        self.submits=0; self.separations=0; self.archives=0; self.separation_error=None; self.archive_error=None
    def submit(self,t): self.submits+=1; return super().submit(t)
    def separate(self,c):
        self.separations+=1
        if self.separation_error: raise self.separation_error
        return super().separate(c)
    def download_stems(self,j):
        self.archives+=1
        if self.archive_error: raise self.archive_error
        return super().download_stems(j)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.adapter=CountingAdapter()
        self.s=AudioService(self.tmp.name,self.adapter,{'BEATMATE_AUDIO_MAX_CANDIDATES':'2'})
        self.addCleanup(self.s.close)
    def song(self,**kw):
        return self.s.create('温暖、克制',uuid.uuid4().hex,creation_mode='song_stems',lyrics='[主歌]\n'+('歌词很长\n'*250),reviewed=True,**kw)
    def finish(self):
        for _ in range(4): self.s.tick()
    def jobs(self):
        with self.s.connect() as c: return [json.loads(r[0]) for r in c.execute('SELECT data FROM audio_stem_jobs')]

    def test_song_lyrics_and_description_have_independent_limits(self):
        brief=prepare('描'*1024,creation_mode='song_stems',lyrics='词'*5000)
        self.assertEqual(len(brief['final_prompt']),1024)
        self.assertEqual(len(brief['lyrics']),5000)
        for desc,lyrics in [('描'*1025,'词'),('描述','词'*5001),('描述','')]:
            with self.assertRaises(ValueError): prepare(desc,creation_mode='song_stems',lyrics=lyrics)
        with self.assertRaises(ValueError): self.s.create('',uuid.uuid4().hex,creation_mode='song_stems',lyrics='词')
        with self.assertRaises(ValueError):
            self.s.create('80 BPM',uuid.uuid4().hex,creation_mode='song_stems',lyrics='词',constraints={'structure':'100 BPM'},reviewed=True)

    def test_summary_sends_only_reviewed_summary_and_keeps_sources(self):
        lyrics='只存在完整原文里\n'*300
        brief=prepare('保持用户原话',creation_mode='lyrics_summary',lyrics=lyrics,arrangement_summary='小调吉他，主歌稀疏，副歌加强鼓组。')
        self.assertNotIn('只存在完整原文里',brief['final_prompt'])
        self.assertEqual(brief['lyrics'],lyrics)
        self.assertEqual(brief['raw_text'],'保持用户原话')
        self.assertIn('不要唱歌',brief['final_prompt'])
        self.assertLessEqual(len(brief['final_prompt']),1024)
        with self.assertRaises(ValueError): prepare('',creation_mode='lyrics_summary',lyrics=lyrics)
        with self.assertRaises(ValueError): prepare('',creation_mode='lyrics_summary',lyrics=lyrics,arrangement_summary='字'*981)

    def test_analysis_idempotency_and_source_changes(self):
        source=dict(raw_text='克制',lyrics='原文\n'*1500,request_id=uuid.uuid4().hex)
        with patch('beatmate.audio.analyze',return_value=dict(summary='克制吉他',provider='test',model='test')) as call:
            first=self.s.analyze_lyrics(**source)
            self.assertEqual(self.s.analyze_lyrics(**source),first)
            self.assertEqual(call.call_count,1)
        b=self.s.preview('克制',creation_mode='lyrics_summary',lyrics=source['lyrics'],arrangement_summary='手动修改后的编曲',analysis_id=first['id'])
        self.assertEqual(b['analysis_id'],first['id'])
        with self.assertRaises(ValueError): self.s.preview('改过',creation_mode='lyrics_summary',lyrics=source['lyrics'],arrangement_summary='摘要',analysis_id=first['id'])
        with self.assertRaises(ConflictError): self.s.analyze_lyrics(**dict(source,lyrics='新原文'))

    def test_failed_analysis_never_auto_repeats_or_exposes_secret(self):
        source=dict(raw_text='',lyrics='完整原文',request_id=uuid.uuid4().hex)
        with patch('beatmate.audio.analyze',side_effect=RuntimeError('secret-key')) as call:
            with self.assertRaises(PlannerError) as error: self.s.analyze_lyrics(**source)
            self.assertNotIn('secret-key',str(error.exception))
            with self.assertRaises(ConflictError): self.s.analyze_lyrics(**source)
            self.assertEqual(call.call_count,1)
        with self.s.connect() as c: data=c.execute('SELECT data FROM audio_analyses').fetchone()[0]
        self.assertNotIn('secret-key',data)
        self.assertIn('完整原文',data)

    def test_deepseek_receives_full_5000_char_lyrics_without_midi_limit(self):
        response=dict(status='completed',output=[dict(type='message',content=[dict(type='output_text',text=json.dumps({'summary':'建议克制吉他，主歌留白，副歌稍加强。'}))])])
        with patch('beatmate.deepseek.DeepSeekPlanner._request',return_value=response) as request:
            result=analyze('', '词'*5000, {}, {'DEEPSEEK_API_KEY':'test'})
        body=request.call_args.args[0]
        self.assertEqual(json.loads(body['input'])['lyrics'],'词'*5000)
        self.assertNotIn('tools',body)
        self.assertEqual(result['provider'],'deepseek')
        with patch('beatmate.deepseek.DeepSeekPlanner._request',return_value=dict(status='completed',output=[{'type':'function_call'}])):
            with self.assertRaises(PlannerError): analyze('','词',{}, {'DEEPSEEK_API_KEY':'test'})

    def test_three_tracks_per_candidate_and_restart(self):
        task=self.song(n=2); self.finish(); h=self.s.history()
        self.assertEqual(len(h['assets']),6)
        self.assertEqual((self.adapter.submits,self.adapter.separations),(1,2))
        for index in (0,1):
            tracks=[a for a in h['assets'] if a['index']==index]
            self.assertEqual({a['track_type'] for a in tracks},{'mix','instrumental','vocals'})
            mix=next(a for a in tracks if a['track_type']=='mix')
            self.assertTrue(all(a['source_audio_asset_id']==mix['id'] for a in tracks))
            for a in tracks: self.assertTrue(self.s.file(a['id'])[0].read_bytes().startswith(b'RIFF'))
        self.assertTrue(all(j['status']=='ready' for j in h['stems']))
        self.assertNotIn('zip_url',json.dumps(h))
        with self.s.connect() as c: self.assertEqual(c.execute('SELECT count(*) FROM audio_stem_jobs').fetchone()[0],2)
        other=AudioService(self.tmp.name,self.adapter,{}); other.tick(); other.close()
        self.assertEqual(self.adapter.separations,2)
        self.assertEqual(self.s.get(task['id'])['status'],'ready')

    def test_zip_download_retry_does_not_resubmit_separation(self):
        self.adapter.archive_error=AudioError('temporary')
        self.song(); self.finish()
        h=self.s.history(); self.assertEqual(len(h['assets']),1)
        job=h['stems'][0]; self.assertEqual(job['status'],'downloading')
        self.adapter.archive_error=None; self.s.retry_stems(job['id']); self.s.tick()
        self.assertEqual(len(self.s.history()['assets']),3)
        self.assertEqual(self.adapter.separations,1)

    def test_uncertain_separation_leaves_mix_available(self):
        self.adapter.separation_error=AudioError('response lost')
        self.song(); self.finish(); self.finish()
        self.assertEqual(self.adapter.separations,1)
        h=self.s.history(); self.assertEqual(h['stems'][0]['status'],'uncertain')
        self.assertEqual(h['assets'][0]['track_type'],'mix')
        self.assertTrue(h['assets'][0]['available'])
        with self.assertRaises(ValueError): self.s.retry_stems(h['stems'][0]['id'])

    def test_process_death_after_paid_submit_cannot_resubmit(self):
        # A BaseException models process termination and bypasses normal handling.
        self.adapter.separation_error=SystemExit()
        self.song(); self.s.tick(); self.s.tick()
        with self.assertRaises(SystemExit): self.s.tick()
        self.assertEqual(self.jobs()[0]['status'],'submitting')
        self.adapter.separation_error=None; self.s.tick()
        self.assertEqual(self.jobs()[0]['status'],'uncertain')
        self.assertEqual(self.adapter.separations,1)

    def test_rejected_separation_is_not_retried(self):
        for failure in (Rejected('rejected',402),BeforeSubmissionError('tls_certificate')):
            with self.subTest(failure=failure):
                self.adapter.separation_error=failure; self.song(); self.finish()
        self.assertTrue(all(j['status']=='failed' for j in self.jobs()))
        self.assertEqual(self.adapter.separations,2)

    def test_stem_timeout_persists_safe_diagnostics_and_never_resubmits(self):
        self.adapter.separation_error=AudioError('Network timeout after request started; response not confirmed')
        self.song();self.finish()
        job=self.s.history()['stems'][0]
        self.assertEqual(job['status'],'uncertain')
        self.assertEqual(job['failure_code'],'network_timeout')
        self.assertIn('超时',job['error'])
        self.assertTrue(job['submitted_at']);self.assertTrue(job['failed_at'])
        self.assertNotIn('response_received_at',job)
        self.adapter.separation_error=None;self.finish()
        self.assertEqual(self.adapter.separations,1)
        self.assertEqual(len(self.s.history()['assets']),1)

    def test_missing_stem_restores_from_saved_archive(self):
        self.song(); self.finish()
        a=next(a for a in self.s.history()['assets'] if a['track_type']=='vocals')
        self.s.file(a['id'])[0].unlink()
        self.s.tick()
        self.assertTrue(self.s.file(a['id'])[0].exists())
        self.assertEqual((self.adapter.separations,self.adapter.archives),(1,1))

    def test_generation_budget_reserves_stem_operations(self):
        self.adapter.provider='mureka'
        self.s.set_budget(2)
        with self.assertRaises(ValueError): self.song(n=2)
        self.assertEqual(len(self.s.history()['tasks']),0)
        task=self.song()
        self.assertEqual(self.s.budget()['used_submissions'],2)
        self.assertEqual(self.s.budget()['remaining_submissions'],0)
        again=self.s.create(task['brief']['raw_text'],task['request_id'],creation_mode='song_stems',lyrics=task['brief']['lyrics'],reviewed=True)
        self.assertEqual(again['id'],task['id'])
        self.assertEqual(self.adapter.submits,0)

    def test_extra_provider_candidate_is_not_separated_without_reservation(self):
        self.song()
        original=self.adapter.query
        with patch.object(self.adapter,'query',side_effect=lambda t:original(dict(t,n=2))): self.finish()
        self.assertEqual(self.adapter.separations,1)
        self.assertEqual(len(self.s.history()['assets']),4)
        self.assertIn('skipped',{j['status'] for j in self.jobs()})

    def test_mureka_routes_and_separation_payload(self):
        adapter=MurekaAdapter({'MUREKA_API_KEY':'test','MUREKA_MODEL':'mureka-9','BEATMATE_AUDIO_LIVE':'1'})
        brief=prepare('风格原话',creation_mode='song_stems',lyrics='歌'*2000)
        task=dict(brief=brief,requested_model='mureka-9',n=1,provider_task_id='123')
        with patch.object(adapter,'request',return_value={}) as request:
            adapter.submit(task)
            args=request.call_args.args
            self.assertEqual(args[:2],('POST','/v1/song/generate'))
            self.assertEqual(args[2]['lyrics'],'歌'*2000)
            self.assertEqual(args[2]['prompt'],'风格原话')
            adapter.query(task); self.assertEqual(request.call_args.args,('GET','/v1/song/query/123'))
            with patch('beatmate.audio_provider.public_target'):
                adapter.separate({'url':'https://cdn.mureka.ai/test.mp3'})
            self.assertEqual(request.call_args.args,('POST','/v1/song/stem',{'url':'https://cdn.mureka.ai/test.mp3','model':'audio-separation-3'}))
        with patch('beatmate.audio_provider.public_target',side_effect=AudioError('unsafe')),patch.object(adapter,'request') as request:
            with self.assertRaises(BeforeSubmissionError): adapter.separate({'url':'http://127.0.0.1/test.mp3'})
            request.assert_not_called()

    def test_http_analysis_and_three_independent_downloads(self):
        httpd=server(None,0,audio=self.s,background_audio=False)
        thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
        def req(method,path,body=None):
            conn=http.client.HTTPConnection('127.0.0.1',httpd.server_port)
            conn.request(method,path,json.dumps(body) if body is not None else None,{'Content-Type':'application/json'})
            response=conn.getresponse(); data=response.read(); code=response.status
            disposition=response.getheader('Content-Disposition')
            if response.getheader('Content-Type')=='application/json': data=json.loads(data)
            conn.close();return code,data,disposition
        try:
            source=dict(raw_text='稀疏吉他',lyrics='歌词原文\n'*700)
            code,analysis,_=req('POST','/audio/analyze-lyrics',dict(source,request_id=uuid.uuid4().hex))
            self.assertEqual(code,200)
            self.assertEqual(req('POST','/audio/preview',dict(source,creation_mode='lyrics_summary',arrangement_summary=analysis['summary'],analysis_id=analysis['id']))[0],200)
            code,task,_=req('POST','/audio/tasks',dict(source,creation_mode='song_stems',request_id=uuid.uuid4().hex,reviewed=True))
            self.assertEqual(code,202);self.finish()
            assets=req('GET','/audio/history')[1]['assets']
            self.assertEqual(len(assets),3)
            for asset in assets:
                code,data,name=req('GET','/audio/assets/'+asset['id']+'/download')
                self.assertEqual(code,200);self.assertTrue(data.startswith(b'RIFF'))
                self.assertIn(asset['track_type'],name)
                self.assertEqual(asset['task_id'],task['id'])
        finally:
            httpd.shutdown();httpd.server_close();thread.join()


class ArchiveTests(unittest.TestCase):
    def pack(self, names):
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as f:
            for name in names: f.writestr(name,b'test')
        return data.getvalue()
    def test_archive_role_mapping_and_rejections(self):
        self.assertEqual(set(split_archive(self.pack(['tracks/Accompaniment.wav','tracks/Vocals.wav']))),{'instrumental','vocals'})
        for names in (['../instrumental.wav','vocals.wav'],['/instrumental.wav','vocals.wav'],
                      ['instrumental.wav'],['instrumental.wav','vocals.wav','vocal.wav'],
                      ['track1.wav','track2.wav'],['instrumental.wav','vocals.wav','../info.txt']):
            with self.subTest(names=names),self.assertRaises(AudioError): split_archive(self.pack(names))
    def test_zip_symlinks_and_uncompressed_limit(self):
        info=zipfile.ZipInfo('instrumental.wav');info.external_attr=(stat.S_IFLNK|0o777)<<16
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as f: f.writestr(info,'elsewhere')
        with self.assertRaises(AudioError): split_archive(data.getvalue())
        with patch('beatmate.audio_stems.MAX_STEM_TOTAL',7):
            with self.assertRaises(AudioError):split_archive(self.pack(['instrumental.wav','vocals.wav']))
