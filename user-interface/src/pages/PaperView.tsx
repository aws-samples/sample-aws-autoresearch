import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import { useProject } from '../components/ProjectLayout';
import { getDataUrl } from '../services/api';

const PAPER_KEY = 'paper/paper.pdf';

export default function PaperView() {
  const { projectId } = useParams<{ projectId: string }>();
  const { project } = useProject();
  const [url, setUrl] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  const load = () => {
    if (!projectId) return;
    setLoading(true);
    setError('');
    getDataUrl(projectId, PAPER_KEY)
      .then((u) => (u ? setUrl(u) : setError('The paper PDF is not available yet.')))
      .catch(() => setError('The paper PDF is not available yet.'))
      .finally(() => setLoading(false));
  };

  useEffect(load, [projectId]);

  if (!project?.paper_pdf_s3_uri) {
    return (
      <Container header={<Header variant="h2">Paper</Header>}>
        <StatusIndicator type="pending">
          No paper has been generated yet. It is written automatically when the research completes
          (budget or patience exhausted) and “Write a scientific paper” is enabled.
        </StatusIndicator>
      </Container>
    );
  }

  return (
    <Container
      header={
        <Header
          variant="h2"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load}>
                Refresh
              </Button>
              <Button
                iconName="external"
                disabled={!url}
                onClick={() => url && window.open(url, '_blank', 'noopener')}
              >
                Open / Download
              </Button>
            </SpaceBetween>
          }
        >
          Paper
        </Header>
      }
    >
      {loading && <StatusIndicator type="loading">Loading PDF…</StatusIndicator>}
      {error && <StatusIndicator type="error">{error}</StatusIndicator>}
      {url && (
        // Native browser PDF rendering — no extra dependency. Falls back to a link if unsupported.
        <object data={url} type="application/pdf" width="100%" style={{ height: '80vh' }}>
          <Box padding="m">
            Your browser can’t display the PDF inline.{' '}
            <a href={url} target="_blank" rel="noopener noreferrer">
              Open the paper in a new tab
            </a>
            .
          </Box>
        </object>
      )}
    </Container>
  );
}
