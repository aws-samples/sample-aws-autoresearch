import { useEffect, useState } from 'react';
import Box from '@cloudscape-design/components/box';
import Icon from '@cloudscape-design/components/icon';
import Spinner from '@cloudscape-design/components/spinner';
import type { TreeEntry } from '../services/api';

function isDir(entry: TreeEntry): boolean {
  if (entry.type) return entry.type === 'dir';
  return false;
}

function entryName(entry: TreeEntry): string {
  if (entry.name) return entry.name;
  if (entry.path) return entry.path.replace(/\/$/, '').split('/').pop() || entry.path;
  return '(unnamed)';
}

interface EntryItemProps {
  entry: TreeEntry;
  depth: number;
  loadChildren: (path: string) => Promise<TreeEntry[]>;
  selectedPath?: string;
  onSelect: (path: string) => void;
}

function EntryItem({ entry, depth, loadChildren, selectedPath, onSelect }: EntryItemProps) {
  const dir = isDir(entry);
  const name = entryName(entry);
  const path = entry.path || name;
  const selected = !dir && selectedPath === path;

  const [open, setOpen] = useState(false);
  const [children, setChildren] = useState<TreeEntry[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  const activate = async () => {
    if (!dir) {
      onSelect(path);
      return;
    }
    const next = !open;
    setOpen(next);
    if (next && children === null) {
      setLoading(true);
      setError(false);
      try {
        setChildren(await loadChildren(path));
      } catch {
        setError(true);
      } finally {
        setLoading(false);
      }
    }
  };

  return (
    <div>
      <div
        role="button"
        tabIndex={0}
        onClick={activate}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            activate();
          }
        }}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          paddingLeft: depth * 14 + 4,
          paddingTop: 2,
          paddingBottom: 2,
          cursor: 'pointer',
          borderRadius: 4,
          backgroundColor: selected ? 'rgba(9,114,211,0.12)' : undefined,
          fontWeight: selected ? 700 : 400,
        }}
      >
        {dir ? (
          <Icon name={open ? 'caret-down-filled' : 'caret-right-filled'} size="small" />
        ) : (
          <span style={{ width: 14, display: 'inline-block' }} />
        )}
        <Icon name={dir ? 'folder' : 'file'} size="small" />
        <span style={{ fontSize: 13, wordBreak: 'break-all' }}>{name}</span>
      </div>
      {dir && open && (
        <div>
          {loading && (
            <div style={{ paddingLeft: (depth + 1) * 14 + 4 }}>
              <Spinner size="normal" />
            </div>
          )}
          {error && (
            <Box color="text-status-error" fontSize="body-s" padding={{ left: 'l' }}>
              Failed to load
            </Box>
          )}
          {children &&
            children.map((child, i) => (
              <EntryItem
                key={(child.path || entryName(child)) + i}
                entry={child}
                depth={depth + 1}
                loadChildren={loadChildren}
                selectedPath={selectedPath}
                onSelect={onSelect}
              />
            ))}
        </div>
      )}
    </div>
  );
}

interface FileTreeProps {
  // Loads the entries of exactly one folder. Root is loaded with `rootPath`.
  loadChildren: (path: string) => Promise<TreeEntry[]>;
  rootPath?: string;
  selectedPath?: string;
  onSelect: (path: string) => void;
}

export default function FileTree({ loadChildren, rootPath = '', selectedPath, onSelect }: FileTreeProps) {
  const [roots, setRoots] = useState<TreeEntry[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    loadChildren(rootPath)
      .then((entries) => {
        if (!cancelled) setRoots(entries);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load tree');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [loadChildren, rootPath]);

  if (loading) return <Spinner />;
  if (error) return <Box color="text-status-error" padding="xs">{error}</Box>;
  if (!roots || roots.length === 0) return <Box color="text-status-inactive" padding="xs">No files.</Box>;

  return (
    <div style={{ overflow: 'auto', maxHeight: '70vh' }}>
      {roots.map((entry, i) => (
        <EntryItem
          key={(entry.path || entryName(entry)) + i}
          entry={entry}
          depth={0}
          loadChildren={loadChildren}
          selectedPath={selectedPath}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}
