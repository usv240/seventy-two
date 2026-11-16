"""One-command deploy from a laptop or CloudShell. No pipeline, no SAM CLI.

    python scripts/deploy.py              build, upload, deploy the stack, upload sources and site
    python scripts/deploy.py --site-only  only re-upload web/ and invalidate the CDN
    python scripts/deploy.py --ingest     also start one ingest run after deploying

Settings come from the environment, so a second copy never collides with the first:
    STACK=measles-ny REGION=us-west-2 ALERT_EMAIL=you@example.org python scripts/deploy.py
    STACK         CloudFormation stack name, and the prefix of every named resource (default seventytwo)
    REGION        AWS Region (default us-east-1; Bedrock access to Claude Haiku 4.5 is needed there)
    ALERT_EMAIL   optional: subscribed to the stack's alarms through SNS (confirm the email AWS sends)
    DOMAIN        the disease this copy watches, a file in src/domains/ (default measles; also hepatitis_a)
    SOURCES       the pages it reads (default sources/sources.json; sources/hepatitis_a.json for hepatitis A)
    FROM_EMAIL    optional: a verified SES sender, which turns on email reminders

Needs: Python 3.12 with boto3, and AWS credentials for the target account.
"""
import json
import mimetypes
import os
import subprocess
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
STACK = os.environ.get("STACK", "seventytwo")
REGION = os.environ.get("REGION", "us-east-1")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "")    # a verified SES address turns on email reminders
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "")  # gets the stack's alarms by email
DOMAIN = os.environ.setdefault("DOMAIN", "measles")  # also read by render.py, which writes it into the pages
SOURCES = os.environ.get("SOURCES", "sources/sources.json")

session = boto3.Session(region_name=REGION)
s3, cfn, cf, sfn = (session.client(n) for n in ("s3", "cloudformation", "cloudfront", "stepfunctions"))
ACCOUNT = session.client("sts").get_caller_identity()["Account"]
ARTIFACTS = f"seventytwo-artifacts-{ACCOUNT}" if (STACK, REGION) == ("seventytwo", "us-east-1") else f"{STACK}-artifacts-{ACCOUNT}-{REGION}"


