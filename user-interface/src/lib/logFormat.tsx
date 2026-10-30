import { Fragment, type ReactNode } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const dim = { color: '#687078' };
const think = { color: '#687078', fontStyle: 'italic' as const };
const err = { color: '#b31d28' };

/* eslint-disable @typescript-eslint/no-explicit-any */

function trunc(s: unknown, n: number): string {
  const str = String(s ?? '');
  return str.length > n ? `${str.slice(0, n)}…` : str;
}

// "30s", "5m 30s", "1h 12m" — for tool heartbeat elapsed times.
function fmtDuration(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return '';
  if (sec < 60) return `${Math.round(sec)}s`;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  if (m < 60) return s ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60);
  const mm = m % 60;
  return mm ? `${h}h ${mm}m` : `${h}h`;
}

// Stable de-dupe key: overlapping polls can re-fetch the same stream-json
// object, so key rendered rows by the object's uuid when present.
export function eventKey(message: string, timestamp: number): string {
  try {
    const parsed = JSON.parse(message);
    return parsed?.uuid || `${timestamp}-${message.slice(0, 20)}`;
  } catch {
    return `raw-${timestamp}-${message.slice(0, 10)}`;
  }
}

// Compact markdown for assistant prose (inline code, code blocks, bold, lists).
const markdownComponents = {
  p: ({ children }: { children?: ReactNode }) => <span>{children}</span>,
  strong: ({ children }: { children?: ReactNode }) => <strong>{children}</strong>,
  code: ({ children, className }: { children?: ReactNode; className?: string }) => {
    if (className) {
      return (
        <pre
          style={{
            background: '#eef1f4',
            padding: '8px 12px',
            borderRadius: 4,
            margin: '4px 0',
            overflow: 'auto',
            fontSize: 12,
            whiteSpace: 'pre-wrap',
          }}
        >
          <code>{children}</code>
        </pre>
      );
    }
    return (
      <code style={{ background: '#eef1f4', padding: '1px 5px', borderRadius: 3, fontSize: 12 }}>
        {children}
      </code>
    );
  },
  pre: ({ children }: { children?: ReactNode }) => <>{children}</>,
  ul: ({ children }: { children?: ReactNode }) => (
    <div style={{ paddingLeft: 16, margin: '2px 0' }}>{children}</div>
  ),
  li: ({ children }: { children?: ReactNode }) => <div>• {children}</div>,
};

function toolSummary(name: string, input: any): string {
  const i = input || {};
  switch (name) {
    case 'Bash':
      return trunc(i.command, 200);
    case 'Read':
    case 'Edit':
    case 'Write':
      return i.file_path || trunc(JSON.stringify(i), 160);
    case 'Glob':
      return i.pattern || i.query || trunc(JSON.stringify(i), 160);
    case 'Grep':
      return i.pattern ? `"${i.pattern}" in ${i.path || '.'}` : trunc(JSON.stringify(i), 160);
    case 'Skill':
      return i.skill || trunc(JSON.stringify(i), 160);
    default:
      return trunc(JSON.stringify(i), 160);
  }
}

function resultText(content: any): string {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content.map((c) => (typeof c === 'string' ? c : c?.text ?? '')).join('');
  }
  return '';
}

