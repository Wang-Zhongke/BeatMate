const $ = (id) => document.getElementById(id);
const labels = {
  purpose: '用途',
  mood: '情绪',
  scene: '场景',
  style: '风格参考',
  instruments: '配器偏好',
  structure: '结构变化',
  avoid: '禁止项',
};
const roles = { mix: '整曲', instrumental: '伴奏', vocals: '人声' };
const states = {
  queued: '等待提交',
  submitting: '提交中',
  generating: '生成中',
  downloading: '保存音频中',
  ready: '已保存',
  failed: '生成失败',
  uncertain: '提交待核对',
};
const paths = {
  play: 'M8 5l11 7-11 7z',
  pause: 'M8 5v14M16 5v14',
  next: 'M5 5l10 7-10 7zM19 5v14',
  previous: 'M19 5L9 12l10 7zM5 5v14',
  music: 'M9 18V5l11-2v13M9 7l11-2M9 18c0 4-7 4-7 0s7-4 7 0M20 16c0 4-7 4-7 0s7-4 7 0',
  heart:
    'M20.8 4.6a5.5 5.5 0 00-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 00-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 000-7.8z',
  trash: 'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7',
  settings: 'M4 7h16M4 17h16M9 4v6M15 14v6',
  pen: 'M4 20l4-1L20 7l-3-3L5 16zM14 7l3 3',
  search: 'M21 21l-6-6M17 10a7 7 0 11-14 0 7 7 0 0114 0',
  download: 'M12 3v12m-5-5l5 5 5-5M4 16v5h16v-5',
  queue: 'M4 5h16M4 11h16M4 17h10M18 15l4 3-4 3z',
  volume: 'M3 9h4l5-4v14l-5-4H3zM16 8a6 6 0 010 8M19 5a10 10 0 010 14',
  chevron: 'M6 9l6 6 6-6',
};
function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  const p = document.createElementNS(svg.namespaceURI, 'path');
  p.setAttribute('d', paths[name] || paths.music);
  svg.append(p);
  return svg;
}
function el(tag, text, cls) {
  const n = document.createElement(tag);
  if (text !== undefined && text !== null) n.textContent = text;
  if (cls) n.className = cls;
  return n;
}
function button(label, fn, cls = 'secondary', glyph) {
  const b = el('button', glyph ? null : label, cls);
  b.type = 'button';
  b.title = label;
  b.setAttribute('aria-label', label);
  if (glyph) {
    b.append(icon(glyph));
    if (!cls.includes('icon-button') && cls !== '') b.append(document.createTextNode(label));
  }
  b.onclick = fn;
  return b;
}
function link(label, url, cls = 'secondary', glyph) {
  const a = el('a', glyph ? null : label, cls);
  a.href = url;
  a.download = '';
  a.title = label;
  a.setAttribute('aria-label', label);
  if (glyph) {
    a.append(icon(glyph));
    if (!cls.includes('icon-button')) a.append(document.createTextNode(label));
  }
  return a;
}
function stored(key, fallback = null) {
  try {
    return JSON.parse(localStorage.getItem(key)) ?? fallback;
  } catch {
    return fallback;
  }
}
function persist(key, value) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(value));
  } catch {}
}
async function api(path, body) {
  const response = await fetch(
    path,
    body === undefined
      ? { cache: 'no-store' }
      : {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        },
  );
  let result;
  try {
    result = await response.json();
  } catch {
    throw Error('本地服务返回异常，请检查服务是否仍在运行。');
  }
  if (!response.ok) {
    const e = Error(result.error || '操作未完成');
    e.status = response.status;
    throw e;
  }
  return result;
}
function clock(seconds) {
  return Number.isFinite(seconds) && seconds >= 0
    ? `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`
    : '0:00';
}
function displayDate(value) {
  return new Date(value).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}
function setGlyph(node, name) {
  node.replaceChildren(icon(name));
}

export {
  $,
  labels,
  roles,
  states,
  icon,
  el,
  button,
  link,
  stored,
  persist,
  api,
  clock,
  displayDate,
  setGlyph,
};
