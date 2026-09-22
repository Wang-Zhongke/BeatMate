"""Small loopback-only HTTP API, standard library, no DAW automation."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
from .model import fields, BeatSpec, EditPlan
from .render import midi_bytes, preview_bytes
from .service import ConflictError
from .llm import PlannerError
from .audio_provider import AudioError


def handler(service, audio=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, body, content_type='application/json'):
            payload = json.dumps(body, ensure_ascii=False).encode() if content_type == 'application/json' else body
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            self.dispatch()

        def do_POST(self):
            self.dispatch()

        def dispatch(self):
            try:
                # A local API is not an invitation for arbitrary browser origins.
                is_audio = audio is not None and (self.path in ('/', '/audio') or self.path.startswith('/audio/'))
                origin = self.headers.get('Origin')
                if is_audio:
                    expected = f'127.0.0.1:{self.server.server_port}'
                    if self.headers.get('Host') != expected or (origin and origin != 'http://' + expected):
                        return self.send(403, {'error': 'Only the loopback same-origin page is allowed'})
                elif origin:
                    return self.send(403, {'error': 'Browser cross-origin requests are disabled'})
                body = {}
                if self.command == 'POST':
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size <= 65536:
                        raise ValueError('Body must be 1..65536 bytes')
                    if self.headers.get_content_type() != 'application/json':
                        raise ValueError('Content-Type must be application/json')
                    body = json.loads(self.rfile.read(size))
                    fields(body, body if isinstance(body, dict) else ())
                if is_audio:
                    from .audio_http import route
                    return route(self, audio, body)
                if service is None:
                    return self.send(404, {'error': 'Use the existing MIDI CLI/API entry for native MIDI'})
                if self.command == 'POST' and self.path == '/parse':
                    fields(body, ['text'], ['text'])
                    result = service.planner.parse_intent(**body)
                    BeatSpec.parse(result['spec'])
                    return self.send(200, result)
                if self.command == 'POST' and self.path == '/plan':
                    fields(body, ['text', 'track_id', 'start_bar', 'end_bar'], ['text', 'track_id', 'start_bar', 'end_bar'])
                    return self.send(200, EditPlan.parse(service.planner.plan_edit(**body)).to_dict())
                if self.command == 'POST' and self.path == '/projects':
                    fields(body, ['text', 'spec', 'protected_tracks'])
                    return self.send(201, service.create_project(**body))
                if self.command == 'POST' and self.path == '/creative/parse':
                    fields(body, ['text','reference_project_id','reference_version'], ['text'])
                    return self.send(200, service.parse_creative(**body))
                if self.command == 'POST' and self.path == '/creative/projects':
                    fields(body, ['text','spec','protected_tracks','reference_project_id','reference_version'], ['text'])
                    return self.send(201, service.create_creative_project(**body))
                match = re.fullmatch(r'/projects/([a-f0-9]{32})(?:/versions/([a-f0-9]{32})(?:/(midi|preview))?|/(edits|plan))?', self.path)
                if not match:
                    return self.send(404, {'error': 'Route not found'})
                pid, vid, artifact, edits = match.groups()
                if self.command == 'POST' and edits == 'plan':
                    fields(body, ['text','base_version','track_id','start_bar','end_bar','protected_tracks'], ['text'])
                    return self.send(200, service.plan_project_edit(pid, **body))
                if self.command == 'POST' and edits == 'edits':
                    fields(body, ['base_version', 'plan', 'protected_tracks'], ['base_version', 'plan'])
                    return self.send(201, service.edit_project(pid, **body))
                if self.command != 'GET' or edits:
                    return self.send(404, {'error': 'Route not found'})
                version = service.get_version(pid, vid)
                if artifact == 'midi':
                    return self.send(200, midi_bytes(version), 'audio/midi')
                if artifact == 'preview':
                    return self.send(200, preview_bytes(version), 'audio/wav')
                self.send(200, version)
            except AudioError as error:
                self.send(409, {'error': str(error)})
            except PlannerError as error:
                self.send(502, {'error': str(error)})
            except ConflictError as error:
                self.send(409, {'error': str(error)})
            except KeyError as error:
                self.send(404, {'error': str(error)})
            except (ValueError, TypeError, UnicodeError) as error:
                self.send(400, {'error': str(error)})
            except Exception:
                self.send(500, {'error': 'Internal error'})
    return Handler


def server(service, port=8765, audio=None, background_audio=True):
    class LocalServer(ThreadingHTTPServer):
        def serve_forever(self, poll_interval=0.5):
            if audio is not None and background_audio:
                audio.start()
            try:
                super().serve_forever(poll_interval)
            finally:
                if audio is not None and background_audio:
                    audio.close()
    return LocalServer(('127.0.0.1', port), handler(service, audio))
