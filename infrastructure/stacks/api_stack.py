"""
API Stack: HTTP API Gateway (Cognito JWT) + control-plane Lambdas.

The AI Researcher (on EC2) reads/writes S3 and CodeCommit directly, but reaches DynamoDB ONLY
through the agent_api Lambda (below) — it has no DynamoDB permissions of its own. The web-app
endpoints here are provisioning, reads, and a few human actions.

Lambdas:
- create_project      : provision repos/S3/log-group + researcher + N STOPPED workers
- delete_project      : tear down one project's instances/repos/log-group/S3/DB rows
- get_experiments     : experiments list + project list                    [read]
- get_workers         : live per-worker EC2 power state                     [read]
- agent_api           : the ONLY DynamoDB path for the AI Researcher agent (invoked directly, no route)
- start_session       : start the AI Researcher instance, status -> active
- stop_session        : stop the AI Researcher instance,  status -> paused
- submit_guidance     : write a human proposal to the Guidance inbox
- get_logs            : tail the project CloudWatch log group               [read]
- wiki_viewer         : CodeCommit tree/file for the {project}-wiki         [read]
- code_viewer         : CodeCommit tree/file/commits/diff for -code         [read]
- data_viewer         : S3 tree/presigned-url/preview                       [read]
"""

from aws_cdk import (
    Stack,
    Duration,
    CfnOutput,
    aws_lambda as _lambda,
    aws_apigatewayv2 as apigwv2,
    aws_apigatewayv2_integrations as integrations,
    aws_apigatewayv2_authorizers as authorizers,
    aws_cognito as cognito,
    aws_dynamodb as dynamodb,
    aws_iam as iam,
    aws_s3 as s3,
    aws_ec2 as ec2,
)
from constructs import Construct


class ApiStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        user_pool: cognito.UserPool,
        user_pool_client: cognito.UserPoolClient,
        projects_table: dynamodb.Table,
        experiments_table: dynamodb.Table,
        guidance_table: dynamodb.Table,
        team_requests_table: dynamodb.Table,
        users_table: dynamodb.Table,
        data_bucket: s3.Bucket,
        instance_profile_name: str,
        instance_role_arn: str,
        researcher_security_group_id: str,
        worker_security_group_id: str,
        worker_key_name: str,
        vpc: ec2.IVpc,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        lambda_dir = "lambda"
        common_env = {
            "PROJECTS_TABLE": projects_table.table_name,
            "EXPERIMENTS_TABLE": experiments_table.table_name,
            "GUIDANCE_TABLE": guidance_table.table_name,
            "TEAM_REQUESTS_TABLE": team_requests_table.table_name,
            "USERS_TABLE": users_table.table_name,
            "DATA_BUCKET": data_bucket.bucket_name,
            "REGION": self.region,
            "ACCOUNT_ID": self.account,
        }

        # Shared authorization helpers (ar_authz.caller/is_owner/is_admin/forbidden) — attached to
        # every control-plane function so per-project authz is enforced from the JWT claims.
        authz_layer = _lambda.LayerVersion(
            self,
            "AuthzLayer",
            code=_lambda.Code.from_asset("lambda_layers/authz"),
            compatible_runtimes=[_lambda.Runtime.PYTHON_3_13],
            description="Per-project authorization helpers (ar_authz).",
        )

        def make_fn(name: str, timeout: int = 30, extra_env: dict | None = None) -> _lambda.Function:
            return _lambda.Function(
                self,
                f"Fn{name}",
                function_name=f"AutoResearch-{name}",
                runtime=_lambda.Runtime.PYTHON_3_13,
                handler="handler.handler",
                code=_lambda.Code.from_asset(f"{lambda_dir}/{name}"),
                timeout=Duration.seconds(timeout),
                environment={**common_env, **(extra_env or {})},
                layers=[authz_layer],
            )

        # Permissions boundary (a ceiling) for every per-project role create_project mints. Even if a
        # bug/compromise in create_project tried to grant a project role more, the boundary caps it to
        # these project-scoped actions — never iam:*, never resources outside this account/region.
        ssm_core_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
        project_boundary = iam.ManagedPolicy(
            self,
            "ProjectRoleBoundary",
            managed_policy_name="auto-research-project-boundary",
            statements=[
                iam.PolicyStatement(actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                                    resources=["arn:aws:bedrock:*::foundation-model/anthropic.*",
                                               f"arn:aws:bedrock:*:{self.account}:inference-profile/*.anthropic.*"]),
                iam.PolicyStatement(actions=["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"],
                                    resources=[data_bucket.bucket_arn, f"{data_bucket.bucket_arn}/*"]),
                iam.PolicyStatement(actions=["codecommit:GitPull", "codecommit:GitPush", "codecommit:GetRepository",
                                             "codecommit:GetBranch", "codecommit:CreateBranch",
                                             "codecommit:MergeBranchesByFastForward", "codecommit:MergeBranchesBySquash"],
                                    resources=[f"arn:aws:codecommit:{self.region}:{self.account}:*"]),
                iam.PolicyStatement(actions=["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"],
                                    resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/auto-research/*"]),
                iam.PolicyStatement(actions=["ssm:GetParameter", "ssm:GetParameters"],
                                    resources=[f"arn:aws:ssm:{self.region}:{self.account}:parameter/auto-research/*"]),
                # SSM Session Manager messaging (the AmazonSSMManagedInstanceCore managed policy the
                # role attaches must also fit inside the boundary, or Session Manager breaks).
                iam.PolicyStatement(actions=["ssmmessages:*", "ec2messages:*", "ssm:UpdateInstanceInformation",
                                             "ssm:ListInstanceAssociations", "ssm:DescribeInstanceProperties",
                                             "ssm:DescribeAssociation", "ssm:GetDeployablePatchSnapshotForInstance"],
                                    resources=["*"]),
                iam.PolicyStatement(actions=["ec2:DescribeInstances", "ec2:DescribeTags",
                                             "ec2:StartInstances", "ec2:StopInstances"], resources=["*"]),
                iam.PolicyStatement(actions=["lambda:InvokeFunction"],
                                    resources=[f"arn:aws:lambda:{self.region}:{self.account}:function:AutoResearch-agent_api"]),
            ],
        )
        role_path_arn = f"arn:aws:iam::{self.account}:role/auto-research/projects/*"
        profile_path_arn = f"arn:aws:iam::{self.account}:instance-profile/auto-research/projects/*"

        # --- create_project: fast API path + async self-invoked provisioning (streamed) ---
        fn_create_project = make_fn(
            "create_project",
            timeout=300,
            extra_env={
                "INSTANCE_PROFILE_NAME": instance_profile_name,
                "RESEARCHER_SECURITY_GROUP_ID": researcher_security_group_id,
                "WORKER_SECURITY_GROUP_ID": worker_security_group_id,
                "WORKER_KEY_NAME": worker_key_name,
                "PERMISSIONS_BOUNDARY_ARN": project_boundary.managed_policy_arn,
                "SSM_CORE_POLICY_ARN": ssm_core_arn,
            },
        )
        projects_table.grant_read_write_data(fn_create_project)
        data_bucket.grant_read_write(fn_create_project)  # stash upload + provisioning payload
        # Async self-invoke for provisioning. Use a LITERAL ARN (the function name is fixed):
        # grant_invoke(self) / fn.function_arn would create a circular dependency between the
        # function, its default role policy, and the API route.
        fn_create_project.add_to_role_policy(
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"],
                resources=[f"arn:aws:lambda:{self.region}:{self.account}:function:AutoResearch-create_project"],
            )
        )
        # EC2 provisioning permissions — least privilege. Rather than a single wildcard
        # statement (which let a compromised Lambda pass ANY role and launch anything), the
        # grants below are split by resource type and constrained:
        #   - iam:PassRole is scoped to the one shared instance role AND only when passed to
        #     EC2, so the Lambda cannot escalate by attaching an arbitrary (e.g. admin) role.
        #   - RunInstances requires the launched instance carry ManagedBy=auto-research, and
        #     Stop/Terminate only apply to instances already tagged that way.
        #   - Describe*/CreateTags-on-launch stay open where AWS offers no resource scoping.

        # 1) Read-only describes — do not support resource-level permissions, so must be "*".
        fn_create_project.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "ec2:DescribeInstances",
                    "ec2:DescribeImages",
                    "ec2:DescribeKeyPairs",
                ],
                resources=["*"],
            )
        )
        # 2) RunInstances — the instance being created must be tagged ManagedBy=auto-research
        #    (the handler always sets this via TagSpecifications).
        fn_create_project.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ec2:RunInstances"],
                resources=[f"arn:aws:ec2:{self.region}:{self.account}:instance/*"],
                conditions={"StringEquals": {"aws:RequestTag/ManagedBy": "auto-research"}},
            )
        )
        # 3) RunInstances — the other resource types a launch touches (pre-existing shared
        #    AMI/SG/subnet/key-pair; volumes/ENIs created for the instance). These have no
        #    request-tag to key on, so scope them to this account/region.
        fn_create_project.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ec2:RunInstances"],
                resources=[
                    f"arn:aws:ec2:{self.region}::image/*",
                    f"arn:aws:ec2:{self.region}:{self.account}:volume/*",
                    f"arn:aws:ec2:{self.region}:{self.account}:network-interface/*",
                    f"arn:aws:ec2:{self.region}:{self.account}:key-pair/*",
                    f"arn:aws:ec2:{self.region}:{self.account}:security-group/*",
                    f"arn:aws:ec2:{self.region}:{self.account}:subnet/*",
                ],
            )
        )
        # 4) CreateTags — only as part of a RunInstances call (tag-on-launch), not standalone.
        fn_create_project.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ec2:CreateTags"],
                resources=[f"arn:aws:ec2:{self.region}:{self.account}:*/*"],
                conditions={"StringEquals": {"ec2:CreateAction": "RunInstances"}},
            )
        )
        # 5) Stop/Terminate — only instances Auto-Research manages (rollback on failure).
        fn_create_project.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ec2:StopInstances", "ec2:TerminateInstances"],
                resources=["*"],
                conditions={"StringEquals": {"aws:ResourceTag/ManagedBy": "auto-research"}},
            )
        )
        # 6) PassRole — the shared instance role (legacy/back-compat) AND any per-project role under
        #    the /auto-research/projects/ path, ONLY to EC2. The Lambda still cannot pass an arbitrary
        #    role (e.g. an admin role outside that path).
        fn_create_project.add_to_role_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[instance_role_arn, role_path_arn],
                conditions={"StringEquals": {"iam:PassedToService": "ec2.amazonaws.com"}},
            )
        )
        # 7) Mint the per-project role + instance profile (isolation). Every created role MUST
        #    carry the permissions boundary (CreateRole is conditioned on it) and live under the
        #    /auto-research/projects/ path, so this can only produce weak, project-scoped roles.
        fn_create_project.add_to_role_policy(
            iam.PolicyStatement(
                actions=["iam:CreateRole"],
                resources=[role_path_arn],
                conditions={"StringEquals": {"iam:PermissionsBoundary": project_boundary.managed_policy_arn}},
            )
        )
        fn_create_project.add_to_role_policy(
            iam.PolicyStatement(
                actions=["iam:PutRolePolicy", "iam:TagRole", "iam:GetRole"],
                resources=[role_path_arn],
            )
        )
        # AttachRolePolicy limited to the one AWS-managed SSM-core policy (Session Manager) — cannot
        # attach an arbitrary managed policy.
        fn_create_project.add_to_role_policy(
            iam.PolicyStatement(
                actions=["iam:AttachRolePolicy"],
                resources=[role_path_arn],
                conditions={"StringEquals": {"iam:PolicyARN": ssm_core_arn}},
            )
        )
        fn_create_project.add_to_role_policy(
            iam.PolicyStatement(
                actions=["iam:CreateInstanceProfile", "iam:AddRoleToInstanceProfile", "iam:GetInstanceProfile"],
                resources=[profile_path_arn],
            )
        )
        # 8) Per-project SSH keypair + per-project SSM params (key material + agent token).
        # CreateKeyPair does not support resource-level scoping the way DeleteKeyPair does; keep it on
        # "*" (creating a keypair is low-risk — the handler names it per-project and stores the private
        # key in the project's SSM path).
        fn_create_project.add_to_role_policy(
            iam.PolicyStatement(actions=["ec2:CreateKeyPair", "ec2:DeleteKeyPair"], resources=["*"])
        )
        fn_create_project.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ssm:PutParameter", "ssm:AddTagsToResource"],
                resources=[f"arn:aws:ssm:{self.region}:{self.account}:parameter/auto-research/projects/*"],
            )
        )
        fn_create_project.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "codecommit:CreateRepository",
                    "codecommit:GetRepository",
                    "codecommit:PutFile",
                    "codecommit:CreateCommit",
                    "codecommit:GetBranch",
                    "codecommit:CreateBranch",
                ],
                resources=[f"arn:aws:codecommit:{self.region}:{self.account}:*"],
            )
        )
        fn_create_project.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "logs:CreateLogGroup", "logs:PutRetentionPolicy",
                    "logs:CreateLogStream", "logs:PutLogEvents",  # stream provisioning progress
                ],
                resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/auto-research/*",
                           f"arn:aws:logs:{self.region}:{self.account}:log-group:/auto-research/*:*"],
            )
        )

        # --- delete_project: tear down one project's resources (cleanup / retry after failure) ---
        fn_delete_project = make_fn("delete_project", timeout=120)
        projects_table.grant_read_write_data(fn_delete_project)
        experiments_table.grant_read_write_data(fn_delete_project)
        guidance_table.grant_read_write_data(fn_delete_project)
        data_bucket.grant_read_write(fn_delete_project)
        # DescribeInstances has no resource-level scoping; Terminate is gated to Auto-Research-managed
        # instances (the handler additionally filters by ProjectId tag before terminating).
        fn_delete_project.add_to_role_policy(
            iam.PolicyStatement(actions=["ec2:DescribeInstances"], resources=["*"])
        )
        fn_delete_project.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ec2:TerminateInstances"],
                resources=["*"],
                conditions={"StringEquals": {"aws:ResourceTag/ManagedBy": "auto-research"}},
            )
        )
        # Repo/log-group deletes constrained to the Auto-Research naming pattern (handler validates the
        # project-id slug), so a caller can't drive deletion of arbitrarily-named repos/log groups.
        fn_delete_project.add_to_role_policy(
            iam.PolicyStatement(
                actions=["codecommit:DeleteRepository"],
                resources=[f"arn:aws:codecommit:{self.region}:{self.account}:*"],
            )
        )
        fn_delete_project.add_to_role_policy(
            iam.PolicyStatement(
                actions=["logs:DeleteLogGroup"],
                resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/auto-research/*"],
            )
        )
        # Async self-invoke for the teardown (fast API path returns 202, then this runs the slow
        # teardown without the API Gateway 29s cap). Literal ARN to avoid a circular dependency.
        fn_delete_project.add_to_role_policy(
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"],
                resources=[f"arn:aws:lambda:{self.region}:{self.account}:function:AutoResearch-delete_project"],
            )
        )
        # Tear down the per-project role + instance profile + keypair + SSM params (GC).
        fn_delete_project.add_to_role_policy(
            iam.PolicyStatement(
                actions=["iam:DeleteRole", "iam:DeleteRolePolicy", "iam:DetachRolePolicy", "iam:GetRole"],
                resources=[role_path_arn],
            )
        )
        fn_delete_project.add_to_role_policy(
            iam.PolicyStatement(
                actions=["iam:RemoveRoleFromInstanceProfile", "iam:DeleteInstanceProfile", "iam:GetInstanceProfile"],
                resources=[profile_path_arn],
            )
        )
        fn_delete_project.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ec2:DeleteKeyPair"],
                resources=[f"arn:aws:ec2:{self.region}:{self.account}:key-pair/*"],
            )
        )
        fn_delete_project.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ssm:DeleteParameter"],
                resources=[f"arn:aws:ssm:{self.region}:{self.account}:parameter/auto-research/projects/*"],
            )
        )

        # --- get_experiments: read board + project list ---
        fn_get_experiments = make_fn("get_experiments")
        projects_table.grant_read_data(fn_get_experiments)
        experiments_table.grant_read_data(fn_get_experiments)

        # --- agent_api: the ONLY DynamoDB path for the AI Researcher agent (invoked directly by
        #     the EC2 instance role, not via HTTP — so no API Gateway route). Owns read+write on the
        #     three tables; the instance role has no DynamoDB permissions of its own. ---
        fn_agent_api = make_fn("agent_api")
        projects_table.grant_read_write_data(fn_agent_api)
        experiments_table.grant_read_write_data(fn_agent_api)
        guidance_table.grant_read_write_data(fn_agent_api)

        # --- get_workers: live per-worker EC2 power state for the dashboard [read] ---
        fn_get_workers = make_fn("get_workers")
        projects_table.grant_read_data(fn_get_workers)
        fn_get_workers.add_to_role_policy(
            iam.PolicyStatement(
                # DescribeInstances does not support resource-level scoping.
                actions=["ec2:DescribeInstances"],
                resources=["*"],
            )
        )

        # --- start_session / stop_session: start/stop the AI Researcher instance ---
        fn_start_session = make_fn("start_session")
        fn_stop_session = make_fn("stop_session")
        for fn in (fn_start_session, fn_stop_session):
            projects_table.grant_read_write_data(fn)
            fn.add_to_role_policy(
                iam.PolicyStatement(
                    actions=["ec2:StartInstances", "ec2:StopInstances", "ec2:DescribeInstances"],
                    resources=["*"],
                    conditions={"StringEquals": {"aws:ResourceTag/ManagedBy": "auto-research"}},
                )
            )
            # DescribeInstances does not support resource-level/tag conditions.
            fn.add_to_role_policy(
                iam.PolicyStatement(actions=["ec2:DescribeInstances"], resources=["*"])
            )

        # --- submit_guidance: human -> agent inbox ---
        fn_submit_guidance = make_fn("submit_guidance")
        guidance_table.grant_write_data(fn_submit_guidance)
        projects_table.grant_read_write_data(fn_submit_guidance)  # also toggles the write_paper flag

        # --- team: per-project owner/admins + join requests ---
        fn_team = make_fn("team")
        projects_table.grant_read_write_data(fn_team)   # updates the admins map
        team_requests_table.grant_read_write_data(fn_team)

        # --- users: per-user budget + budget-admin surface ---
        fn_users = make_fn("users", extra_env={"USER_POOL_ID": user_pool.user_pool_id})
        users_table.grant_read_write_data(fn_users)
        fn_users.add_to_role_policy(
            iam.PolicyStatement(actions=["cognito-idp:ListUsers"], resources=[user_pool.user_pool_arn])
        )
        # create_project reserves budget, delete_project refunds it → both need the users table.
        users_table.grant_read_write_data(fn_create_project)
        users_table.grant_read_write_data(fn_delete_project)

        # --- get_logs: CloudWatch tail (update stream) ---
        fn_get_logs = make_fn("get_logs")
        fn_get_logs.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "logs:GetLogEvents",
                    "logs:FilterLogEvents",
                    "logs:DescribeLogStreams",
                ],
                resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/auto-research/*:*"],
            )
        )

        # --- viewer Lambdas (read-only) ---
        fn_wiki_viewer = make_fn("wiki_viewer")
        fn_code_viewer = make_fn("code_viewer")
        for fn in (fn_wiki_viewer, fn_code_viewer):
            fn.add_to_role_policy(
                iam.PolicyStatement(
                    actions=[
                        "codecommit:GetFolder",
                        "codecommit:GetFile",
                        "codecommit:GetBlob",
                        "codecommit:GetBranch",
                        "codecommit:GetCommit",
                        "codecommit:GetDifferences",
                        "codecommit:ListBranches",
                        "codecommit:GetRepository",
                    ],
                    resources=[f"arn:aws:codecommit:{self.region}:{self.account}:*"],
                )
            )

        fn_data_viewer = make_fn("data_viewer")
        data_bucket.grant_read(fn_data_viewer)

        # --- HTTP API Gateway ---
        # Restrict CORS to the web app's CloudFront origin(s). Pass at deploy:
        #   cdk deploy ... -c allowed_origins=https://<dist>.cloudfront.net
        # (deploy.sh wires this from the WebAppStack DistributionUrl output). Defaults to "*" only
        # when unset, so a fresh/first deploy still works before the distribution domain is known.
        allowed_origins_ctx = self.node.try_get_context("allowed_origins")
        allowed_origins = (
            [o.strip() for o in allowed_origins_ctx.split(",") if o.strip()]
            if isinstance(allowed_origins_ctx, str) and allowed_origins_ctx.strip()
            else ["*"]
        )
        http_api = apigwv2.HttpApi(
            self,
            "HttpApi",
            api_name="AutoResearch-API",
            cors_preflight=apigwv2.CorsPreflightOptions(
                allow_origins=allowed_origins,
                allow_methods=[
                    apigwv2.CorsHttpMethod.GET,
                    apigwv2.CorsHttpMethod.POST,
                    apigwv2.CorsHttpMethod.PUT,
                    apigwv2.CorsHttpMethod.DELETE,
                    apigwv2.CorsHttpMethod.OPTIONS,
                ],
                allow_headers=["Authorization", "Content-Type"],
            ),
        )

        jwt_authorizer = authorizers.HttpJwtAuthorizer(
            "CognitoAuth",
            jwt_issuer=f"https://cognito-idp.{self.region}.amazonaws.com/{user_pool.user_pool_id}",
            jwt_audience=[user_pool_client.user_pool_client_id],
        )

        GET = apigwv2.HttpMethod.GET
        POST = apigwv2.HttpMethod.POST
        DELETE = apigwv2.HttpMethod.DELETE
        PUT = apigwv2.HttpMethod.PUT
        routes = [
            ("CreateProject", POST, "/projects", fn_create_project),
            ("DeleteProject", DELETE, "/projects/{projectId}", fn_delete_project),
            ("ListProjects", GET, "/projects", fn_get_experiments),
            ("GetExperiments", GET, "/projects/{projectId}/experiments", fn_get_experiments),
            ("GetWorkers", GET, "/projects/{projectId}/workers", fn_get_workers),
            ("StartSession", POST, "/projects/{projectId}/start", fn_start_session),
            ("StopSession", POST, "/projects/{projectId}/stop", fn_stop_session),
            ("SubmitGuidance", POST, "/projects/{projectId}/guidance", fn_submit_guidance),
            ("TeamRequestJoin", POST, "/projects/{projectId}/team/requests", fn_team),
            ("TeamMyRequest", GET, "/projects/{projectId}/team/requests/mine", fn_team),
            ("TeamListRequests", GET, "/projects/{projectId}/team/requests", fn_team),
            ("TeamAdmitMember", POST, "/projects/{projectId}/team/members", fn_team),
            ("TeamRemoveMember", DELETE, "/projects/{projectId}/team/members/{sub}", fn_team),
            ("GetMe", GET, "/me", fn_users),
            ("ListUsers", GET, "/users", fn_users),
            ("SetUserBudget", PUT, "/users/{sub}/budget", fn_users),
            ("GetLogs", GET, "/projects/{projectId}/logs", fn_get_logs),
            ("WikiTree", GET, "/projects/{projectId}/wiki/tree", fn_wiki_viewer),
            ("WikiFile", GET, "/projects/{projectId}/wiki/file", fn_wiki_viewer),
            ("CodeTree", GET, "/projects/{projectId}/code/tree", fn_code_viewer),
            ("CodeFile", GET, "/projects/{projectId}/code/file", fn_code_viewer),
            ("CodeCommits", GET, "/projects/{projectId}/code/commits", fn_code_viewer),
            ("CodeDiff", GET, "/projects/{projectId}/code/diff", fn_code_viewer),
            ("DataTree", GET, "/projects/{projectId}/data/tree", fn_data_viewer),
            ("DataUrl", GET, "/projects/{projectId}/data/url", fn_data_viewer),
            ("DataPreview", GET, "/projects/{projectId}/data/preview", fn_data_viewer),
        ]

        for name, method, path, fn in routes:
            http_api.add_routes(
                path=path,
                methods=[method],
                integration=integrations.HttpLambdaIntegration(name, fn),
                authorizer=jwt_authorizer,
            )

        CfnOutput(self, "ApiUrl", value=http_api.url or "")
