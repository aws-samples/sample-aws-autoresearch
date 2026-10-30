"""
create_project: provision a project from an uploaded bootstrap directory — ASYNC + OBSERVABLE.

Two modes in one function:
- API mode (fast, <2s): validate the request, write a `provisioning` project record, stash the
  upload in S3, kick off async provisioning (self-invoke), and return 202 immediately. The UI
  navigates to the project page at once and watches the provisioning stream — no long spinner.
- Provision mode (async): do the heavy work step by step (repos -> commits -> instances), streaming
  progress to the `provisioning` CloudWatch stream and updating the record. On failure it ROLLS
  BACK any instances it created and sets status `provision_failed` with the exact error, so nothing
  is orphaned and the cause is visible.

Project statuses: provisioning -> active (auto_start, default: researcher runs immediately) or
setup (auto_start=false: ready to Start) | provision_failed (see provisioning_error).
"""

import base64
import json
import os
import re
import secrets
import time
import boto3
from datetime import datetime, timezone
from decimal import Decimal
from ar_authz import caller

dynamodb = boto3.resource("dynamodb")
codecommit = boto3.client("codecommit")
ec2 = boto3.client("ec2")
logs_client = boto3.client("logs")
s3 = boto3.client("s3")
lam = boto3.client("lambda")
iam = boto3.client("iam")
ssm = boto3.client("ssm")

PROJECTS_TABLE = os.environ["PROJECTS_TABLE"]
USERS_TABLE = os.environ.get("USERS_TABLE", "")
DATA_BUCKET = os.environ["DATA_BUCKET"]
INSTANCE_PROFILE_NAME = os.environ["INSTANCE_PROFILE_NAME"]
RESEARCHER_SG = os.environ["RESEARCHER_SECURITY_GROUP_ID"]
WORKER_SG = os.environ["WORKER_SECURITY_GROUP_ID"]
WORKER_KEY_NAME = os.environ["WORKER_KEY_NAME"]
REGION = os.environ["REGION"]
ACCOUNT_ID = os.environ["ACCOUNT_ID"]
PERMISSIONS_BOUNDARY_ARN = os.environ.get("PERMISSIONS_BOUNDARY_ARN", "")
SSM_CORE_POLICY_ARN = os.environ.get("SSM_CORE_POLICY_ARN", "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore")

# Per-project isolation: each project runs on its OWN IAM role/instance profile + SSH key,
# scoped so a compromised box can reach only its own S3 prefix, repos, log group, and key.
IAM_PATH = "/auto-research/projects/"


def _proj_role_name(project_id):
    return f"auto-research-proj-{project_id}"[:64]


def _proj_key_name(project_id):
    return f"auto-research-{project_id}-worker"


def _worker_key_param(project_id):
    return f"/auto-research/projects/{project_id}/worker-key"


def _agent_token_param(project_id):
    return f"/auto-research/projects/{project_id}/agent-token"


def _create_project_iam(project_id):
    """Create the per-project role + instance profile (idempotent). Returns the instance-profile name.
    The inline policy scopes S3/repos/logs/SSM to THIS project; the permissions boundary caps it."""
    role_name = _proj_role_name(project_id)
    bucket = DATA_BUCKET
    git_actions = ["codecommit:GitPull", "codecommit:GitPush", "codecommit:GetRepository",
                   "codecommit:GetBranch", "codecommit:CreateBranch",
                   "codecommit:MergeBranchesByFastForward", "codecommit:MergeBranchesBySquash"]
    policy = {"Version": "2012-10-17", "Statement": [
        {"Sid": "Bedrock", "Effect": "Allow",
         "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
         "Resource": ["arn:aws:bedrock:*::foundation-model/anthropic.*",
                      f"arn:aws:bedrock:*:{ACCOUNT_ID}:inference-profile/*.anthropic.*"]},
        {"Sid": "S3Project", "Effect": "Allow",
         "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
         "Resource": [f"arn:aws:s3:::{bucket}/{project_id}/*"]},
        {"Sid": "S3Platform", "Effect": "Allow", "Action": ["s3:GetObject"],
         "Resource": [f"arn:aws:s3:::{bucket}/_platform/bundle/*",
                      f"arn:aws:s3:::{bucket}/_platform/settings/{project_id}.json"]},
        {"Sid": "S3List", "Effect": "Allow", "Action": ["s3:ListBucket"],
         "Resource": [f"arn:aws:s3:::{bucket}"]},
        {"Sid": "CodeCommit", "Effect": "Allow", "Action": git_actions,
         "Resource": [f"arn:aws:codecommit:{REGION}:{ACCOUNT_ID}:{project_id}-code",
                      f"arn:aws:codecommit:{REGION}:{ACCOUNT_ID}:{project_id}-wiki"]},
        {"Sid": "Logs", "Effect": "Allow",
         "Action": ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"],
         "Resource": [f"arn:aws:logs:{REGION}:{ACCOUNT_ID}:log-group:/auto-research/{project_id}",
                      f"arn:aws:logs:{REGION}:{ACCOUNT_ID}:log-group:/auto-research/{project_id}:*"]},
        {"Sid": "SSMParams", "Effect": "Allow", "Action": ["ssm:GetParameter", "ssm:GetParameters"],
         "Resource": [f"arn:aws:ssm:{REGION}:{ACCOUNT_ID}:parameter/auto-research/api-url",
                      f"arn:aws:ssm:{REGION}:{ACCOUNT_ID}:parameter/auto-research/projects/{project_id}/*"]},
        {"Sid": "EC2Describe", "Effect": "Allow",
         "Action": ["ec2:DescribeInstances", "ec2:DescribeTags"], "Resource": ["*"]},
        {"Sid": "EC2StartStop", "Effect": "Allow", "Action": ["ec2:StartInstances", "ec2:StopInstances"],
         "Resource": ["*"], "Condition": {"StringEquals": {"aws:ResourceTag/ManagedBy": "auto-research"}}},
        {"Sid": "AgentApi", "Effect": "Allow", "Action": ["lambda:InvokeFunction"],
         "Resource": [f"arn:aws:lambda:{REGION}:{ACCOUNT_ID}:function:AutoResearch-agent_api"]},
    ]}
    trust = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}]}
    tags = [{"Key": "ManagedBy", "Value": "auto-research"}, {"Key": "ProjectId", "Value": project_id}]
    try:
        kw = dict(Path=IAM_PATH, RoleName=role_name, AssumeRolePolicyDocument=json.dumps(trust), Tags=tags)
        if PERMISSIONS_BOUNDARY_ARN:
            kw["PermissionsBoundary"] = PERMISSIONS_BOUNDARY_ARN
        iam.create_role(**kw)
    except iam.exceptions.EntityAlreadyExistsException:
        pass
    iam.put_role_policy(RoleName=role_name, PolicyName="project-scope", PolicyDocument=json.dumps(policy))
    try:
        iam.attach_role_policy(RoleName=role_name, PolicyArn=SSM_CORE_POLICY_ARN)
    except Exception:  # noqa: BLE001
        pass
    try:
        # No Tags= here: tagging an instance profile needs iam:TagInstanceProfile; the role is
        # already tagged, and the profile name encodes the project, so profile tags add nothing.
        iam.create_instance_profile(Path=IAM_PATH, InstanceProfileName=role_name)
    except iam.exceptions.EntityAlreadyExistsException:
        pass
    try:
        iam.add_role_to_instance_profile(InstanceProfileName=role_name, RoleName=role_name)
    except iam.exceptions.LimitExceededException:
        pass  # role already attached
    return role_name


