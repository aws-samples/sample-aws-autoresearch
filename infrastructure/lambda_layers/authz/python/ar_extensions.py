"""Domain extensions: specialised external systems a project's AI Researcher uses (design.md "Domain extensions").

An extension is installed once per deployment (an ``AutoResearchExtension-<name>`` stack uploads it to
``s3://<data bucket>/_platform/extensions/<name>/``: ``extension.json``, ``client/`` wheels, ``skill/``) and enabled
per project. Its **credential provider** creates the project's own credentials at project creation and removes them
at deletion; the Lambdas here run it. Its hooks that act on the credentials (``disable-user`` when the budget is
spent, ``delete-user`` on deletion) run here too; command hooks (``health``, ``on_stop``) and the client run on the
researcher.

Providers: ``cognito-user`` (a user in a Cognito user pool, optionally in groups; its password in the project's SSM
path). The manifest's provider block is the only configuration; the extension's stack grants the Lambdas the
provider's permissions on that one resource.
"""

from __future__ import annotations

import json
import re
import secrets
import string

PREFIX = "_platform/extensions"
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
BUILTIN_HOOKS = {"disable-user", "enable-user", "delete-user"}


class ExtensionError(ValueError):
    pass


def manifest_key(name: str) -> str:
    return f"{PREFIX}/{name}/extension.json"


def password_param(project_id: str, name: str) -> str:
    return f"/auto-research/projects/{project_id}/ext/{name}/password"


def load_manifest(s3, bucket: str, name: str) -> dict:
    if not NAME_RE.match(name or ""):
        raise ExtensionError(f"invalid extension name {name!r}")
    try:
        doc = json.loads(s3.get_object(Bucket=bucket, Key=manifest_key(name))["Body"].read())
    except Exception as e:  # noqa: BLE001 - NoSuchKey or unreadable
        raise ExtensionError(f"extension {name!r} is not installed in this deployment") from e
    if doc.get("name") != name:
        raise ExtensionError(f"extension {name!r}: its manifest names {doc.get('name')!r}")
    return doc


def list_installed(s3, bucket: str) -> list[dict]:
    """Every installed extension's manifest (public fields only) with when it was installed."""
    out = []
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=f"{PREFIX}/", Delimiter="/")
    for p in resp.get("CommonPrefixes", []):
        name = p["Prefix"][len(PREFIX) + 1:].strip("/")
        try:
            obj = s3.get_object(Bucket=bucket, Key=manifest_key(name))
            doc = json.loads(obj["Body"].read())
        except Exception:  # noqa: BLE001 - a half-installed extension is skipped
            continue
        out.append({**public(doc), "installed_at": obj["LastModified"].isoformat()})
    return sorted(out, key=lambda d: d["name"])


def public(doc: dict) -> dict:
    """What the web app shows of a manifest: no pool ids, client ids or URLs other than the web app's."""
    prov = doc.get("provider") or {}
    return {"name": doc.get("name"), "display_name": doc.get("display_name") or doc.get("name"),
            "description": doc.get("description", ""), "cost_note": doc.get("cost_note", ""),
            "web_url": doc.get("web_url", ""), "provider_type": prov.get("type"),
            "metered": bool((doc.get("cost") or {}).get("command"))}


def required_by(config: dict) -> list[str]:
    ext = config.get("extensions") or []
    if not isinstance(ext, list) or not all(isinstance(e, str) for e in ext):
        raise ExtensionError("auto-research.json: 'extensions' must be a list of extension names")
    return list(dict.fromkeys(ext))


def username(project_id: str, doc: dict) -> str:
    prov = doc.get("provider") or {}
    return (str(prov.get("username_prefix", "ar-")) + project_id)[:128]


def _password(n: int = 32) -> str:
    """A password every Cognito policy accepts: upper, lower, digit and symbol, 32 characters."""
    alphabet = string.ascii_letters + string.digits + "!#%+-=_."
    while True:
        p = "".join(secrets.choice(alphabet) for _ in range(n))
        if (any(c.islower() for c in p) and any(c.isupper() for c in p) and any(c.isdigit() for c in p)
                and any(c in "!#%+-=_." for c in p)):
            return p


def _idp(session, doc: dict):
    prov = doc["provider"]
    return session.client("cognito-idp", region_name=prov.get("region"))


def provision(session, project_id: str, doc: dict, *, ssm) -> dict:
    """Create the project's credentials; return the record kept on the project (no secrets)."""
    prov = doc.get("provider") or {}
    if prov.get("type") != "cognito-user":
        raise ExtensionError(f"extension {doc.get('name')}: provider type {prov.get('type')!r} is not supported")
    idp, user, pool = _idp(session, doc), username(project_id, doc), prov["user_pool_id"]
    pw = _password()
    try:
        idp.admin_create_user(UserPoolId=pool, Username=user, MessageAction="SUPPRESS", TemporaryPassword=_password())
    except idp.exceptions.UsernameExistsException:
        idp.admin_enable_user(UserPoolId=pool, Username=user)        # a re-provisioned project
    idp.admin_set_user_password(UserPoolId=pool, Username=user, Password=pw, Permanent=True)
    for g in prov.get("groups") or []:
        idp.admin_add_user_to_group(UserPoolId=pool, Username=user, GroupName=g)
    param = password_param(project_id, doc["name"])
    ssm.put_parameter(Name=param, Value=pw, Type="SecureString", Overwrite=True,
                      Description=f"{doc['name']} password of {user} (domain extension)")
    health = check(session, doc, user, pw)
    return {"status": "ok" if health == "ok" else "unhealthy", "health": health, "provider": "cognito-user",
            "identity": user, "username": user, "password_param": param, "web_url": doc.get("web_url", "")}


def check(session, doc: dict, user: str, pw: str) -> str:
    """'ok' when the credentials sign in (the extension's health check at creation), else the reason."""
    prov = doc["provider"]
    if not prov.get("client_id"):
        return "ok"
    try:
        _idp(session, doc).initiate_auth(AuthFlow="USER_PASSWORD_AUTH", ClientId=prov["client_id"],
                                         AuthParameters={"USERNAME": user, "PASSWORD": pw})
        return "ok"
    except Exception as e:  # noqa: BLE001
        return f"sign-in failed: {type(e).__name__}: {str(e)[:200]}"


def run_builtin(session, hook: str, doc: dict, rec: dict) -> str:
    """Run a credential hook (disable-user, enable-user, delete-user); returns what it did."""
    if hook not in BUILTIN_HOOKS:
        raise ExtensionError(f"{hook!r} is not a platform hook (command hooks run on the researcher)")
    idp, pool, user = _idp(session, doc), doc["provider"]["user_pool_id"], rec.get("username")
    if not user:
        return "no user"
    try:
        if hook == "disable-user":
            idp.admin_disable_user(UserPoolId=pool, Username=user)
            idp.admin_user_global_sign_out(UserPoolId=pool, Username=user)
        elif hook == "enable-user":
            idp.admin_enable_user(UserPoolId=pool, Username=user)
        else:
            idp.admin_delete_user(UserPoolId=pool, Username=user)
    except idp.exceptions.UserNotFoundException:
        return "user already gone"
    return f"{hook} {user}"


def hook_for(doc: dict, event: str, default: str) -> str:
    return str((doc.get("hooks") or {}).get(event) or default)
