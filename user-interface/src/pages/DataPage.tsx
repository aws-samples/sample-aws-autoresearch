import { useCallback, useState } from 'react';
import { useParams } from 'react-router-dom';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Container from '@cloudscape-design/components/container';
import Grid from '@cloudscape-design/components/grid';
import Header from '@cloudscape-design/components/header';
import Spinner from '@cloudscape-design/components/spinner';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import FileTree from '../components/FileTree';
import { getDataTree, getDataUrl, getDataPreview, type TreeEntry } from '../services/api';

// Extensions we can render inline as text; everything else is download-only.
const TEXT_EXT = new Set([
  'txt', 'md', 'json', 'jsonl', 'csv', 'tsv', 'log', 'py', 'sh', 'yaml', 'yml', 'toml', 'html', 'xml',
]);

function isText(path: string): boolean {
  return TEXT_EXT.has(path.split('.').pop()?.toLowerCase() || '');
}

export default function DataPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const [selected, setSelected] = useState<string | undefined>();
  const [preview, setPreview] = useState('');
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // The data_viewer endpoint is already scoped to this project's S3 folder ({projectId}/...),
  // so the tree only ever shows files under the project folder.
  const loadChildren = useCallback(
    (path: string): Promise<TreeEntry[]> => getDataTree(projectId!, path),
    [projectId]
  );

  const openFile = useCallback(
    async (path: string) => {
      if (!projectId || !path) return;
      setSelected(path);
      setError('');
      setTruncated(false);
      if (!isText(path)) {
        setPreview('');
        return;
      }
      setLoading(true);
      try {
        const res = await getDataPreview(projectId, path);
        setPreview(res.content || '');
        setTruncated(!!res.truncated);
      } catch (err) {
        setPreview('');
        setError(err instanceof Error ? err.message : 'Failed to load preview');
      } finally {
        setLoading(false);
      }
    },
    [projectId]
  );

  const download = async () => {
    if (!projectId || !selected) return;
    try {
      const url = await getDataUrl(projectId, selected);
      if (url) window.open(url, '_blank', 'noopener,noreferrer');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to get download link');
    }
  };

  return (
    <SpaceBetween size="s">
      {error && <StatusIndicator type="warning">{error}</StatusIndicator>}
      <Grid gridDefinition={[{ colspan: 4 }, { colspan: 8 }]}>
        <Container header={<Header variant="h3">Files</Header>}>
          <FileTree loadChildren={loadChildren} selectedPath={selected} onSelect={openFile} />
        </Container>
        <Container
          header={
            <Header
              variant="h3"
              actions={
                selected ? (
                  <Button iconName="download" onClick={download}>
                    Download
                  </Button>
                ) : undefined
              }
            >
              {selected || 'Select a file'}
            </Header>
          }
        >
          {!selected ? (
            <Box color="text-status-inactive">Choose a file from the tree.</Box>
          ) : loading ? (
            <Box textAlign="center" padding="l">
              <Spinner />
            </Box>
          ) : isText(selected) ? (
            <SpaceBetween size="xs">
              {truncated && (
                <StatusIndicator type="info">
                  Large file — showing the first part only. Use Download for the full file.
                </StatusIndicator>
              )}
              <pre
                style={{
                  margin: 0,
                  padding: 12,
                  fontSize: 12,
                  overflow: 'auto',
                  maxHeight: '70vh',
                  borderRadius: 6,
                  background: '#f6f8fa',
                  whiteSpace: 'pre-wrap',
                }}
              >
                {preview}
              </pre>
            </SpaceBetween>
          ) : (
            <Box color="text-status-inactive">
              Binary file — use <b>Download</b> to fetch it (checkpoints, images, archives, …).
            </Box>
          )}
        </Container>
      </Grid>
    </SpaceBetween>
  );
}