def _create_project_key(project_id):
    """Create a per-project SSH keypair and stash the private key in SSM (SecureString). Returns the
    SSM parameter path (stored on the record as ssh_key_param — the runner reads it to SSH to workers)."""
    key_name = _proj_key_name(project_id)
    param = _worker_key_param(project_id)
    try:
        ec2.delete_key_pair(KeyName=key_name)  # idempotent re-provision
    except Exception:  # noqa: BLE001
        pass
    resp = ec2.create_key_pair(KeyName=key_name, KeyType="ed25519")
    ssm.put_parameter(Name=param, Value=resp["KeyMaterial"], Type="SecureString", Overwrite=True)
    return key_name, param


def _create_agent_token(project_id):
    """Per-project capability token: a random secret only THIS project's role can read (its SSM path).
    The researcher sends it with every agent_api call; agent_api binds it to project_id (§6.3)."""
    token = secrets.token_urlsafe(32)
    ssm.put_parameter(Name=_agent_token_param(project_id), Value=token, Type="SecureString", Overwrite=True)
    return token

SEED_DIR = os.path.join(os.path.dirname(__file__), "seed")


def _resp(code, body):
    return {"statusCode": code, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body, default=str)}


def _slug(s):
    """Canonical, resource-safe project id: lowercase, non-[a-z0-9] -> '-', <=40 chars."""
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return s[:40].strip("-")


def handler(event, context):
    # Async provisioning self-invoke carries {"_provision": true, "project_id": ...}.
    if isinstance(event, dict) and event.get("_provision"):
        return _provision(event.get("project_id", ""))
    return _fast_api(event, context)


# --------------------------------------------------------------------------- API (fast) mode

