import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Alert from '@cloudscape-design/components/alert';
import Badge from '@cloudscape-design/components/badge';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import Input from '@cloudscape-design/components/input';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Table from '@cloudscape-design/components/table';
import {
  getMe,
  listProjects,
  listUsers,
  setUserBudget,
  fmtUsd,
  type Project,
  type UserInfo,
} from '../services/api';

export default function UserPage() {
  const navigate = useNavigate();
  const [me, setMe] = useState<UserInfo | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [users, setUsers] = useState<UserInfo[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({}); // sub -> edited budget string
  const [savingSub, setSavingSub] = useState('');
  const [error, setError] = useState('');

  const loadMe = useCallback(async () => {
    const m = await getMe();
    setMe(m);
    return m;
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const m = await loadMe();
        const all = await listProjects();
        setProjects(all.filter((p) => p.owner === m.user_sub));
        if (m.is_budget_admin) setUsers(await listUsers());
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load your account');
      }
    })();
  }, [loadMe]);

  const saveBudget = async (u: UserInfo) => {
    const raw = drafts[u.user_sub];
    const value = Number(raw);
    if (!Number.isFinite(value) || value < 0) {
      setError('Budget must be a number ≥ 0.');
      return;
    }
    setSavingSub(u.user_sub);
    setError('');
    try {
      await setUserBudget(u.user_sub, value, u.username);
      setUsers(await listUsers());
      if (u.user_sub === me?.user_sub) await loadMe(); // editing myself → refresh My Budget
      setDrafts((d) => {
        const next = { ...d };
        delete next[u.user_sub];
        return next;
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update budget');
    } finally {
      setSavingSub('');
    }
  };

  return (
    <SpaceBetween size="l">
      {error && <Alert type="error" dismissible onDismiss={() => setError('')}>{error}</Alert>}

      <Container
        header={
          <Header
            variant="h1"
            description={me?.username ? `Signed in as ${me.username}` : undefined}
          >
            My account
          </Header>
        }
      >
        <KeyValuePairs
          columns={2}
          items={[
            { label: 'My budget (available)', value: me ? fmtUsd(me.budget) : '—' },
            {
              label: 'Role',
              value: me?.is_budget_admin ? <Badge color="blue">Budget administrator</Badge> : 'User',
            },
          ]}
        />
      </Container>

      {/* My projects — those I own. Each shows its max budget and spend so far. */}
      <Container header={<Header variant="h2" counter={`(${projects.length})`}>My projects</Header>}>
        <Table
          items={projects}
          empty="You don't own any projects yet."
          columnDefinitions={[
            {
              id: 'name',
              header: 'Project',
              cell: (p) => (
                <Button variant="link" onClick={() => navigate(`/projects/${p.project_id}`)}>
                  {p.project_id}
                </Button>
              ),
            },
            { id: 'status', header: 'Status', cell: (p) => p.status },
            { id: 'budget', header: 'Max budget', cell: (p) => fmtUsd(p.budget_usd) },
            { id: 'spent', header: 'Spent', cell: (p) => fmtUsd(p.spent_usd) },
          ]}
        />
      </Container>

      {/* Budget administration — only for budget admins. Edit any user's budget in place. */}
      {me?.is_budget_admin && (
        <Container header={<Header variant="h2" counter={`(${users.length})`}>All users — budgets</Header>}>
          <Table
            items={users}
            empty="No users found."
            columnDefinitions={[
              { id: 'username', header: 'User', cell: (u) => u.username || u.user_sub },
              {
                id: 'admin',
                header: 'Role',
                cell: (u) => (u.is_budget_admin ? <Badge color="blue">Budget admin</Badge> : 'User'),
              },
              { id: 'current', header: 'Current budget', cell: (u) => fmtUsd(u.budget) },
              {
                id: 'edit',
                header: 'Set budget (USD)',
                cell: (u) => (
                  <SpaceBetween direction="horizontal" size="xs">
                    <Input
                      type="number"
                      value={drafts[u.user_sub] ?? String(u.budget)}
                      onChange={({ detail }) =>
                        setDrafts((d) => ({ ...d, [u.user_sub]: detail.value }))
                      }
                    />
                    <Button
                      variant="primary"
                      loading={savingSub === u.user_sub}
                      disabled={drafts[u.user_sub] === undefined || drafts[u.user_sub] === String(u.budget)}
                      onClick={() => saveBudget(u)}
                    >
                      Save
                    </Button>
                  </SpaceBetween>
                ),
              },
            ]}
          />
        </Container>
      )}
    </SpaceBetween>
  );
}
