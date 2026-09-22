"""Offline contract tests. No real credentials or external API calls."""
import copy
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import traceback
import unittest
import urllib.error
from unittest.mock import Mock, patch

from beatmate.api import server
from beatmate.config import create_planner
from beatmate.deepseek import DeepSeekPlanner, _NoRedirect
from beatmate.llm import OpenAIPlanner, PlannerError
from beatmate.model import BAR, BeatSpec, EditPlan, digest
from beatmate.planner import MockPlanner
from beatmate.render import midi_bytes
from beatmate.service import Service, ConflictError
from beatmate.smoke_deepseek import run_smoke


ENV = {'DEEPSEEK_API_KEY': 'unit-test-placeholder-not-a-real-key'}


def response(data=None, *, text=None):
    return {'status': 'completed', 'output': [
        {'type': 'reasoning', 'content': []},
        {'type': 'message', 'role': 'assistant', 'status': 'completed', 'content': [
            {'type': 'output_text', 'text': text if text is not None else json.dumps(data)}]}]}


class OfflineCase(unittest.TestCase):
    def setUp(self):
        # Any accidental real DeepSeek request fails, even if a developer has a key.
        self.network_guard = patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('External network forbidden'))
        self.network_guard.start()
        self.addCleanup(self.network_guard.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def planner(self, data=None):
        return DeepSeekPlanner(environ=ENV, transport=lambda _: response(data))


class DeepSeekRequestTests(OfflineCase):
    def test_request_endpoint_headers_schema_and_system_constraints(self):
        stream = io.BytesIO(json.dumps(response(BeatSpec().to_dict())).encode())
        opener = Mock()
        opener.open.return_value = stream
        with patch('urllib.request.build_opener', return_value=opener) as build:
            planner = DeepSeekPlanner(environ=ENV)
            parsed = planner.parse_intent('90 BPM boom bap')
        request = opener.open.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, 'https://api.deepseek.com/responses')
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + ENV['DEEPSEEK_API_KEY'])
        self.assertEqual(request.get_header('Content-type'), 'application/json')
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(opener.open.call_args.kwargs, {'timeout': 45})
        self.assertIsInstance(build.call_args.args[0], _NoRedirect)
        self.assertEqual(set(payload), {'model','max_output_tokens','instructions','input','text','reasoning'})
        self.assertEqual(payload['reasoning'], {'effort':'none'})
        self.assertEqual(payload['model'], 'deepseek-flash')
        self.assertIn('No audio generation', payload['instructions'])
        fmt = payload['text']['format']
        self.assertEqual((fmt['type'],fmt['strict']), ('json_schema',True))
        self.assertFalse(fmt['schema']['additionalProperties'])
        self.assertEqual(set(fmt['schema']['required']), set(BeatSpec.__dataclass_fields__))
        self.assertEqual(parsed['planner'],'deepseek:deepseek-flash')
        self.assertNotIn(ENV['DEEPSEEK_API_KEY'],json.dumps(parsed)+json.dumps(payload))

    def test_custom_model_and_base_url(self):
        planner=DeepSeekPlanner(environ={**ENV,'DEEPSEEK_MODEL':'custom-flash-snapshot','DEEPSEEK_BASE_URL':'https://gateway.example/v1/'},transport=lambda _:response(BeatSpec().to_dict()))
        self.assertEqual(planner.endpoint,'https://gateway.example/v1/responses')
        self.assertEqual(planner.parse_intent('beat')['planner'],'deepseek:custom-flash-snapshot')

    def test_invalid_urls_and_credentials(self):
        cases=[{'DEEPSEEK_API_KEY':''},{'DEEPSEEK_API_KEY':'bad\nkey'},{'DEEPSEEK_MODEL':''},
               {'DEEPSEEK_MODEL':'bad\nmodel'}]
        cases += [{'DEEPSEEK_BASE_URL':url} for url in ('','http://api.deepseek.com','https://user:secret@host',
                  'https://host?token=secret','https://host/#fragment','https://host/responses','https://host:bad','https://host/ bad')]
        for extra in cases:
            with self.subTest(extra=extra), self.assertRaises(ValueError) as caught:
                DeepSeekPlanner(environ={**ENV,**extra})
            self.assertNotIn(ENV['DEEPSEEK_API_KEY'],str(caught.exception))

    def test_redirect_is_never_followed(self):
        self.assertIsNone(_NoRedirect().redirect_request(None,None,302,'Found',{},'https://other.example'))

    def test_transport_errors_are_sanitized_once_no_fallback(self):
        cases=[(401,'authentication'),(403,'authentication'),(429,'rate_limit'),(400,'request_rejected'),
               (402,'request_rejected'),(404,'request_rejected'),(422,'request_rejected'),(500,'http_error'),(302,'http_error')]
        for status, code in cases:
            secret=ENV['DEEPSEEK_API_KEY']
            error=urllib.error.HTTPError('https://example.invalid/'+secret,status,secret,{},io.BytesIO(secret.encode()))
            opener=Mock()
            opener.open.side_effect=error
            with self.subTest(status=status), patch('urllib.request.build_opener',return_value=opener):
                with self.assertRaises(PlannerError) as caught:
                    DeepSeekPlanner(environ=ENV).parse_intent('beat')
                self.assertEqual(caught.exception.code,code)
                self.assertNotIn(secret,''.join(traceback.format_exception(caught.exception)))
                self.assertEqual(opener.open.call_count,1)

    def test_network_timeout_and_bad_wire_json(self):
        for error in (TimeoutError('secret'), urllib.error.URLError('secret'), http.client.IncompleteRead(b'secret')):
            opener=Mock()
            opener.open.side_effect=error
            with patch('urllib.request.build_opener',return_value=opener), self.assertRaises(PlannerError) as caught:
                DeepSeekPlanner(environ=ENV).parse_intent('beat')
            self.assertEqual(caught.exception.code,'network_unavailable')
            self.assertNotIn('secret',str(caught.exception))
        for raw in (b'', b'{', b'null', b'[]', b'\xff', b'x'*1_048_577):
            opener=Mock()
            opener.open.return_value=io.BytesIO(raw)
            with patch('urllib.request.build_opener',return_value=opener), self.assertRaises(PlannerError):
                DeepSeekPlanner(environ=ENV).parse_intent('beat')


