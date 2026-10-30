import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import Badge from '@cloudscape-design/components/badge';
import Box from '@cloudscape-design/components/box';
import BreadcrumbGroup from '@cloudscape-design/components/breadcrumb-group';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import Link from '@cloudscape-design/components/link';
import Spinner from '@cloudscape-design/components/spinner';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Tabs from '@cloudscape-design/components/tabs';
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import ForceGraph2D from 'react-force-graph-2d';
import { slugify } from '../lib/wikilinks';
import { getWikiModel, type WikiModel, type WikiPage } from '../lib/wikiModel';

/* eslint-disable @typescript-eslint/no-explicit-any */

const GROUP_COLORS: Record<string, string> = {
  concepts: '#0972d3',
  entities: '#037f0c',
  comparisons: '#ec7211',
  queries: '#9469d6',
  root: '#687078',
};

function groupColor(group: string): string {
  return GROUP_COLORS[group] || '#687078';
}

// Scaffolding pages excluded from the graph (they link to everything and add no signal).
const GRAPH_EXCLUDE = new Set(['index.md', 'SCHEMA.md', 'log.md']);

// Resolve a repo-relative path (with ./ ../ or leading /) against the folder of
// the current page. e.g. base 'concepts/l4.md' + 'research-context.md' →
// 'concepts/research-context.md'; base + '../index.md' → 'index.md'.
function resolveRelativePath(basePath: string, rel: string): string {
  const dir = basePath.includes('/') ? basePath.slice(0, basePath.lastIndexOf('/')) : '';
  let p = rel.split(/[?#]/)[0];
  const parts: string[] = [];
  if (p.startsWith('/')) {
    p = p.slice(1);
  } else if (dir) {
    parts.push(...dir.split('/'));
  }
  for (const seg of p.split('/')) {
    if (seg === '' || seg === '.') continue;
    if (seg === '..') {
      parts.pop();
      continue;
    }
    parts.push(seg);
  }
  return parts.join('/');
}

// Resolve any in-wiki link form to a page: wiki:<slug>, a repo-relative .md
// path, or (fallback) the slugified basename. Returns undefined for external
// links (http/mailto) or links that resolve to no page.
function resolveWikiLink(
  href: string | undefined,
  currentPath: string,
  model: WikiModel
): WikiPage | undefined {
  if (!href) return undefined;
  if (href.startsWith('wiki:')) return model.bySlug.get(slugify(href.slice('wiki:'.length)));
  if (/^(https?:|mailto:|#)/i.test(href)) return undefined;

  const clean = href.split(/[?#]/)[0];
  const normalized = resolveRelativePath(currentPath, clean);
  let pg = model.byPath.get(normalized);
  if (pg) return pg;
  if (!normalized.endsWith('.md')) {
    pg = model.byPath.get(`${normalized}.md`);
    if (pg) return pg;
  }
  const base = normalized.split('/').pop() || normalized;
  return model.bySlug.get(slugify(base));
}

export default function WikiPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const [searchParams, setSearchParams] = useSearchParams();

  const [model, setModel] = useState<WikiModel | null>(null);
  const [loadingModel, setLoadingModel] = useState(true);
  const [modelError, setModelError] = useState('');

  const [activeTab, setActiveTab] = useState('browse');
  const [selected, setSelected] = useState<string | undefined>();

  const graphRef = useRef<HTMLDivElement>(null);
  const fgRef = useRef<any>(null);
  const [gdim, setGdim] = useState({ width: 800, height: 560 });

  // Build the client-side wiki model once (cached per project).
  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    setLoadingModel(true);
    setModelError('');
    getWikiModel(projectId)
      .then((m) => {
        if (!cancelled) setModel(m);
      })
      .catch((err) => {
        if (!cancelled) setModelError(err instanceof Error ? err.message : 'Failed to crawl wiki');
      })
      .finally(() => {
        if (!cancelled) setLoadingModel(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const openPage = useCallback(
    (path: string) => {
      setSelected(path);
      setActiveTab('browse');
      const pg = model?.byPath.get(path);
      if (pg) setSearchParams({ name: pg.slug });
    },
    [model, setSearchParams]
  );

  // Resolve ?name= or default to index.md once the model is ready.
  const nameParam = searchParams.get('name');
  useEffect(() => {
    if (!model || selected) return;
    if (nameParam) {
      const pg = model.bySlug.get(slugify(nameParam));
      if (pg) {
        setSelected(pg.path);
        return;
      }
    }
    const index =
      model.pages.find((p) => p.path === 'index.md') ||
      model.pages.find((p) => p.group === 'root') ||
      model.pages[0];
    if (index) setSelected(index.path);
  }, [model, nameParam, selected]);

  useEffect(() => {
    if (!graphRef.current) return;
    const obs = new ResizeObserver((entries) => {
      for (const e of entries) {
        setGdim({ width: e.contentRect.width, height: Math.max(e.contentRect.height, 500) });
      }
    });
    obs.observe(graphRef.current);
    return () => obs.disconnect();
  }, [activeTab, model]);

  const forceData = useMemo(() => {
    if (!model) return { nodes: [], links: [] };
    const degree = (path: string) =>
      (model.outgoing.get(path)?.length || 0) + (model.backlinks.get(path)?.length || 0);
    const pages = model.pages.filter((p) => !GRAPH_EXCLUDE.has(p.path));
    return {
      nodes: pages.map((p) => ({
        id: p.path,
        name: p.title,
        group: p.group,
        color: groupColor(p.group),
        val: 1 + degree(p.path),
      })),
      links: pages.flatMap((p) =>
        (model.outgoing.get(p.path) || [])
          .filter((target) => !GRAPH_EXCLUDE.has(target))
          .map((target) => ({ source: p.path, target }))
      ),
    };
  }, [model]);

  // Spread the graph out: longer links (weaker attraction between connected pages)
  // and stronger charge (more repulsion between all pages). Re-applied whenever the
  // graph mounts or its data changes, then the simulation is reheated to settle.
  useEffect(() => {
    const fg = fgRef.current;
    if (!fg) return;
    fg.d3Force('charge')?.strength(-160);
    fg.d3Force('link')?.distance(80);
    fg.d3ReheatSimulation();
  }, [forceData, activeTab]);

  const current: WikiPage | undefined = selected ? model?.byPath.get(selected) : undefined;
  const currentBacklinks = current ? model?.backlinks.get(current.path) || [] : [];
  const indexPage: WikiPage | undefined =
    model?.pages.find((p) => p.path === 'index.md') ||
    model?.pages.find((p) => p.group === 'root') ||
    model?.pages[0];

  const markdownComponents = {
    a: ({ href, children }: { href?: string; children?: React.ReactNode }) => {
      const resolved = model ? resolveWikiLink(href, current?.path || '', model) : undefined;
      if (resolved) {
        return (
          <Link
            href="#"
            onFollow={(e) => {
              e.preventDefault();
              openPage(resolved.path);
            }}
          >
            {children}
          </Link>
        );
      }
      // External links open in a new tab; unresolved in-wiki links are inert.
      if (href && /^(https?:|mailto:)/i.test(href)) {
        return (
          <a href={href} target="_blank" rel="noopener noreferrer">
            {children}
          </a>
        );
      }
      return <span style={{ color: '#b31d28', textDecoration: 'underline dashed' }}>{children}</span>;
    },
  };

  const renderedBody = useMemo(
    () => (current ? current.body.replace(/\[\[([^\]]+)\]\]/g, (_m, t: string) => `[${t}](wiki:${slugify(t)})`) : ''),
    [current]
  );

  // ---- Browse tab: opens at the wiki index and is navigated via in-page hyperlinks
  // (wikilinks + backlinks + breadcrumb). No file tree — use the Graph tab for an overview. ----
  const browseTab = (
    <Container header={<Header variant="h3">{current?.title || 'Wiki'}</Header>}>
      {!current ? (
        <Box color="text-status-inactive">Loading the wiki index…</Box>
      ) : (
        <SpaceBetween size="m">
            {/* Wiki index › current page. The group (concepts/entities/…) has no page of its
                own, so it is not shown as a crumb — a dead link there just confused people. */}
            <BreadcrumbGroup
              items={[
                { text: 'Wiki', href: 'wiki-index' },
                { text: current.title, href: 'wiki-title' },
              ]}
              onFollow={(e) => {
                e.preventDefault();
                if (e.detail.href === 'wiki-index' && indexPage) openPage(indexPage.path);
                else if (e.detail.href === 'wiki-title') openPage(current.path);
              }}
            />
            {current.tags.length > 0 && (
              <SpaceBetween direction="horizontal" size="xs">
                {current.tags.map((t) => (
                  <Badge key={t} color="blue">
                    {t}
                  </Badge>
                ))}
              </SpaceBetween>
            )}
            <div className="markdown-body" style={{ lineHeight: 1.6 }}>
              {/* SECURITY: wiki pages are agent-authored → untrusted. Safety relies on react-markdown@9
                  NOT rendering raw HTML by default. NEVER add `rehype-raw`/`dangerouslySetInnerHTML`
                  here, and keep the urlTransform allowlist minimal. */}
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={markdownComponents}
                // react-markdown's default sanitizer blanks any non-http(s) protocol,
                // which would strip the wiki:<slug> hrefs our [[wikilinks]] compile to.
                // Let those through; everything else keeps the default safety transform.
                urlTransform={(url) => (url.startsWith('wiki:') ? url : defaultUrlTransform(url))}
              >
                {renderedBody}
              </ReactMarkdown>
            </div>
            {currentBacklinks.length > 0 && (
              <Container header={<Header variant="h3">Linked from ({currentBacklinks.length})</Header>}>
                <SpaceBetween size="xs">
                  {currentBacklinks.map((path) => {
                    const pg = model?.byPath.get(path);
                    return (
                      <Link
                        key={path}
                        href="#"
                        onFollow={(e) => {
                          e.preventDefault();
                          openPage(path);
                        }}
                      >
                        {pg?.title || path}
                      </Link>
                    );
                  })}
                </SpaceBetween>
              </Container>
            )}
        </SpaceBetween>
      )}
    </Container>
  );

  // ---- Graph tab ----
  const graphTab = (
    <SpaceBetween size="l">
      <Container header={<Header variant="h3">Groups</Header>}>
        <SpaceBetween direction="horizontal" size="l">
          {Object.entries(GROUP_COLORS).map(([group, color]) => (
            <div key={group} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <div style={{ width: 12, height: 12, borderRadius: '50%', backgroundColor: color }} />
              <span style={{ textTransform: 'capitalize' }}>{group}</span>
            </div>
          ))}
        </SpaceBetween>
      </Container>
      <Container>
        {model && model.edgeCount === 0 ? (
          <Box textAlign="center" padding="xxl" color="text-status-inactive">
            No links between pages yet. As the researcher adds [[wikilinks]], the graph will fill in.
          </Box>
        ) : (
          <div ref={graphRef} style={{ width: '100%', height: 560 }}>
            <ForceGraph2D
              ref={fgRef}
              graphData={forceData}
              width={gdim.width}
              height={gdim.height}
              nodeLabel="name"
              nodeColor="color"
              nodeVal="val"
              linkColor={() => '#aab7b8'}
              linkWidth={1}
              onNodeClick={(node: any) => openPage(node.id)}
              nodeCanvasObject={(node: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
                const size = Math.sqrt(node.val || 1) * 2.5;
                ctx.beginPath();
                ctx.arc(node.x || 0, node.y || 0, size, 0, 2 * Math.PI);
                ctx.fillStyle = node.color;
                ctx.fill();
                if (globalScale > 1.4) {
                  const fontSize = 11 / globalScale;
                  ctx.font = `${fontSize}px Sans-Serif`;
                  ctx.textAlign = 'center';
                  ctx.textBaseline = 'middle';
                  ctx.fillStyle = '#16191f';
                  ctx.fillText(node.name, node.x || 0, (node.y || 0) + size + fontSize);
                }
              }}
            />
          </div>
        )}
      </Container>
    </SpaceBetween>
  );

  if (loadingModel) {
    return (
      <Box textAlign="center" padding="xxxl">
        <SpaceBetween size="s" alignItems="center">
          <Spinner size="large" />
          <Box color="text-status-inactive">Crawling the wiki…</Box>
        </SpaceBetween>
      </Box>
    );
  }

  if (modelError) {
    return (
      <Box textAlign="center" padding="xxxl">
        <StatusIndicator type="warning">{modelError}</StatusIndicator>
      </Box>
    );
  }

  return (
    <Tabs
      activeTabId={activeTab}
      onChange={({ detail }) => setActiveTab(detail.activeTabId)}
      tabs={[
        { id: 'browse', label: 'Browse', content: browseTab },
        {
          id: 'graph',
          label: `Graph (${forceData.nodes.length})`,
          content: graphTab,
        },
      ]}
    />
  );
}
