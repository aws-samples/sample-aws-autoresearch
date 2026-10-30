import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import Box from '@cloudscape-design/components/box';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import Spinner from '@cloudscape-design/components/spinner';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Link from '@cloudscape-design/components/link';
import Button from '@cloudscape-design/components/button';
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { getCodeFile, getDataUrl } from '../services/api';
import { slugify } from '../lib/wikilinks';

const REPORT_PATH = 'report/status.md';

// Resolves ./report/foo.png (and report/foo.png) to a presigned S3 URL lazily.
function PresignedImage({
  projectId,
  src,
  alt,
}: {
  projectId: string;
  src?: string;
  alt?: string;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!src) return;
    if (/^https?:\/\//.test(src)) {
      setUrl(src);
      return;
    }
    const key = src.replace(/^\.\//, '').replace(/^\//, '');
    getDataUrl(projectId, key)
      .then((u) => (u ? setUrl(u) : setFailed(true)))
      .catch(() => setFailed(true));
  }, [projectId, src]);

  if (failed) return <Box color="text-status-inactive">[chart unavailable: {alt || src}]</Box>;
  if (!url) return <Spinner />;
  return <img src={url} alt={alt || ''} style={{ maxWidth: '100%', borderRadius: 6 }} />;
}

export default function StatusView() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const [content, setContent] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = () => {
    if (!projectId) return;
    setLoading(true);
    setError('');
    getCodeFile(projectId, REPORT_PATH)
      .then((res) => setContent(res.content || ''))
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load report'))
      .finally(() => setLoading(false));
  };

  useEffect(load, [projectId]);

  // Convert [[wikilink]] into a markdown link the renderer intercepts.
  const rendered = content.replace(/\[\[([^\]]+)\]\]/g, (_m, text: string) => {
    return `[${text}](wiki:${slugify(text)})`;
  });

  return (
    <Container
      header={
        <Header
          variant="h2"
          actions={
            <Button iconName="refresh" onClick={load}>
              Refresh
            </Button>
          }
        >
          Research status report
        </Header>
      }
    >
      {loading ? (
        <Box textAlign="center" padding="l">
          <Spinner size="large" />
        </Box>
      ) : error ? (
        <StatusIndicator type="warning">
          {error} — the agent may not have written {REPORT_PATH} yet.
        </StatusIndicator>
      ) : (
        <div className="markdown-body" style={{ lineHeight: 1.6 }}>
          {/* SECURITY: this content is agent/repo-authored → untrusted. react-markdown@9 does NOT
              render raw HTML by default, which is what keeps it XSS-safe. NEVER add `rehype-raw` or
              `dangerouslySetInnerHTML` here, and keep the urlTransform allowlist minimal. */}
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            // react-markdown blanks non-http(s) protocols by default, which would strip
            // the wiki:<slug> hrefs our [[wikilinks]] compile to. Let those through.
            urlTransform={(url) => (url.startsWith('wiki:') ? url : defaultUrlTransform(url))}
            components={{
              img: ({ src, alt }) => (
                <PresignedImage projectId={projectId!} src={typeof src === 'string' ? src : undefined} alt={alt} />
              ),
              a: ({ href, children }) => {
                if (href && href.startsWith('wiki:')) {
                  const slug = href.slice('wiki:'.length);
                  return (
                    <Link
                      href="#"
                      onFollow={(e) => {
                        e.preventDefault();
                        navigate(`/projects/${projectId}/wiki?name=${encodeURIComponent(slug)}`);
                      }}
                    >
                      {children}
                    </Link>
                  );
                }
                return (
                  <a href={href} target="_blank" rel="noopener noreferrer">
                    {children}
                  </a>
                );
              },
            }}
          >
            {rendered}
          </ReactMarkdown>
        </div>
      )}
    </Container>
  );
}
