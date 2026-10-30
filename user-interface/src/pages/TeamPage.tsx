import { useCallback, useEffect, useState } from 'react';
import Alert from '@cloudscape-design/components/alert';
import Badge from '@cloudscape-design/components/badge';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Table from '@cloudscape-design/components/table';
import { useProject } from '../components/ProjectLayout';
import {
  admitMember,
  getTeam,
  removeMember,
  type TeamInfo,
  type TeamRequest,
} from '../services/api';

// Owner/admin-only view: shows the owner, current admins, and pending join requests. The tab is
// only rendered when the caller is owner/admin (see ProjectLayout), and the API also enforces this.
export default function TeamPage() {
  const { project, role, refresh } = useProject();
  const projectId = project.project_id;
  const [team, setTeam] = useState<TeamInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [busySub, setBusySub] = useState('');   // which row is mid-action
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      setTeam(await getTeam(projectId));
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load the team');
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    load();
  }, [load]);

  const handleAdmit = async (r: TeamRequest) => {
    setBusySub(r.user_sub);
    try {
      await admitMember(projectId, r.user_sub);
      await Promise.all([load(), refresh()]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to admit member');
    } finally {
      setBusySub('');
    }
  };

  const handleRemove = async (sub: string) => {
    setBusySub(sub);
    try {
      await removeMember(projectId, sub);
      await Promise.all([load(), refresh()]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to remove member');
    } finally {
      setBusySub('');
    }
  };

  const adminEntries = Object.entries(team?.admins || {});   // [[sub, username], ...]
  const requests = team?.requests || [];

  return (
    <SpaceBetween size="l">
      {error && <Alert type="error" dismissible onDismiss={() => setError('')}>{error}</Alert>}

      {/* Owner + admins list. Admins have write access; only the owner can remove them. */}
      <Container
        header={<Header variant="h2" counter={`(${1 + adminEntries.length})`}>Members</Header>}
      >
        <Table
          items={[
            {
              sub: team?.owner || project.owner || '',
              username: team?.owner_username || project.owner_username || '',
              kind: 'owner' as const,
            },
            ...adminEntries.map(([sub, username]) => ({ sub, username, kind: 'admin' as const })),
          ]}
          loading={loading}
          empty="No members yet."
          columnDefinitions={[
            {
              id: 'user',
              header: 'User',
              cell: (m) => <Box fontWeight="bold">{m.username || m.sub || '—'}</Box>,
            },
            {
              id: 'role',
              header: 'Role',
              cell: (m) =>
                m.kind === 'owner' ? <Badge color="green">Owner</Badge> : <Badge color="blue">Admin</Badge>,
            },
            {
              id: 'actions',
              header: '',
              // Owner cannot be removed; only the owner sees the Remove button on admins.
              cell: (m) =>
                m.kind === 'admin' && role === 'owner' ? (
                  <Button
                    iconName="remove"
                    variant="inline-icon"
                    ariaLabel={`Remove ${m.username}`}
                    loading={busySub === m.sub}
                    onClick={() => handleRemove(m.sub)}
                  />
                ) : null,
            },
          ]}
        />
      </Container>

      {/* Pending "Join the team" requests — an Admit action promotes them to admin. */}
      <Container
        header={<Header variant="h2" counter={`(${requests.length})`}>Join requests</Header>}
      >
        <Table
          items={requests}
          loading={loading}
          empty="No pending requests."
          columnDefinitions={[
            {
              id: 'user',
              header: 'User',
              cell: (r) => <Box fontWeight="bold">{r.username || r.user_sub}</Box>,
            },
            {
              id: 'note',
              header: 'Note',
              cell: (r) => r.note || '—',
            },
            {
              id: 'requested',
              header: 'Requested',
              cell: (r) => (r.requested_at ? new Date(r.requested_at).toLocaleString() : '—'),
            },
            {
              id: 'actions',
              header: '',
              cell: (r) => (
                <Button
                  variant="primary"
                  loading={busySub === r.user_sub}
                  onClick={() => handleAdmit(r)}
                >
                  Admit
                </Button>
              ),
            },
          ]}
        />
      </Container>
    </SpaceBetween>
  );
}
