import { getWikiTree, getWikiFile } from '../services/api';
import { slugify } from './wikilinks';

export interface WikiPage {
  path: string; // e.g. concepts/l4-training.md
  group: string; // top-level folder, or 'root' for top-level files
  slug: string; // basename without .md
  title: string;
  tags: string[];
  body: string;
}

export interface WikiModel {
  pages: WikiPage[];
  bySlug: Map<string, WikiPage>;
  byPath: Map<string, WikiPage>;
  outgoing: Map<string, string[]>; // path -> linked page paths
  backlinks: Map<string, string[]>; // path -> paths that link here
  edgeCount: number;
}

const CRAWL_CONCURRENCY = 6;

/* eslint-disable @typescript-eslint/no-explicit-any */

function parseFrontmatter(content: string): { frontmatter: Record<string, unknown>; body: string } {
  const m = content.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/);
  if (!m) return { frontmatter: {}, body: content };
  const fm: Record<string, unknown> = {};
  let key = '';
  for (const line of m[1].split('\n')) {
    const kv = line.match(/^(\w[\w-]*):\s*(.*)$/);
    if (kv) {
      key = kv[1];
      const v = kv[2].trim();
      if (v.startsWith('[') && v.endsWith(']')) {
        fm[key] = v
          .slice(1, -1)
          .split(',')
          .map((s) => s.trim().replace(/^["']|["']$/g, ''))
          .filter(Boolean);
      } else if (v === '') {
        fm[key] = [];
      } else {
        fm[key] = v.replace(/^["']|["']$/g, '');
      }
    } else {
      const item = line.match(/^\s*-\s+(.*)$/);
      if (item && key) {
        if (!Array.isArray(fm[key])) fm[key] = [];
        (fm[key] as string[]).push(item[1].trim().replace(/^["']|["']$/g, ''));
      }
    }
  }
  return { frontmatter: fm, body: m[2] };
}

function firstHeading(body: string): string | undefined {
  const m = body.match(/^#\s+(.+)$/m);
  return m ? m[1].trim() : undefined;
}

function extractWikilinks(body: string): string[] {
  const out: string[] = [];
  const re = /\[\[([^\]]+)\]\]/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(body))) out.push(m[1]);
  return out;
}

function makePage(path: string, content: string): WikiPage {
  const { frontmatter, body } = parseFrontmatter(content);
  const slug = (path.split('/').pop() || path).replace(/\.md$/, '');
  const group = path.includes('/') ? path.split('/')[0] : 'root';
  const fmTitle = typeof frontmatter.title === 'string' ? (frontmatter.title as string) : '';
  const title = fmTitle || firstHeading(body) || slug;
  const tags = Array.isArray(frontmatter.tags) ? (frontmatter.tags as string[]) : [];
  return { path, group, slug, title, tags, body };
}

async function collectMdPaths(projectId: string): Promise<string[]> {
  const out: string[] = [];
  const walk = async (path: string): Promise<void> => {
    let entries;
    try {
      entries = await getWikiTree(projectId, path);
    } catch {
      return;
    }
    for (const e of entries) {
      const p = e.path || e.name || '';
      if (!p) continue;
      if (e.type === 'dir') {
        const name = e.name || p.replace(/\/$/, '').split('/').pop() || '';
        if (name === 'raw') continue; // skip the raw source dump
        await walk(p);
      } else if (p.endsWith('.md')) {
        out.push(p);
      }
    }
  };
  await walk('');
  return out;
}

async function mapLimit<T, R>(items: T[], limit: number, fn: (t: T) => Promise<R>): Promise<R[]> {
  const results = new Array<R>(items.length);
  let idx = 0;
  const worker = async () => {
    while (idx < items.length) {
      const i = idx++;
      results[i] = await fn(items[i]);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length || 1) }, worker));
  return results;
}

async function crawl(projectId: string): Promise<WikiModel> {
  const paths = await collectMdPaths(projectId);
  const files = await mapLimit(paths, CRAWL_CONCURRENCY, async (p) => {
    try {
      const r = await getWikiFile(projectId, p);
      return { path: p, content: r.content || '' };
    } catch {
      return { path: p, content: '' };
    }
  });

  const pages = files.map((f) => makePage(f.path, f.content));
  const bySlug = new Map<string, WikiPage>();
  const byPath = new Map<string, WikiPage>();
  for (const pg of pages) {
    byPath.set(pg.path, pg);
    if (!bySlug.has(pg.slug)) bySlug.set(pg.slug, pg);
  }

  const outgoing = new Map<string, string[]>();
  const backlinks = new Map<string, string[]>();
  let edgeCount = 0;
  for (const pg of pages) {
    const targets = new Set<string>();
    for (const link of extractWikilinks(pg.body)) {
      const t = bySlug.get(slugify(link));
      if (t && t.path !== pg.path) targets.add(t.path);
    }
    outgoing.set(pg.path, Array.from(targets));
    edgeCount += targets.size;
    for (const t of targets) {
      const arr = backlinks.get(t) || [];
      if (!arr.includes(pg.path)) arr.push(pg.path);
      backlinks.set(t, arr);
    }
  }

  return { pages, bySlug, byPath, outgoing, backlinks, edgeCount };
}

// Cache the crawl per project so tab switches don't re-fetch.
const cache = new Map<string, Promise<WikiModel>>();

export function getWikiModel(projectId: string, force = false): Promise<WikiModel> {
  if (force) cache.delete(projectId);
  let existing = cache.get(projectId);
  if (!existing) {
    existing = crawl(projectId);
    cache.set(projectId, existing);
    // On failure, evict so a later attempt can retry.
    existing.catch(() => cache.delete(projectId));
  }
  return existing;
}
