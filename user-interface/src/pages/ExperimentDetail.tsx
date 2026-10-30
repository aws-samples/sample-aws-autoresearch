import { useCallback, useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import Alert from '@cloudscape-design/components/alert';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Spinner from '@cloudscape-design/components/spinner';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import LogFeed from '../components/LogFeed';
import RenderedLog, { logSurfaceStyle } from '../components/RenderedLog';
import {
  getExperiments,
  getDataUrl,
  getDataPreview,
  fmtMetric,
  fmtUsd,
  type Experiment,
} from '../services/api';

const POLL_MS = 5000;

function outcomeIndicator(state: string) {
  switch (state) {
    case 'running':
      return <StatusIndicator type="loading">running</StatusIndicator>;
    case 'kept':
      return <StatusIndicator type="success">kept</StatusIndicator>;
    case 'discarded':
      return <StatusIndicator type="stopped">discarded</StatusIndicator>;
    case 'crashed':
      return <StatusIndicator type="error">crashed</StatusIndicator>;
    default:
      return <StatusIndicator type="info">{state}</StatusIndicator>;
  }
}

function fmtDuration(s?: number): string {
  if (!s) return '—';
  const m = Math.floor(s / 60);
  const sec = Math.round(s % 60);
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
}

export default function ExperimentDetail() {
  const { projectId, experimentId } = useParams<{ projectId: string; experimentId: string }>();
  const navigate = useNavigate();
  const [experiment, setExperiment] = useState<Experiment | null>(null);
  const [loading, setLoading] = useState(true);

  // Full log (rendered inline once the experiment has finished).
  const [logText, setLogText] = useState<string | null>(null);
  const [logTruncated, setLogTruncated] = useState(false);
  const [logLoading, setLogLoading] = useState(false);
  const [logNote, setLogNote] = useState('');

  const load = useCallback(async () => {
    if (!projectId) return;
    try {
      const exps = await getExperiments(projectId);
      setExperiment(exps.find((e) => e.experiment_id === experimentId) || null);
    } finally {
      setLoading(false);
    }
  }, [projectId, experimentId]);

  useEffect(() => {
    load();
  }, [load]);

  const isRunning = experiment?.state === 'running';

  // While running, keep the record fresh (state / log_stream).
  useEffect(() => {
    if (!isRunning) return;
    const id = setInterval(load, POLL_MS);
    return () => clearInterval(id);
  }, [isRunning, load]);

  // Once finished, immediately fetch + render the whole experiment log (the sub-agent's archive).
  useEffect(() => {
    if (!projectId || !experimentId || !experiment || isRunning) return;
    if (logText !== null || logLoading) return;
    setLogLoading(true);
    setLogNote('');
    getDataPreview(projectId, `experiments/${experimentId}/agent.jsonl`)
      .then((r) => {
        setLogText(r.content || '');
        setLogTruncated(!!r.truncated);
      })
      .catch(() => {
        setLogText('');
        setLogNote('Full log not available for this experiment.');
      })
      .finally(() => setLogLoading(false));
  }, [projectId, experimentId, experiment, isRunning, logText, logLoading]);

  const download = async (key: string) => {
    if (!projectId) return;
    const url = await getDataUrl(projectId, key);
    if (url) window.open(url, '_blank', 'noopener,noreferrer');
  };

  if (loading) {
    return (
      <Box textAlign="center" padding="xxxl">
        <Spinner size="large" />
      </Box>
    );
  }
  if (!experiment) {
    return (
      <Box textAlign="center" padding="xxxl">
        <StatusIndicator type="error">Experiment not found</StatusIndicator>
      </Box>
    );
  }

  const e = experiment;

  return (
    <SpaceBetween size="l">
      <Button variant="link" onClick={() => navigate(`/projects/${projectId}/experiments`)}>
        &larr; Back to experiments
      </Button>

      {/* Summary + structured info */}
      <Container header={<Header variant="h1" description={e.branch}>{e.experiment_id}</Header>}>
        <ColumnLayout columns={4} variant="text-grid">
          <div>
            <Box variant="awsui-key-label">Outcome</Box>
            {outcomeIndicator(e.state)}
          </div>
          <div>
            <Box variant="awsui-key-label">Metric</Box>
            {fmtMetric(e.metric_value)}
          </div>
          <div>
            <Box variant="awsui-key-label">Peak VRAM</Box>
            {e.peak_vram_mb ? `${e.peak_vram_mb} MB` : '—'}
          </div>
          <div>
            <Box variant="awsui-key-label">Cost</Box>
            {e.cost_usd !== undefined ? fmtUsd(e.cost_usd) : '—'}
          </div>
          <div>
            <Box variant="awsui-key-label">Run time</Box>
            {fmtDuration(e.run_seconds)}
          </div>
          <div>
            <Box variant="awsui-key-label">Worker uptime</Box>
            {fmtDuration(e.worker_uptime_seconds)}
          </div>
          <div>
            <Box variant="awsui-key-label">Worker</Box>
            <Box fontSize="body-s">{e.worker_id || '—'}</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Completed</Box>
            {e.completed_at ? new Date(e.completed_at).toLocaleString() : '—'}
          </div>
        </ColumnLayout>
      </Container>

      {e.hypothesis && (
        <Container header={<Header variant="h3">Hypothesis</Header>}>
          <Box>{e.hypothesis}</Box>
        </Container>
      )}
      {e.description && (
        <Container header={<Header variant="h3">Change to src/train.py</Header>}>
          <Box variant="p">{e.description}</Box>
        </Container>
      )}

      {/* Log: live stream while running, full rendered log once finished. */}
      {isRunning ? (
        <Container
          header={
            <Header variant="h2" description="Real-time stream from the run-experiment sub-agent.">
              Live experiment stream
            </Header>
          }
        >
          {e.log_stream ? (
            <LogFeed projectId={projectId!} stream={e.log_stream} height={480} showStreamTag={false} />
          ) : (
            <Alert type="info">Waiting for the sub-agent to start its log stream.</Alert>
          )}
        </Container>
      ) : (
        <Container
          header={
            <Header
              variant="h2"
              description="The complete sub-agent log for this experiment."
              actions={
                <SpaceBetween direction="horizontal" size="xs">
                  <Button iconName="download" onClick={() => download(`experiments/${e.experiment_id}/agent.jsonl`)}>
                    agent.jsonl
                  </Button>
                  <Button iconName="download" onClick={() => download(`experiments/${e.experiment_id}/run.log`)}>
                    run.log
                  </Button>
                </SpaceBetween>
              }
            >
              Experiment log
            </Header>
          }
        >
          {logLoading ? (
            <Box textAlign="center" padding="l">
              <Spinner />
            </Box>
          ) : logNote ? (
            <Box color="text-status-inactive">{logNote}</Box>
          ) : (
            <SpaceBetween size="xs">
              {logTruncated && (
                <StatusIndicator type="info">
                  Large log — showing the first part. Use the agent.jsonl download for the full file.
                </StatusIndicator>
              )}
              <div style={{ ...logSurfaceStyle, maxHeight: '70vh' }}>
                <RenderedLog text={logText || ''} emptyText="(empty log)" />
              </div>
            </SpaceBetween>
          )}
        </Container>
      )}
    </SpaceBetween>
  );
}
