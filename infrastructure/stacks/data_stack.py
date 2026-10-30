"""
Data Stack: DynamoDB tables + S3 bucket.

- AutoResearch-Projects: project registry + live session state
- AutoResearch-Experiments: single-writer experiment ledger (written by the AI Researcher)
- AutoResearch-Guidance: human -> agent inbox (pulled by the always-on AI Researcher)
- S3 bucket: all project data, results, report charts
"""

from aws_cdk import (
    Stack,
    RemovalPolicy,
    CfnOutput,
    aws_dynamodb as dynamodb,
    aws_s3 as s3,
)
from constructs import Construct


class DataStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.projects_table = dynamodb.Table(
            self,
            "ProjectsTable",
            table_name="AutoResearch-Projects",
            partition_key=dynamodb.Attribute(
                name="project_id", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            point_in_time_recovery=True,   # cheap recovery posture (security-review #10)
        )

        self.experiments_table = dynamodb.Table(
            self,
            "ExperimentsTable",
            table_name="AutoResearch-Experiments",
            partition_key=dynamodb.Attribute(
                name="project_id", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="experiment_id", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            point_in_time_recovery=True,
        )

        # Human -> agent inbox. The always-on AI Researcher drains unprocessed
        # items at the top of each loop iteration (no orchestrator wake model).
        self.guidance_table = dynamodb.Table(
            self,
            "GuidanceTable",
            table_name="AutoResearch-Guidance",
            partition_key=dynamodb.Attribute(
                name="project_id", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="guidance_id", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            point_in_time_recovery=True,
        )

        # Pending "join the team" requests: a user asks to become an admin of a project; an
        # owner/admin admits them (see the `team` Lambda). PK project_id, SK user_sub.
        self.team_requests_table = dynamodb.Table(
            self,
            "TeamRequestsTable",
            table_name="AutoResearch-TeamRequests",
            partition_key=dynamodb.Attribute(
                name="project_id", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="user_sub", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            point_in_time_recovery=True,
        )

        # Per-user budget: one item per Cognito user. `budget` is the spendable USD balance (starts 0);
        # creating a project reserves its max budget, deleting refunds the unspent part.
        self.users_table = dynamodb.Table(
            self,
            "UsersTable",
            table_name="AutoResearch-Users",
            partition_key=dynamodb.Attribute(
                name="user_sub", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            point_in_time_recovery=True,
        )

        # Explicit name so it matches the instance-role S3 policy in ComputeStack
        # (arn:aws:s3:::auto-research-{account}-{region}). Without this, CDK auto-names
        # the bucket and the instance role cannot read/write it.
        self.data_bucket = s3.Bucket(
            self,
            "DataBucket",
            bucket_name=f"auto-research-{self.account}-{self.region}",
            removal_policy=RemovalPolicy.RETAIN,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
        )

        CfnOutput(self, "ProjectsTableName", value=self.projects_table.table_name)
        CfnOutput(self, "ExperimentsTableName", value=self.experiments_table.table_name)
        CfnOutput(self, "GuidanceTableName", value=self.guidance_table.table_name)
        CfnOutput(self, "TeamRequestsTableName", value=self.team_requests_table.table_name)
        CfnOutput(self, "UsersTableName", value=self.users_table.table_name)
        CfnOutput(self, "DataBucketName", value=self.data_bucket.bucket_name)
