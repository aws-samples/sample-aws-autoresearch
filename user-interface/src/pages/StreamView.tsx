import { useState } from 'react';
import { useParams } from 'react-router-dom';
import Alert from '@cloudscape-design/components/alert';
import Badge from '@cloudscape-design/components/badge';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Textarea from '@cloudscape-design/components/textarea';
import Checkbox from '@cloudscape-design/components/checkbox';
import LogFeed from '../components/LogFeed';
import { useProject } from '../components/ProjectLayout';
import { getDataUrl, submitGuidance, setWritePaper } from '../services/api';

const RESEARCHER_LOG_KEY = 'logs/researcher.jsonl';

export default function StreamView() {
  const { projectId } = useParams<{ projectId: string }>();
  const [guidance, setGuidance] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState('');
  const [downloading, setDownloading] = useState(false);
  const { project, refresh, role } = useProject();
  const [paperBusy, setPaperBusy] = useState(false);

  const handleToggleWritePaper = async (checked: boolean) => {
    if (!projectId) return;
    setPaperBusy(true);
    try {
      await setWritePaper(projectId, checked);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update the paper setting');
    } finally {
      setPaperBusy(false);
    }
  };

  const handleDownloadFull = async () => {
    if (!projectId) return;
    setDownloading(true);
    try {
      const url = await getDataUrl(projectId, RESEARCHER_LOG_KEY);
      if (url) window.open(url, '_blank', 'noopener');
      else setError('Full researcher log is not available yet.');
    } catch {
      setError('Full researcher log is not available yet.');
    } finally {
      setDownloading(false);
    }
  };

  const handleSubmitGuidance = async () => {
    if (!projectId || !guidance.trim()) return;
    setSubmitting(true);
    setSubmitted(false);
    try {
      await submitGuidance(projectId, guidance.trim());
      setGuidance('');
      setSubmitted(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to submit guidance');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <SpaceBetween size="l">
      <Container
        header={
          <Header
            variant="h2"
            description="Live tail of the AI Researcher — instance boot (deps, bundle sync, clone, bootstrap) through the running research loop."
            actions={
              <Button iconName="download" loading={downloading} onClick={handleDownloadFull}>
                Download full researcher log
              </Button>
            }
          >
            Researcher Stream
          </Header>
        }
      >
        {error && <Alert type="error" dismissible onDismiss={() => setError('')}>{error}</Alert>}
        <LogFeed
          projectId={projectId!}
          stream="boot"
          height="55vh"
          emptyText="No activity yet. The stream updates every few seconds."
        />
      </Container>

      {/* Guidance is injected into the autonomous agent loop → owner/admin only (also enforced by the API). */}
      {(role === 'owner' || role === 'admin') && <Container
        header={
          <Header
            variant="h2"
            description="Steer the researcher — a proposal, a redirection, or a focus. It is pulled at the top of the next loop."
          >
            Submit guidance
          </Header>
        }
      >
        <SpaceBetween size="s">
          {submitted && (
            <Alert type="success" dismissible onDismiss={() => setSubmitted(false)}>
              Guidance submitted to the researcher's inbox.
            </Alert>
          )}
          <Textarea
            value={guidance}
            onChange={({ detail }) => setGuidance(detail.value)}
            placeholder="e.g. Focus on optimizer changes; try Muon with a higher learning rate before revisiting the LR schedule."
            rows={3}
          />
          <Box>
            <Button
              variant="primary"
              loading={submitting}
              disabled={!guidance.trim()}
              onClick={handleSubmitGuidance}
            >
              Send guidance
            </Button>{' '}
            <Badge color="grey">proposed_by: human</Badge>
          </Box>
          <Checkbox
            checked={!!project?.write_paper}
            disabled={paperBusy}
            onChange={({ detail }) => handleToggleWritePaper(detail.checked)}
          >
            Write a scientific paper when the research completes (budget/patience exhausted).
          </Checkbox>
        </SpaceBetween>
      </Container>}
    </SpaceBetween>
  );
}
