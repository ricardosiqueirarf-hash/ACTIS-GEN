"""Shared WhatsApp browser lease and DOM contract (no application internals)."""
from __future__ import annotations

import fcntl
import hashlib
import re
import threading
import time
from contextlib import contextmanager

from meuharness import storage

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()

class BrowserBusyError(RuntimeError):
    pass

@contextmanager
def browser_lease(agent_id: str, *, wait_seconds: float = 0.0):
    """Serialize sync/send across threads and processes, optionally waiting for foreground work."""
    key = hashlib.sha256(str(agent_id).encode()).hexdigest()
    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, threading.Lock())
    while not lock.acquire(blocking=False):
        if time.monotonic() >= deadline:
            raise BrowserBusyError("WhatsApp ocupado por outra operação")
        time.sleep(0.1)
    handle = None
    try:
        folder = storage.DATA_DIR / "runtime-locks"
        folder.mkdir(parents=True, exist_ok=True)
        handle = (folder / (key + ".lock")).open("a")
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise BrowserBusyError("WhatsApp ocupado por outro processo") from exc
                time.sleep(0.1)
        yield
    finally:
        if handle is not None:
            handle.close()
        lock.release()

def normalize_phone(value: str | None) -> str:
    raw = str(value or "").strip()
    if raw.endswith(("@c.us", "@s.whatsapp.net")):
        raw = raw.split("@", 1)[0]
    if not re.fullmatch(r"\+?[\d\s().-]+", raw):
        return ""
    digits = re.sub(r"\D", "", raw)
    if len(digits) in {10, 11}:
        digits = "55" + digits
    return digits if 10 <= len(digits) <= 15 else ""

def phone_aliases(value: str) -> set[str]:
    """Brazil's legacy WhatsApp mobile ID may omit only the added ninth digit."""
    phone = normalize_phone(value)
    aliases = {phone} if phone else set()
    if phone.startswith("55"):
        if len(phone) == 13 and phone[4] == "9" and phone[5] in "6789":
            aliases.add(phone[:4] + phone[5:])
        elif len(phone) == 12 and phone[4] in "6789":
            aliases.add(phone[:4] + "9" + phone[4:])
    return aliases

MESSAGE_SCAN_JS = r"""(() => {
  const root=document.querySelector('#main');
  if(!root) return [];
  const norm=s=>String(s||'').replace(/\s+/g,' ').trim();
  const rows=[...root.querySelectorAll('[data-testid^="conv-msg-"][data-id],.message-in[data-id],.message-out[data-id],[data-id^="true_"],[data-id^="false_"]')];
  const seen=new Set(),out=[];
  for(const row of rows){
    const id=row.getAttribute('data-id')||'';
    if(!id||seen.has(id)) continue;
    seen.add(id);
    const pre=row.querySelector('[data-pre-plain-text]');
    const meta=pre?.getAttribute('data-pre-plain-text')||'';
    const labels=[...row.querySelectorAll('[aria-label]')].map(e=>norm(e.getAttribute('aria-label')));
    const icons=[...row.querySelectorAll('svg title,[data-icon]')].map(e=>e.getAttribute('data-icon')||e.textContent||'');
    const ack=labels.some(s=>/^(Enviada|Enviado|Entregue|Lida|Sent|Delivered|Read)$/i.test(s))
      ||icons.some(s=>/^(msg-check|msg-dblcheck|msg-dblcheck-ack|wds-ic-sent|wds-ic-delivered|wds-ic-read)$/.test(s));
    const outgoing=row.matches('.message-out,[data-id^="true_"]')
      ||!!row.querySelector('.message-out,[data-icon="tail-out"]')
      ||labels.some(s=>/^(Você:|You:)$/.test(s))||ack;
    const textNode=pre?.querySelector('[data-testid="selectable-text"],span.selectable-text,[data-testid="msg-text"]')
      ||row.querySelector('[data-testid="selectable-text"],span.selectable-text,[data-testid="msg-text"]');
    let content=(textNode?.innerText||'').trim(),type='text';
    const description=[row.innerText||'',...labels,...icons].join(' ');
    if(/\b(pdf|docx|xlsx)\b/i.test(description)&&!textNode) type='document';
    else if(row.querySelector('audio')||/audio-play|ptt-status|Reproduzir áudio|Play voice/i.test(description)) type='audio';
    else if(row.querySelector('video')||/video-play/.test(description)) type='video';
    else if(!textNode&&row.querySelector('img[src^="blob:"]')) type='image';
    if(!content) content=(row.innerText||'').trim();
    if(!content||(!meta&&type==='text'&&!textNode)) continue;
    let sender='',timestamp=null;
    const author=meta.match(/\]\s*(.*):\s*$/);if(author) sender=author[1].trim();
    const tm=meta.match(/^\[(\d{1,2}):(\d{2}),\s*(\d{1,2})\/(\d{1,2})\/(\d{2,4})\]/);
    if(tm){
      let year=Number(tm[5]);if(year<100)year+=2000;
      const pad=n=>String(n).padStart(2,'0');
      timestamp=year+'-'+pad(tm[4])+'-'+pad(tm[3])+'T'+pad(tm[1])+':'+pad(tm[2])+':00';
    }
    out.push({external_id:id,direction:outgoing?'outgoing':'incoming',sender,content,
      timestamp,message_type:type,raw_meta:meta,transport_ack:ack});
  }
  return out.slice(-300);
})()"""
