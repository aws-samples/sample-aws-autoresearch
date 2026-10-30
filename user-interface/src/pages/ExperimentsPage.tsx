import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import Badge from '@cloudscape-design/components/badge';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import Link from '@cloudscape-design/components/link';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Table from '@cloudscape-design/components/table';
import { useProject } from '../components/ProjectLayout';
import {
  getExperiments,
  getWorkers,
  fmtMetric,
  fmtUsd,
  type Experiment,
  type WorkerInfo,
} from '../services/api';

const POLL_MS = 5000;
const TERMINAL = ['kept', 'discarded', 'crashed'];

function outcomeBadge(state: string) {
  switch (state) {
    case 'running':
      return <Badge color="blue">running</Badge>;
    case 'kept':
      return <Badge color="green">kept</Badge>;
    case 'discarded':
      return <Badge color="grey">discarded</Badge>;
    case 'crashed':
      return <Badge color="red">crashed</Badge>;
    default:
      return <Badge color="grey">{state}</Badge>;
  }
}

function workerLiveBadge(state: string | undefined) {
  switch (state) {
    case 'running':
      return <Badge color="green">running</Badge>;
    case 'pending':
      return <Badge color="blue">pending</Badge>;
    case 'stopping':
    case 'shutting-down':
      return <Badge color="blue">{state}</Badge>;
    case 'stopped':
      return <Badge color="grey">stopped</Badge>;
    case 'terminated':
      return <Badge color="red">terminated</Badge>;
    default:
      return <Badge color="grey">{state || 'unknown'}</Badge>;
  }
}

function fmtDuration(s?: number): string {
  if (!s) return '—';
  const m = Math.floor(s / 60);
  const sec = Math.round(s % 60);
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
}

export default function ExperimentsPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const { project } = useProject();
  const navigate = useNavigate();
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [workers, setWorkers] = useState<WorkerInfo[]>([]);

  const poll = useCallback(async () => {
    if (!projectId) return;
    try {
      const [exps, wks] = await Promise.all([getExperiments(projectId), getWorkers(projectId)]);
      setExperiments(exps);
      setWorkers(wks);
    } catch {
      /* tolerate transient errors */
    }
  }, [projectId]);

  useEffect(() => {
    poll();
    const id = setInterval(poll, POLL_MS);
    return () => clearInterval(id);
  }, [poll]);

  const openExp = (id: string) => navigate(`/projects/${projectId}/experiments/${id}`);

  const running = experiments.filter((e) => e.state === 'running');
  const workersById = new Map(workers.map((w) => [w.instance_id, w]));
  const past = experiments
    .filter((e) => TERMINAL.includes(e.state))
    .sort((a, b) =>
      (b.completed_at || b.created_at || '').localeCompare(a.completed_at || a.created_at || '')
    );

  return (
    <SpaceBetween size="l">
      {/* 0. Experiment parameters — the run's stopping/pacing knobs (chosen at creation). */}
      <Container header={<Header variant="h2">Experiment parameters</Header>}>
        <KeyValuePairs
          columns={3}
          items={[
            {
              label: 'Patience',
              value: project.patience != null && project.patience !== '' ? `${project.patience} steps` : '—',
            },
            {
              label: 'Training Time Budget',
              value: project.time_budget_seconds ? `${project.time_budget_seconds}s` : '—',
            },
            {
              label: 'Experiment timeout',
              value: project.experiment_timeout_minutes ? `${project.experiment_timeout_minutes} min` : '—',
            },
          ]}
        />
      </Container>

      {/* 1. Current experiments — the experiment(s) executing right now, each with a link to its live
            stream. Runner-agnostic: an "ec2" experiment shows its GPU worker + live state; a "local"
            experiment runs on the researcher itself (no worker) but still has a stream to open. */}
      <Container header={<Header variant="h2" counter={`(${running.length})`}>Current experiments</Header>}>
        {running.length === 0 ? (
          <Box color="text-status-inactive">No experiment running right now.</Box>
        ) : (
          <SpaceBetween size="s">
            {running.map((exp) => {
              const local = project.experiment_runner === 'local';
              const worker = exp.worker_id ? workersById.get(exp.worker_id) : undefined;
              return (
                <div
                  key={exp.experiment_id}
                  style={{ border: '1px solid #e9ebed', borderRadius: 6, padding: 12, background: '#fff' }}
                >
                  <SpaceBetween size="xs">
                    <SpaceBetween direction="horizontal" size="s" alignItems="center">
                      <Box fontWeight="bold">{exp.experiment_id}</Box>
                      <Badge color="blue">running</Badge>
                      {local ? (
                        <Badge color="grey">local — on researcher</Badge>
                      ) : (
                        <>
                          <Box color="text-status-inactive" fontSize="body-s">
                            {worker?.name || exp.worker_id || 'worker'}
                          </Box>
                          {workerLiveBadge(worker?.state)}
                        </>
                      )}
                    </SpaceBetween>
                    {exp.hypothesis && <Box fontSize="body-s">{exp.hypothesis}</Box>}
                    <Button iconName="external" onClick={() => openExp(exp.experiment_id)}>
                      Open live stream
                    </Button>
                  </SpaceBetween>
                </div>
              );
            })}
          </SpaceBetween>
        )}
      </Container>

      {/* 2. Past experiments — each links to its detail page (full log + summary). */}
      <Table
        variant="container"
        header={<Header variant="h2" counter={`(${past.length})`}>Past experiments</Header>}
        items={past}
        empty="No finished experiments yet."
        columnDefinitions={[
          {
            id: 'id',
            header: 'Experiment',
            cell: (e) => (
              <Link href="#" onFollow={(ev) => { ev.preventDefault(); openExp(e.experiment_id); }}>
                {e.experiment_id}
              </Link>
            ),
          },
          { id: 'outcome', header: 'Outcome', cell: (e) => outcomeBadge(e.state) },
          { id: 'metric', header: 'Metric', cell: (e) => fmtMetric(e.metric_value) },
          { id: 'cost', header: 'Cost', cell: (e) => (e.cost_usd !== undefined ? fmtUsd(e.cost_usd) : '—') },
          { id: 'runtime', header: 'Run time', cell: (e) => fmtDuration(e.run_seconds) },
          {
            id: 'completed',
            header: 'Completed',
            cell: (e) => (e.completed_at ? new Date(e.completed_at).toLocaleString() : '—'),
          },
        ]}
      />
    </SpaceBetween>
  );
}
