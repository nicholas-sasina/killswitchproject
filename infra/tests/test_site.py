"""The website: a private bucket, the CloudFront distribution in front of it, and the upload of a build."""
import sys
from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from burn_infra.site import BurnSiteStack  # noqa: E402
from burn_contracts import names  # noqa: E402  (path added by burn_infra)

ENV = cdk.Environment(account="123456789012", region="us-east-1")
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
IMMUTABLE = "public, max-age=31536000, immutable"


def site_template(site_dir: Path) -> Template:
    return Template.from_stack(BurnSiteStack(cdk.App(), "BurnSite", site_dir=site_dir, env=ENV))


@pytest.fixture(scope="module")
def unbuilt(tmp_path_factory):
    # A checkout where the site was never built: there is no dist folder at all.
    return site_template(tmp_path_factory.mktemp("site") / "dist")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    dist = tmp_path_factory.mktemp("dist")
    (dist / "index.html").write_text('<!doctype html><script type="module" src="/assets/index-3f9a1c.js"></script>')
    (dist / "assets").mkdir()
    (dist / "assets" / "index-3f9a1c.js").write_text("console.log('burn')")
    return site_template(dist)


def distribution(template: Template) -> dict:
    found = template.find_resources("AWS::CloudFront::Distribution")
    assert len(found) == 1
    return next(iter(found.values()))["Properties"]["DistributionConfig"]


def deployments(template: Template) -> dict[str, dict]:
    return template.find_resources("Custom::CDKBucketDeployment")


# --- the bucket ---------------------------------------------------------------------------------