def _fast_api(event, context):
    try:
        body = json.loads(event.get("body", "{}"))
    except Exception as e:  # noqa: BLE001
        return _resp(400, {"error": f"Invalid request body: {e}"})

    uploaded = _decode_files(body.get("files", []))
    if "auto-research.json" not in uploaded:
        return _resp(400, {"error": "Uploaded folder must contain auto-research.json at its root"})
    try:
        config = json.loads(uploaded["auto-research.json"].decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return _resp(400, {"error": f"auto-research.json is not valid JSON: {e}"})

    project_id = _slug(body.get("project_name") or "")
    if not project_id:
        return _resp(400, {"error": "Project name is required (enter it at project creation)"})

    table = dynamodb.Table(PROJECTS_TABLE)
    if table.get_item(Key={"project_id": project_id}).get("Item"):
        return _resp(409, {"error": f"Project '{project_id}' already exists — choose a different name or delete it first."})

    now = datetime.now(timezone.utc).isoformat()

    # Log group + provisioning stream up front, so the UI can tail progress immediately.
    try:
        logs_client.create_log_group(logGroupName=f"/auto-research/{project_id}")
        logs_client.put_retention_policy(logGroupName=f"/auto-research/{project_id}", retentionInDays=90)
    except logs_client.exceptions.ResourceAlreadyExistsException:
        pass
    try:
        logs_client.create_log_stream(logGroupName=f"/auto-research/{project_id}", logStreamName="provisioning")
    except Exception:  # noqa: BLE001
        pass

    # Stash the upload in S3 (async invoke payload is capped at 256KB; files can be MBs).
    s3.put_object(Bucket=DATA_BUCKET, Key=f"_provisioning/{project_id}.json",
                  Body=json.dumps(body).encode("utf-8"))

    # Minimal record so the project is visible + cleanable even if provisioning fails.
    # Experiment runner: "ec2" (default) launches GPU workers; "local" runs experiments on the
    # (GPU) researcher itself, so there are zero workers.
    runner = body.get("experiment_runner", "ec2")
    if runner not in ("ec2", "local"):
        runner = "ec2"
    worker_count = 0 if runner == "local" else max(1, int(body.get("worker_count", 1)))
    researcher_type = body.get("researcher_instance_type", "m8g.large")

    # Record the creator as owner (immutable Cognito sub) + username for display; admins start empty.
    # Owner/admins drive write authorization (start/stop/guidance) and delete (owner-only).
    who = caller(event)

    # Budget: the project's max budget is reserved from the creator's user budget. It must be > $1 and
    # not exceed the creator's available budget. The reservation is a single atomic conditional
    # decrement (race-safe); delete_project refunds the unspent part.
    try:
        max_budget = float(body.get("budget_usd"))
    except (TypeError, ValueError):
        return _resp(400, {"error": "A project budget (budget_usd) is required."})
    if not max_budget > 1:
        return _resp(400, {"error": "Project budget must be greater than $1."})
    if not who["sub"]:
        return _resp(403, {"error": "Could not identify the requesting user."})
    try:
        dynamodb.Table(USERS_TABLE).update_item(
            Key={"user_sub": who["sub"]},
            UpdateExpression="SET budget = budget - :m",
            ConditionExpression="budget >= :m",
            ExpressionAttributeValues={":m": Decimal(str(max_budget))},
        )
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        return _resp(400, {"error": "Insufficient budget. Ask a budget administrator to grant you more."})

    try:
        table.put_item(
            Item={
                "project_id": project_id,
                "owner": who["sub"],
                "owner_username": who["username"],
                "admins": {},
                "budget_reserved": True,
                "description": config.get("description", ""),
                "metric_name": config.get("metric_name", ""),
                "status": "provisioning",
                "provisioning_state": "queued",
                "provisioning_error": "",
                "model": _regional_model(body.get("model") or "us.anthropic.claude-opus-4-8"),
                "experiment_runner": runner,
                "worker_instance_type": body.get("worker_instance_type", "g4dn.xlarge"),
                "researcher_instance_type": researcher_type,
                "worker_count": worker_count,
                "budget_usd": str(max_budget),
                "patience": str(body.get("patience", 10)),
                "time_budget_seconds": int(body.get("time_budget_seconds", 300)),
                "experiment_timeout_minutes": int(body.get("experiment_timeout_minutes", 15)),
                "write_paper": bool(body.get("write_paper", False)),
                "spent_usd": "0",
                "researcher_cost_usd": "0",
                "s3_data": f"s3://{DATA_BUCKET}/{project_id}",
                "created_at": now,
            },
            ConditionExpression="attribute_not_exists(project_id)",
        )
    except Exception:  # noqa: BLE001 — the reservation already happened; refund it if the row wasn't written
        dynamodb.Table(USERS_TABLE).update_item(
            Key={"user_sub": who["sub"]},
            UpdateExpression="SET budget = budget + :m",
            ExpressionAttributeValues={":m": Decimal(str(max_budget))},
        )
        raise

    _plog(project_id, "queued", "Project accepted. Starting provisioning…")

    # Kick off async provisioning (this same function, provision mode).
    lam.invoke(FunctionName=context.function_name, InvocationType="Event",
               Payload=json.dumps({"_provision": True, "project_id": project_id}).encode("utf-8"))

    return _resp(202, {"project_id": project_id, "status": "provisioning"})


# --------------------------------------------------------------------------- provision (async) mode

def _provision(project_id):
    table = dynamodb.Table(PROJECTS_TABLE)
    created_instances = []
    try:
        body = json.loads(s3.get_object(Bucket=DATA_BUCKET, Key=f"_provisioning/{project_id}.json")["Body"].read())
        uploaded = _decode_files(body.get("files", []))
        config = json.loads(uploaded["auto-research.json"].decode("utf-8"))
        # README.md is the researcher's brief (goal, background, seed refs, starting ideas). It is
        # committed to the repo AND inlined into CLAUDE.md + used to seed the wiki.
        readme = uploaded.get("README.md", b"").decode("utf-8", "replace")
        # time_budget_seconds comes from the Create-Project form (not auto-research.json); inject it
        # into the committed config so it is the experiment's self-enforced compute budget + shows in
        # CLAUDE.md. (The hard experiment timeout is experiment_timeout_minutes, stored in the project
        # record and enforced at dispatch — see dispatch-experiment.sh.)
        config["time_budget_seconds"] = int(body.get("time_budget_seconds", 300))
        model = _regional_model(body.get("model") or "us.anthropic.claude-opus-4-8")
        read_only_files = config.get("read_only_files", [])
        runner = body.get("experiment_runner", "ec2")
        if runner not in ("ec2", "local"):
            runner = "ec2"
        worker_type = body.get("worker_instance_type", "g4dn.xlarge")
        researcher_type = body.get("researcher_instance_type", "m8g.large")
        # Local mode: experiments run on the (GPU) researcher itself — zero workers, and the
        # researcher boots the Deep Learning AMI (CUDA/drivers) instead of plain Ubuntu.
        worker_count = 0 if runner == "local" else max(1, int(body.get("worker_count", 1)))
        worker_ebs_gb = int(body.get("worker_ebs_gb", 150))
        code_repo, wiki_repo = f"{project_id}-code", f"{project_id}-wiki"

        _plog(project_id, "creating-repos", f"Creating CodeCommit repos {code_repo}, {wiki_repo}…")
        _create_repo(code_repo, f"Experiment code for {project_id}")
        _create_repo(wiki_repo, f"LLM Wiki for {project_id}")

        _plog(project_id, "committing", "Committing the research bootstrap to the code repo…")
        _commit_code_repo(code_repo, uploaded, config, read_only_files, project_id, readme)
        _plog(project_id, "committing", "Publishing skills + scripts to S3…")
        _upload_platform_bundle(project_id, read_only_files)
        _plog(project_id, "committing", "Seeding the wiki repo…")
        _commit_wiki_repo(wiki_repo, project_id, readme)

        _plog(project_id, "resolving-amis", "Resolving AMIs + minting the project's IAM role and SSH key…")
        # In local mode the researcher is the GPU compute node → give it the Deep Learning AMI
        # (CUDA/drivers), like a worker. In ec2 mode it stays the cheap CPU Ubuntu box.
        researcher_ami, researcher_root = _get_ami("worker" if runner == "local" else "researcher", researcher_type)
        worker_ami, worker_root = _get_ami("worker")
        # Per-project isolation: this project's own role/instance-profile, SSH key, and agent token.
        profile_name = _create_project_iam(project_id)
        project_key_name, ssh_key_param = _create_project_key(project_id)
        agent_token = _create_agent_token(project_id)
        # Local-mode researcher needs a real disk for datasets/checkpoints (like a worker); the ec2
        # CPU researcher only orchestrates, so a small root volume is enough.
        researcher_ebs_gb = worker_ebs_gb if runner == "local" else 40

        _plog(project_id, "creating-researcher", f"Launching AI Researcher ({researcher_type}, runner={runner})…")
        researcher_id = _create_instance(
            name=f"researcher-{project_id}", instance_type=researcher_type, ami=researcher_ami,
            root_device=researcher_root, project_id=project_id, role="researcher",
            sg_id=RESEARCHER_SG, key_name=None, ebs_gb=researcher_ebs_gb, model=model,
            instance_profile_name=profile_name)
        created_instances.append(researcher_id)

        worker_ids = []
        for i in range(worker_count):
            _plog(project_id, "creating-workers", f"Launching GPU worker {i + 1}/{worker_count} ({worker_type})…")
            wid = _create_instance(
                name=f"worker-{project_id}-{i}", instance_type=worker_type, ami=worker_ami,
                root_device=worker_root, project_id=project_id, role="worker",
                sg_id=WORKER_SG, key_name=project_key_name, ebs_gb=worker_ebs_gb, model=None,
                instance_profile_name=profile_name)
            worker_ids.append(wid)
            created_instances.append(wid)

        # auto_start (default true): finish provisioning with the project already ACTIVE. The
        # researcher's first boot starts the auto-research-boot service (see _user_data), whose loop
        # reads status=active and runs directly — no manual Start needed. Set auto_start=false to
        # leave the project in `setup`: the same first-boot loop sees status != active and self-stops
        # via researcher-bootstrap, so the instance ends stopped until a human presses Start.
        auto_start = bool(body.get("auto_start", True))
        start_status = "active" if auto_start else "setup"
        table.update_item(
            Key={"project_id": project_id},
            UpdateExpression=(
                "SET #s=:st, provisioning_state=:ps, researcher_instance_id=:r, "
                "worker_instance_ids=:ws, "
                "ssh_key_param=:k, agent_token=:tok, instance_profile_name=:ip, worker_key_name=:wk, "
                "code_repo=:cr, wiki_repo=:wr, report_key=:rk, "
                "researcher_log_stream=:rls, researcher_log_s3_uri=:rlu, best_metric=:bm"
            ),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":st": start_status, ":ps": "done", ":r": researcher_id, ":ws": worker_ids,
                ":k": ssh_key_param, ":tok": agent_token, ":ip": profile_name, ":wk": project_key_name,
                ":cr": f"codecommit::{REGION}://{code_repo}", ":wr": f"codecommit::{REGION}://{wiki_repo}",
                ":rk": "report/status.md", ":rls": f"researcher-{researcher_id}",
                ":rlu": f"s3://{DATA_BUCKET}/{project_id}/logs/researcher.jsonl", ":bm": "",
            },
        )
        _plog(project_id, "done",
              "Provisioning complete — AI Researcher starting automatically." if auto_start
              else "Provisioning complete. Ready to Start.")
        s3.delete_object(Bucket=DATA_BUCKET, Key=f"_provisioning/{project_id}.json")
        return {"ok": True}

    except Exception as e:  # noqa: BLE001
        msg = _friendly_error(e)
        # Roll back: terminate any instances we created (so nothing is orphaned / keeps costing).
        for iid in created_instances:
            try:
                ec2.terminate_instances(InstanceIds=[iid])
            except Exception:  # noqa: BLE001
                pass
        try:
            table.update_item(
                Key={"project_id": project_id},
                UpdateExpression="SET #s=:f, provisioning_state=:ps, provisioning_error=:e",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":f": "provision_failed", ":ps": "failed", ":e": msg},
            )
        except Exception:  # noqa: BLE001
            pass
        # Delete the stashed upload on failure too (it holds the raw bootstrap files, which may
        # contain secrets a user embedded) — on success it's already deleted above.
        try:
            s3.delete_object(Bucket=DATA_BUCKET, Key=f"_provisioning/{project_id}.json")
        except Exception:  # noqa: BLE001
            pass
        _plog(project_id, "failed", f"PROVISIONING FAILED: {msg}")
        if created_instances:
            _plog(project_id, "failed", f"Rolled back {len(created_instances)} instance(s) — no orphaned compute.")
        return {"ok": False, "error": msg}


