import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Alert from '@cloudscape-design/components/alert';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Container from '@cloudscape-design/components/container';
import Form from '@cloudscape-design/components/form';
import Checkbox from '@cloudscape-design/components/checkbox';
import FormField from '@cloudscape-design/components/form-field';
import Header from '@cloudscape-design/components/header';
import Input from '@cloudscape-design/components/input';
import Select from '@cloudscape-design/components/select';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import { createProject, getMe, fmtUsd, type BootstrapFile } from '../services/api';

const MAX_BYTES = 5 * 1024 * 1024; // API Gateway / Lambda sync payload guardrail.
const IGNORED_SEGMENTS = new Set(['.git', '.venv', 'node_modules', '__pycache__', '.DS_Store']);

// Canonical, resource-safe project id. MUST stay in sync with `_slug` in the create_project
// Lambda (which is the authority and re-slugs on the server). Preview only; the server decides.
function slugify(name: string): string {
  const s = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return s.slice(0, 40).replace(/^-+|-+$/g, '');
}

function arrayBufferToBase64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let binary = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

export default function CreateProjectPage() {
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [researcherType, setResearcherType] = useState({ label: 'm8g.large (Graviton)', value: 'm8g.large' });
  const [workerType, setWorkerType] = useState({ label: 'g4dn.xlarge (T4 16GB)', value: 'g4dn.xlarge' });
  const [workerCount, setWorkerCount] = useState('1');
  const [execMode, setExecMode] = useState({ label: 'EC2 GPU workers (default)', value: 'ec2' });
  const [localGpuType, setLocalGpuType] = useState({ label: 'g4dn.xlarge (T4 16GB)', value: 'g4dn.xlarge' });
  const [model, setModel] = useState({ label: 'Claude Opus 4.8', value: 'us.anthropic.claude-opus-4-8' });
  const [budget, setBudget] = useState('30');
  const [availBudget, setAvailBudget] = useState<number | null>(null); // caller's available USD budget
  const [patience, setPatience] = useState('10');
  const [timeBudget, setTimeBudget] = useState('300');
  const [experimentTimeoutMin, setExperimentTimeoutMin] = useState('15');
  const [autoStart, setAutoStart] = useState(true);
  const [writePaper, setWritePaper] = useState(false);

  const [files, setFiles] = useState<BootstrapFile[]>([]);
  const [totalBytes, setTotalBytes] = useState(0);
  const [folderName, setFolderName] = useState('');
  const [projectName, setProjectName] = useState('');
  const [nameEdited, setNameEdited] = useState(false);

  const [reading, setReading] = useState(false);
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const handleFolderChosen = async (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return;
    setReading(true);
    setError('');
    setFiles([]);
    try {
      const picked = Array.from(fileList);
      const topSegment = picked[0]?.webkitRelativePath.split('/')[0] || '';
      let total = 0;
      const collected: BootstrapFile[] = [];

      for (const file of picked) {
        const relParts = file.webkitRelativePath.split('/');
        // Strip the first path segment (the uploaded folder's own name).
        const rel = relParts.length > 1 ? relParts.slice(1).join('/') : relParts[0];
        if (!rel) continue;
        // Skip files inside any ignored directory (or ignored file names).
        if (rel.split('/').some((seg) => IGNORED_SEGMENTS.has(seg))) continue;

        total += file.size;
        if (total > MAX_BYTES) {
          setError(
            `Bootstrap folder too large (> ${fmtBytes(MAX_BYTES)}) — don't include data, checkpoints, or .venv.`
          );
          setFiles([]);
          setTotalBytes(total);
          setReading(false);
          return;
        }
        const buf = await file.arrayBuffer();
        collected.push({ path: rel, content_b64: arrayBufferToBase64(buf) });
      }

      if (!collected.some((f) => f.path === 'auto-research.json')) {
        setError('The bootstrap folder must contain an auto-research.json at its top level.');
        setFiles([]);
        setTotalBytes(total);
        setReading(false);
        return;
      }

      setFiles(collected);
      setTotalBytes(total);
      setFolderName(topSegment);
      if (!nameEdited) setProjectName(slugify(topSegment));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to read the selected folder.');
    } finally {
      setReading(false);
    }
  };

  useEffect(() => {
    getMe().then((me) => setAvailBudget(me.budget)).catch(() => setAvailBudget(null));
  }, []);

  const projectId = slugify(projectName);
  const runLocally = execMode.value === 'local';
  const budgetNum = parseFloat(budget);
  // Project budget must be > $1 and cannot exceed the caller's available budget.
  const budgetValid =
    Number.isFinite(budgetNum) && budgetNum > 1 && (availBudget === null || budgetNum <= availBudget);
  const canSubmit =
    files.length > 0 && !error && !reading && projectId.length > 0 && budgetValid &&
    (runLocally || parseInt(workerCount, 10) > 0);

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setError('');
    try {
      const result = await createProject({
        project_name: projectId,
        experiment_runner: runLocally ? 'local' : 'ec2',
        researcher_instance_type: runLocally ? localGpuType.value : researcherType.value,
        worker_instance_type: workerType.value,
        worker_count: runLocally ? 0 : parseInt(workerCount, 10),
        model: model.value,
        budget_usd: parseFloat(budget),
        patience: parseInt(patience, 10),
        time_budget_seconds: parseInt(timeBudget, 10),
        experiment_timeout_minutes: parseInt(experimentTimeoutMin, 10),
        auto_start: autoStart,
        write_paper: writePaper,
        files,
      });
      navigate(`/projects/${result.project_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create project');
      setSubmitting(false);
    }
  };

  return (
    <Form
      header={<Header variant="h1">Create New Project</Header>}
      actions={
        <SpaceBetween direction="horizontal" size="xs">
          <Button variant="link" onClick={() => navigate('/')}>
            Cancel
          </Button>
          <Button variant="primary" loading={submitting} disabled={!canSubmit} onClick={handleSubmit}>
            Create Project
          </Button>
        </SpaceBetween>
      }
    >
      <SpaceBetween size="l">
        {error && <Alert type="error">{error}</Alert>}

        <Container header={<Header variant="h2">Project</Header>}>
          <FormField
            label="Project name"
            description="Unique id for this run. All resources (code/wiki repos, S3 prefix, log group, instances) are prefixed with it — so you can run the same demo again under a new name without deleting earlier runs. Auto-fills from the uploaded folder; edit it freely."
            constraintText={projectId ? `Resources will be prefixed: ${projectId}` : 'Required'}
          >
            <Input
              value={projectName}
              onChange={({ detail }) => {
                setProjectName(detail.value);
                setNameEdited(true);
              }}
              placeholder="circle-packing"
            />
          </FormField>
        </Container>

        <Container header={<Header variant="h2">Compute</Header>}>
          <SpaceBetween size="m">
            <FormField label="How experiments run" description={runLocally
              ? 'Local: the researcher IS a GPU instance and runs experiments on itself (zero workers). The GPU is held for the whole run — no per-experiment capacity waits — but you pay for it while it sits idle between experiments (agent think time). Best when worker GPU capacity is scarce.'
              : 'EC2 workers: a cheap CPU researcher orchestrates, and each experiment runs on a separate GPU worker that is powered on only for that run (pay-per-use, parallel across workers).'}>
              <Select
                selectedOption={execMode}
                onChange={({ detail }) => setExecMode(detail.selectedOption as typeof execMode)}
                options={[
                  { label: 'EC2 GPU workers (default)', value: 'ec2' },
                  { label: 'Local (run on the GPU researcher)', value: 'local' },
                ]}
              />
            </FormField>
            {runLocally ? (
              <FormField label="Researcher GPU instance" description="The single GPU instance that runs both the researcher loop and its experiments. It stays on for the whole session.">
                <Select
                  selectedOption={localGpuType}
                  onChange={({ detail }) => setLocalGpuType(detail.selectedOption as typeof localGpuType)}
                  options={[
                    { label: 'g4dn.xlarge (T4 16GB)', value: 'g4dn.xlarge' },
                    { label: 'g5.xlarge (A10G 24GB)', value: 'g5.xlarge' },
                    { label: 'g6.xlarge (L4 24GB)', value: 'g6.xlarge' },
                    { label: 'p5.4xlarge (1× H100)', value: 'p5.4xlarge' },
                  ]}
                />
              </FormField>
            ) : (
              <>
                <FormField label="AI Researcher instance type" description="Cheap CPU node that runs continuously while the session is active.">
                  <Select
                    selectedOption={researcherType}
                    onChange={({ detail }) => setResearcherType(detail.selectedOption as typeof researcherType)}
                    options={[
                      { label: 'm8g.large (Graviton)', value: 'm8g.large' },
                      { label: 'm9g.large (Graviton)', value: 'm9g.large' },
                      { label: 'm7i.large (x86)', value: 'm7i.large' },
                      { label: 'm7i.xlarge (x86)', value: 'm7i.xlarge' },
                      { label: 't3.large (x86)', value: 't3.large' },
                    ]}
                  />
                </FormField>
                <FormField label="Worker instance type" description="GPU worker, STOPPED by default; started only while an experiment runs.">
                  <Select
                    selectedOption={workerType}
                    onChange={({ detail }) => setWorkerType(detail.selectedOption as typeof workerType)}
                    options={[
                      { label: 'g4dn.xlarge (T4 16GB)', value: 'g4dn.xlarge' },
                      { label: 'g5.xlarge (A10G 24GB)', value: 'g5.xlarge' },
                      { label: 'g6.xlarge (L4 24GB)', value: 'g6.xlarge' },
                      { label: 'p5.4xlarge (1× H100)', value: 'p5.4xlarge' },
                    ]}
                  />
                </FormField>
                <FormField label="Number of workers">
                  <Input type="number" value={workerCount} onChange={({ detail }) => setWorkerCount(detail.value)} />
                </FormField>
              </>
            )}
            <FormField label="Model" description="Bedrock model the AI Researcher runs on.">
              <Select
                selectedOption={model}
                onChange={({ detail }) => setModel(detail.selectedOption as typeof model)}
                options={[
                  { label: 'Claude Opus 4.8', value: 'us.anthropic.claude-opus-4-8' },
                  { label: 'Claude Sonnet 4.5', value: 'us.anthropic.claude-sonnet-4-5-20250929-v1:0' },
                ]}
              />
            </FormField>
            <FormField
              label="Maximum budget (USD)"
              description={
                availBudget === null
                  ? 'Cap on total GPU + Bedrock spend. Reserved from your budget at creation.'
                  : `Cap on total GPU + Bedrock spend. Reserved from your budget at creation. Your available budget: ${fmtUsd(availBudget)}.`
              }
              errorText={
                budget && !budgetValid
                  ? budgetNum <= 1
                    ? 'Budget must be greater than $1.'
                    : `Budget exceeds your available budget (${fmtUsd(availBudget ?? 0)}). Ask a budget administrator to grant you more.`
                  : undefined
              }
            >
              <Input type="number" value={budget} onChange={({ detail }) => setBudget(detail.value)} />
            </FormField>
            <FormField label="Patience (steps)" description="Stop the research after this many consecutive steps with no new best (kept) solution. 0 disables it.">
              <Input type="number" value={patience} onChange={({ detail }) => setPatience(detail.value)} />
            </FormField>
            <FormField label="Training Time Budget (s)" description="Wall-clock compute budget for one experiment. The agent makes its training/eval code self-limit to this budget (e.g. a time check in the training loop) so every experiment gets the same compute and results stay comparable. Not a hard kill — that's the experiment timeout below.">
              <Input type="number" value={timeBudget} onChange={({ detail }) => setTimeBudget(detail.value)} />
            </FormField>
            <FormField label="Experiment timeout (minutes)" description="Hard cap on one experiment, enforced at dispatch: if the run-experiment sub-agent exceeds it, the whole experiment is terminated (and its worker stopped). Set above the expected wall-clock time (compute + startup + scoring).">
              <Input type="number" value={experimentTimeoutMin} onChange={({ detail }) => setExperimentTimeoutMin(detail.value)} />
            </FormField>
            <FormField label="Start automatically">
              <Checkbox checked={autoStart} onChange={({ detail }) => setAutoStart(detail.checked)}>
                Start the AI Researcher immediately after provisioning (it begins spending right away).
                Uncheck to provision the instances stopped and press Start when ready.
              </Checkbox>
            </FormField>
            <FormField label="Write a scientific paper">
              <Checkbox checked={writePaper} onChange={({ detail }) => setWritePaper(detail.checked)}>
                When the research completes (budget or patience exhausted), write up the results as a
                IEEE-conference PDF. You can also toggle this later from the project's guidance box.
              </Checkbox>
            </FormField>
          </SpaceBetween>
        </Container>

        <Container header={<Header variant="h2">Bootstrap folder</Header>}>
          <SpaceBetween size="m">
            <FormField
              label="Upload bootstrap folder"
              description="Point at a folder such as demos/circle-packing/. It must contain auto-research.json plus the code under study. .git, .venv, node_modules, __pycache__ and .DS_Store are skipped automatically."
            >
              {/* Cloudscape has no directory picker, so use a hidden native input with webkitdirectory. */}
              <input
                ref={(el) => {
                  fileInputRef.current = el;
                  if (el) {
                    el.setAttribute('webkitdirectory', '');
                    el.setAttribute('directory', '');
                  }
                }}
                type="file"
                multiple
                style={{ display: 'none' }}
                onChange={(e) => handleFolderChosen(e.target.files)}
              />
              <SpaceBetween direction="horizontal" size="xs" alignItems="center">
                <Button iconName="upload" loading={reading} onClick={() => fileInputRef.current?.click()} formAction="none">
                  Choose folder
                </Button>
                {reading && <StatusIndicator type="loading">Reading files…</StatusIndicator>}
              </SpaceBetween>
            </FormField>

            {files.length > 0 && !reading && (
              <StatusIndicator type="success">
                {files.length} files, {fmtBytes(totalBytes)}
                {folderName ? ` from "${folderName}"` : ''}
              </StatusIndicator>
            )}

            {files.length > 0 && (
              <Box>
                <Box variant="awsui-key-label">Files to upload</Box>
                <div style={{ maxHeight: 200, overflow: 'auto', fontFamily: 'Monaco, Menlo, monospace', fontSize: 12 }}>
                  {files.map((f) => (
                    <div key={f.path}>{f.path}</div>
                  ))}
                </div>
              </Box>
            )}
          </SpaceBetween>
        </Container>
      </SpaceBetween>
    </Form>
  );
}
