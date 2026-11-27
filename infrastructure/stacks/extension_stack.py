"""A domain extension, installed in this deployment (design.md "Domain extensions").

    cdk deploy AutoResearchExtension-<name> -c extensions=<dir>[,<dir>...]     (scripts/deploy.sh passes it on)

``<dir>`` is an extension package built by the external system (robots-sim: integrations/autoresearch/build.py):
``extension.json`` (the manifest), ``client/`` (wheels), ``skill/`` (SKILL.md). The stack uploads it to
``s3://<data bucket>/_platform/extensions/<name>/`` and grants the control-plane Lambdas exactly what the manifest's
credential provider needs, on its one resource:

- ``cognito-user``: create, password, group, disable, enable, sign-out and delete users of that user pool
  (create_project creates the project's user, agent_api disables it when the budget is spent, delete_project
  deletes it). The project roles and their permissions boundary are unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

from aws_cdk import Fn, Stack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3deploy
from constructs import Construct

COGNITO_USER_ACTIONS = ["cognito-idp:AdminCreateUser", "cognito-idp:AdminSetUserPassword",
                        "cognito-idp:AdminAddUserToGroup", "cognito-idp:AdminDisableUser",
                        "cognito-idp:AdminEnableUser", "cognito-idp:AdminUserGlobalSignOut",
                        "cognito-idp:AdminDeleteUser", "cognito-idp:AdminGetUser"]


def load_manifest(ext_dir: str) -> dict:
    path = Path(ext_dir) / "extension.json"
    if not path.exists():
        raise ValueError(f"{ext_dir}: no extension.json (build the extension package first)")
    doc = json.loads(path.read_text())
    for k in ("name", "provider"):
        if k not in doc:
            raise ValueError(f"{path}: '{k}' is required")
    return doc


def provider_statements(doc: dict, account: str) -> list[iam.PolicyStatement]:
    prov = doc["provider"]
    if prov.get("type") == "cognito-user":
        region = prov.get("region")
        arn = f"arn:aws:cognito-idp:{region}:{account}:userpool/{prov['user_pool_id']}"
        return [iam.PolicyStatement(sid="ExtensionCognitoUsers", actions=COGNITO_USER_ACTIONS, resources=[arn])]
    raise ValueError(f"extension {doc['name']}: provider type {prov.get('type')!r} is not supported")


# the control-plane Lambdas that run providers and credential hooks (exported by AutoResearchApiStack)
FUNCTION_ROLE_EXPORTS = ("AutoResearch-create-project-role-arn", "AutoResearch-delete-project-role-arn",
                         "AutoResearch-agent-api-role-arn")


class DomainExtensionStack(Stack):
    """No construct references into the other stacks (the data bucket by its fixed name, the Lambda roles by their
    fixed exports), so installing or removing an extension never changes them."""

    def __init__(self, scope: Construct, construct_id: str, *, ext_dir: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        doc = load_manifest(ext_dir)
        name = doc["name"]
        bucket = s3.Bucket.from_bucket_name(self, "Data", f"auto-research-{self.account}-{self.region}")
        s3deploy.BucketDeployment(
            self, "Package", sources=[s3deploy.Source.asset(ext_dir)], destination_bucket=bucket,
            destination_key_prefix=f"_platform/extensions/{name}/", prune=True, retain_on_delete=False)
        roles = [iam.Role.from_role_arn(self, f"Fn{i}", Fn.import_value(e), mutable=True)
                 for i, e in enumerate(FUNCTION_ROLE_EXPORTS)]
        iam.Policy(self, "ProviderAccess", statements=provider_statements(doc, self.account), roles=roles)