class DeepSeekValidationTests(OfflineCase):
    def setUp(self):
        super().setUp()
        self.service=Service(Path(self.tmp.name)/'db')
        self.before=self.service.create_project(spec={},protected_tracks=['kick','snare','bass','chords'])

    def assert_unchanged(self):
        self.assertEqual(self.service.get_version(self.before['project_id']),self.before)
        with self.service.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM versions').fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM projects').fetchone()[0],1)

    def test_incomplete_reason_is_safe_and_never_commits(self):
        for reason in ('max_output_tokens', 'content_filter', ENV['DEEPSEEK_API_KEY']):
            candidate = {'status':'incomplete', 'incomplete_details':{'reason':reason}}
            self.service.planner = DeepSeekPlanner(environ=ENV, transport=lambda _:candidate)
            with self.assertRaises(PlannerError) as caught:
                self.service.create_project(text='beat')
            if reason in ('max_output_tokens','content_filter'):
                self.assertIn(reason,str(caught.exception))
            self.assertNotIn(ENV['DEEPSEEK_API_KEY'],str(caught.exception))
            self.assert_unchanged()

    def test_malformed_responses_never_create_versions(self):
        variants=[None,[],{}, {'status':'incomplete'}, {'status':'failed'},
                  {'status':'completed','output':[]},
                  {'status':'completed','output':[{'type':'function_call','name':'execute_python'}]},
                  {'status':'completed','output':[{'type':'message','content':[{'type':'refusal'}]}]},
                  response(text=''),response(text='{'),response(text='```json\n{}\n```'),
                  response(text='{"bpm":90,"bpm":100}'),response(text='{"swing":NaN}'),
                  response(text='null'),response(text='[]')]
        incomplete=response(BeatSpec().to_dict())
        incomplete['output'][1]['status']='incomplete'
        variants.append(incomplete)
        extra=copy.deepcopy(response(BeatSpec().to_dict()))
        extra['output'].append({'type':'function_call','name':'write_file'})
        variants.append(extra)
        for candidate in variants:
            self.service.planner=DeepSeekPlanner(environ=ENV,transport=lambda _, candidate=candidate:candidate)
            with self.subTest(candidate=candidate), self.assertRaises(PlannerError):
                self.service.create_project(text='beat')
            self.assert_unchanged()

    def test_strict_spec_fields_and_business_limits(self):
        valid=BeatSpec().to_dict()
        variants=[{},dict(valid,extra='ignored?'),dict(valid,bpm=True),dict(valid,bpm=221),
                  dict(valid,bars=33),dict(valid,key='H'),dict(valid,mode='dorian'),dict(valid,swing=.9),dict(valid,seed=-1)]
        for spec in variants:
            self.service.planner=self.planner(spec)
            with self.subTest(spec=spec),self.assertRaises(PlannerError):
                self.service.create_project(text='beat')
            self.assert_unchanged()

    def test_successful_creation_records_provider_but_not_credentials(self):
        self.service.planner = self.planner(BeatSpec().to_dict())
        created = self.service.create_project(text='beat')
        self.assertEqual(created['audit']['planner'], 'deepseek:deepseek-flash')
        self.assertNotIn(ENV['DEEPSEEK_API_KEY'], json.dumps(created))
        with self.service.connect() as conn:
            snapshots = conn.execute('SELECT data FROM versions').fetchall()
        self.assertNotIn(ENV['DEEPSEEK_API_KEY'], str(snapshots))

    def test_expanded_scope_unknown_fields_and_operations_rejected(self):
        variants=[dict(operation='density',value=16,track_id='kick'),
                  dict(operation='density',value=16,start_bar=1),dict(operation='density',value=16,end_bar=8),
                  dict(operation='density',value=16,protected_tracks=[]),dict(operation='run_python',value=0),
                  dict(operation='density',value=17),dict(operation='velocity',value=127),
                  dict(operation='mute',value=True),dict(operation='mute',value='0'),
                  dict(operation='transpose',value=12),{}]
        for data in variants:
            with self.subTest(data=data),self.assertRaises(PlannerError):
                self.planner(data).plan_edit('改一下','hihat',3,4)
            self.assert_unchanged()

    def test_invalid_caller_scope_is_rejected_before_model(self):
        transport=Mock(side_effect=AssertionError('Must not call model'))
        planner=DeepSeekPlanner(environ=ENV,transport=transport)
        for scope in [('unknown',1,2),('hihat',0,2),('hihat',4,3),('hihat',1,33)]:
            with self.assertRaises(ValueError):
                planner.plan_edit('edit',*scope)
        transport.assert_not_called()

    def test_end_to_end_scope_history_midi_and_protection(self):
        planner=self.planner(dict(operation='density',value=32))
        plan=planner.plan_edit('加密32','hihat',3,4)
        before_hash=digest(self.before)
        after=self.service.edit_project(self.before['project_id'],self.before['version_id'],plan)
        self.assertEqual(after['parent_id'],self.before['version_id'])
        self.assertNotEqual(after['version_id'],self.before['version_id'])
        for a,b in zip(self.before['tracks'],after['tracks']):
            if a['id']!='hihat':
                self.assertEqual(a,b)
            else:
                outside=lambda t:[n for n in t['notes'] if not 2*BAR<=n['start_tick']<4*BAR]
                self.assertEqual(outside(a),outside(b))
        for hashes in after['audit']['protected_hashes'].values():
            self.assertEqual(hashes['before'],hashes['after'])
        self.assertEqual(digest(self.service.get_version(self.before['project_id'],self.before['version_id'])),before_hash)
        self.assertTrue(midi_bytes(after).startswith(b'MThd'))
        with self.assertRaises(ConflictError):
            self.service.edit_project(self.before['project_id'],self.before['version_id'],plan)

    def test_executor_rejects_protected_track_and_final_velocity_without_commit(self):
        for track, operation, value in [('kick','mute',0),('hihat','velocity',126)]:
            plan=self.planner(dict(operation=operation,value=value)).plan_edit('edit',track,3,4)
            with self.assertRaises(ValueError):
                self.service.edit_project(self.before['project_id'],self.before['version_id'],plan)
            self.assert_unchanged()
        # Extra protection comes from the caller, never from the planner.
        plan=self.planner(dict(operation='mute',value=0)).plan_edit('edit','hihat',3,4)
        with self.assertRaises(ValueError):
            self.service.edit_project(self.before['project_id'],self.before['version_id'],plan,protected_tracks=['hihat'])
        self.assert_unchanged()

    def test_provider_failures_never_commit(self):
        for code in ('authentication','rate_limit','network_unavailable','invalid_response','request_rejected'):
            def fail(_):
                raise PlannerError('Provider failed',code=code)
            self.service.planner=DeepSeekPlanner(environ=ENV,transport=fail)
            with self.assertRaises(PlannerError):
                self.service.create_project(text='beat')
            with self.assertRaises(PlannerError):
                self.service.planner.plan_edit('edit','hihat',3,4)
            self.assert_unchanged()


