import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Header from '@cloudscape-design/components/header';
import Modal from '@cloudscape-design/components/modal';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Table from '@cloudscape-design/components/table';
import {
  listProjects,
  deleteProject,
  getCurrentUserId,
  projectRole,
  fmtUsd,
  type Project,
} from '../services/api';

export default function HomePage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [target, setTarget] = useState<Project | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [userSub, setUserSub] = useState('');
  const navigate = useNavigate();

  useEffect(() => {
    getCurrentUserId().then(setUserSub).catch(() => setUserSub(''));
  }, []);

  const load = (silent = false) => {
    if (!silent) setLoading(true);
    listProjects()
      .then(setProjects)
      .catch(() => setProjects([]))
      .finally(() => {
        if (!silent) setLoading(false);
      });
  };

  // Poll so provisioning/deleting projects update live — a deleting project shows "Deleting…" and
  // then disappears from the list once its async teardown removes the row (real cleanup feedback).
  useEffect(() => {
    load();
    const id = setInterval(() => load(true), 5000);
    return () => clearInterval(id);
  }, []);

  const handleDelete = async () => {
    if (!target) return;
    setDeleting(true);
    try {
      await deleteProject(target.project_id); // returns 202; teardown runs async
      setTarget(null);
      load(true); // row flips to "Deleting…", then vanishes on a later poll
    } finally {
      setDeleting(false);
    }
  };

  const statusIndicator = (status: string) => {
    switch (status) {
      case 'provisioning':
        return <StatusIndicator type="in-progress">Provisioning</StatusIndicator>;
      case 'active':
        return <StatusIndicator type="success">Active</StatusIndicator>;
      case 'setup':
        return <StatusIndicator type="pending">Setup</StatusIndicator>;
      case 'paused':
        return <StatusIndicator type="stopped">Paused</StatusIndicator>;
      case 'deleting':
        return <StatusIndicator type="in-progress">Deleting…</StatusIndicator>;
      case 'provision_failed':
        return <StatusIndicator type="error">Provision failed</StatusIndicator>;
      case 'completed':
        return <StatusIndicator type="info">Completed</StatusIndicator>;
      default:
        return <StatusIndicator type="info">{status}</StatusIndicator>;
    }
  };

  return (
    <>
      <Table
        header={
          <Header
            variant="h1"
            actions={
              <Button variant="primary" onClick={() => navigate('/projects/new')}>
                Create New Project
              </Button>
            }
            counter={`(${projects.length})`}
          >
            Research Projects
          </Header>
        }
        items={projects}
        loading={loading}
        loadingText="Loading projects..."
        empty="No projects yet. Create your first research project."
        columnDefinitions={[
          {
            id: 'name',
            header: 'Project',
            minWidth: 160,
            width: 200,
            cell: (item) => (
              <Button variant="link" onClick={() => navigate(`/projects/${item.project_id}`)}>
                {item.project_id}
              </Button>
            ),
          },
          { id: 'status', header: 'Status', cell: (item) => statusIndicator(item.status) },
          {
            id: 'owner',
            header: 'Owner',
            cell: (item) => item.owner_username || '—',
          },
          {
            id: 'experiments',
            header: 'Experiments',
            cell: (item) =>
              item.experiment_count === undefined || item.experiment_count === null
                ? '—'
                : item.experiment_count,
          },
          {
            id: 'description',
            header: 'Description',
            cell: (item) =>
              (item.description || '').slice(0, 80) + ((item.description || '').length > 80 ? '…' : ''),
          },
          {
            id: 'cost',
            header: 'Spend',
            cell: (item) => `${fmtUsd(item.spent_usd)} / ${fmtUsd(item.budget_usd)}`,
          },
          {
            id: 'created',
            header: 'Created',
            cell: (item) =>
              item.created_at
                ? new Date(item.created_at).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })
                : '—',
          },
          {
            id: 'actions',
            header: '',
            // Only the owner can delete (server-enforced too); others see no delete affordance.
            cell: (item) =>
              projectRole(item, userSub) === 'owner' ? (
                <Button iconName="remove" variant="inline-icon" ariaLabel="Delete project" onClick={() => setTarget(item)} />
              ) : null,
          },
        ]}
      />

      <Modal
        visible={!!target}
        onDismiss={() => setTarget(null)}
        header="Delete project"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setTarget(null)}>
                Cancel
              </Button>
              <Button variant="primary" loading={deleting} onClick={handleDelete}>
                Delete
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        This tears down all resources for <b>{target?.project_id}</b> — instances, repos, log group,
        S3 data, and database rows. This cannot be undone.
      </Modal>
    </>
  );
}