def _friendly_error(e):
    s = str(e)
    if "InsufficientInstanceCapacity" in s:
        return ("InsufficientInstanceCapacity: no On-Demand capacity for the chosen GPU worker in "
                "this region right now. Attach an On-Demand Capacity Reservation / EC2 Capacity Block "
                "for the instance type, or pick a g5.xlarge / g6.xlarge worker. (" + s[:300] + ")")
    if "VcpuLimitExceeded" in s or "InstanceLimitExceeded" in s:
        return "EC2 vCPU/instance quota exceeded for this instance family — request a quota increase. (" + s[:300] + ")"
    if "AccessDenied" in s or "UnauthorizedOperation" in s:
        return "Permission denied during provisioning — check the create_project Lambda role. (" + s[:300] + ")"
    return s[:500]


def _plog(project_id, state, msg):
    """Stream a provisioning step to CloudWatch + reflect the latest state on the record."""
    try:
        ts = int(time.time() * 1000)
        logs_client.put_log_events(
            logGroupName=f"/auto-research/{project_id}", logStreamName="provisioning",
            logEvents=[{"timestamp": ts, "message": msg}])
    except Exception:  # noqa: BLE001
        pass
    try:
        dynamodb.Table(PROJECTS_TABLE).update_item(
            Key={"project_id": project_id},
            UpdateExpression="SET provisioning_state=:s, provisioning_msg=:m",
            ExpressionAttributeValues={":s": state, ":m": msg})
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- uploads / repos / files / ec2

