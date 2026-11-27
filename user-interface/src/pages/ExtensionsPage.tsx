import { useEffect, useState } from 'react';
import Alert from '@cloudscape-design/components/alert';
import Box from '@cloudscape-design/components/box';
import Header from '@cloudscape-design/components/header';
import Link from '@cloudscape-design/components/link';
import Table from '@cloudscape-design/components/table';
import { listExtensions, type InstalledExtension } from '../services/api';

// Read-only: the domain extensions installed in this deployment (each by its own AutoResearchExtension-<name>
// stack; design.md "Domain extensions"). Projects enable them in the Create Project form.
export default function ExtensionsPage() {
  const [items, setItems] = useState<InstalledExtension[] | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    listExtensions()
      .then(setItems)
      .catch((e) => {
        setError(e instanceof Error ? e.message : 'Could not load the extensions');
        setItems([]);
      });
  }, []);

  return (
    <Table
      header={
        <Header
          variant="h1"
          counter={items ? `(${items.length})` : undefined}
          description="Specialised external systems a project's AI Researcher can use (simulation, training, lab APIs). Installed per deployment with CDK; each enabled project gets its own credentials, and the extension's calls count against the project's budget."
        >
          Domain extensions
        </Header>
      }
      loading={items === null}
      items={items ?? []}
      empty={
        error ? <Alert type="error">{error}</Alert> : (
          <Box textAlign="center" color="inherit">
            No domain extensions are installed. Deploy one with <code>./scripts/deploy.sh -c extensions=&lt;dir&gt;</code>.
          </Box>
        )
      }
      columnDefinitions={[
        { id: 'name', header: 'Extension', cell: (e) => <><Box fontWeight="bold">{e.display_name}</Box><Box fontSize="body-s">{e.name}</Box></> },
        { id: 'description', header: 'What it does', cell: (e) => e.description },
        { id: 'cost', header: 'Cost', cell: (e) => <>{e.cost_note || '—'}{e.metered && <Box fontSize="body-s" color="text-status-info">Metered into project spend</Box>}</> },
        { id: 'credentials', header: 'Project credentials', cell: (e) => e.provider_type || '—' },
        { id: 'web', header: 'Web app', cell: (e) => (e.web_url ? <Link external href={e.web_url}>Open</Link> : '—') },
        { id: 'installed', header: 'Installed', cell: (e) => new Date(e.installed_at).toLocaleString() },
      ]}
      wrapLines
    />
  );
}
