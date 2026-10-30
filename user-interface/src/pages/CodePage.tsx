import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import Box from '@cloudscape-design/components/box';
import Container from '@cloudscape-design/components/container';
import Grid from '@cloudscape-design/components/grid';
import Header from '@cloudscape-design/components/header';
import Link from '@cloudscape-design/components/link';
import Spinner from '@cloudscape-design/components/spinner';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Table from '@cloudscape-design/components/table';
import Tabs from '@cloudscape-design/components/tabs';
import { Highlight, themes } from 'prism-react-renderer';
import FileTree from '../components/FileTree';
import {
  getCodeTree,
  getCodeFile,
  getCodeCommits,
  getCodeDiff,
  type Commit,
  type FileDiff,
} from '../services/api';

const LANG_BY_EXT: Record<string, string> = {
  py: 'python',
  ts: 'typescript',
  tsx: 'tsx',
  js: 'javascript',
  jsx: 'jsx',
  json: 'json',
  md: 'markdown',
  sh: 'bash',
  yaml: 'yaml',
  yml: 'yaml',
  toml: 'toml',
};

function langOf(path: string): string {
  const ext = path.split('.').pop()?.toLowerCase() || '';
  return LANG_BY_EXT[ext] || 'text';
}

function CodeBlock({ code, language }: { code: string; language: string }) {
  return (
    <Highlight theme={themes.github} code={code} language={language}>
      {({ style, tokens, getLineProps, getTokenProps }) => (
        <pre style={{ ...style, margin: 0, padding: 12, fontSize: 12, overflow: 'auto', maxHeight: '70vh', borderRadius: 6 }}>
          {tokens.map((line, i) => (
            <div key={i} {...getLineProps({ line })}>
              <span style={{ display: 'inline-block', width: 40, color: '#aab7b8', userSelect: 'none' }}>{i + 1}</span>
              {line.map((token, key) => (
                <span key={key} {...getTokenProps({ token })} />
              ))}
            </div>
          ))}
        </pre>
      )}
    </Highlight>
  );
}

// Lightweight unified-diff renderer — reused for per-commit diffs (the repo deliberately avoids a
// React-version-coupled diff library; this renders the backend's unified-diff text with coloring).
function DiffBlock({ diff }: { diff: string }) {
  return (
    <pre style={{ margin: 0, padding: 12, fontSize: 12, overflow: 'auto', maxHeight: '70vh', borderRadius: 6, background: '#f6f8fa' }}>
      {diff.split('\n').map((line, i) => {
        let bg: string | undefined;
        let color = '#1b1b1b';
        if (line.startsWith('+') && !line.startsWith('+++')) {
          bg = '#e6ffed';
          color = '#046a1f';
        } else if (line.startsWith('-') && !line.startsWith('---')) {
          bg = '#ffeef0';
          color = '#b31d28';
        } else if (line.startsWith('@@')) {
          color = '#0969da';
        } else if (line.startsWith('diff ') || line.startsWith('index ') || line.startsWith('+++') || line.startsWith('---')) {
          color = '#687078';
        }
        return (
          <div key={i} style={{ background: bg, color, whiteSpace: 'pre-wrap' }}>
            {line || ' '}
          </div>
        );
      })}
    </pre>
  );
}

export default function CodePage() {
  const { projectId } = useParams<{ projectId: string }>();
  const [selected, setSelected] = useState<string | undefined>();
  const [content, setContent] = useState('');
  const [loadingFile, setLoadingFile] = useState(false);
  const [error, setError] = useState('');

  const [commits, setCommits] = useState<Commit[]>([]);
  const [selectedCommit, setSelectedCommit] = useState<string | undefined>();
  const [commitDiffs, setCommitDiffs] = useState<FileDiff[] | null>(null);
  const [loadingDiff, setLoadingDiff] = useState(false);

  const loadChildren = useCallback((path: string) => getCodeTree(projectId!, path), [projectId]);

  const openFile = useCallback(
    async (path: string) => {
      if (!projectId || !path) return;
      setSelected(path);
      setLoadingFile(true);
      try {
        const res = await getCodeFile(projectId, path);
        setContent(res.content || '');
      } catch (err) {
        setContent('');
        setError(err instanceof Error ? err.message : 'Failed to load file');
      } finally {
        setLoadingFile(false);
      }
    },
    [projectId]
  );

  useEffect(() => {
    if (!projectId) return;
    getCodeCommits(projectId).then(setCommits).catch(() => {/* optional */});
  }, [projectId]);

  // Click a commit → show the diff it introduced (commit vs its parent).
  const openCommitDiff = useCallback(
    async (id: string) => {
      if (!projectId || !id) return;
      setSelectedCommit(id);
      setCommitDiffs(null);
      setLoadingDiff(true);
      try {
        setCommitDiffs(await getCodeDiff(projectId, id));
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load diff');
        setCommitDiffs([]);
      } finally {
        setLoadingDiff(false);
      }
    },
    [projectId]
  );

  const filesTab = (
    <Grid gridDefinition={[{ colspan: 4 }, { colspan: 8 }]}>
      <Container header={<Header variant="h3">Files</Header>}>
        <FileTree loadChildren={loadChildren} selectedPath={selected} onSelect={openFile} />
      </Container>
      <Container header={<Header variant="h3">{selected || 'Select a file'}</Header>}>
        {loadingFile ? (
          <Box textAlign="center" padding="l">
            <Spinner />
          </Box>
        ) : selected ? (
          <CodeBlock code={content} language={langOf(selected)} />
        ) : (
          <Box color="text-status-inactive">Choose a file from the tree.</Box>
        )}
      </Container>
    </Grid>
  );

  const commitsTab = (
    <SpaceBetween size="m">
      <Table
        variant="container"
        header={<Header variant="h3" counter={`(${commits.length})`}>Commits</Header>}
        items={commits}
        empty="No commits available."
        columnDefinitions={[
          {
            id: 'sha',
            header: 'Commit',
            cell: (c) => {
              const id = c.id || c.sha || '';
              return (
                <Link href="#" onFollow={(e) => { e.preventDefault(); openCommitDiff(id); }}>
                  {id.slice(0, 10)}
                </Link>
              );
            },
          },
          { id: 'message', header: 'Message', cell: (c) => c.message || '—' },
          { id: 'author', header: 'Author', cell: (c) => c.author || '—' },
          { id: 'date', header: 'Date', cell: (c) => (c.date ? new Date(c.date).toLocaleString() : '—') },
        ]}
      />
      {selectedCommit && (
        <Container header={<Header variant="h3" description={selectedCommit}>Diff — changes in this commit</Header>}>
          {loadingDiff ? (
            <Box textAlign="center" padding="l">
              <Spinner />
            </Box>
          ) : commitDiffs && commitDiffs.length > 0 ? (
            <SpaceBetween size="m">
              {commitDiffs.map((d, i) => (
                <Container key={i} header={<Header variant="h3">{d.path || `file ${i + 1}`}</Header>}>
                  <DiffBlock diff={d.diff || d.patch || '(no textual diff)'} />
                </Container>
              ))}
            </SpaceBetween>
          ) : (
            <Box color="text-status-inactive">No changes in this commit.</Box>
          )}
        </Container>
      )}
    </SpaceBetween>
  );

  return (
    <SpaceBetween size="s">
      {error && <StatusIndicator type="warning">{error}</StatusIndicator>}
      <Tabs
        tabs={[
          { id: 'files', label: 'Files', content: filesTab },
          { id: 'commits', label: 'Commits', content: commitsTab },
        ]}
      />
    </SpaceBetween>
  );
}
