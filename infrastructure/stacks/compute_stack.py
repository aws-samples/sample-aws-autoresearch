"""
Compute Stack: Shared IAM role, instance profile, VPC, security groups, worker SSH keypair.

Both project instances (the AI Researcher + the GPU worker) share this infrastructure.
The IAM role grants: Bedrock, S3 (the one project bucket), CodeCommit, CloudWatch Logs, SSM
(Session Manager + Parameter Store), Secrets Manager, EC2 describe/start/stop (tag-scoped), and
lambda:InvokeFunction on the AutoResearch-agent_api Lambda — its ONLY path to DynamoDB, which the
role does not access directly.

Security groups:
- researcher SG: egress-only (reached only via AWS APIs it calls).
- worker SG: egress-only + inbound SSH (22) ONLY from the researcher SG, so the
  run-experiment sub-agent can drive the GPU worker over VPC-private SSH.
"""

from aws_cdk import (
    Stack,
    CfnOutput,
    aws_iam as iam,
    aws_ec2 as ec2,
)
from constructs import Construct


class ComputeStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.vpc = ec2.Vpc.from_lookup(self, "DefaultVpc", is_default=True)

        # AI Researcher (CPU) instance — egress only.
        self.researcher_sg = ec2.SecurityGroup(
            self,
            "ResearcherSG",
            vpc=self.vpc,
            description="Auto-research AI Researcher - egress only",
            allow_all_outbound=True,
        )

        # GPU worker — egress only + inbound SSH from the researcher SG only.
        self.worker_sg = ec2.SecurityGroup(
            self,
            "WorkerSG",
            vpc=self.vpc,
            description="Auto-research GPU worker - egress + SSH from researcher",
            allow_all_outbound=True,
        )
        self.worker_sg.add_ingress_rule(
            peer=ec2.Peer.security_group_id(self.researcher_sg.security_group_id),
            connection=ec2.Port.tcp(22),
            description="SSH from the AI Researcher (run-experiment sub-agent)",
        )

        # Back-compat alias (older references expect `security_group`).
        self.security_group = self.researcher_sg

        # Worker SSH keypair: CDK creates it; the private key lands in SSM Parameter
        # Store (SecureString) at /ec2/keypair/<key-id>. The researcher reads it at boot.
        self.worker_key_pair = ec2.KeyPair(
            self,
            "WorkerKeyPair",
            key_pair_name="auto-research-worker-key",
            type=ec2.KeyPairType.ED25519,
        )
        self.worker_key_name = self.worker_key_pair.key_pair_name

        self.role = iam.Role(
            self,
            "InstanceRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            description="Shared IAM role for all auto-research EC2 instances",
        )

        # Bedrock (foundation models + cross-region inference profiles)
        self.role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=[
                    "arn:aws:bedrock:*::foundation-model/anthropic.*",
                    # Cross-region inference profiles are geo-scoped (us./eu./apac.) plus global.;
                    # grant all so it works in any deploy region.
                    f"arn:aws:bedrock:*:{self.account}:inference-profile/us.anthropic.*",
                    f"arn:aws:bedrock:*:{self.account}:inference-profile/eu.anthropic.*",
                    f"arn:aws:bedrock:*:{self.account}:inference-profile/apac.anthropic.*",
                    f"arn:aws:bedrock:*:{self.account}:inference-profile/global.anthropic.*",
                ],
            )
        )

        # S3 — scoped to auto-research bucket
        self.role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:DeleteObject"],
                resources=[
                    f"arn:aws:s3:::auto-research-{self.account}-{self.region}",
                    f"arn:aws:s3:::auto-research-{self.account}-{self.region}/*",
                ],
            )
        )

        # CodeCommit — all repos (instances need clone/push/pull)
        self.role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "codecommit:GitPull",
                    "codecommit:GitPush",
                    "codecommit:GetRepository",
                    "codecommit:GetBranch",
                    "codecommit:CreateBranch",
                    "codecommit:MergeBranchesByFastForward",
                    "codecommit:MergeBranchesBySquash",
                ],
                resources=[f"arn:aws:codecommit:{self.region}:{self.account}:*"],
            )
        )

        # CloudWatch Logs
        self.role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogStreams",
                ],
                resources=[
                    f"arn:aws:logs:{self.region}:{self.account}:log-group:/auto-research/*"
                ],
            )
        )

        # SSM Session Manager
        self.role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "AmazonSSMManagedInstanceCore"
            )
        )

        # EC2 describe + start/stop (orchestrator starts workers, workers self-stop)
        self.role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "ec2:DescribeInstances",
                    "ec2:StartInstances",
                    "ec2:StopInstances",
                ],
                resources=["*"],
                conditions={
                    "StringEquals": {
                        "aws:ResourceTag/ManagedBy": "auto-research"
                    }
                },
            )
        )
        self.role.add_to_policy(
            iam.PolicyStatement(
                # DescribeTags: boot/researcher scripts read ProjectId/Role/Model from instance tags.
                # DescribeInstances: sub-agent resolves the worker's private IP. Neither supports
                # resource-level scoping, so they are unconditional describes.
                actions=["ec2:DescribeInstances", "ec2:DescribeTags"],
                resources=["*"],
            )
        )

        # The AI Researcher does NOT touch DynamoDB directly. All project/experiment/guidance
        # state goes through the agent control-plane API (the AutoResearch-agent_api Lambda), which
        # owns the DynamoDB access; the instance role may only invoke that one function. A literal
        # ARN (the function name is fixed) avoids a circular dependency with the API stack.
        self.role.add_to_policy(
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"],
                resources=[
                    f"arn:aws:lambda:{self.region}:{self.account}:function:AutoResearch-agent_api"
                ],
            )
        )

        # SSM Parameter Store — read the API URL and the worker SSH private key
        # (CDK stores the keypair material at /ec2/keypair/<key-id>).
        self.role.add_to_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter", "ssm:GetParameters"],
                resources=[
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter/auto-research/*",
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter/ec2/keypair/*",
                ],
            )
        )

        # Secrets Manager (optional alt transport for the SSH key).
        self.role.add_to_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[
                    f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:auto-research/*"
                ],
            )
        )

        # Instance profile. IAM instance profiles are account-global, so the name is region-scoped
        # to let Auto-Research be deployed in multiple regions of one account without collisions.
        profile_name = f"auto-research-instance-profile-{self.region}"
        self.instance_profile = iam.CfnInstanceProfile(
            self,
            "InstanceProfile",
            roles=[self.role.role_name],
            instance_profile_name=profile_name,
        )
        self.instance_profile_name = profile_name

        CfnOutput(self, "InstanceProfileArn", value=self.instance_profile.attr_arn)
        CfnOutput(self, "InstanceProfileName", value=self.instance_profile_name)
        CfnOutput(self, "ResearcherSecurityGroupId", value=self.researcher_sg.security_group_id)
        CfnOutput(self, "WorkerSecurityGroupId", value=self.worker_sg.security_group_id)
        CfnOutput(self, "WorkerKeyName", value=self.worker_key_name)
        CfnOutput(self, "WorkerKeyPairId", value=self.worker_key_pair.key_pair_id)
        CfnOutput(self, "RoleArn", value=self.role.role_arn)
