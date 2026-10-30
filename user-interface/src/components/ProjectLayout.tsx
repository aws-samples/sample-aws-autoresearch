import { useCallback, useEffect, useState } from 'react';
import { Outlet, useOutletContext, useParams, useNavigate, useLocation } from 'react-router-dom';
import Alert from '@cloudscape-design/components/alert';
import Badge from '@cloudscape-design/components/badge';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import Modal from '@cloudscape-design/components/modal';
import Popover from '@cloudscape-design/components/popover';
import Spinner from '@cloudscape-design/components/spinner';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Tabs from '@cloudscape-design/components/tabs';
import LogFeed from './LogFeed';
import {
  getProject,
  getWorkers,
  startSession,
  stopSession,
  deleteProject,
  getCurrentUserId,
  getMyJoinRequest,
  projectRole,
  requestToJoin,
  fmtMetric,
  fmtUsd,
  type Project,
  type ProjectRole,
  type WorkerInfo,
} from '../services/api';

interface ProjectContext {
  project: Project;
  refresh: () => Promise<void>;
  role: ProjectRole; // caller's role on this project — owner/admin gate write UI; viewer is read-only
}

export function useProject(): ProjectContext {
  return useOutletContext<ProjectContext>();
}

export function projectStatusIndicator(status: string | undefined) {
  switch (status) {
    case 'provisioning':
      return <StatusIndicator type="in-progress">Provisioning</StatusIndicator>;
    case 'active':
      return <StatusIndicator type="success">Active</StatusIndicator>;
    case 'setup':
      return <StatusIndicator type="pending">Setup</StatusIndicator>;
    case 'paused':
      return <StatusIndicator type="stopped">Paused</StatusIndicator>;
    case 'provision_failed':
      return <StatusIndicator type="error">Provision failed</StatusIndicator>;
    case 'completed':
      return <StatusIndicator type="info">Completed</StatusIndicator>;
    default:
      return <StatusIndicator type="info">{status || 'unknown'}</StatusIndicator>;
  }
}

