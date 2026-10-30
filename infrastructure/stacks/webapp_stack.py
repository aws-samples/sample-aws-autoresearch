"""
WebApp Stack: S3 + CloudFront static hosting for the React dashboard.
"""

from aws_cdk import (
    Stack,
    RemovalPolicy,
    CfnOutput,
    aws_s3 as s3,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
)
from constructs import Construct


class WebAppStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        ui_bucket = s3.Bucket(
            self,
            "UIBucket",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # CloudFront (and its Origin Access Control) is a global service, so the OAC name is
        # account-global. Scope it by region so Auto-Research can run in multiple regions of one
        # account without a name collision.
        oac = cloudfront.S3OriginAccessControl(
            self,
            "UIOAC",
            origin_access_control_name=f"auto-research-ui-oac-{self.region}",
        )

        distribution = cloudfront.Distribution(
            self,
            "Distribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(
                    ui_bucket, origin_access_control=oac
                ),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
            ),
            default_root_object="index.html",
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html",
                ),
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path="/index.html",
                ),
            ],
            minimum_protocol_version=cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
        )

        CfnOutput(self, "UIBucketName", value=ui_bucket.bucket_name)
        CfnOutput(self, "DistributionId", value=distribution.distribution_id)
        CfnOutput(self, "DistributionUrl", value=f"https://{distribution.domain_name}")