def _decode_files(files):
    out = {}
    for f in files or []:
        path = (f.get("path") or "").lstrip("/")
        if not path or ".." in path.split("/"):
            continue
        if any(p in (".git", ".venv", "__pycache__", "node_modules") for p in path.split("/")):
            continue
        if "content_b64" in f:
            out[path] = base64.b64decode(f["content_b64"])
        elif "content" in f:
            out[path] = f["content"].encode("utf-8")
    return out


def _create_repo(name, description):
    try:
        codecommit.create_repository(repositoryName=name, repositoryDescription=description)
    except codecommit.exceptions.RepositoryNameExistsException:
        pass


def _commit_code_repo(repo, uploaded, config, read_only_files, project_id, readme):
    """Commit ONLY the user's research artifact. The managed skills/scripts (.claude, .auto-research)
    and the deny-rules settings are NOT committed here — they are delivered to the instance's HOME at
    boot from S3 (see _upload_platform_bundle + the boot.sh in _user_data), so the repo stays clean."""
    files = {}
    for path, content in uploaded.items():
        if path == "wiki" or path.startswith("wiki/"):
            continue
        files[path] = content
    # Commit the augmented config (form-injected time_budget_seconds) so the committed
    # auto-research.json carries the form value, not whatever was in the uploaded one.
    files["auto-research.json"] = json.dumps(config, indent=2).encode()
    # Auto-Research owns CLAUDE.md — always (re)generate it, ignoring any uploaded copy.
    files["CLAUDE.md"] = _claude_md(config, readme).encode()
    files.setdefault("report/status.md", _status_seed(config, project_id).encode())
    if not any(p == "src" or p.startswith("src/") for p in files):
        files["src/.gitkeep"] = b""
    put_files = [{"filePath": p, "fileContent": c} for p, c in files.items()]
    try:
        codecommit.create_commit(
            repositoryName=repo, branchName="main", putFiles=put_files,
            commitMessage="Initial project (uploaded research bootstrap)",
            authorName="auto-research", email="auto-research@system")
    except codecommit.exceptions.ParentCommitIdRequiredException:
        pass


def _upload_platform_bundle(project_id, read_only_files):
    """Publish the runtime seed (skills + scripts) to a SHARED S3 location and the project's
    deny-rules settings to a per-project key. The instance boot.sh syncs these into ~/.claude and
    ~/.auto-research so the agent has its skills/scripts without them living in the code repo.

    Layout:
      s3://{bucket}/_platform/bundle/.claude/skills/...     (shared; overwritten each creation)
      s3://{bucket}/_platform/bundle/.auto-research/...      (shared)
      s3://{bucket}/_platform/settings/{project}.json        (per-project deny rules)
    """
    if os.path.isdir(SEED_DIR):
        for root, _dirs, names in os.walk(SEED_DIR):
            for n in names:
                abs_path = os.path.join(root, n)
                rel = os.path.relpath(abs_path, SEED_DIR)
                with open(abs_path, "rb") as f:
                    s3.put_object(Bucket=DATA_BUCKET, Key=f"_platform/bundle/{rel}", Body=f.read())
    s3.put_object(
        Bucket=DATA_BUCKET,
        Key=f"_platform/settings/{project_id}.json",
        Body=json.dumps(_settings(read_only_files), indent=2).encode(),
    )


