"""BurnSite stack: Burn's public website, served from a private S3 bucket through CloudFront.

StaticSite is the reusable part. It takes a name and a built folder (index.html plus hashed files
under assets/, as Vite writes them) and knows nothing else about the website, so the dashboard can
use it too.

- The bucket blocks all public access, refuses plain HTTP and is encrypted with S3-managed keys.
  Only the distribution reads it, through origin access control.
- CloudFront serves HTTPS on its own *.cloudfront.net address (no custom domain) and redirects HTTP.
  Paths with no file behind them get index.html, so the site's own routes load on a refresh.
- Every response carries HSTS, nosniff, frame-options DENY, a strict referrer policy and a CSP that
  allows only the site's own files.
- index.html is sent with no-cache, so browsers check for a new version on every visit. Files under
  assets/ have a content hash in their names, so browsers keep them for a year.
- The folder is uploaded only when it has been built (it has an index.html). Without a build, synth
  and deploy still work, create the bucket and distribution, and leave the files already uploaded.
- `make teardown` deletes the bucket, its files and the distribution.

The address is published to SSM (names.SITE_URL_PARAM) and as the stack output Url.
"""
import sys
from pathlib import Path

import aws_cdk as cdk
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3deploy
from aws_cdk import aws_ssm as ssm
from constructs import Construct

from .assets import REPO

# Fixed resource names live in the contracts package; infra has no dependency on it, so add its path.
sys.path.insert(0, str(REPO / "contracts" / "python"))
from burn_contracts import names  # noqa: E402

SITE_DIST = REPO / "site" / "dist"  # what `npm run build` writes in site/

# A self-contained Vite React build: its own scripts, styles, images and fonts, nothing from another
# origin. Vite inlines files under 4 KB as data: URIs, so small images and fonts arrive that way.
# Links to the dashboard are ordinary navigation, which a CSP does not restrict.
SITE_CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
])

HASHED = "assets/*"  # Vite's output with a content hash in every file name
NO_CACHE = s3deploy.CacheControl.no_cache()
IMMUTABLE = s3deploy.CacheControl.from_string("public, max-age=31536000, immutable")


class StaticSite(Construct):
    """A private S3 bucket behind a CloudFront distribution, filled from a built folder.

    name labels the distribution and its headers policy in the console. source_dir is the built
    folder; it is uploaded only if it has an index.html.
    """

    def __init__(self, scope: Construct, construct_id: str, *, name: str, source_dir: Path,
                 content_security_policy: str = SITE_CSP) -> None:
        super().__init__(scope, construct_id)

        self.bucket = s3.Bucket(
            self, "Bucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            removal_policy=cdk.RemovalPolicy.DESTROY,
            auto_delete_objects=True,  # teardown empties it first, so the bucket can go too
        )

        headers = cloudfront.ResponseHeadersPolicy(
            self, "Headers",
            comment=f"{name}: security headers",
            security_headers_behavior=cloudfront.ResponseSecurityHeadersBehavior(
                strict_transport_security=cloudfront.ResponseHeadersStrictTransportSecurity(
                    access_control_max_age=cdk.Duration.days(365), include_subdomains=True, override=True),
                content_type_options=cloudfront.ResponseHeadersContentTypeOptions(override=True),
                frame_options=cloudfront.ResponseHeadersFrameOptions(
                    frame_option=cloudfront.HeadersFrameOption.DENY, override=True),
                referrer_policy=cloudfront.ResponseHeadersReferrerPolicy(
                    referrer_policy=cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN,
                    override=True),
                content_security_policy=cloudfront.ResponseHeadersContentSecurityPolicy(
                    content_security_policy=content_security_policy, override=True),
            ),
        )

        self.distribution = cloudfront.Distribution(
            self, "Distribution",
            comment=name,
            default_behavior=cloudfront.BehaviorOptions(
                # Origin access control: CloudFront signs its requests, and the bucket policy admits
                # only this distribution.
                origin=origins.S3BucketOrigin.with_origin_access_control(self.bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                compress=True,
                response_headers_policy=headers,
            ),
            default_root_object="index.html",
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
            http_version=cloudfront.HttpVersion.HTTP2_AND_3,
            # A path with no file behind it is one of the site's own routes: answer with the app.
            # S3 says 403, not 404, for a missing key when the reader may not list the bucket.
            error_responses=[
                cloudfront.ErrorResponse(http_status=status, response_http_status=200,
                                         response_page_path="/index.html")
                for status in (403, 404)
            ],
        )

        self.url = f"https://{self.distribution.distribution_domain_name}"

        if (source_dir / "index.html").is_file():
            self._upload(source_dir)
        else:
            cdk.Annotations.of(self).add_info(
                f"{source_dir} has no index.html: nothing is uploaded, and files already uploaded stay")

    def _upload(self, source_dir: Path) -> None:
        source = [s3deploy.Source.asset(str(source_dir))]
        # The upload Lambda's log group goes with the stack. Both uploads share that Lambda.
        log_group = logs.LogGroup(self, "UploadLogs", retention=logs.RetentionDays.ONE_WEEK,
                                  removal_policy=cdk.RemovalPolicy.DESTROY)

        # Hashed files first, so a new index.html never names a file that is not there yet. Old hashed
        # files stay (no prune), so a page loaded before a deploy can still fetch what it names.
        hashed = s3deploy.BucketDeployment(
            self, "UploadHashed",
            sources=source,
            destination_bucket=self.bucket,
            exclude=["*"], include=[HASHED],
            cache_control=[IMMUTABLE],
            prune=False,
            retain_on_delete=True,  # removing this upload from the stack never deletes the live site
            output_object_keys=False,  # the uploaded keys could overflow CloudFormation's 4 KB response
            log_group=log_group,
        )
        # Everything else: index.html and any unhashed file. Browsers revalidate these on every visit,
        # and the invalidation clears CloudFront's copies.
        pages = s3deploy.BucketDeployment(
            self, "UploadPages",
            sources=source,
            destination_bucket=self.bucket,
            exclude=[HASHED],  # also keeps this upload's prune away from the hashed files
            cache_control=[NO_CACHE],
            retain_on_delete=True,
            distribution=self.distribution,
            distribution_paths=["/*"],
            output_object_keys=False,
            log_group=log_group,
        )
        pages.node.add_dependency(hashed)


class BurnSiteStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, *, site_dir: Path = SITE_DIST, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        site = StaticSite(self, "Website", name="Burn website", source_dir=site_dir)

        ssm.StringParameter(self, "UrlParam", parameter_name=names.SITE_URL_PARAM, string_value=site.url)
        cdk.CfnOutput(self, "Url", value=site.url, description="The website's address")