def test_the_bucket_blocks_all_public_access_and_is_encrypted(unbuilt):
    unbuilt.resource_count_is("AWS::S3::Bucket", 1)
    unbuilt.has_resource_properties("AWS::S3::Bucket", {
        "PublicAccessBlockConfiguration": {"BlockPublicAcls": True, "BlockPublicPolicy": True,
                                           "IgnorePublicAcls": True, "RestrictPublicBuckets": True},
        "BucketEncryption": {"ServerSideEncryptionConfiguration": [
            {"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]},
    })


def test_the_bucket_refuses_plain_http(unbuilt):
    unbuilt.has_resource_properties("AWS::S3::BucketPolicy", {"PolicyDocument": {"Statement": Match.array_with([
        Match.object_like({"Effect": "Deny", "Action": "s3:*",
                           "Condition": {"Bool": {"aws:SecureTransport": "false"}}})])}})


def test_teardown_deletes_the_bucket_and_its_files(unbuilt):
    unbuilt.has_resource("AWS::S3::Bucket", {"DeletionPolicy": "Delete", "UpdateReplacePolicy": "Delete"})
    unbuilt.resource_count_is("Custom::S3AutoDeleteObjects", 1)


# --- the distribution ---------------------------------------------------------------------------

def test_only_the_distribution_reads_the_bucket_through_origin_access_control(unbuilt):
    unbuilt.resource_count_is("AWS::CloudFront::OriginAccessControl", 1)
    unbuilt.has_resource_properties("AWS::CloudFront::OriginAccessControl", {"OriginAccessControlConfig": {
        "OriginAccessControlOriginType": "s3", "SigningBehavior": "always", "SigningProtocol": "sigv4",
        "Name": Match.any_value()}})
    unbuilt.resource_count_is("AWS::CloudFront::CloudFrontOriginAccessIdentity", 0)  # no legacy OAI
    [origin] = distribution(unbuilt)["Origins"]
    assert "Fn::GetAtt" in origin["OriginAccessControlId"]
    assert origin["S3OriginConfig"] == {"OriginAccessIdentity": ""}
    unbuilt.has_resource_properties("AWS::S3::BucketPolicy", {"PolicyDocument": {"Statement": Match.array_with([
        Match.object_like({"Effect": "Allow", "Action": "s3:GetObject",
                           "Principal": {"Service": "cloudfront.amazonaws.com"},
                           "Condition": {"StringEquals": {"AWS:SourceArn": Match.any_value()}}})])}})


def test_the_distribution_redirects_to_https_and_serves_index_html_at_the_root(unbuilt):
    config = distribution(unbuilt)
    assert config["DefaultRootObject"] == "index.html"
    assert config["DefaultCacheBehavior"]["ViewerProtocolPolicy"] == "redirect-to-https"
    assert config["DefaultCacheBehavior"]["Compress"] is True
    assert config["PriceClass"] == "PriceClass_100"
    assert config["HttpVersion"] == "http2and3"
    assert "ViewerCertificate" not in config  # CloudFront's own certificate, no custom domain
    assert "Aliases" not in config


def test_paths_with_no_file_get_the_app(unbuilt):
    responses = {r["ErrorCode"]: r for r in distribution(unbuilt)["CustomErrorResponses"]}
    assert set(responses) == {403, 404}
    for response in responses.values():
        assert response["ResponseCode"] == 200
        assert response["ResponsePagePath"] == "/index.html"


def test_every_response_carries_the_security_headers(unbuilt):
    unbuilt.resource_count_is("AWS::CloudFront::ResponseHeadersPolicy", 1)
    policy = next(iter(unbuilt.find_resources("AWS::CloudFront::ResponseHeadersPolicy").items()))
    headers = policy[1]["Properties"]["ResponseHeadersPolicyConfig"]["SecurityHeadersConfig"]
    assert headers["StrictTransportSecurity"] == {"AccessControlMaxAgeSec": 31536000, "IncludeSubdomains": True,
                                                  "Override": True}
    assert headers["ContentSecurityPolicy"] == {"ContentSecurityPolicy": CSP, "Override": True}
    assert headers["ContentTypeOptions"] == {"Override": True}
    assert headers["FrameOptions"] == {"FrameOption": "DENY", "Override": True}
    assert headers["ReferrerPolicy"] == {"ReferrerPolicy": "strict-origin-when-cross-origin", "Override": True}
    assert distribution(unbuilt)["DefaultCacheBehavior"]["ResponseHeadersPolicyId"] == {"Ref": policy[0]}


# --- the address --------------------------------------------------------------------------------

def test_the_address_is_published_to_ssm_and_as_an_output(unbuilt):
    dist_id = next(iter(unbuilt.find_resources("AWS::CloudFront::Distribution")))
    url = {"Fn::Join": ["", ["https://", {"Fn::GetAtt": [dist_id, "DomainName"]}]]}
    unbuilt.has_resource_properties("AWS::SSM::Parameter", {"Name": names.SITE_URL_PARAM, "Value": url})
    unbuilt.has_output("Url", {"Value": url})


# --- uploading a build --------------------------------------------------------------------------

def test_without_a_build_nothing_is_uploaded_but_the_site_still_exists(unbuilt):
    assert deployments(unbuilt) == {}
    unbuilt.resource_count_is("AWS::S3::Bucket", 1)
    unbuilt.resource_count_is("AWS::CloudFront::Distribution", 1)


def test_a_build_is_uploaded_in_two_parts_with_their_own_caching(built):
    found = deployments(built)
    assert len(found) == 2
    by_part = {"assets" if r["Properties"].get("Include") == ["assets/*"] else "pages": (cid, r)
               for cid, r in found.items()}
    hashed_id, hashed = by_part["assets"]
    _, pages = by_part["pages"]

    assert hashed["Properties"]["Exclude"] == ["*"]
    assert hashed["Properties"]["SystemMetadata"] == {"cache-control": IMMUTABLE}
    assert hashed["Properties"]["Prune"] is False  # a page loaded before a deploy can still fetch its files

    assert pages["Properties"]["Exclude"] == ["assets/*"]
    assert "Include" not in pages["Properties"]
    assert pages["Properties"]["SystemMetadata"] == {"cache-control": "no-cache"}
    assert pages["Properties"]["DistributionPaths"] == ["/*"]
    assert pages["Properties"]["DistributionId"] == {"Ref": next(iter(
        built.find_resources("AWS::CloudFront::Distribution")))}
    assert hashed_id in pages["DependsOn"]  # index.html goes up after the files it names

    for _, resource in by_part.values():
        assert resource["Properties"]["RetainOnDelete"] is True  # a deploy without a build keeps the site
        assert resource["Properties"]["OutputObjectKeys"] is False


def test_the_upload_logs_go_with_the_stack(built):
    built.has_resource("AWS::Logs::LogGroup", {"DeletionPolicy": "Delete"})