class ConfigurationTests(OfflineCase):
    def test_default_mock_and_configuration_isolation(self):
        self.assertIsInstance(create_planner(environ={}),MockPlanner)
        self.assertIsInstance(create_planner(environ={'DEEPSEEK_API_KEY':'','OPENAI_API_KEY':'','DEEPSEEK_MODEL':''}),MockPlanner)
        planner=create_planner(environ={**ENV,'BEATMATE_PLANNER':'deepseek'})
        self.assertIsInstance(planner,DeepSeekPlanner)
        self.assertEqual(planner.api_key,ENV['DEEPSEEK_API_KEY'])
        with self.assertRaises(ValueError):
            create_planner(environ={'BEATMATE_PLANNER':'deepseek','OPENAI_API_KEY':'openai-test-only'})
        planner=create_planner('openai',environ={'OPENAI_API_KEY':'openai-test-only','BEATMATE_MODEL':'configured-model','DEEPSEEK_API_KEY':''})
        self.assertIsInstance(planner,OpenAIPlanner)
        self.assertNotIsInstance(planner,DeepSeekPlanner)
        with self.assertRaises(ValueError):
            create_planner('openai',environ=ENV)

    def test_explicit_mock_overrides_bad_environment_and_unknown_fails(self):
        self.assertIsInstance(create_planner('mock',environ={'BEATMATE_PLANNER':'invalid'}),MockPlanner)
        for name in ('', 'invalid'):
            with self.assertRaises(ValueError):
                create_planner(environ={'BEATMATE_PLANNER':name})

    def test_cli_environment_selection_and_override(self):
        from beatmate.cli import main
        for argument,env,expected in [([],{'BEATMATE_PLANNER':'deepseek',**ENV},DeepSeekPlanner),
                                      (['--planner','mock'],{'BEATMATE_PLANNER':'deepseek'},MockPlanner),
                                      ([],{},MockPlanner)]:
            with patch.dict('os.environ',env,clear=True),patch('sys.argv',['beatmate','--db',str(Path(self.tmp.name)/'cli.db'),'serve',*argument]),patch('beatmate.cli.server') as serve,patch('builtins.print'):
                main()
                self.assertIsInstance(serve.call_args.args[0].planner,expected)
                serve.return_value.serve_forever.assert_called_once()

    def test_cli_invalid_provider_fails_before_database_creation(self):
        from beatmate.cli import main
        path=Path(self.tmp.name)/'must-not-exist.db'
        with patch.dict('os.environ',{'BEATMATE_PLANNER':'invalid'},clear=True),patch('sys.argv',['beatmate','--db',str(path),'serve']),patch('sys.stderr',new=io.StringIO()),self.assertRaises(SystemExit):
            main()
        self.assertFalse(path.exists())

    def test_demo_remains_offline_with_deepseek_environment(self):
        from beatmate.cli import main
        output = Path(self.tmp.name) / 'demo'
        with patch.dict('os.environ', {'BEATMATE_PLANNER': 'deepseek'}, clear=True), patch('sys.argv',
                ['beatmate', '--db', str(Path(self.tmp.name) / 'demo.db'), 'demo', '--output', str(output)]), patch('builtins.print'):
            main()
        self.assertEqual(json.loads((output / 'v1.json').read_text())['audit']['planner'], 'mock')
        self.assertTrue((output / 'v2.mid').read_bytes().startswith(b'MThd'))