def _commit_wiki_repo(repo, project_id, readme):
    # Auto-Research owns the wiki seed — always generate it from the README brief, ignoring any
    # uploaded wiki/ folder.
    put_files = _generated_wiki(readme)
    try:
        codecommit.create_commit(
            repositoryName=repo, branchName="main", putFiles=put_files,
            commitMessage="Initialize wiki", authorName="auto-research", email="auto-research@system")
    except codecommit.exceptions.ParentCommitIdRequiredException:
        pass


def _generated_wiki(readme):
    # The project README.md is the researcher's brief (goal, background, seed refs, starting ideas).
    # Seed the wiki's research-context page from it verbatim; the agent expands the wiki from there.
    brief = readme.strip() or "(No project brief provided — see README.md.)"
    domain = "the project"
    for line in readme.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            domain = s.split(".")[0]
            break
    return [
        {"filePath": "SCHEMA.md", "fileContent": (
            f"# Wiki Schema\n\n## Domain\n{domain}\n\n"
            "## Conventions\n- Pages use `[[wikilinks]]`\n- Each page targets 50-150 lines; distill to state-of-the-art\n\n"
            "## Tag Taxonomy\n- `#experiment` `#technique` `#lesson` `#literature` `#baseline`\n").encode()},
        {"filePath": "index.md", "fileContent": (
            "# Wiki Index\n\n## Concepts\n- [Research Context](concepts/research-context.md) — the project brief (README.md)\n").encode()},
        {"filePath": "log.md", "fileContent": b"# Wiki Log\n"},
        {"filePath": "concepts/research-context.md", "fileContent": (
            f"# Research Context\n\nSeeded from the project brief (README.md).\n\n{brief}\n").encode()},
        {"filePath": "entities/.gitkeep", "fileContent": b""},
        {"filePath": "comparisons/.gitkeep", "fileContent": b""},
        {"filePath": "queries/.gitkeep", "fileContent": b""},
        {"filePath": "raw/papers/.gitkeep", "fileContent": b""},
    ]


def _claude_md(config, readme):
    direction = "Lower is better" if config.get("metric_direction") == "lower_is_better" else "Higher is better"
    mod = ", ".join(config.get("modifiable_files", [])) or "(the code under src/)"
    ro = ", ".join(config.get("read_only_files", [])) or "(none)"
    brief = readme.strip() or "(no README.md was provided with this project)"
    return f"""# AI Researcher

You are an autonomous AI Researcher. You run the scientific method end-to-end: build context,
design an experiment, run it on the GPU worker (via a sub-agent), learn, and repeat.

## Goal
Optimize {config.get('metric_name','the metric')}. {direction}. Your full brief — the objective,
background, seed references, and starting ideas — is the project README reproduced at the bottom of
this file. Read it first.

## Rules
- You may modify: {mod}
- You may NOT modify: {ro}, auto-research.json (enforced by .claude/settings.json)
- Training time budget: {config.get('time_budget_seconds', 300)} seconds. Make your training/eval
  code self-limit to roughly this wall-clock (e.g. a time check in the training loop) so every
  experiment gets the same compute and results stay comparable. The run is NOT force-killed at this
  point — the hard cap is the separate per-experiment timeout — so spending far more or less than the
  budget produces misleading, incomparable numbers.
- Evaluate with: {config.get('evaluation_command','')}
- The metric is read from the JSON the evaluation writes (metrics.json: {{"metric": <number>}}).

## Loop
Your loop invokes `/research-step` each iteration. Follow it exactly and never pause to ask whether
to continue — you are autonomous until the human stops the session.

## Memory
Persistent memory is the LLM Wiki (`~/workspace/wiki`), `report/status.md`, and the experiments
ledger. Re-read them each iteration; do not rely on chat history.

---

# Project brief (README.md)

{brief}
"""


def _settings(read_only_files):
    deny = []
    for f in read_only_files:
        deny += [f"Edit({f})", f"Write({f})"]
    deny += ["Edit(auto-research.json)", "Write(auto-research.json)"]
    return {"permissions": {"deny": deny}}


def _status_seed(config, project_id):
    return f"""# {project_id} — Research Status

**Best {config.get('metric_name','metric')}:** (not started)  ·  **Experiments:** 0  ·  **Spend:** $0

## Current direction
Project created; awaiting the first research iteration.

## What we've learned
(nothing yet)

## Next up
See the project brief (README.md) for the starting experiment ideas.
"""


def _key_param(key_name):
    try:
        resp = ec2.describe_key_pairs(KeyNames=[key_name])
        return f"/ec2/keypair/{resp['KeyPairs'][0]['KeyPairId']}"
    except Exception:  # noqa: BLE001
        return ""


def _cpu_arch(instance_type):
    """EC2 arch for an instance type. Graviton families carry a digit immediately followed by 'g'
    (m8g, c7g, t4g, m9g) → arm64; x86/GPU families do not (m7i, t3, g5, p5, g4dn) → x86_64."""
    fam = (instance_type or "").split(".")[0]
    return "arm64" if re.search(r"[0-9]g", fam) else "x86_64"


