#!/usr/bin/env python3
import os
import aws_cdk as cdk
from stacks.data_stack import DataStack
from stacks.auth_stack import AuthStack
from stacks.compute_stack import ComputeStack
from stacks.api_stack import ApiStack
from stacks.webapp_stack import WebAppStack

app = cdk.App()

# Account + region come from the environment (CDK_DEFAULT_ACCOUNT / CDK_DEFAULT_REGION, which the
# AWS CLI/CDK populate from the active credentials and $AWS_REGION). Region defaults to us-east-1 —
# where GPU capacity and Bedrock inference profiles are most available — but any region works.
env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
)

data_stack = DataStack(app, "AutoResearchDataStack", env=env)
auth_stack = AuthStack(app, "AutoResearchAuthStack", env=env)
compute_stack = ComputeStack(app, "AutoResearchComputeStack", env=env)
api_stack = ApiStack(
    app,
    "AutoResearchApiStack",
    user_pool=auth_stack.user_pool,
    user_pool_client=auth_stack.user_pool_client,
    projects_table=data_stack.projects_table,
    experiments_table=data_stack.experiments_table,
    guidance_table=data_stack.guidance_table,
    team_requests_table=data_stack.team_requests_table,
    users_table=data_stack.users_table,
    data_bucket=data_stack.data_bucket,
    instance_profile_name=compute_stack.instance_profile_name,
    instance_role_arn=compute_stack.role.role_arn,
    researcher_security_group_id=compute_stack.researcher_sg.security_group_id,
    worker_security_group_id=compute_stack.worker_sg.security_group_id,
    worker_key_name=compute_stack.worker_key_name,
    vpc=compute_stack.vpc,
    env=env,
)
webapp_stack = WebAppStack(app, "AutoResearchWebAppStack", env=env)

app.synth()
