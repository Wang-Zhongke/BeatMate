"""Durable single-worker audio MVP. Separate SQLite DB; never opens MIDI snapshots."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
import wave
from .audio_library import LibraryMixin, music_title
from .audio_health import RuntimeHealth, UI_VERSION
from .audio_export import ExportMixin
from .audio_catalog import CatalogMixin, install_catalog
from .audio_progress import task_progress
from .audio_input import prepare, workflow_source
from .audio_analysis import analyze, analysis_config
from .audio_stems import split_archive, MAX_STEM_ZIP
from .audio_midi import midi_archive, MAX_MIDI_ZIP, MAX_MIDI_FILE
from .llm import PlannerError
from .audio_provider import create_audio_adapter, AudioError, Rejected, BeforeSubmissionError, MAX_AUDIO
from .service import ConflictError


SAFE_AUDIO_ERRORS = {'Invalid provider task ID': '供应商查询结果的任务ID缺失或格式不符合契约', 'Provider task ID mismatch': '供应商返回的任务ID与已保存任务不一致', 'Unknown provider task status; query original task again': '供应商返回了未识别的任务状态', 'Invalid or empty provider candidates': '供应商已完成响应中候选列表为空或格式异常', 'Invalid or duplicate candidate ID': '候选ID缺失、重复或格式异常', 'Invalid candidate index': '候选序号index缺失或不是整数', 'Invalid provider duration': '供应商duration不是正整数毫秒', 'Invalid candidate URL': '候选下载地址字段格式异常', 'Invalid provider JSON response': '供应商返回的内容不是有效JSON对象', 'Provider changed completed candidate identity': '供应商返回的已完成候选身份发生变化', 'Network failure after request started; response not confirmed': 'HTTP请求已发出，但未完整收到响应；可能超时或连接中断', 'Response size invalid': '供应商响应长度不符合限制', 'Empty, oversized or incomplete response': '供应商响应为空、过大或不完整', 'Audio validation needs ffmpeg for non-PCM formats; original download will be retried': '当前音频格式需要系统解码器验证，原文件尚未保存', 'Audio decoder rejected incomplete or corrupt audio': '系统解码器拒绝了不完整或损坏的音频', 'Unsupported or corrupt audio; HTML is not audio': '下载内容不是受支持的音频，可能是错误页面'}


# Keep budget values exact across JSON/JavaScript and SQLite.
MAX_EXACT_BUDGET = 2**53 - 1

def now(): return datetime.now(timezone.utc).isoformat()
def encode(value): return json.dumps(value, ensure_ascii=False, sort_keys=True)
def sha(data): return hashlib.sha256(data).hexdigest()
def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32}', value): raise ValueError('Invalid local ID')
    return value


def inspect_audio(path):
    """Fully read PCM WAV; decode other genuine formats without changing the original."""
    data = path.read_bytes()
    if not data or len(data) > MAX_AUDIO: raise AudioError('Empty or oversized audio')
    if data[:4] == b'RIFF' and data[8:12] == b'WAVE':
        if int.from_bytes(data[4:8], 'little') + 8 != len(data): raise AudioError('Incomplete WAV container')
        offset=12
        while offset < len(data):
            if offset+8>len(data): raise AudioError('Incomplete WAV chunk header')
            size=int.from_bytes(data[offset+4:offset+8],'little')
            if offset+8+size>len(data): raise AudioError('Incomplete WAV chunk')
            offset+=8+size+(size%2)
        try:
            with wave.open(io.BytesIO(data), 'rb') as f:
                frames, rate = f.getnframes(), f.getframerate()
                if frames <= 0 or rate <= 0 or len(f.readframes(frames)) != frames*f.getnchannels()*f.getsampwidth():
                    raise ValueError()
                return 'wav', frames/rate
        except (EOFError, ValueError):
            raise AudioError('Incomplete WAV frames') from None
        except wave.Error as error:
            if 'unknown format' not in str(error): raise AudioError('Corrupt WAV') from None
            # Some vendor WAVs are float/extensible: use a real decoder if available.
            fmt = 'wav'
    elif data[:4] == b'fLaC': fmt = 'flac'
    elif data[:3] == b'ID3' or data[:2] in (b'\xff\xfb', b'\xff\xfa', b'\xff\xf3', b'\xff\xf2'): fmt = 'mp3'
    elif data[4:8] == b'ftyp': fmt = 'm4a'
    else: raise AudioError('Unsupported or corrupt audio; HTML is not audio')
    with tempfile.TemporaryDirectory() as tmp:
        decoded = Path(tmp)/'decoded.wav'
        if shutil.which('ffmpeg'):
            command = ['ffmpeg','-v','error','-xerror','-i',str(path),'-f','wav','-acodec','pcm_s16le',str(decoded)]
        elif Path('/usr/bin/afconvert').exists():
            command = ['/usr/bin/afconvert','-f','WAVE','-d','LEI16',str(path),str(decoded)]
        else: raise AudioError('Audio validation needs ffmpeg for non-PCM formats; original download will be retried')
        try:
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            with wave.open(str(decoded),'rb') as f:
                frames=f.getnframes()
                if frames<=0 or len(f.readframes(frames)) != frames*f.getnchannels()*f.getsampwidth(): raise ValueError()
                duration=frames/f.getframerate()
        except Exception: raise AudioError('Audio decoder rejected incomplete or corrupt audio') from None
    return fmt, duration


class AudioService(LibraryMixin, ExportMixin, CatalogMixin):
    def __init__(self, root='.beatmate/audio', adapter=None, env=None):
        self.root=Path(root).resolve(); self.root.mkdir(parents=True, exist_ok=True)
        self.assets=self.root/'assets'; self.assets.mkdir(exist_ok=True)
        self.db=str(self.root/'audio.sqlite3')
        self.env=os.environ if env is None else env
        self.adapter=adapter or create_audio_adapter(self.env)
        self.max_candidates=int(self.env.get('BEATMATE_AUDIO_MAX_CANDIDATES','1'))
        self.max_calls=int(self.env.get('BEATMATE_AUDIO_MAX_SUBMISSIONS','1'))
        if not 1<=self.max_candidates<=2: raise ValueError('Audio limits: candidates 1..2')
        if not 1<=self.max_calls<=MAX_EXACT_BUDGET: raise ValueError('累计请求上限须为可精确表示的正整数')
        self.default_mode=self.env.get('BEATMATE_AUDIO_PROMPT_MODE','direct')
        if self.default_mode not in ('direct','template'): raise ValueError('Invalid BEATMATE_AUDIO_PROMPT_MODE')
        self.stop_event=threading.Event(); self.thread=None
        self.health=RuntimeHealth()
        self._file_cache={}
        self._cache_lock=threading.Lock()
        with self.connect() as c:
            c.executescript('''
              CREATE TABLE IF NOT EXISTS audio_notes(id TEXT PRIMARY KEY, asset TEXT, seconds REAL, text TEXT, created_at TEXT);
              CREATE TABLE IF NOT EXISTS audio_task_library(id TEXT PRIMARY KEY, deleted_at TEXT);
              CREATE TABLE IF NOT EXISTS audio_library(id TEXT PRIMARY KEY, title TEXT, favorite INTEGER NOT NULL DEFAULT 0, deleted_at TEXT, purged INTEGER NOT NULL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS audio_settings(key TEXT PRIMARY KEY, value INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS audio_projects(id TEXT PRIMARY KEY, created_at TEXT, selected TEXT);
              CREATE TABLE IF NOT EXISTS audio_requests(id TEXT PRIMARY KEY, dedup TEXT UNIQUE, fingerprint TEXT, project TEXT, data TEXT);
              CREATE TABLE IF NOT EXISTS audio_tasks(id TEXT PRIMARY KEY, data TEXT);
              CREATE TABLE IF NOT EXISTS audio_results(id TEXT PRIMARY KEY, task TEXT, project TEXT, data TEXT);
              CREATE TABLE IF NOT EXISTS audio_analyses(id TEXT PRIMARY KEY, fingerprint TEXT, data TEXT);
              CREATE TABLE IF NOT EXISTS audio_stem_jobs(id TEXT PRIMARY KEY, task TEXT, data TEXT);
              CREATE TRIGGER IF NOT EXISTS request_update BEFORE UPDATE ON audio_requests BEGIN SELECT RAISE(ABORT,'immutable request'); END;
              CREATE TRIGGER IF NOT EXISTS request_delete BEFORE DELETE ON audio_requests BEGIN SELECT RAISE(ABORT,'immutable request'); END;
              CREATE TRIGGER IF NOT EXISTS result_update BEFORE UPDATE ON audio_results BEGIN SELECT RAISE(ABORT,'immutable result'); END;
              CREATE TRIGGER IF NOT EXISTS result_delete BEFORE DELETE ON audio_results BEGIN SELECT RAISE(ABORT,'immutable result'); END;
            ''')

            install_catalog(c)

    @contextmanager
    def connect(self):
        c=sqlite3.connect(self.db,timeout=15)
        try:
            with c: yield c
        finally: c.close()

    def _budget(self, c):
        row=c.execute("SELECT value FROM audio_settings WHERE key='max_submissions'").fetchone()
        limit=row[0] if row else self.max_calls
        requests=[json.loads(r[0]) for r in c.execute('SELECT data FROM audio_requests')]
        used=sum(t.get('paid_request_units',1) for t in requests if t['provider']=='mureka')
        return dict(max_submissions=limit, used_submissions=used,
                    remaining_submissions=max(0,limit-used),
                    budget_source='saved' if row else 'environment')

    def budget(self):
        with self.connect() as c: return self._budget(c)

    def set_budget(self, max_submissions):
        if type(max_submissions) is not int or not 1 <= max_submissions <= MAX_EXACT_BUDGET:
            raise ValueError('本地累计请求上限须为可精确表示的正整数')
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute("INSERT INTO audio_settings VALUES ('max_submissions',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(max_submissions,))
            return self._budget(c)

    def config(self):
        return dict(ui_version=UI_VERSION, runtime_revision=self.health.revision, diagnostics_version=3, provider=self.adapter.provider, model=self.adapter.model if not self.adapter.errors() else None,
                    service='https://api.mureka.ai' if self.adapter.provider=='mureka' else 'offline',
                    errors=self.adapter.errors(), configured=not self.adapter.errors(),
                    max_candidates=self.max_candidates,**self.budget(),prompt_mode=self.default_mode,
                    creation_modes=['instrumental', 'lyrics', 'lyrics_summary', 'song_stems'],
                    lyric_analysis=analysis_config(self.env,self.adapter.provider=='mock'),
                    stem_model='audio-separation-3',stem_midi_export=True,library_version=3,
                    account_access='unverified' if self.adapter.provider=='mureka' else 'not_applicable')

    def analyze_lyrics(self, raw_text, lyrics, request_id, constraints=None):
        identifier(request_id)
        source=workflow_source(raw_text,lyrics,constraints)
        fingerprint=sha(encode(source).encode())
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT fingerprint,data FROM audio_analyses WHERE id=?',(request_id,)).fetchone()
            if row:
                if row[0]!=fingerprint: raise ConflictError('分析请求ID已用于其他原文')
                result=json.loads(row[1])
                if result['status']=='ready': return result
                raise ConflictError('上次分析尚未确认或已失败，不会自动重发；可手动写摘要，或主动重新分析')
            c.execute('INSERT INTO audio_analyses VALUES (?,?,?)',(request_id,fingerprint,encode(dict(status='submitting',source=source))))
        try:
            result=dict(analyze(**source,env=self.env,offline=self.adapter.provider=='mock'),
                        id=request_id,status='ready',source=source,created_at=now())
        except Exception:
            with self.connect() as c:
                c.execute('UPDATE audio_analyses SET data=? WHERE id=?',(encode(dict(status='failed',source=source)),request_id))
            raise PlannerError('歌词分析未完成，请检查DeepSeek配置或网络。不会自动重发；原文保留，可手动编写摘要。') from None
        with self.connect() as c: c.execute('UPDATE audio_analyses SET data=? WHERE id=?',(encode(result),request_id))
        return result

    def preview(self, raw_text, prompt_mode=None, constraints=None, creation_mode=None, lyrics='', arrangement_summary='', analysis_id=None):
        brief=prepare(raw_text, prompt_mode or self.default_mode, constraints, creation_mode, lyrics, arrangement_summary, analysis_id)
        if analysis_id:
            with self.connect() as c: row=c.execute('SELECT fingerprint,data FROM audio_analyses WHERE id=?',(analysis_id,)).fetchone()
            source=workflow_source(raw_text,lyrics,constraints)
            if not row or row[0]!=sha(encode(source).encode()) or json.loads(row[1])['status']!='ready':
                raise ValueError('歌词或制作要求已改变，请重新分析，或改为手动摘要')
        return brief

    def create(self, raw_text, request_id, prompt_mode=None, constraints=None, n=1, project_id=None,
               reviewed=False, source_audio_asset_id=None, creation_mode=None, lyrics='', arrangement_summary='', analysis_id=None, title=''):
        identifier(request_id)
        title=music_title(title)
        if type(n) is not int or not 1<=n<=self.max_candidates: raise ValueError('Candidate count exceeds configured limit')
        brief=self.preview(raw_text,prompt_mode,constraints,creation_mode,lyrics,arrangement_summary,analysis_id)
        paid_units=1+n if creation_mode=='song_stems' else 1
        if brief['detected_conflict']: raise ValueError('原文与选项中的BPM冲突，请先修改')
        if brief['requires_review'] and reviewed is not True: raise ValueError('请核对原文与选项的潜在冲突，并确认最终描述')
        body=dict(brief=brief,n=n,project_id=project_id,source_audio_asset_id=source_audio_asset_id)
        if title: body['title']=title
        fingerprint=sha(encode(body).encode())
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            old=c.execute('SELECT id,fingerprint FROM audio_requests WHERE dedup=?',(request_id,)).fetchone()
            if old:
                if old[1]!=fingerprint: raise ConflictError('Request ID reused with different content')
                return self._task(c,old[0])
            if self.adapter.errors(): raise ValueError('; '.join(self.adapter.errors()))
            if self.adapter.provider=='mureka':
                budget=self._budget(c)
                if budget['remaining_submissions']<paid_units:
                    raise ValueError(f"本地额度不足：已占用{budget['used_submissions']} / 上限{budget['max_submissions']}；本流程需要{paid_units}次（含预留分离）。请在调用额度设置调整；本次未提交给Mureka。")
            if project_id:
                identifier(project_id)
                if not c.execute('SELECT 1 FROM audio_projects WHERE id=?',(project_id,)).fetchone(): raise KeyError('Audio project not found')
            else:
                project_id=uuid.uuid4().hex
                c.execute('INSERT INTO audio_projects VALUES (?,?,NULL)',(project_id,now()))
            source_hash=None
            if source_audio_asset_id:
                row=c.execute('SELECT data FROM audio_results WHERE id=? AND project=?',(identifier(source_audio_asset_id),project_id)).fetchone()
                if not row: raise ValueError('Reference asset must belong to the audio project')
                source_hash=json.loads(row[0])['sha256']
            task=dict(id=uuid.uuid4().hex,request_id=request_id,project_id=project_id,brief=brief,n=n,
                      provider=self.adapter.provider,requested_model=self.adapter.model,service=getattr(self.adapter,'base',None),
                      returned_model=None,provider_task_id=None,created_at=now(),status='queued',
                      source_audio_asset_id=source_audio_asset_id,source_sha256=source_hash,
                      reference_semantics='lineage_only_no_audio_conditioning',actual_cost={'status':'unknown'},
                      error=None,attempted=False,next_attempt=0,failures=0)
            if title: task['title']=title
            if creation_mode=='song_stems': task['paid_request_units']=paid_units
            c.execute('INSERT INTO audio_requests VALUES (?,?,?,?,?)',(task['id'],request_id,fingerprint,project_id,encode(task)))
            c.execute('INSERT INTO audio_tasks VALUES (?,?)',(task['id'],encode(task)))
        return task

    def _task(self,c,tid):
        row=c.execute('SELECT data FROM audio_tasks WHERE id=?',(identifier(tid),)).fetchone()
        if not row: raise KeyError('Audio task not found')
        return json.loads(row[0])

    def get(self,tid):
        with self.connect() as c: task=self._task(c,tid)
        task.pop('choices',None) # Temporary provider URLs never need to reach the browser.
        if task['status']=='ready':
            with self.connect() as c:
                aids=[a['id'] for (data,) in c.execute('SELECT data FROM audio_results WHERE task=?',(tid,))
                      if (a:=json.loads(data))['source_audio_asset_id']==a['id'] and a['id'] not in self._purged_ids()]
            try:
                for aid in aids: self.file(aid,cached=True)
            except (AudioError,OSError):
                task.update(status='downloading',error='Local asset missing or damaged; restoring original bytes')
        task['progress']=task_progress(task)
        return task

    def save(self,task):
        with self.connect() as c: c.execute('UPDATE audio_tasks SET data=? WHERE id=?',(encode(task),task['id']))

    def history(self,source_ids=None,extra_task_ids=None):
        bounded=source_ids is not None
        with self.connect() as c:
            if bounded:
                ids=source_ids or []
                placeholders=','.join('?' for _ in ids) or 'NULL'
                results=[json.loads(r[0]) for r in c.execute(f"SELECT data FROM audio_results WHERE json_extract(data,'$.source_audio_asset_id') IN ({placeholders}) ORDER BY rowid",ids)]
                tids=list(dict.fromkeys([a['task_id'] for a in results]+(extra_task_ids or [])))
                marks=','.join('?' for _ in tids) or 'NULL'
                tasks=[json.loads(r[0]) for r in c.execute(f'SELECT data FROM audio_tasks WHERE id IN ({marks}) ORDER BY rowid DESC',tids)]
                pids=list(dict.fromkeys(t['project_id'] for t in tasks))
                marks=','.join('?' for _ in pids) or 'NULL'
                projects=[dict(id=r[0],created_at=r[1],selected_audio_asset_id=r[2]) for r in c.execute(f'SELECT * FROM audio_projects WHERE id IN ({marks}) ORDER BY created_at DESC',pids)]
                for result in results:
                    if result['id']==result['source_audio_asset_id']:
                        result['version_count']=c.execute("SELECT COUNT(*) FROM audio_results WHERE task=? AND json_extract(data,'$.source_audio_asset_id')=id",(result['task_id'],)).fetchone()[0]
            else:
                projects=[dict(id=r[0],created_at=r[1],selected_audio_asset_id=r[2]) for r in c.execute('SELECT * FROM audio_projects ORDER BY created_at DESC')]
                tasks=[json.loads(r[0]) for r in c.execute('SELECT data FROM audio_tasks ORDER BY rowid DESC')]
                results=[json.loads(r[0]) for r in c.execute('SELECT data FROM audio_results ORDER BY rowid')]
        purged=self._purged_ids()
        original_tasks={a['task_id'] for a in results if a['source_audio_asset_id']==a['id']}
        results=[a for a in results if a['source_audio_asset_id'] not in purged]
        active_tasks={a['task_id'] for a in results}
        tasks=[t for t in tasks if t['id'] not in original_tasks or t['id'] in active_tasks]
        with self.connect() as c:
            task_deletions=dict(c.execute('SELECT id,deleted_at FROM audio_task_library'))
        for t in tasks:
            t['deleted_at']=task_deletions.get(t['id'])
            t.pop('choices',None)
        for result in results:
            try: self.file(result['id'],cached=True); result['available']=True
            except (AudioError, OSError): result['available']=False
        for task in tasks:
            if task['status']=='ready' and any(a['task_id']==task['id'] and a['source_audio_asset_id']==a['id'] and not a['available'] for a in results):
                task.update(status='downloading',error='Local asset missing or damaged; restoring original bytes')
        with self.connect() as c:
            stems=[json.loads(r[0]) for r in c.execute(f'SELECT data FROM audio_stem_jobs WHERE id IN ({placeholders})',ids)] if bounded else [json.loads(r[0]) for r in c.execute('SELECT data FROM audio_stem_jobs')]
        stems=[j for j in stems if j['id'] not in purged]
        for job in stems:
            job.pop('zip_url',None)
            job.pop('midi_zip_url',None)
            if job.get('midi_status')=='ready':
                try:
                    self.midi_file(job['id'])
                    for item in job.get('midi_files',[]): self.midi_file(job['id'],item['id'])
                except (KeyError,AudioError,OSError):
                    job.update(midi_status='downloading',midi_error='本地MIDI缺失或损坏，正在恢复原文件。')
            if job['status']=='ready' and any(a['source_audio_asset_id']==job['source_asset_id'] and not a['available'] for a in results):
                job.update(status='downloading',error='本地分轨缺失或损坏，正在从原分离文件包恢复。')
        with self.connect() as c:
            library=[dict(id=r[0],title=r[1],favorite=bool(r[2]),deleted_at=r[3]) for r in c.execute('SELECT id,title,favorite,deleted_at FROM audio_library WHERE purged=0'+(f' AND id IN ({placeholders})' if bounded else ''),ids if bounded else [])]
        for task in tasks: task['progress']=task_progress(task)
        return dict(projects=projects,tasks=tasks,assets=results,stems=stems,library=library)

    def asset(self,aid):
        with self.connect() as c:
            row=c.execute('SELECT data FROM audio_results WHERE id=?',(identifier(aid),)).fetchone()
        if not row: raise KeyError('Audio asset not found')
        return json.loads(row[0])

    def file(self,aid, cached=False):
        a=self.asset(aid)
        if a['source_audio_asset_id'] in self._purged_ids(): raise AudioError('作品已永久删除')
        path=self.assets/a['file']
        if path.is_symlink() or path.resolve().parent!=self.assets or not path.is_file(): raise AudioError('Local audio missing or unsafe')
        stat=path.stat()
        signature=(stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns,a['sha256'])
        with self._cache_lock:
            valid=cached and self._file_cache.get(aid)==signature
        if not valid:
            if stat.st_size>MAX_AUDIO or sha(path.read_bytes())!=a['sha256']: raise AudioError('Local audio hash mismatch')
            with self._cache_lock:
                if len(self._file_cache)>2048: self._file_cache.clear()
                self._file_cache[aid]=signature
        return path,a

    def select(self,project_id,asset_id):
        _,a=self.file(asset_id)
        if a['project_id']!=identifier(project_id): raise ValueError('Asset belongs to another project')
        with self.connect() as c: c.execute('UPDATE audio_projects SET selected=? WHERE id=?',(asset_id,project_id))
        return dict(project_id=project_id,selected_audio_asset_id=asset_id)

    def retry(self,tid):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE'); t=self._task(c,tid)
            if t['status'] not in ('generating','downloading'): raise ValueError('Only query/download can be retried; uncertain submissions cannot be resent')
            t['next_attempt']=0
            c.execute('UPDATE audio_tasks SET data=? WHERE id=?',(encode(t),tid))
        return self.get(tid)

    def _response(self,t,r):
        # Only structural facts; never persist arbitrary response bodies, URLs or secrets.
        if isinstance(r,dict):
            choices=r.get('choices')
            status=r.get('status')
            t['response_summary']={
                'status':status if status in ('preparing','queued','running','streaming','succeeded','failed','timeouted','cancelled') else 'unrecognized',
                'id_type':type(r.get('id')).__name__,
                'choice_count':len(choices) if isinstance(choices,list) else None,
                'candidate_fields':[{k:type(ch.get(k)).__name__ for k in ('id','index','duration','wav_url','flac_url','url')} for ch in choices[:3] if isinstance(ch,dict)] if isinstance(choices,list) else [],
            }
        if not isinstance(r,dict) or not isinstance(r.get('id'),str) or not re.fullmatch(r'[\w-]{1,160}',r['id']): raise AudioError('Invalid provider task ID')
        if t['provider_task_id'] and t['provider_task_id']!=r['id']: raise AudioError('Provider task ID mismatch')
        t['provider_task_id']=r['id']
        self.save(t) # Preserve confirmed ID even if later response validation fails.
        if isinstance(r.get('model'),str) and re.fullmatch(r'[\w.-]{1,80}',r['model']): t['returned_model']=r['model']
        for field in ('created_at','finished_at'):
            if type(r.get(field)) is int: t['provider_'+field]=r[field]
        status=r.get('status')
        if status in ('failed','timeouted','cancelled'):
            t.update(status='failed',error='Provider task '+status); return
        if status in ('preparing','queued','running','streaming'):
            if t['status']!='downloading': t['status']='generating'
            return
        if status!='succeeded': raise AudioError('Unknown provider task status; query original task again')
        choices=r.get('choices')
        if not isinstance(choices,list) or not 1<=len(choices)<=3: raise AudioError('Invalid or empty provider candidates')
        cleaned=[]; ids=set()
        for position,ch in enumerate(choices):
            if not isinstance(ch,dict) or not isinstance(ch.get('id'),str) or not re.fullmatch(r'[\w-]{1,160}',ch['id']) or ch['id'] in ids:
                raise AudioError('Invalid or duplicate candidate ID')
            ids.add(ch['id'])
            if 'index' in ch and type(ch['index']) is not int: raise AudioError('Invalid candidate index')
            item={k:ch[k] for k in ('id','index','duration','wav_url','flac_url','url') if k in ch}
            item['index']=ch.get('index',position)
            item['index_source']='provider' if 'index' in ch else 'response_order'
            if 'duration' in item and (type(item['duration']) is not int or item['duration']<=0): raise AudioError('Invalid provider duration')
            if any(not isinstance(item[k],str) or len(item[k])>8192 for k in ('wav_url','flac_url','url') if k in item): raise AudioError('Invalid candidate URL')
            cleaned.append(item)
        if t.get('choices') and {x['id'] for x in t['choices']}!=ids: raise AudioError('Provider changed completed candidate identity')
        t.update(status='downloading',choices=cleaned)

    def _store_audio(self,t,choice,data,aid,track_type,source_asset=None):
        try:
            self.file(aid); return self.asset(aid)
        except KeyError: existing=None
        except (AudioError,OSError): existing=self.asset(aid)
        if not isinstance(data,bytes) or not data or len(data)>MAX_AUDIO: raise AudioError('Invalid audio download')
        fd,name=tempfile.mkstemp(prefix='.download-',dir=self.assets)
        temp=Path(name)
        try:
            with os.fdopen(fd,'wb') as f: f.write(data); f.flush(); os.fsync(f.fileno())
            fmt,duration=inspect_audio(temp); digest=sha(data)
            if existing and existing['sha256']!=digest: raise AudioError('Downloaded bytes changed; immutable source cannot be replaced')
            filename=digest+'.'+fmt; target=self.assets/filename
            if target.is_symlink(): raise AudioError('Unsafe asset target')
            if target.exists() and sha(target.read_bytes())==digest: temp.unlink()
            else: os.replace(temp,target)
            target.chmod(0o444)
            asset=dict(id=aid,source_audio_asset_id=source_asset or aid,task_id=t['id'],project_id=t['project_id'],
                       provider=t['provider'],requested_model=t['requested_model'],returned_model=t['returned_model'],
                       provider_task_id=t['provider_task_id'],provider_candidate_id=choice['id'],index=choice['index'],
                       index_source=choice.get('index_source','provider'),track_type=track_type,
                       created_at=now(),file=filename,format=fmt,sha256=digest,duration_seconds=duration,
                       provider_duration_ms=choice.get('duration'),measured_bpm=None,measured_key=None,
                       actual_cost={'status':'unknown'},source='synthetic_test_signal' if t['provider']=='mock' else 'mureka_stem' if source_asset else 'mureka_original',
                       source_url_field='stem_zip' if source_asset else 'wav_url' if choice.get('wav_url') else 'flac_url' if choice.get('flac_url') else 'url')
            if source_asset: asset['separation_model']='audio-separation-3'
            if not existing:
                with self.connect() as c: c.execute('INSERT INTO audio_results VALUES (?,?,?,?)',(aid,t['id'],t['project_id'],encode(asset)))
            return existing or asset
        finally:
            if temp.exists(): temp.unlink()

    def _download(self,t):
        song=t['brief'].get('creation_mode')=='song_stems'
        for position,choice in enumerate(t['choices']):
            aid=uuid.uuid5(uuid.NAMESPACE_URL,t['id']+':'+choice['id']).hex
            if aid in self._purged_ids(): continue
            try: self.file(aid)
            except (KeyError,AudioError,OSError):
                self._store_audio(t,choice,self.adapter.download(choice),aid,'mix' if song else 'instrumental')
            if song:
                job=dict(id=aid,task_id=t['id'],source_asset_id=aid,choice_id=choice['id'],index=choice['index'],
                         provider=t['provider'],service=t['service'],status='queued' if position<t['n'] else 'skipped',next_attempt=0,failures=0,
                         error=None if position<t['n'] else '供应商返回了额外候选，未预留其分离额度；已保留整曲，未自动付费分离。',
                         model='audio-separation-3',created_at=now())
                with self.connect() as c: c.execute('INSERT OR IGNORE INTO audio_stem_jobs VALUES (?,?,?)',(aid,t['id'],encode(job)))
        t.update(status='ready',error=None,completed_at=now())

    def _save_stem(self,job):
        with self.connect() as c: c.execute('UPDATE audio_stem_jobs SET data=? WHERE id=?',(encode(job),job['id']))

    def _stem_archive(self,job,midi=False):
        limit=MAX_MIDI_ZIP if midi else MAX_STEM_ZIP
        hash_key='midi_zip_sha256' if midi else 'zip_sha256'
        path=self.root/(('midi-stem-' if midi else 'stem-')+job['id']+'.zip')
        if path.is_symlink(): raise AudioError('分离文件包路径不安全')
        if path.is_file() and path.stat().st_size<=limit:
            data=path.read_bytes()
            if job.get(hash_key)==sha(data): return data
        data=self.adapter.download_midi(job) if midi else self.adapter.download_stems(job)
        if not isinstance(data,bytes) or not 0<len(data)<=limit: raise AudioError('分离文件包为空或过大')
        digest=sha(data)
        if job.get(hash_key) and job[hash_key]!=digest: raise AudioError('分离文件包发生变化，不能覆盖原文件')
        fd,name=tempfile.mkstemp(prefix='.stem-',dir=self.root)
        try:
            with os.fdopen(fd,'wb') as f: f.write(data); f.flush(); os.fsync(f.fileno())
            os.replace(name,path)
        finally:
            if os.path.exists(name): os.unlink(name)
        job[hash_key]=digest
        self._save_stem(job)
        return data

    def retry_stems(self,aid):
        identifier(aid)
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT data FROM audio_stem_jobs WHERE id=?',(aid,)).fetchone()
            if not row: raise KeyError('分离任务不存在')
            job=json.loads(row[0])
            if job['status']!='downloading': raise ValueError('仅可恢复原分离文件下载；不会重新调用付费分离')
            job['next_attempt']=0
            c.execute('UPDATE audio_stem_jobs SET data=? WHERE id=?',(encode(job),aid))
        return {'status':'downloading'}

    def _tick_stems(self):
        with self.connect() as c: jobs=[json.loads(r[0]) for r in c.execute('SELECT data FROM audio_stem_jobs')]
        for job in jobs:
            if job['id'] in self._purged_ids(): continue
            if job['provider']!=self.adapter.provider or job['service']!=getattr(self.adapter,'base',None): continue
            if job['status']=='submitting':
                job.update(status='uncertain',error='分离提交结果未确认，不会自动重发；整曲仍可下载。')
                self._save_stem(job)
            if job['status']=='ready':
                try:
                    for role in ('instrumental','vocals'):
                        self.file(uuid.uuid5(uuid.NAMESPACE_URL,job['id']+':'+role).hex,cached=True)
                except (KeyError,AudioError,OSError):
                    job.update(status='downloading',next_attempt=0)
            if job['status'] not in ('queued','downloading') or job['next_attempt']>time.time(): continue
            if self.adapter.errors(): continue
            operation=job['status']
            job['last_attempt_at']=now(); self._save_stem(job)
            try:
                with self.connect() as c: task=self._task(c,job['task_id'])
                if operation=='queued':
                    choice=next((c for c in task.get('choices',[]) if c['id']==job['choice_id']),None)
                    if choice is None: raise BeforeSubmissionError('target_validation')
                    # Checkpoint BEFORE paid POST; process death can never reissue it.
                    job.update(status='submitting',submitted_at=now()); self._save_stem(job)
                    result=self.adapter.separate(choice)
                    job['response_received_at']=now()
                    if not isinstance(result,dict) or not isinstance(result.get('zip_url'),str) or not 0<len(result['zip_url'])<=8192:
                        raise AudioError('Invalid stem response')
                    job.update(status='downloading',zip_url=result['zip_url'])
                    midi_url=result.get('midi_zip_url')
                    if isinstance(midi_url,str) and 0<len(midi_url)<=8192:
                        job.update(midi_zip_url=midi_url,midi_status='downloading',midi_next_attempt=0)
                    else:
                        job.update(midi_status='unavailable',midi_error='供应商本次未返回可用的MIDI文件地址。')
                    if type(result.get('expires_at')) is int: job['expires_at']=result['expires_at']
                    self._save_stem(job)
                tracks=split_archive(self._stem_archive(job))
                source=self.asset(job['source_asset_id'])
                for role,data in tracks.items():
                    aid=uuid.uuid5(uuid.NAMESPACE_URL,job['id']+':'+role).hex
                    self._store_audio(task,dict(id=job['choice_id'],index=job['index'],duration=source.get('provider_duration_ms')),
                                      data,aid,role,source_asset=source['id'])
                job.update(status='ready',error=None,completed_at=now(),failures=0)
            except Exception as error:
                if job['status'] in ('queued','submitting'):
                    job['status']='failed' if isinstance(error,(Rejected,BeforeSubmissionError)) else 'uncertain'
                job['failures']+=1
                job['failed_at']=now()
                job['provider_http_status']=error.status if isinstance(error,Rejected) else None
                job['failure_code']=(error.code if isinstance(error,BeforeSubmissionError) else
                    'http_rejected' if isinstance(error,Rejected) else {
                        'Network timeout after request started; response not confirmed':'network_timeout',
                        'Network failure after request started; response not confirmed':'network_failure',
                        'Invalid stem response':'invalid_stem_response',
                        'Invalid provider JSON response':'invalid_json',
                    }.get(str(error),'unclassified'))
                job['error']=('分离提交未确认，不会自动重发；整曲仍保留。' if job['status']=='uncertain' else
                              '分离提交失败，请核对模型权限、网络和额度；不会自动重发。' if job['status']=='failed' else
                              '伴奏/人声文件下载或校验未完成；仅恢复原文件，不会重新生成或付费分离。')
                if job['failure_code']=='network_timeout' and job['status']=='uncertain':
                    job['error']='等待分离响应超时，供应商可能已扣费；未收到下载地址，不能自动重发。请联系供应商找回原分离结果。'
                job['next_attempt']=time.time()+min(300,5*2**min(job['failures'],6))
            self._save_stem(job)

    def _midi_job(self, aid):
        with self.connect() as c:
            row=c.execute('SELECT data FROM audio_stem_jobs WHERE id=?',(identifier(aid),)).fetchone()
        if not row: raise KeyError('分离任务不存在')
        return json.loads(row[0])

    def midi_file(self, aid, file_id=None):
        job=self._midi_job(aid)
        if aid in self._purged_ids(): raise AudioError('作品已永久删除')
        if job.get('midi_status')!='ready': raise AudioError('MIDI尚未保存，请查看下载状态')
        if file_id is None:
            path=self.root/('midi-stem-'+job['id']+'.zip')
            info=dict(sha256=job['midi_zip_sha256'],size_limit=MAX_MIDI_ZIP)
        else:
            identifier(file_id)
            info=next((f for f in job.get('midi_files',[]) if f['id']==file_id),None)
            if info is None: raise KeyError('该候选没有此MIDI文件')
            path=self.assets/info['file']
            if path.resolve().parent!=self.assets: raise AudioError('MIDI文件路径不安全')
        if path.is_symlink() or not path.is_file() or path.stat().st_size>info.get('size_limit',MAX_MIDI_FILE):
            raise AudioError('MIDI文件缺失或不安全')
        if sha(path.read_bytes())!=info['sha256']: raise AudioError('MIDI文件校验失败')
        return path

    def retry_midi(self, aid):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT data FROM audio_stem_jobs WHERE id=?',(identifier(aid),)).fetchone()
            if not row: raise KeyError('分离任务不存在')
            job=json.loads(row[0])
            if not job.get('midi_zip_url') or job.get('midi_status') not in ('downloading','ready'):
                raise ValueError('没有可恢复的MIDI下载地址；不会重新付费分离')
            job.update(midi_status='downloading',midi_next_attempt=0)
            c.execute('UPDATE audio_stem_jobs SET data=? WHERE id=?',(encode(job),aid))
        return {'midi_status':'downloading'}

    def _tick_midi(self):
        with self.connect() as c: jobs=[json.loads(r[0]) for r in c.execute('SELECT data FROM audio_stem_jobs')]
        for job in jobs:
            if job['id'] in self._purged_ids(): continue
            if job['provider']!=self.adapter.provider or job['service']!=getattr(self.adapter,'base',None): continue
            if not job.get('midi_status'):
                if job.get('midi_zip_url'): job.update(midi_status='downloading',midi_next_attempt=0)
                elif job.get('zip_url'):
                    job.update(midi_status='unavailable',midi_error='旧分离结果未保存MIDI地址，无法补下载；不会自动重新付费分离。')
                else: continue
                self._save_stem(job)
            if job['midi_status']=='ready':
                try:
                    self.midi_file(job['id'])
                    for item in job['midi_files']: self.midi_file(job['id'],item['id'])
                    continue
                except (KeyError,AudioError,OSError): job.update(midi_status='downloading',midi_next_attempt=0)
            if job['midi_status']!='downloading' or job.get('midi_next_attempt',0)>time.time(): continue
            job['midi_last_attempt_at']=now(); self._save_stem(job)
            try:
                files=midi_archive(self._stem_archive(job,midi=True))
                manifest=[]
                for name,data in files:
                    digest=sha(data)
                    fid=uuid.uuid5(uuid.NAMESPACE_URL,job['id']+':midi:'+name).hex
                    old=next((f for f in job.get('midi_files',[]) if f['id']==fid),None)
                    if old and old['sha256']!=digest: raise AudioError('MIDI内容发生变化')
                    path=self.assets/(digest+'.mid')
                    if path.is_symlink(): raise AudioError('MIDI文件路径不安全')
                    fd,temp=tempfile.mkstemp(prefix='.midi-',dir=self.assets)
                    try:
                        with os.fdopen(fd,'wb') as f: f.write(data); f.flush(); os.fsync(f.fileno())
                        os.replace(temp,path)
                    finally:
                        if os.path.exists(temp): os.unlink(temp)
                    manifest.append(dict(id=fid,name=name,file=path.name,sha256=digest,bytes=len(data)))
                job.update(midi_files=manifest,midi_status='ready',midi_error=None,midi_failures=0,midi_completed_at=now())
            except Exception:
                failures=job.get('midi_failures',0)+1
                job.update(midi_status='downloading',midi_failures=failures,
                           midi_error='MIDI下载或校验未完成，请检查网络、下载域名或文件格式；只恢复下载，不会重新付费分离。',
                           midi_next_attempt=time.time()+min(300,5*2**min(failures,6)))
            self._save_stem(job)

    def tick(self):
        # Cross-process flock covers submit + response persistence, query and download.
        with (self.root/'worker.lock').open('a') as lock:
            try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: return
            with self.connect() as c: tasks=[json.loads(r[0]) for r in c.execute('SELECT data FROM audio_tasks')]
            for t in tasks:
                if t['status']=='ready':
                    with self.connect() as c:
                        aids=[a['id'] for (data,) in c.execute('SELECT data FROM audio_results WHERE task=?',(t['id'],))
                              if (a:=json.loads(data))['source_audio_asset_id']==a['id'] and a['id'] not in self._purged_ids()]
                    try:
                        for aid in aids: self.file(aid,cached=True)
                    except (AudioError,OSError):
                        t.update(status='downloading',next_attempt=0,failures=1)
                        self.save(t)
                if t['status']=='submitting':
                    t.update(status='generating' if t['provider_task_id'] else 'uncertain',
                             error='Worker stopped during submit; never automatically submit again'); self.save(t)
                if t['status'] not in ('queued','generating','downloading') or t['next_attempt']>time.time(): continue
                if t['provider']!=self.adapter.provider or t['service']!=getattr(self.adapter,'base',None): continue
                if self.adapter.errors():
                    t.update(error='Provider configuration unavailable; restore backend configuration',next_attempt=time.time()+30); self.save(t); continue
                operation=t['status']
                t['last_attempt_at']=now(); self.save(t)
                try:
                    if operation=='queued':
                        t.update(status='submitting',attempted=True); self.save(t)
                        self._response(t,self.adapter.submit(t))
                    elif operation=='generating': self._response(t,self.adapter.query(t))
                    else:
                        if t.get('failures',0): self._response(t,self.adapter.query(t))
                        if t['status']=='downloading': self._download(t)
                    t['failures']=0
                    if t['status'] not in ('failed','uncertain'): t['error']=None
                    t['next_attempt']=time.time()+(0 if self.adapter.provider=='mock' else 5)
                except Exception as error:
                    if operation=='queued': t['status']='generating' if t['provider_task_id'] else 'failed' if isinstance(error,(Rejected,BeforeSubmissionError)) else 'uncertain'
                    t['failures']+=1
                    t['provider_http_status']=error.status if isinstance(error,Rejected) else None
                    t['failure_code']=error.code if isinstance(error,BeforeSubmissionError) else None
                    # Never persist arbitrary exception/response strings (may contain credentials).
                    t['error']=('提交已被供应商拒绝，请检查账户权限、模型或额度' if isinstance(error,Rejected) and operation=='queued' else
                                '提交结果不确定：禁止自动重发，请核对供应商账户' if t['status']=='uncertain' else
                                '查询或下载未完成；将恢复原任务，请检查网络、下载域名或音频文件格式')
                    detail=SAFE_AUDIO_ERRORS.get(str(error)) if isinstance(error,AudioError) else None
                    if detail:
                        t['error'] += '；具体原因：' + detail
                    if isinstance(error,Rejected):
                        t['error'] += f'（HTTP {error.status}）'
                    if isinstance(error,BeforeSubmissionError):
                        detail={
                            'tls_certificate':'TLS证书校验失败，请配置可信CA（SSL_CERT_FILE）',
                            'dns_resolution':'无法解析服务域名，请检查DNS或网络连接',
                            'tcp_connect':'无法连接服务端口，请检查网络、防火墙或代理连接方式',
                            'tls_handshake':'TLS安全连接建立失败，请检查网络和可信证书配置',
                            'connection_setup':'DNS/TCP/TLS连接建立失败，请检查网络连接',
                            'target_validation':'网络目标校验未通过，请检查官方地址或下载域名配置',
                        }.get(error.code,'网络连接建立失败')
                        t['failure_operation']=operation
                        t['error']=(('生成请求尚未发送：'+detail+'。本次没有向供应商提交生成，可检查网络后重新创作。')
                                    if operation=='queued' else
                                    ('原生成任务已提交，本次'+('进度查询' if operation=='generating' else '查询或下载')+
                                     '连接失败：'+detail+'。将继续恢复原任务，请勿重复生成。'))
                    t['next_attempt']=time.time()+min(300,5*2**min(t['failures'],6))
                self.save(t)
            self._tick_stems()
            self._tick_midi()

    def start(self):
        if self.thread and self.thread.is_alive(): return
        def run():
            while not self.stop_event.is_set():
                try: self.tick()
                except Exception: pass # Next tick recovers from durable state; never log secrets.
                self.stop_event.wait(1)
        self.thread=threading.Thread(target=run,daemon=True); self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread: self.thread.join(timeout=2)