// Compact per-worker state badge (the "GPU Workers" section header already says GPU).
function gpuStateBadge(state: string | undefined) {
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

// Short, human-friendly worker label: the Name tag if present, else the tail of the instance id.
function workerLabel(w: WorkerInfo, index: number): string {
  if (w.name) return w.name;
  const tail = w.instance_id?.slice(-6) || `${index}`;
  return `worker ${index} (…${tail})`;
}

const TABS = [
  { id: '', label: 'Researcher Stream' },
  { id: 'status', label: 'Research status' },
  { id: 'experiments', label: 'Experiments' },
  { id: 'wiki', label: 'Wiki' },
  { id: 'code', label: 'Code' },
  { id: 'data', label: 'Data' },
];

const PROVISIONING_STEPS = [
  'queued',
  'creating-repos',
  'committing',
  'resolving-amis',
  'creating-researcher',
  'creating-workers',
  'done',
];

export default function ProjectLayout() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const [project, setProject] = useState<Project | null>(null);
  const [workers, setWorkers] = useState<WorkerInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [actionError, setActionError] = useState('');
  const [userSub, setUserSub] = useState('');
  const [joining, setJoining] = useState(false);
  const [joinRequested, setJoinRequested] = useState(false);

  useEffect(() => {
    getCurrentUserId().then(setUserSub).catch(() => setUserSub(''));
  }, []);

  // If the caller is a viewer, check whether they've already requested to join — so the
  // "Join the team" button stays hidden ("Request sent") across reloads, not just this session.
  useEffect(() => {
    if (!projectId || !userSub || !project) return;
    if (projectRole(project, userSub) !== 'viewer') return;
    getMyJoinRequest(projectId)
      .then((r) => setJoinRequested(!!r.requested))
      .catch(() => {});
  }, [projectId, userSub, project]);

  const refresh = useCallback(async () => {
    if (!projectId) return;
    const p = await getProject(projectId);
    setProject(p);
    setLoading(false);
    // Live per-worker states (best-effort — never block the view on this).
    getWorkers(projectId).then(setWorkers).catch(() => setWorkers([]));
  }, [projectId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // While provisioning, poll the project so the view flips automatically.
  useEffect(() => {
    if (project?.status !== 'provisioning') return;
    const id = setInterval(refresh, 3500);
    return () => clearInterval(id);
  }, [project?.status, refresh]);

  // While a session is active, poll live worker states (they flip on/off as concurrent
  // experiments start and stop their workers).
  useEffect(() => {
    if (project?.status !== 'active' || !projectId) return;
    const id = setInterval(() => {
      getWorkers(projectId).then(setWorkers).catch(() => {});
    }, 5000);
    return () => clearInterval(id);
  }, [project?.status, projectId]);

  const handleStart = async () => {
    if (!projectId) return;
    setBusy(true);
    try {
      await startSession(projectId);
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  const handleStop = async () => {
    if (!projectId) return;
    setBusy(true);
    try {
      await stopSession(projectId);
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async () => {
    if (!projectId) return;
    setDeleting(true);
    setActionError('');
    try {
      await deleteProject(projectId);
      navigate('/');
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to delete project');
      setDeleting(false);
      setConfirmDelete(false);
    }
  };

  if (loading) {
    return (
      <Box textAlign="center" padding="xxxl">
        <Spinner size="large" />
      </Box>
    );
  }

  if (!project) {
    return (
      <Box textAlign="center" padding="xxxl">
        <StatusIndicator type="error">Project not found</StatusIndicator>
      </Box>
    );
  }

  const isProvisioning = project.status === 'provisioning';
  const isFailed = project.status === 'provision_failed';
  const sessionActive = project.status === 'active';

  // Caller's role drives which controls appear (server-side authz is the real gate).
  const role = projectRole(project, userSub);
  const canWrite = role === 'owner' || role === 'admin';

  const handleJoin = async () => {
    if (!projectId) return;
    setJoining(true);
    setActionError('');
    try {
      await requestToJoin(projectId);
      setJoinRequested(true);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to request access');
    } finally {
      setJoining(false);
    }
  };

  // Top-right role affordance: a badge for owner/admin, else a "Join the team" request button.
  const roleControl =
    role === 'owner' ? (
      <Badge color="green">Owner</Badge>
    ) : role === 'admin' ? (
      <Badge color="blue">Admin</Badge>
    ) : joinRequested ? (
      <Badge color="grey">Request sent</Badge>
    ) : (
      <Button iconName="add-plus" loading={joining} onClick={handleJoin}>
        Join the team
      </Button>
    );

  const deleteButton = (
    <Button iconName="remove" loading={deleting} onClick={() => setConfirmDelete(true)}>
      Delete
    </Button>
  );

  const deleteModal = (
    <Modal
      visible={confirmDelete}
      onDismiss={() => setConfirmDelete(false)}
      header="Delete project"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={() => setConfirmDelete(false)}>
              Cancel
            </Button>
            <Button variant="primary" loading={deleting} onClick={handleDelete}>
              Delete
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      This tears down all resources for <b>{project.project_id}</b> — instances, repos, log group,
      S3 data, and database rows — so it stops incurring AWS cost. The teardown runs in the
      background: the project shows <b>Deleting…</b> in the list and disappears once everything is
      removed. This cannot be undone.
    </Modal>
  );

  // The Start/Stop control lives inline with the Session state (below), not in the header actions.
  // Only owner/admin may start/stop; viewers see the status indicator alone.
  const sessionControl =
    canWrite && !isProvisioning && !isFailed ? (
      <SpaceBetween direction="horizontal" size="xs" alignItems="center">
        {sessionActive ? (
          <Button loading={busy} onClick={handleStop}>
            Stop session
          </Button>
        ) : (
          <Button variant="primary" loading={busy} onClick={handleStart}>
            Start session
          </Button>
        )}
        {/* Info affordance — keeps the Session column narrow; details on click. */}
        <Popover
          dismissButton={false}
          position="top"
          size="small"
          triggerType="custom"
          content="The AI Researcher starts switched off and stops when paused, so it costs nothing while idle. Press Start to run; Stop to pause."
        >
          <Button variant="inline-icon" iconName="status-info" ariaLabel="Session info" />
        </Popover>
      </SpaceBetween>
    ) : null;

  const headerActions = (
    <SpaceBetween direction="horizontal" size="xs" alignItems="center">
      {roleControl}
      <Button iconName="refresh" onClick={refresh}>
        Refresh
      </Button>
      {role === 'owner' && deleteButton}
    </SpaceBetween>
  );

  const header = (
    <Container
      header={
        <Header variant="h1" description={project.description} actions={headerActions}>
          {project.project_id}
        </Header>
      }
    >
      <ColumnLayout columns={5} variant="text-grid">
        <div>
          <Box variant="awsui-key-label">Session</Box>
          <SpaceBetween size="xs">
            {projectStatusIndicator(project.status)}
            {sessionControl}
          </SpaceBetween>
        </div>
        <div>
          <Box variant="awsui-key-label">
            GPU Workers{workers.length ? ` (${workers.length})` : project.worker_count ? ` (${project.worker_count})` : ''}
          </Box>
          {workers.length > 0 ? (
            <SpaceBetween size="xxs">
              {workers.map((w, i) => (
                <SpaceBetween
                  key={w.instance_id || i}
                  direction="horizontal"
                  size="xs"
                  alignItems="center"
                >
                  <Box fontSize="body-s">{workerLabel(w, i)}</Box>
                  {gpuStateBadge(w.state)}
                </SpaceBetween>
              ))}
            </SpaceBetween>
          ) : (
            <Box fontSize="body-s" color="text-status-inactive">—</Box>
          )}
        </div>
        <div>
          <Box variant="awsui-key-label">Best {project.metric_name || 'metric'}</Box>
          {fmtMetric(project.best_metric)}
        </div>
        <div>
          <Box variant="awsui-key-label">Spend</Box>
          {fmtUsd(project.spent_usd)} / {fmtUsd(project.budget_usd)}
        </div>
        <div>
          <Box variant="awsui-key-label">Model</Box>
          <Box fontSize="body-s">{project.model || '—'}</Box>
        </div>
      </ColumnLayout>
    </Container>
  );

  // Provisioning view: progress + live tail of the `provisioning` stream. No tabs.
  if (isProvisioning) {
    const stepIndex = PROVISIONING_STEPS.indexOf(project.provisioning_state || '');
    return (
      <SpaceBetween size="l">
        {actionError && <Alert type="error">{actionError}</Alert>}
        {header}
        <Container
          header={
            <Header variant="h2" description="Setting up instances, repositories, and storage. This can take a few minutes.">
              Provisioning
            </Header>
          }
        >
          <SpaceBetween size="m">
            <SpaceBetween direction="horizontal" size="s" alignItems="center">
              <Spinner />
              <StatusIndicator type="in-progress">
                {project.provisioning_state || 'queued'}
                {stepIndex >= 0 ? ` (step ${stepIndex + 1} of ${PROVISIONING_STEPS.length})` : ''}
              </StatusIndicator>
            </SpaceBetween>
            {project.provisioning_msg && <Box>{project.provisioning_msg}</Box>}
            <Box variant="awsui-key-label">Provisioning log</Box>
            <LogFeed
              projectId={projectId!}
              stream="provisioning"
              height={320}
              showStreamTag={false}
              emptyText="Waiting for provisioning to begin…"
            />
          </SpaceBetween>
        </Container>
        {deleteModal}
      </SpaceBetween>
    );
  }

  // Failure view: actionable error + delete + retry note. No tabs.
  if (isFailed) {
    return (
      <SpaceBetween size="l">
        {header}
        <Alert
          type="error"
          header="Provisioning failed"
          action={
            <Button loading={deleting} onClick={() => setConfirmDelete(true)}>
              Delete project
            </Button>
          }
        >
          <SpaceBetween size="s">
            <Box>{project.provisioning_error || 'The project could not be provisioned.'}</Box>
            <Box color="text-body-secondary" fontSize="body-s">
              Delete this project and retry with different compute — e.g. a g5.xlarge worker for a
              dry-run, or the same p5.4xlarge once GPU capacity is attached (On-Demand Capacity
              Reservation or EC2 Capacity Block).
            </Box>
          </SpaceBetween>
        </Alert>
        {actionError && <Alert type="error">{actionError}</Alert>}
        <Container header={<Header variant="h3">Provisioning log</Header>}>
          <LogFeed
            projectId={projectId!}
            stream="provisioning"
            active={false}
            height={280}
            showStreamTag={false}
            emptyText="No provisioning log available."
          />
        </Container>
        {deleteModal}
      </SpaceBetween>
    );
  }

  // Normal view: header + tabs + routed sub-view.
  const base = `/projects/${projectId}`;
  const rest = location.pathname.slice(base.length).replace(/^\//, '').split('/')[0];
  // Reveal the Paper tab only once a built PDF exists; the Team tab only for owner/admin.
  const tabs = [
    ...TABS,
    ...(project.paper_pdf_s3_uri ? [{ id: 'paper', label: 'Paper' }] : []),
    ...(canWrite ? [{ id: 'team', label: 'Team' }] : []),
  ];
  const activeTabId = tabs.some((t) => t.id === rest) ? rest : '';

  return (
    <SpaceBetween size="l">
      {actionError && <Alert type="error">{actionError}</Alert>}
      {header}
      <Tabs
        activeTabId={activeTabId}
        onChange={({ detail }) =>
          navigate(detail.activeTabId ? `${base}/${detail.activeTabId}` : base)
        }
        tabs={tabs.map((t) => ({ id: t.id, label: t.label, content: null }))}
      />
      <Outlet context={{ project, refresh, role } satisfies ProjectContext} />
      {deleteModal}
    </SpaceBetween>
  );
}
