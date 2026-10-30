"""
Auth Stack: Cognito UserPool + IdentityPool for dashboard authentication.
"""

from aws_cdk import (
    Stack,
    CfnOutput,
    aws_cognito as cognito,
    aws_iam as iam,
)
from constructs import Construct


class AuthStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.user_pool = cognito.UserPool(
            self,
            "UserPool",
            password_policy=cognito.PasswordPolicy(
                min_length=8,
                require_digits=True,
                require_lowercase=True,
                require_uppercase=True,
                require_symbols=True,
            ),
        )

        self.user_pool_client = cognito.UserPoolClient(
            self,
            "UserPoolClient",
            user_pool=self.user_pool,
            generate_secret=False,
            auth_flows=cognito.AuthFlow(user_password=True, user_srp=True),
        )

        self.identity_pool = cognito.CfnIdentityPool(
            self,
            "IdentityPool",
            allow_unauthenticated_identities=False,
            cognito_identity_providers=[
                cognito.CfnIdentityPool.CognitoIdentityProviderProperty(
                    client_id=self.user_pool_client.user_pool_client_id,
                    provider_name=self.user_pool.user_pool_provider_name,
                )
            ],
        )

        authenticated_role = iam.Role(
            self,
            "AuthRole",
            assumed_by=iam.FederatedPrincipal(
                "cognito-identity.amazonaws.com",
                conditions={
                    "StringEquals": {
                        "cognito-identity.amazonaws.com:aud": self.identity_pool.ref
                    },
                    "ForAnyValue:StringLike": {
                        "cognito-identity.amazonaws.com:amr": "authenticated"
                    },
                },
                assume_role_action="sts:AssumeRoleWithWebIdentity",
            ),
        )

        cognito.CfnIdentityPoolRoleAttachment(
            self,
            "IdentityPoolRoles",
            identity_pool_id=self.identity_pool.ref,
            roles={"authenticated": authenticated_role.role_arn},
        )

        CfnOutput(self, "CognitoUserPoolId", value=self.user_pool.user_pool_id)
        CfnOutput(self, "CognitoClientId", value=self.user_pool_client.user_pool_client_id)
        CfnOutput(self, "CognitoIdentityPoolId", value=self.identity_pool.ref)