def _regional_model(model):
    """Rewrite a cross-region inference-profile geo prefix to match the deploy region.

    Inference profiles are geo-scoped: us-* → 'us.', eu-* → 'eu.', ap-* → 'apac.'. A project
    config authored for one region (e.g. 'us.anthropic.claude-opus-4-8') is remapped here so it
    still resolves in whatever region Auto-Research is deployed to. 'global.' profiles and bare
    foundation-model ids are left untouched.
    """
    if not model:
        return model
    prefix = ("eu" if REGION.startswith("eu-")
              else "apac" if REGION.startswith("ap-")
              else "us")
    return re.sub(r"^(us|eu|apac)\.anthropic\.", f"{prefix}.anthropic.", model)


def _get_ami(role, instance_type=None):
    if role == "worker":
        # GPU workers use the x86_64 Deep Learning AMI regardless of the researcher's arch.
        filters = [{"Name": "name", "Values": ["Deep Learning*Ubuntu 22.04*"]},
                   {"Name": "state", "Values": ["available"]}, {"Name": "architecture", "Values": ["x86_64"]}]
        owners = ["amazon"]
    else:
        # Researcher: pick the Ubuntu AMI matching the chosen instance's architecture, so a Graviton
        # (arm64) researcher (e.g. m9g.large) boots an arm64 image and an x86 one boots amd64.
        arch = _cpu_arch(instance_type)
        ubuntu_arch = "arm64" if arch == "arm64" else "amd64"
        filters = [{"Name": "name", "Values": [f"ubuntu/images/hvm-ssd*/ubuntu-jammy-22.04-{ubuntu_arch}-server-*"]},
                   {"Name": "state", "Values": ["available"]}, {"Name": "architecture", "Values": [arch]}]
        owners = ["099720109477"]
    resp = ec2.describe_images(Owners=owners, Filters=filters)
    images = sorted(resp["Images"], key=lambda x: x["CreationDate"], reverse=True)
    if not images:
        raise RuntimeError(f"No AMI found for role={role}")
    return images[0]["ImageId"], images[0].get("RootDeviceName", "/dev/sda1")


