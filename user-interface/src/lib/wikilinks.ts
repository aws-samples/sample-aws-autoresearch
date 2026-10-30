import type { TreeEntry } from '../services/api';

export function slugify(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/\.md$/, '')
    .replace(/\s+/g, '-')
    .replace(/[^a-z0-9-_/]/g, '');
}

// Lazily walk the per-level folder tree (BFS, depth-limited) to find the file
// whose basename slug matches the given wikilink name/slug. `load(path)` returns
// the entries of one folder; root is the empty path.
export async function findFileBySlug(
  load: (path: string) => Promise<TreeEntry[]>,
  nameOrSlug: string,
  maxDepth = 4
): Promise<string | undefined> {
  const target = slugify(nameOrSlug);
  const queue: { path: string; depth: number }[] = [{ path: '', depth: 0 }];
  const seen = new Set<string>();

  while (queue.length) {
    const { path, depth } = queue.shift()!;
    if (seen.has(path)) continue;
    seen.add(path);

    let entries: TreeEntry[];
    try {
      entries = await load(path);
    } catch {
      continue;
    }

    for (const e of entries) {
      if (e.type === 'dir') continue;
      const base = (e.path || e.name || '').split('/').pop() || '';
      if (slugify(base) === target) return e.path || e.name;
    }
    if (depth < maxDepth) {
      for (const e of entries) {
        if (e.type === 'dir' && e.path) queue.push({ path: e.path, depth: depth + 1 });
      }
    }
  }
  return undefined;
}