// Turns a single CloudWatch event message into a compact, readable node.
// Returns null when the event should be skipped entirely (pure noise).
export function renderLogMessage(message: string): ReactNode | null {
  const text = (message ?? '').trim();
  if (!text) return null;

  let obj: any;
  try {
    obj = JSON.parse(text);
  } catch {
    // Plain text line (our own logs, provisioning steps, etc.)
    return <span>{message}</span>;
  }
  if (!obj || typeof obj !== 'object') return <span>{message}</span>;

  const lines: ReactNode[] = [];

  switch (obj.type) {
    case 'assistant': {
      const content = obj.message?.content;
      if (Array.isArray(content)) {
        for (const c of content) {
          if (c?.type === 'text' && c.text?.trim()) {
            // SECURITY: agent-authored log text → untrusted. Safe only because react-markdown@9 does
            // NOT render raw HTML. NEVER add `rehype-raw`/`dangerouslySetInnerHTML` to this renderer.
            lines.push(
              <Markdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                {c.text}
              </Markdown>
            );
          } else if (c?.type === 'thinking' && c.thinking?.trim()) {
            lines.push(<span style={think}>💭 {trunc(c.thinking, 200)}</span>);
          } else if (c?.type === 'tool_use') {
            lines.push(
              <span>
                🔧 {c.name} — <span style={dim}>{toolSummary(c.name, c.input)}</span>
              </span>
            );
          }
        }
      }
      break;
    }
    case 'user': {
      const content = obj.message?.content;
      if (Array.isArray(content)) {
        for (const c of content) {
          if (c?.type === 'tool_result') {
            const stdout = obj.tool_use_result?.stdout;
            const body = stdout || resultText(c.content);
            const isError = c.is_error || (obj.tool_use_result?.stderr && !stdout);
            lines.push(
              <span style={isError ? err : undefined}>↳ {trunc(body, 400)}</span>
            );
          }
        }
      }
      break;
    }
    // Heartbeat while a long-running tool (e.g. a Bash call that dispatches an experiment
    // sub-agent) is still executing. Surface which tool is running and for how long, so a
    // quiet stream reads as "still working, N min in" rather than an opaque "tool_progress".
    case 'tool_progress': {
      const dur = fmtDuration(Number(obj.elapsed_time_seconds));
      lines.push(
        <span style={dim}>
          ⏳ {obj.tool_name || 'tool'} still running{dur ? ` — ${dur}` : ''}…
        </span>
      );
      break;
    }
    case 'system': {
      if (obj.subtype === 'thinking_tokens') return null;
      if (obj.subtype === 'init') {
        lines.push(
          <span>
            ⚡ Session started — model: {obj.model || '?'}, cwd: {obj.cwd || '?'}
          </span>
        );
        lines.push(
          <span style={dim}>
            Tools: {(obj.tools || []).length} | Skills: {(obj.skills || []).length}
          </span>
        );
      } else if (obj.subtype === 'api_retry') {
        lines.push(
          <span style={err}>
            API retry {obj.attempt}/{obj.max_retries} — {obj.error} ({obj.error_status})
          </span>
        );
      } else if (obj.subtype === 'vcs_state_changed') {
        const label = [obj.kind ?? obj.vcs_kind, obj.branch].filter(Boolean).join(' ');
        lines.push(<span style={dim}>⎇ {label || 'vcs'}</span>);
      } else if (obj.subtype === 'task_started') {
        lines.push(
          <span>
            ▶ Sub-agent started — <span style={dim}>{trunc(obj.description || obj.task_id, 200)}</span>
          </span>
        );
      } else if (obj.subtype === 'task_notification') {
        const ok = obj.status === 'completed';
        lines.push(
          <span style={ok ? undefined : err}>
            {ok ? '✅' : '⚠️'} Sub-agent {obj.status || 'update'} —{' '}
            <span style={dim}>{trunc(obj.summary || obj.description || obj.task_id, 200)}</span>
          </span>
        );
      } else {
        lines.push(<span style={dim}>· {obj.subtype || 'system'}</span>);
      }
      break;
    }
    case 'result': {
      const meta: string[] = [];
      if (obj.num_turns != null) meta.push(`${obj.num_turns} turns`);
      const cost = obj.total_cost_usd ?? obj.cost_usd ?? obj.cost;
      if (cost != null && Number.isFinite(Number(cost))) meta.push(`$${Number(cost).toFixed(4)}`);
      const body = trunc(typeof obj.result === 'string' ? obj.result : obj.result?.result ?? '(completed)', 300);
      lines.push(
        <span>
          ✅ {body}
          {meta.length ? ` (${meta.join(', ')})` : ''}
        </span>
      );
      break;
    }
    default:
      lines.push(
        <span style={dim}>
          {obj.type || 'event'}
          {obj.subtype ? `/${obj.subtype}` : ''}
        </span>
      );
  }

  if (lines.length === 0) return null;
  return (
    <>
      {lines.map((l, i) => (
        <div key={i} style={{ wordBreak: 'break-word' }}>
          {l}
        </div>
      ))}
    </>
  );
}
