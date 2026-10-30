import Box from '@cloudscape-design/components/box';
import type { LogEvent } from '../services/api';
import { renderLogMessage, eventKey } from '../lib/logFormat';

const STREAM_COLORS: Record<string, string> = {
  researcher: '#0972d3',
  'run-experiment': '#037f0c',
  worker: '#ec7211',
};

function streamColor(stream: string): string {
  if (STREAM_COLORS[stream]) return STREAM_COLORS[stream];
  if (stream.startsWith('experiment/')) return '#037f0c';
  if (stream.startsWith('researcher')) return '#0972d3';
  return '#687078';
}

function fmtTime(ts: number): string {
  if (!ts) return '';
  const ms = ts < 1e12 ? ts * 1000 : ts;
  return new Date(ms).toLocaleTimeString();
}

interface Row {
  key: string;
  timestamp?: number;
  stream?: string;
  node: React.ReactNode;
}

// Build rows from either a list of LogEvents (dedupe by uuid, keep order) or a
// raw file body (one line per row, empty lines dropped, no timestamps).
function buildRows(events?: LogEvent[], text?: string): Row[] {
  const rows: Row[] = [];
  if (events) {
    // De-dupe by uuid (Map keeps first insertion order, updates in place).
    const deduped = Array.from(
      new Map(events.map((e) => [eventKey(e.message, e.timestamp), e])).values()
    );
    deduped.forEach((e, i) => {
      const node = renderLogMessage(e.message);
      if (node === null) return; // skipped noise (e.g. thinking_tokens)
      rows.push({ key: eventKey(e.message, e.timestamp) + i, timestamp: e.timestamp, stream: e.stream, node });
    });
  } else if (text != null) {
    text.split('\n').forEach((line, i) => {
      if (!line.trim()) return;
      const node = renderLogMessage(line);
      if (node === null) return;
      rows.push({ key: `line-${i}`, node });
    });
  }
  return rows;
}

interface RenderedLogProps {
  events?: LogEvent[];
  text?: string;
  showStreamTag?: boolean;
  emptyText?: string;
}

// Shared log renderer: same row layout + monospace container for the live feed
// and for archived-file previews. `renderLogMessage` handles per-line content.
export default function RenderedLog({
  events,
  text,
  showStreamTag = false,
  emptyText = 'No activity yet.',
}: RenderedLogProps) {
  const rows = buildRows(events, text);
  if (rows.length === 0) {
    return <Box color="text-status-inactive">{emptyText}</Box>;
  }
  return (
    <>
      {rows.map((row) => (
        <div key={row.key} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
          {row.timestamp !== undefined && (
            <span style={{ color: '#687078', flexShrink: 0 }}>{fmtTime(row.timestamp)}</span>
          )}
          {showStreamTag && row.stream && (
            <span style={{ color: streamColor(row.stream), fontWeight: 700, flexShrink: 0 }}>
              [{row.stream}]
            </span>
          )}
          <span style={{ flex: 1, minWidth: 0, wordBreak: 'break-word' }}>{row.node}</span>
        </div>
      ))}
    </>
  );
}

// Shared container styling for the monospace log surface.
export const logSurfaceStyle: React.CSSProperties = {
  overflowY: 'auto',
  fontFamily: 'Monaco, Menlo, monospace',
  fontSize: 12,
  lineHeight: 1.6,
  background: '#f6f8fa',
  borderRadius: 6,
  padding: 12,
};