def _user_data():
    return ("""#!/bin/bash
set -e
mkdir -p /opt/auto-research
cat > /opt/auto-research/boot.sh <<'BOOT'
#!/bin/bash
set -uo pipefail
BL=/var/log/ar-boot.log
exec > >(tee -a "$BL") 2>&1     # capture ALL boot output to a file (streamed to CloudWatch below)
echo "[boot] $(date -u +%FT%TZ) starting"
T=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
imds(){ curl -s -H "X-aws-ec2-metadata-token: $T" "http://169.254.169.254/latest/meta-data/$1"; }
IID=$(imds instance-id); export AWS_REGION=$(imds placement/region)
# Cache-first apt: install from the AMI's existing package lists; only refresh indexes (skipping the
# multi-MB Translation files) if that fails. Avoids a ~47 MB `apt-get update` on every boot.
apt_install(){ DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$@" 2>/dev/null || { apt-get update -y -o Acquire::Languages=none && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$@"; }; }
# AWS CLI FIRST (needed for tags + the boot-log shipper). unzip (cache-first) is needed for its installer.
if ! command -v aws >/dev/null; then
  command -v unzip >/dev/null || apt_install unzip
  case "$(uname -m)" in aarch64|arm64) ACLI=aarch64 ;; *) ACLI=x86_64 ;; esac   # arm64 (Graviton) vs x86
  curl -s "https://awscli.amazonaws.com/awscli-exe-linux-${ACLI}.zip" -o /tmp/awscliv2.zip && \
  unzip -q /tmp/awscliv2.zip -d /tmp && /tmp/aws/install >/dev/null 2>&1
fi
export PATH=/usr/local/bin:$PATH
# Resolve role/project from tags WITHOUT jq (not installed yet) via server-side --query.
ROLE=$(aws ec2 describe-tags --filters "Name=resource-id,Values=$IID" --region "$AWS_REGION" --query "Tags[?Key=='Role'].Value | [0]" --output text)
PID=$(aws ec2 describe-tags --filters "Name=resource-id,Values=$IID" --region "$AWS_REGION" --query "Tags[?Key=='ProjectId'].Value | [0]" --output text)
# --- Boot-phase visibility: start streaming this script's output to CloudWatch `boot` NOW — BEFORE
#     the jq/pip install — so the entire boot is observable. Uses python3 (preinstalled), no jq. ---
GRP="/auto-research/$PID"
aws logs create-log-group --log-group-name "$GRP" --region "$AWS_REGION" 2>/dev/null || true
aws logs create-log-stream --log-group-name "$GRP" --log-stream-name boot --region "$AWS_REGION" 2>/dev/null || true
( LAST=0
  while true; do
    TOTAL=$(wc -l < "$BL" 2>/dev/null || echo 0)
    if [ "$TOTAL" -gt "$LAST" ]; then
      EV=$(sed -n "$((LAST+1)),${TOTAL}p" "$BL" | python3 -c 'import sys,json,time; t=int(time.time()*1000); print(json.dumps([{"timestamp":t,"message":l.rstrip()} for l in sys.stdin if l.strip()]))')
      [ "$EV" = "[]" ] || aws logs put-log-events --log-group-name "$GRP" --log-stream-name boot --log-events "$EV" --region "$AWS_REGION" >/dev/null 2>&1 || true
      LAST=$TOTAL
    fi
    sleep 3
  done ) &
# jq + pip (cache-first) — now visible in the boot log. git/curl are preinstalled on the base AMI.
command -v jq >/dev/null || apt_install jq
# poppler-utils (pdftotext) — used by the web-search skill to read PDFs.
command -v pdftotext >/dev/null || apt_install poppler-utils
# pip — needed only to install the aws_auto_research library below (ensurepip avoids the apt mirror).
python3 -m pip --version >/dev/null 2>&1 || python3 -m ensurepip --upgrade >/dev/null 2>&1 || apt_install python3-pip
U=ubuntu
# NOTE: git-remote-codecommit + the code/wiki repo clone now happen inside `aws-research researcher
# boot` (Python) — installed/run once the library is present, so they are not duplicated here.
# Skills + the library live in the user's HOME (NOT the code repo). Sync them from S3 each boot.
aws s3 sync "s3://__DATA_BUCKET__/_platform/bundle/" "/home/$U/" --only-show-errors || true
mkdir -p "/home/$U/.claude" "/home/$U/.auto-research"
aws s3 cp "s3://__DATA_BUCKET__/_platform/settings/${PID}.json" "/home/$U/.claude/settings.json" --only-show-errors 2>/dev/null || true
chown -R $U:$U "/home/$U/.claude" "/home/$U/.auto-research" 2>/dev/null || true
chmod +x "/home/$U/.auto-research/"*.sh 2>/dev/null || true
# Install the aws_auto_research runtime into an ISOLATED venv at /opt/ar (researcher-only), then
# symlink its `aws-research` onto /usr/local/bin so BOTH root (researcher boot) and the ubuntu user
# (loop + sub-agents) find it. A venv avoids the root-user + non-PEP440 (devscripts) pip warnings and
# PEP-668 entirely — no system-wide pip installs. boot.py installs the rest (grcm, matplotlib) here too.
if [ "$ROLE" = "researcher" ] && [ -d "/home/$U/lib" ]; then
  # Ubuntu ships venv/ensurepip in a separate apt package; install it first (if missing) so the
  # `python3 -m venv` below doesn't emit a failed-attempt + apt "you need python3-venv" notice.
  python3 -c "import ensurepip" 2>/dev/null || apt_install python3-venv python3.10-venv
  python3 -m venv /opt/ar || { apt_install python3-venv python3.10-venv; python3 -m venv /opt/ar; }
  /opt/ar/bin/pip install --quiet --upgrade pip >/dev/null 2>&1 || true
  /opt/ar/bin/pip install --quiet "/home/$U/lib" || echo "[boot] WARNING: aws_auto_research install failed"
  ln -sf /opt/ar/bin/aws-research /usr/local/bin/aws-research
  command -v aws-research >/dev/null || echo "[boot] WARNING: aws-research CLI not on PATH"
fi
if [ "$ROLE" = "researcher" ]; then exec env AWS_REGION="$AWS_REGION" PATH="/usr/local/bin:$PATH" aws-research researcher boot; else exec bash "/home/$U/.auto-research/worker-bootstrap.sh"; fi
BOOT
chmod +x /opt/auto-research/boot.sh
cat > /etc/systemd/system/auto-research-boot.service <<'UNIT'
[Unit]
Description=Auto-Research boot
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
TimeoutStartSec=0
ExecStart=/opt/auto-research/boot.sh
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable auto-research-boot.service
# START the enabled service on THIS first boot so the researcher runs immediately after provisioning
# (auto_start). The loop self-stops via researcher-bootstrap when status != active (auto_start=false /
# setup), so this single path serves both modes. Subsequent boots (after a human Start) auto-start the
# enabled service via multi-user.target. (Do NOT `shutdown` here: that would leave the instance stopped
# with nothing to start it, since provisioning launches the instance running and never re-starts it.)
systemctl start auto-research-boot.service
""").replace("__DATA_BUCKET__", DATA_BUCKET)


def _create_instance(*, name, instance_type, ami, root_device, project_id, role, sg_id, key_name, ebs_gb,
                     model, instance_profile_name=None):
    tags = [{"Key": "Name", "Value": name}, {"Key": "ProjectId", "Value": project_id},
            {"Key": "Role", "Value": role}, {"Key": "ManagedBy", "Value": "auto-research"}]
    if model:
        tags.append({"Key": "Model", "Value": model})
    kwargs = dict(
        ImageId=ami, InstanceType=instance_type, MinCount=1, MaxCount=1,
        IamInstanceProfile={"Name": instance_profile_name or INSTANCE_PROFILE_NAME}, SecurityGroupIds=[sg_id],
        InstanceInitiatedShutdownBehavior="stop", UserData=_user_data(),
        BlockDeviceMappings=[{"DeviceName": root_device,
                              "Ebs": {"VolumeSize": ebs_gb, "VolumeType": "gp3", "DeleteOnTermination": True}}],
        TagSpecifications=[{"ResourceType": "instance", "Tags": tags}])
    if key_name:
        kwargs["KeyName"] = key_name
    # A just-created instance profile is eventually consistent — RunInstances can transiently fail
    # with "Invalid IAM Instance Profile" for a few seconds. Retry that specific error.
    last = None
    for attempt in range(10):
        try:
            return ec2.run_instances(**kwargs)["Instances"][0]["InstanceId"]
        except Exception as e:  # noqa: BLE001
            last = e
            if "Invalid IAM Instance Profile" in str(e) or "instance profile" in str(e).lower():
                time.sleep(3)
                continue
            raise
    raise last
