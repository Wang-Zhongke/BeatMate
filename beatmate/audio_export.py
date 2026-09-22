"""Listening notes and portable exports of already-downloaded music."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import re
import tempfile
import uuid
import zipfile
from .audio_provider import AudioError

MAX_EXPORT = 512 * 1024 * 1024


def safe_name(value):
    text=re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', '_', value).strip(' .')[:70]
    return 'BeatMate-' + (text or '未命名作品')


class ExportMixin:
    def notes(self, aid):
        asset=self.asset(aid)
        if asset['source_audio_asset_id'] in self._purged_ids(): raise AudioError('作品已永久删除')
        with self.connect() as c:
            return [dict(id=r[0],asset_id=aid,seconds=r[1],text=r[2],created_at=r[3]) for r in
                    c.execute('SELECT id,seconds,text,created_at FROM audio_notes WHERE asset=? ORDER BY seconds,created_at',(aid,))]

    def update_note(self, aid, action, seconds=None, text=None, note_id=None):
        from .audio import now, identifier
        asset=self.asset(aid)
        if asset['source_audio_asset_id'] in self._purged_ids(): raise AudioError('作品已永久删除')
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if action=='add':
                if type(seconds) not in (float,int) or not math.isfinite(seconds) or not 0<=seconds<=asset['duration_seconds']:
                    raise ValueError('备注时间须在这条音轨的时长内')
                if not isinstance(text,str) or not text.strip() or len(text)>500 or '\x00' in text:
                    raise ValueError('备注须为 1–500 字符')
                if c.execute('SELECT COUNT(*) FROM audio_notes WHERE asset=?',(aid,)).fetchone()[0]>=500:
                    raise ValueError('每条音轨最多保存 500 条备注')
                c.execute('INSERT INTO audio_notes VALUES (?,?,?,?,?)',(uuid.uuid4().hex,aid,seconds,text.strip(),now()))
            elif action=='delete':
                identifier(note_id)
                if not c.execute('DELETE FROM audio_notes WHERE id=? AND asset=?',(note_id,aid)).rowcount:
                    raise KeyError('备注不存在')
            else: raise ValueError('无效备注操作')
        return self.notes(aid)

    @contextmanager
    def export_bundle(self, aid):
        """No remote retrieval. Unavailable items are explicit in the manifest."""
        with (self.root/'worker.lock').open('a') as lock:
            try: fcntl.flock(lock,fcntl.LOCK_SH|fcntl.LOCK_NB)
            except BlockingIOError: raise AudioError('正在处理音频，请稍后打包导出') from None
            source=self.asset(aid)
            if source['source_audio_asset_id']!=aid: raise ValueError('请从作品导出整套文件')
            if aid in self._purged_ids(): raise AudioError('作品已永久删除')
            with self.connect() as c:
                task=self._task(c,source['task_id'])
                meta=c.execute('SELECT title FROM audio_library WHERE id=?',(aid,)).fetchone()
                tracks=[json.loads(r[0]) for r in c.execute('SELECT data FROM audio_results WHERE task=?',(task['id'],))]
                row=c.execute('SELECT data FROM audio_stem_jobs WHERE id=?',(aid,)).fetchone()
            job=json.loads(row[0]) if row else {}
            title=(meta[0] if meta else None) or task.get('title') or '未命名作品'
            folder=safe_name(title)
            manifest=dict(title=title,work_id=aid,provider=source['provider'],model=source['requested_model'],
                          created_at=source['created_at'],files=[],unavailable=[],notes=[],
                          separation_status=job.get('status','not_requested'),midi_status=job.get('midi_status','not_requested'))
            total=0
            with tempfile.TemporaryFile() as output:
                with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_STORED) as archive:
                    def add(path,name,metadata):
                        nonlocal total
                        data=path.read_bytes()
                        if total+len(data)>MAX_EXPORT: raise AudioError('导出超过 512 MB，请分开下载音轨')
                        total+=len(data)
                        archive.writestr(folder+'/'+name,data)
                        manifest['files'].append(dict(path=name,bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),**metadata))
                    for track in tracks:
                        if track['source_audio_asset_id']!=aid: continue
                        try: path,a=self.file(track['id'])
                        except (AudioError,OSError):
                            manifest['unavailable'].append(dict(kind=track['track_type'],reason='本地音频缺失或校验未通过'));continue
                        add(path,track['track_type']+'.'+track['format'],dict(kind=track['track_type'],duration_seconds=track['duration_seconds'],format=track['format']))
                        manifest['notes'].extend(self.notes(track['id']))
                    if job.get('midi_status')=='ready':
                        try:
                            add(self.midi_file(aid),'midi/original.zip',dict(kind='midi_archive'))
                        except (AudioError,KeyError,OSError):
                            manifest['unavailable'].append(dict(kind='midi_archive',reason='本地 MIDI 包不可用'))
                        for item in job.get('midi_files',[]):
                            try: path=self.midi_file(aid,item['id'])
                            except (AudioError,KeyError,OSError):
                                manifest['unavailable'].append(dict(kind='midi',reason='本地 MIDI 文件不可用'));continue
                            add(path,'midi/'+safe_name(item['name'])+'-'+item['id'][:8]+'.mid',dict(kind='midi',original_name=item['name']))
                    elif job:
                        manifest['unavailable'].append(dict(kind='midi',reason='MIDI 尚未提供或尚未下载完成'))
                    if job and job.get('status')!='ready':
                        manifest['unavailable'].append(dict(kind='stems',reason='分轨尚未全部保存'))
                    if not manifest['files']: raise AudioError('尚无可导出的本地文件，请先恢复下载')
                    archive.writestr(folder+'/manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
                    archive.writestr(folder+'/创作描述.txt',task['brief'].get('raw_text',''))
                    if task['brief'].get('lyrics'): archive.writestr(folder+'/歌词.txt',task['brief']['lyrics'])
                    archive.writestr(folder+'/使用说明.txt','此包包含当前已保存且通过校验的原文件，未进行新的生成、分离或下载。\n缺失项目和试听备注见 manifest.json。\nMIDI 是音符转录，不含原始音色、效果器或混音；请在 DAW 中选择乐器。\nMock 文件仅为测试信号。\n')
                output.seek(0)
                yield output,folder+'.zip'
