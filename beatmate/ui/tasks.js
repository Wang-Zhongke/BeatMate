import { $, el, button, states, displayDate } from './common.js';

export function recoveryDetail(task, job) {
  const detail = el('details', null, 'recovery-detail');
  detail.dataset.progress = job?.id || task.id;
  detail.append(el('summary', '进度与恢复信息'));
  const p = task.progress;
  if (p) {
    detail.append(
      el('p', '当前步骤：' + p.stage),
      el('p', '已确认：' + p.last_success),
      el('p', p.next_action),
    );
    if (p.last_attempt_at) detail.append(el('p', '最近尝试：' + displayDate(p.last_attempt_at)));
  }
  detail.append(el('p', '本地任务：' + task.id, 'task-id'));
  detail.append(el('p', '供应商任务：' + (task.provider_task_id || '尚未取得'), 'task-id'));
  if (job) {
    const steps = {
      queued: '等待分离',
      submitting: '分离响应等待中',
      downloading: '保存分轨',
      ready: '分轨已保存',
      failed: '分离已停止',
      uncertain: '分离待核对',
      skipped: '未请求分离',
    };
    detail.append(el('p', '分离：' + (steps[job.status] || job.status)));
    if (job.response_received_at)
      detail.append(el('p', '已收到分离响应：' + displayDate(job.response_received_at)));
    if (job.last_attempt_at)
      detail.append(el('p', '分轨最近尝试：' + displayDate(job.last_attempt_at)));
    if (job.midi_status)
      detail.append(
        el(
          'p',
          'MIDI：' +
            ({ ready: '已保存', downloading: '下载待完成', unavailable: '未提供' }[
              job.midi_status
            ] || job.midi_status),
        ),
      );
    if (job.midi_last_attempt_at)
      detail.append(el('p', 'MIDI 最近尝试：' + displayDate(job.midi_last_attempt_at)));
  }
  return detail;
}

export function renderTasks({
  historyData,
  view,
  filter,
  search,
  cfg,
  recover,
  libraryAction,
  toast,
  fail,
}) {
  const region = $('task-region');
  region.replaceChildren();
  let count = 0;
  for (const task of historyData.tasks) {
    if (historyData.activity && !historyData.activity.ids.includes(task.id)) continue;
    const assets = historyData.assets.filter((a) => a.task_id === task.id);
    const jobs = historyData.stems.filter((j) => j.task_id === task.id);
    const unresolved = jobs.filter(
      (j) => !['ready', 'skipped'].includes(j.status) || j.midi_status === 'downloading',
    );
    if (assets.length && !unresolved.length && !task.error) continue;
    if (assets.length) {
      const visibleAssets = assets.filter(
        (a) =>
          a.source_audio_asset_id === a.id &&
          !(historyData.library || []).find((m) => m.id === a.id)?.deleted_at,
      );
      if (view === 'trash' || !visibleAssets.length) continue;
    } else if (!!task.deleted_at !== (view === 'trash')) continue;
    if (
      view === 'favorites' ||
      filter === 'favorites' ||
      (filter === 'mock' && task.provider !== 'mock') ||
      (search &&
        !String(task.title || '')
          .toLocaleLowerCase()
          .includes(search.toLocaleLowerCase()))
    )
      continue;
    const row = el('article', null, 'pending-row');
    row.append(
      el('span', assets.length ? '音轨待完成' : states[task.status] || task.status, 'task-state'),
      el('h3', task.title || `未命名作品 · ${displayDate(task.created_at)}`),
    );
    if (task.error) row.append(el('p', task.error, 'error'));
    if (task.progress) row.append(el('p', task.progress.next_action, 'help'));
    function resume(label, path) {
      const b = button(label, async () => {
        b.disabled = true;
        try {
          await recover(path);
        } finally {
          b.disabled = false;
        }
      });
      row.append(b);
    }
    if (['generating', 'downloading'].includes(task.status) && task.error)
      resume(
        task.status === 'generating' ? '立即查询原任务' : '恢复原音频下载',
        '/audio/tasks/' + task.id + '/retry',
      );
    if (!assets.length) row.append(recoveryDetail(task));
    for (const job of unresolved) {
      row.append(
        el('p', job.error || '正在处理音轨。', job.error ? 'error' : 'help'),
        recoveryDetail(task, job),
      );
      if (job.status === 'downloading' && job.error)
        resume('恢复分轨下载', '/audio/stems/' + job.id + '/retry');
      if (job.midi_status === 'downloading' && job.midi_error) {
        row.append(el('p', job.midi_error, 'error'));
        resume('恢复 MIDI 下载', '/audio/stems/' + job.id + '/midi/retry');
      }
      if (['failed', 'uncertain'].includes(job.status))
        row.append(
          el('p', '整曲仍可下载。请核对供应商账户及原分离结果，不会自动重新付费分离。', 'help'),
        );
    }
    if (!assets.length && ['failed', 'uncertain'].includes(task.status))
      row.append(
        button(
          task.deleted_at ? '恢复记录' : '删除记录',
          async () => {
            if ((cfg?.library_version || 0) < 3) {
              toast('请重启本地服务后再删除记录。');
              return;
            }
            try {
              await libraryAction([task.id], task.deleted_at ? 'restore_task' : 'trash_task');
              toast(
                task.deleted_at
                  ? '记录已恢复，不会重新生成。'
                  : '记录已移入回收站，原始调用记录保留。',
              );
            } catch (e) {
              fail(e);
            }
          },
          task.deleted_at ? 'secondary' : 'danger',
        ),
      );
    region.append(row);
    count++;
  }
  region.hidden = !count;
  if (count)
    region.prepend(
      el('h3', view === 'trash' ? '已移除的任务记录' : '任务动态', 'task-region-title'),
    );
  return count;
}