class SmokeTests(OfflineCase):
    def test_smoke_default_and_missing_key_skip_without_network(self):
        with patch('beatmate.smoke_deepseek.DeepSeekPlanner',side_effect=AssertionError('Must not initialize')):
            self.assertEqual(run_smoke(environ=ENV)['status'],'SKIPPED')
            result=run_smoke(enabled=True,environ={})
            self.assertEqual((result['status'],result['calls']),('SKIPPED',0))

    def test_smoke_two_injected_calls_complete_real_local_execution(self):
        responses=[response(BeatSpec(bars=4,seed=7).to_dict()),response(dict(operation='density',value=16))]
        with patch.object(DeepSeekPlanner,'_request',side_effect=responses) as request:
            result=run_smoke(enabled=True,environ=ENV,output=self.tmp.name)
        self.assertEqual((result['status'],result['calls'],request.call_count),('PASS',2,2))
        self.assertTrue((Path(result['output'])/'after.mid').is_file())
        self.assertNotIn(ENV['DEEPSEEK_API_KEY'],(Path(result['output'])/'report.json').read_text())

    def test_smoke_network_skip_and_auth_failure(self):
        for code,status in [('network_unavailable','SKIPPED'),('authentication','FAIL')]:
            with patch.object(DeepSeekPlanner,'_request',side_effect=PlannerError('Safe error',code=code)) as request:
                result=run_smoke(enabled=True,environ=ENV,output=self.tmp.name)
            self.assertEqual((result['status'],result['calls'],request.call_count),(status,1,1))
            self.assertEqual(list(Path(self.tmp.name).iterdir()),[])

    def test_smoke_wrong_semantics_fails_not_200_success(self):
        with patch.object(DeepSeekPlanner,'_request',return_value=response(BeatSpec(bars=8).to_dict())) as request:
            result=run_smoke(enabled=True,environ=ENV,output=self.tmp.name)
        self.assertEqual((result['status'],request.call_count),('FAIL',1))
        self.assertEqual(list(Path(self.tmp.name).iterdir()),[])


class DeepSeekHTTPTests(OfflineCase):
    def test_http_provider_failure_is_502_and_no_version(self):
        planner=DeepSeekPlanner(environ=ENV,transport=lambda _:response({'unexpected':'output'}))
        service=Service(Path(self.tmp.name)/'http.db',planner)
        httpd=server(service,0)
        thread=threading.Thread(target=httpd.serve_forever,daemon=True)
        thread.start()
        try:
            for path,body in [('/projects',dict(text='beat')),('/plan',dict(text='edit',track_id='hihat',start_bar=3,end_bar=4))]:
                conn=http.client.HTTPConnection('127.0.0.1',httpd.server_port,timeout=5)
                try:
                    conn.request('POST',path,json.dumps(body),{'Content-Type':'application/json'})
                    reply=conn.getresponse()
                    self.assertEqual(reply.status,502)
                    self.assertNotIn(ENV['DEEPSEEK_API_KEY'],reply.read().decode())
                finally:
                    conn.close()
            with service.connect() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM versions').fetchone()[0],0)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join()
