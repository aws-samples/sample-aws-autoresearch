import { useCallback, useEffect, useRef, useState } from 'react';
import Box from '@cloudscape-design/components/box';
import Toggle from '@cloudscape-design/components/toggle';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Alert from '@cloudscape-design/components/alert';
import { getLogs, type LogEvent } from '../services/api';
import RenderedLog, { logSurfaceStyle } from './RenderedLog';

interface LogFeedProps {
  projectId: string;
  stream?: string;
  // When true (default) keep polling forward with next_start; otherwise fetch once.
  active?: boolean;
  pollMs?: number;
  height?: number | string;
  emptyText?: string;
  showStreamTag?: boolean;
}

// Reusable, forward-cursor log tail. Used for both the researcher stream and
// per-experiment sub-agent streams. Appends only newer events across polls.
export default function LogFeed({
  projectId,
  stream,
  active = true,
  pollMs = 4000,
  height = '55vh',
  emptyText = 'No activity yet.',
  showStreamTag = false, // single-stream tails don't need a per-line [stream] tag
}: LogFeedProps) {
  const [events, setEvents] = useState<LogEvent[]>([]);
  const [autoScroll, setAutoScroll] = useState(true);
  const [error, setError] = useState('');
  const cursorRef = useRef<number | undefined>(undefined);
  const feedRef = useRef<HTMLDivElement>(null);

  const poll = useCallback(async () => {
    if (!projectId || !stream) return;
    try {
      const page = await getLogs(projectId, { stream, start: cursorRef.current });
      if (page.next_start !== undefined) cursorRef.current = page.next_start;
      else if (page.events.length) {
        cursorRef.current = Math.max(...page.events.map((e) => e.timestamp || 0)) + 1;
      }
      if (page.events.length) {
        const sorted = [...page.events].sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0));
        setEvents((prev) => [...prev, ...sorted]);
      }
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load logs');
    }
  }, [projectId, stream]);

  // Reset the feed whenever the target stream changes.
  useEffect(() => {
    setEvents([]);
    cursorRef.current = undefined;
    if (!stream) return;
    poll();
    if (!active) return;
    const id = setInterval(poll, pollMs);
    return () => clearInterval(id);
  }, [poll, active, pollMs, stream]);

  useEffect(() => {
    if (autoScroll && feedRef.current) {
      feedRef.current.scrollTop = feedRef.current.scrollHeight;
    }
  }, [events, autoScroll]);

  // Follow-the-tail like a terminal: scrolling up (away from the bottom) pauses auto-scroll so the
  // reader stays put as new events append; scrolling back to the bottom resumes it. The programmatic
  // scroll above always lands at the bottom, so it reads as "at bottom" here and never fights the user.
  const onScroll = useCallback(() => {
    const el = feedRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    setAutoScroll((prev) => (prev === atBottom ? prev : atBottom));
  }, []);

  return (
    <SpaceBetween size="xs">
      {error && <Alert type="error">{error}</Alert>}
      <Box textAlign="right">
        <Toggle checked={autoScroll} onChange={({ detail }) => setAutoScroll(detail.checked)}>
          Auto-scroll
        </Toggle>
      </Box>
      <div ref={feedRef} onScroll={onScroll} style={{ ...logSurfaceStyle, height }}>
        {!stream ? (
          <Box color="text-status-inactive">No log stream for this actor yet.</Box>
        ) : (
          <RenderedLog events={events} showStreamTag={showStreamTag} emptyText={emptyText} />
        )}
      </div>
    </SpaceBetween>
  );
}