def ensure_bucket():
    try:
        s3.head_bucket(Bucket=ARTIFACTS)
    except ClientError:
        s3.create_bucket(Bucket=ARTIFACTS, **({} if REGION == "us-east-1" else {"CreateBucketConfiguration": {"LocationConstraint": REGION}}))
        s3.put_public_access_block(Bucket=ARTIFACTS, PublicAccessBlockConfiguration={
            "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})


def build(skip_layer):
    py = ROOT / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / "python"
    args = [str(py if py.exists() else sys.executable), str(ROOT / "scripts" / "build.py")]
    if skip_layer:
        args.append("--skip-layer")
    out = subprocess.check_output(args, text=True).strip().splitlines()
    return out[-2], out[-1]


def upload(local, key):
    try:
        s3.head_object(Bucket=ARTIFACTS, Key=key)
        return  # content-addressed name: already there
    except ClientError:
        s3.upload_file(str(local), ARTIFACTS, key)


def ensure_push_key():
    """The Web Push signing key: made once, kept as an SSM SecureString, never in code or in the template."""
    ssm = session.client("ssm")
    name = f"/{STACK}/vapid-private-key"
    try:
        ssm.get_parameter(Name=name)
        return
    except ClientError as e:
        if e.response["Error"]["Code"] != "ParameterNotFound":
            raise
    import base64
    from cryptography.hazmat.primitives.asymmetric import ec
    raw = ec.generate_private_key(ec.SECP256R1()).private_numbers().private_value.to_bytes(32, "big")
    ssm.put_parameter(Name=name, Type="SecureString", Value=base64.urlsafe_b64encode(raw).rstrip(b"=").decode(),
                      Description="Seventy-Two Web Push (VAPID) private key")
    print("created", name)


def origin_secret():
    """The header CloudFront adds to every API request, so a call that skips CloudFront is refused. A random
    value made once and kept as an SSM SecureString (never in code); the template receives it as a NoEcho
    parameter. To rotate it: delete the parameter, deploy with ORIGIN_SECRET_PREVIOUS set to the old value
    (the API accepts both while CloudFront updates), then deploy again without it."""
    import secrets
    ssm = session.client("ssm")
    name = f"/{STACK}/origin-secret"
    try:
        return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
    except ClientError as e:
        if e.response["Error"]["Code"] != "ParameterNotFound":
            raise
    value = secrets.token_urlsafe(32)
    ssm.put_parameter(Name=name, Type="SecureString", Value=value, Description="Seventy-Two: CloudFront to API origin header")
    print("created", name)
    return value


def deploy_stack(code_key, layer_key):
    s3.upload_file(str(ROOT / "template.yaml"), ARTIFACTS, "template/template.yaml")
    url = f"https://{ARTIFACTS}.s3.{REGION}.amazonaws.com/template/template.yaml"
    params = [{"ParameterKey": k, "ParameterValue": v} for k, v in
              {"CodeBucket": ARTIFACTS, "CodeKey": code_key, "LayerKey": layer_key, "FromEmail": FROM_EMAIL,
               "AlertEmail": ALERT_EMAIL, "Domain": DOMAIN, "OriginSecret": origin_secret(),
               "OriginSecretPrevious": os.environ.get("ORIGIN_SECRET_PREVIOUS", "")}.items()]
    try:
        cfn.describe_stacks(StackName=STACK)
        kind = "UPDATE"
    except ClientError:
        kind = "CREATE"
    name = f"deploy-{int(time.time())}"
    cfn.create_change_set(StackName=STACK, ChangeSetName=name, ChangeSetType=kind, TemplateURL=url, Parameters=params,
                          Capabilities=["CAPABILITY_IAM", "CAPABILITY_AUTO_EXPAND"])
    while True:
        cs = cfn.describe_change_set(StackName=STACK, ChangeSetName=name)
        if cs["Status"] in ("CREATE_COMPLETE", "FAILED"):
            break
        time.sleep(3)
    if cs["Status"] == "FAILED":
        if "didn't contain changes" in cs.get("StatusReason", "") or "No updates" in cs.get("StatusReason", ""):
            print("stack unchanged")
            return
        sys.exit(f"change set failed: {cs.get('StatusReason')}")
    print(f"{kind}: {len(cs['Changes'])} resource changes")
    cfn.execute_change_set(StackName=STACK, ChangeSetName=name)
    waiter = cfn.get_waiter("stack_create_complete" if kind == "CREATE" else "stack_update_complete")
    try:
        waiter.wait(StackName=STACK, WaiterConfig={"Delay": 10, "MaxAttempts": 180})
    except Exception:
        events = cfn.describe_stack_events(StackName=STACK)["StackEvents"]
        for e in events[:25]:
            if "FAILED" in e["ResourceStatus"]:
                print(e["LogicalResourceId"], e["ResourceStatus"], e.get("ResourceStatusReason", "")[:300])
        sys.exit("deploy failed")


def outputs():
    o = cfn.describe_stacks(StackName=STACK)["Stacks"][0].get("Outputs", [])
    return {x["OutputKey"]: x["OutputValue"] for x in o}


def upload_site(out):
    sys.path.insert(0, str(ROOT / "scripts"))
    import render
    bucket, web = out["SiteBucketName"], render.render(out.get("SiteUrl", ""))
    for p in web.rglob("*"):
        if p.is_file():
            key = p.relative_to(web).as_posix()
            ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            if p.suffix in (".js", ".mjs"):
                ctype = "text/javascript"
            if p.suffix == ".webmanifest":
                ctype = "application/manifest+json"
            # pages and the service worker are always checked for a newer copy
            cache = "no-cache" if p.suffix == ".html" or p.name in ("config.json", "sw.js") else "public, max-age=300"
            s3.upload_file(str(p), bucket, key, ExtraArgs={"ContentType": ctype, "CacheControl": cache})
    cf.create_invalidation(DistributionId=out["DistributionId"], InvalidationBatch={
        "Paths": {"Quantity": 1, "Items": ["/*"]}, "CallerReference": str(time.time())})


def upload_sources(out):
    s3.upload_file(str(ROOT / SOURCES), out["ArchiveBucketName"], "config/sources.json",
                   ExtraArgs={"ContentType": "application/json"})


def main():
    site_only = "--site-only" in sys.argv
    if not site_only:
        ensure_bucket()
        code, layer = build(skip_layer="--skip-layer" in sys.argv)
        upload(ROOT / "dist" / code, f"code/{code}")
        upload(ROOT / "dist" / layer, f"layer/{layer}")
        ensure_push_key()
        deploy_stack(f"code/{code}", f"layer/{layer}")
    out = outputs()
    upload_sources(out)
    if (ROOT / "web").exists():
        upload_site(out)
    if "--ingest" in sys.argv:
        ex = sfn.start_execution(stateMachineArn=out["IngestStateMachineArn"], name=f"manual-{int(time.time())}")
        print("ingest started:", ex["executionArn"])
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
