import { fetchAuthSession, getCurrentUser } from 'aws-amplify/auth';

const API_URL = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '');

async function authFetch(path: string, options?: RequestInit): Promise<Response> {
  const session = await fetchAuthSession();
  const token = session.tokens?.accessToken?.toString();
  return fetch(`${API_URL}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options?.headers,
    },
  });
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = '';
    try {
      const text = await res.text();
      try {
        const parsed = JSON.parse(text);
        msg = parsed?.error || parsed?.message || text;
      } catch {
        msg = text;
      }
    } catch {
      /* ignore */
    }
    throw new Error(msg || `${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

// --- Shared types ---------------------------------------------------------

export type ProjectStatus =
  | 'provisioning'
  | 'setup'
  | 'active'
  | 'paused'
  | 'deleting'
  | 'provision_failed'
  | 'completed';

export interface Project {
  project_id: string;
  owner?: string; // Cognito sub of the creator — has write + delete permission
  owner_username?: string; // owner's display name (for the Owner column + badge)
  admins?: Record<string, string>; // { sub: username } of admins with write access
  budget_reserved?: boolean; // true if this project reserved budget from its owner (refunded on delete)
  description?: string; // short one-line summary (from auto-research.json), for the list + homepage
  status: ProjectStatus | string;
  researcher_instance_id?: string;
  researcher_instance_type?: string;
  worker_instance_ids?: string[];
  worker_instance_type?: string;
  worker_count?: number;
  experiment_runner?: 'ec2' | 'local' | string; // "ec2": GPU workers; "local": runs on the researcher

  code_repo?: string;
  wiki_repo?: string;
  s3_data?: string;
  report_key?: string;
  best_metric?: number | string;
  metric_name?: string; // objective's name (from auto-research.json), for display; may be empty
  experiment_count?: number | null;
  model?: string;
  budget_usd?: number | string;
  patience?: number | string; // stop after this many consecutive steps with no kept improvement
  time_budget_seconds?: number | string; // per-experiment training compute budget (self-enforced)
  experiment_timeout_minutes?: number | string; // hard per-experiment cap, enforced at dispatch
  write_paper?: boolean; // when true, the researcher writes a paper when research completes
  paper_pdf_s3_uri?: string; // set once the paper PDF has been built + uploaded (reveals the Paper tab)
  spent_usd?: number | string;
  created_at?: string;
  // Async provisioning progress (status === 'provisioning' | 'provision_failed').
  provisioning_state?: string; // queued|creating-repos|committing|resolving-amis|creating-researcher|creating-workers|done|failed
  provisioning_msg?: string;
  provisioning_error?: string;
  // Observability: the AI Researcher writes its own tailable stream + S3 archive.
  researcher_log_stream?: string;
  researcher_log_s3_uri?: string;
}

// Single lifecycle field: an experiment is `running` (a worker is executing it) or one of the
// three terminal outcomes. (Simplified from the old state+status pair.)
export type ExperimentState = 'running' | 'kept' | 'discarded' | 'crashed';

export interface Experiment {
  project_id: string;
  experiment_id: string;
  state: ExperimentState | string;
  worker_id?: string; // the GPU worker this experiment ran on
  hypothesis?: string;
  description?: string;
  branch?: string;
  metric_value?: number | string;
  peak_vram_mb?: number | string;
  run_seconds?: number;
  worker_uptime_seconds?: number;
  cost_usd?: number;
  created_at?: string;
  completed_at?: string;
  // Observability: each sub-agent writes its own tailable stream + S3 archives.
  log_stream?: string; // e.g. experiment/<experiment_id>
  agent_log_s3_uri?: string; // full sub-agent log archive
  run_log_s3_uri?: string; // raw training log archive
}

export interface LogEvent {
  timestamp: number;
  message: string;
  stream: string;
}

export interface LogPage {
  events: LogEvent[];
  next_start?: number;
  count?: number;
}

// The tree endpoints return the entries of exactly ONE folder (not a nested
// tree). Renderers load subfolders lazily by re-querying with the folder path.
export interface TreeEntry {
  name?: string;
  path?: string;
  type?: 'file' | 'dir' | string;
  size?: number;
}

export interface Commit {
  id?: string;
  sha?: string;
  message?: string;
  author?: string;
  date?: string;
  branch?: string;
  [key: string]: unknown;
}

export interface FileDiff {
  path?: string;
  diff?: string;
  patch?: string;
  [key: string]: unknown;
}

export interface DataEntry {
  key?: string;
  name?: string;
  size?: number;
  type?: 'file' | 'dir' | string;
  last_modified?: string;
  [key: string]: unknown;
}

// --- Projects -------------------------------------------------------------

export async function listProjects(): Promise<Project[]> {
  const data = await json<{ projects?: Project[] }>(await authFetch('/projects'));
  return data.projects ?? [];
}

export async function getProject(projectId: string): Promise<Project | null> {
  const projects = await listProjects();
  return projects.find((p) => p.project_id === projectId) ?? null;
}

// --- Current user + per-project role -----------------------------------------

// The signed-in user's Cognito `sub` (getCurrentUser().userId is the sub). Cached for the session.
let _cachedUserId: string | null = null;
export async function getCurrentUserId(): Promise<string> {
  if (_cachedUserId) return _cachedUserId;
  try {
    _cachedUserId = (await getCurrentUser()).userId || '';
  } catch {
    _cachedUserId = '';
  }
  return _cachedUserId;
}

export type ProjectRole = 'owner' | 'admin' | 'viewer';

// The caller's role on a project — matches the server-side authz (ar_authz): owner > admin > viewer.
export function projectRole(project: Project | null | undefined, userSub: string): ProjectRole {
  if (!project || !userSub) return 'viewer';
  if (project.owner === userSub) return 'owner';
  if (project.admins && userSub in project.admins) return 'admin';
  return 'viewer';
}

// --- Team (owner + admins + join requests) -----------------------------------

export interface TeamRequest {
  project_id: string;
  user_sub: string;
  username?: string;
  note?: string;
  requested_at?: string;
  status?: string;
}

export interface TeamInfo {
  owner: string;
  owner_username: string;
  admins: Record<string, string>;
  requests: TeamRequest[];
}

// Whether the signed-in user already has a pending join request (so the UI hides "Join the team"
// across reloads). Callable by any authenticated user, unlike the owner/admin-only request list.
export async function getMyJoinRequest(
  projectId: string
): Promise<{ requested: boolean; role: ProjectRole }> {
  return json(await authFetch(`/projects/${projectId}/team/requests/mine`));
}

export async function requestToJoin(projectId: string, note?: string): Promise<{ status: string }> {
  return json(
    await authFetch(`/projects/${projectId}/team/requests`, {
      method: 'POST',
      body: JSON.stringify({ note: note ?? '' }),
    })
  );
}

export async function getTeam(projectId: string): Promise<TeamInfo> {
  return json(await authFetch(`/projects/${projectId}/team/requests`));
}

export async function admitMember(projectId: string, sub: string): Promise<{ admitted: string }> {
  return json(
    await authFetch(`/projects/${projectId}/team/members`, {
      method: 'POST',
      body: JSON.stringify({ sub }),
    })
  );
}

export async function removeMember(projectId: string, sub: string): Promise<{ removed: string }> {
  return json(
    await authFetch(`/projects/${projectId}/team/members/${encodeURIComponent(sub)}`, {
      method: 'DELETE',
    })
  );
}

// --- Users & budgets ---------------------------------------------------------

export interface UserInfo {
  user_sub: string;
  username: string;
  budget: number; // spendable USD balance
  is_budget_admin: boolean;
}

// The caller's own user record (budget + admin flag). Lazy-creates the row at $0 on first call.
export async function getMe(): Promise<UserInfo> {
  return json(await authFetch('/me'));
}

// Budget-admin only: every pool user + their budget.
export async function listUsers(): Promise<UserInfo[]> {
  const data = await json<{ users?: UserInfo[] }>(await authFetch('/users'));
  return data.users ?? [];
}

// Budget-admin only: set a user's budget (absolute USD). `username` is passed so a first-time row
// stores a display name.
export async function setUserBudget(
  sub: string,
  budget: number,
  username?: string
): Promise<{ user_sub: string; budget: number }> {
  return json(
    await authFetch(`/users/${encodeURIComponent(sub)}/budget`, {
      method: 'PUT',
      body: JSON.stringify({ budget, username }),
    })
  );
}

export interface WorkerInfo {
  instance_id: string;
  state: string; // running | stopped | pending | stopping | shutting-down | terminated | unknown
  instance_type?: string;
  name?: string | null;
}

export async function getWorkers(projectId: string): Promise<WorkerInfo[]> {
  const data = await json<{ workers?: WorkerInfo[] }>(
    await authFetch(`/projects/${projectId}/workers`)
  );
  return data.workers ?? [];
}

export interface BootstrapFile {
  path: string; // repo-relative, e.g. "auto-research.json", "src/train.py"
  content_b64: string;
}

export interface CreateProjectBody {
  project_name: string;
  experiment_runner?: 'ec2' | 'local'; // "ec2" (default): GPU workers; "local": run on the GPU researcher, 0 workers
  researcher_instance_type: string;
  worker_instance_type: string;
  worker_count: number;
  budget_usd?: number;
  patience?: number; // stop after this many consecutive steps without a kept improvement (default 10)
  time_budget_seconds?: number; // intended per-experiment compute budget, self-enforced (default 300)
  experiment_timeout_minutes?: number; // hard cap on one experiment, enforced at dispatch (default 15)
  write_paper?: boolean; // write a scientific paper when the research completes (default false)
  model?: string; // Bedrock model / inference-profile id; overrides the Lambda default.
  auto_start?: boolean; // default true: researcher runs immediately after provisioning (else status=setup, press Start).
  files: BootstrapFile[];
}

export async function createProject(
  body: CreateProjectBody
): Promise<{ project_id: string; status?: string }> {
  return json(await authFetch('/projects', { method: 'POST', body: JSON.stringify(body) }));
}

export async function deleteProject(
  projectId: string
): Promise<{ project_id: string; status?: string; deleted?: string[] }> {
  // Returns 202 immediately (status "deleting"); teardown runs async and the project row disappears
  // from the list when it completes.
  return json(await authFetch(`/projects/${projectId}`, { method: 'DELETE' }));
}

export async function startSession(projectId: string): Promise<{ status: string }> {
  return json(await authFetch(`/projects/${projectId}/start`, { method: 'POST' }));
}

export async function stopSession(projectId: string): Promise<{ status: string }> {
  return json(await authFetch(`/projects/${projectId}/stop`, { method: 'POST' }));
}

export async function submitGuidance(
  projectId: string,
  text: string
): Promise<{ guidance_id: string }> {
  return json(
    await authFetch(`/projects/${projectId}/guidance`, {
      method: 'POST',
      body: JSON.stringify({ text }),
    })
  );
}

// Toggle the write_paper flag after creation (same endpoint as guidance; no guidance item is added).
export async function setWritePaper(
  projectId: string,
  write_paper: boolean
): Promise<{ write_paper: boolean }> {
  return json(
    await authFetch(`/projects/${projectId}/guidance`, {
      method: 'POST',
      body: JSON.stringify({ write_paper }),
    })
  );
}

// --- Experiments ----------------------------------------------------------

export async function getExperiments(projectId: string): Promise<Experiment[]> {
  const data = await json<{ experiments?: Experiment[] }>(
    await authFetch(`/projects/${projectId}/experiments`)
  );
  return data.experiments ?? [];
}

// --- Logs (update stream) -------------------------------------------------

// Forward-cursor log tail. Pass `start` (the previous `next_start`) to fetch
// only newer events; the response echoes a stable `next_start` even when empty.
export async function getLogs(
  projectId: string,
  opts?: { stream?: string; start?: number; limit?: number }
): Promise<LogPage> {
  const q = new URLSearchParams();
  if (opts?.stream) q.set('stream', opts.stream);
  if (opts?.start !== undefined) q.set('start', String(opts.start));
  if (opts?.limit) q.set('limit', String(opts.limit));
  const qs = q.toString() ? `?${q.toString()}` : '';
  const data = await json<{ events?: LogEvent[]; next_start?: number; count?: number }>(
    await authFetch(`/projects/${projectId}/logs${qs}`)
  );
  return { events: data.events ?? [], next_start: data.next_start, count: data.count };
}

// --- Wiki -----------------------------------------------------------------

// Lists the entries of one wiki folder. Root = empty path (or '/').
export async function getWikiTree(projectId: string, path = ''): Promise<TreeEntry[]> {
  const qs = path ? `?path=${encodeURIComponent(path)}` : '';
  const data = await json<{ entries?: TreeEntry[] }>(
    await authFetch(`/projects/${projectId}/wiki/tree${qs}`)
  );
  return data.entries ?? [];
}

export async function getWikiFile(
  projectId: string,
  path: string
): Promise<{ path: string; content: string }> {
  return json(
    await authFetch(`/projects/${projectId}/wiki/file?path=${encodeURIComponent(path)}`)
  );
}

// --- Code -----------------------------------------------------------------

// Lists the entries of one code folder. Root = empty path (or '/').
export async function getCodeTree(projectId: string, path = ''): Promise<TreeEntry[]> {
  const qs = path ? `?path=${encodeURIComponent(path)}` : '';
  const data = await json<{ entries?: TreeEntry[] }>(
    await authFetch(`/projects/${projectId}/code/tree${qs}`)
  );
  return data.entries ?? [];
}

export async function getCodeFile(
  projectId: string,
  path: string
): Promise<{ path: string; content: string }> {
  return json(
    await authFetch(`/projects/${projectId}/code/file?path=${encodeURIComponent(path)}`)
  );
}

export async function getCodeCommits(projectId: string): Promise<Commit[]> {
  const data = await json<{ commits?: Commit[] }>(
    await authFetch(`/projects/${projectId}/code/commits`)
  );
  return data.commits ?? [];
}

export async function getCodeDiff(projectId: string, ref: string): Promise<FileDiff[]> {
  const data = await json<{ diffs?: FileDiff[] | string }>(
    await authFetch(`/projects/${projectId}/code/diff?ref=${encodeURIComponent(ref)}`)
  );
  // Tolerate either an array of file diffs or a single unified-diff string.
  if (typeof data.diffs === 'string') return [{ diff: data.diffs }];
  return data.diffs ?? [];
}

// --- Data (S3) ------------------------------------------------------------

export async function getDataTree(projectId: string, prefix?: string): Promise<DataEntry[]> {
  const qs = prefix ? `?prefix=${encodeURIComponent(prefix)}` : '';
  const data = await json<{ entries?: DataEntry[] }>(
    await authFetch(`/projects/${projectId}/data/tree${qs}`)
  );
  return data.entries ?? [];
}

export async function getDataUrl(projectId: string, key: string): Promise<string> {
  const data = await json<{ url?: string }>(
    await authFetch(`/projects/${projectId}/data/url?key=${encodeURIComponent(key)}`)
  );
  return data.url ?? '';
}

export async function getDataPreview(
  projectId: string,
  key: string
): Promise<{ content: string; truncated?: boolean; key?: string }> {
  return json(await authFetch(`/projects/${projectId}/data/preview?key=${encodeURIComponent(key)}`));
}

// --- Helpers --------------------------------------------------------------

export function fmtMetric(v: number | string | undefined): string {
  if (v === undefined || v === null || v === '') return '—';
  const n = typeof v === 'number' ? v : parseFloat(v);
  return Number.isFinite(n) ? n.toFixed(4) : String(v);
}

export function fmtUsd(v: number | string | undefined): string {
  const n = typeof v === 'number' ? v : parseFloat(String(v ?? 0));
  return `$${Number.isFinite(n) ? n.toFixed(2) : '0.00'}`;
}
