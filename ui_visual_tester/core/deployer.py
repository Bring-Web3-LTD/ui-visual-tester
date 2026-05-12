import os
import time
import shutil
import zipfile
from pathlib import Path
import boto3
from config import (
    AWS_REGION, ECS_CLUSTER, ECS_TASK_DEFINITION,
    ECS_SUBNETS, ECS_SECURITY_GROUPS, ECS_CONTAINER_NAME,
    DEFAULT_BRANCH, DEFAULT_GITHUB_REPO,
    DEFAULT_FRONTEND_BRANCH, DEFAULT_FRONTEND_REPO,
    DESTROY_AFTER_HOURS,
    S3_EXTENSIONS_BUCKET,
    ECS_POLL_INTERVAL, ECS_POLL_TIMEOUT,
    EXTENSIONS_DIR,
)


# ── ECS: Deploy environment ──────────────────────────────
def deploy_environment(env_name: str, platform_cfg: dict,
                       branch: str = None, frontend_branch: str = None) -> str:

    ecs = boto3.client("ecs", region_name=AWS_REGION)

    env_vars = [
        {"name": "ENV_NAME",                      "value": env_name},
        {"name": "BRANCH",                        "value": branch or DEFAULT_BRANCH},
        {"name": "GITHUB_REPO",                   "value": DEFAULT_GITHUB_REPO},
        {"name": "FRONTEND_BRANCH",               "value": frontend_branch or DEFAULT_FRONTEND_BRANCH},
        {"name": "FRONTEND_GITHUB_REPO",          "value": DEFAULT_FRONTEND_REPO},
        {"name": "DESTROY_AFTER_HOURS",            "value": DESTROY_AFTER_HOURS},
        {"name": "FRONTEND_IDENTIFIER",            "value": platform_cfg["identifier"]},
        {"name": "SKIP_FRONTEND",                  "value": "false"},
        {"name": "EXTENSION_ZIP_UPLOAD",           "value": "true"},
        {"name": "EXTENSION_ZIP_S3_BUCKET",        "value": S3_EXTENSIONS_BUCKET},
    ]

    print(f"  Launching ECS task: env={env_name}, identifier={platform_cfg['identifier']}")

    resp = ecs.run_task(
        cluster=ECS_CLUSTER,
        taskDefinition=ECS_TASK_DEFINITION,
        launchType="FARGATE",
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": ECS_SUBNETS,
                "securityGroups": ECS_SECURITY_GROUPS,
                "assignPublicIp": "ENABLED",
            }
        },
        overrides={
            "containerOverrides": [{
                "name": ECS_CONTAINER_NAME,
                "environment": env_vars,
            }]
        },
    )

    task_arn = resp["tasks"][0]["taskArn"]
    print(f"  Task started: {task_arn}")
    return task_arn

# ── ECS: Poll until task completes ───────────────────────
def wait_for_task(task_arn: str):
    ecs = boto3.client("ecs", region_name=AWS_REGION)
    elapsed = 0

    while elapsed < ECS_POLL_TIMEOUT:
        resp = ecs.describe_tasks(cluster=ECS_CLUSTER, tasks=[task_arn])
        task = resp["tasks"][0]
        status = task["lastStatus"]
        print(f"  Task status: {status} ({elapsed}s elapsed)")

        if status == "STOPPED":
            containers = task.get("containers", [])
            for c in containers:
                exit_code = c.get("exitCode", -1)
                if exit_code != 0:
                    reason = c.get("reason", "unknown")
                    raise RuntimeError(
                        f"ECS task failed: container={c['name']}, "
                        f"exitCode={exit_code}, reason={reason}"
                    )
            print("  Task completed successfully!")
            return

        time.sleep(ECS_POLL_INTERVAL)
        elapsed += ECS_POLL_INTERVAL

    raise TimeoutError(f"ECS task did not finish within {ECS_POLL_TIMEOUT}s")

# ── S3: Download extension zip ───────────────────────────
def download_extension(env_name: str, platform_name: str) -> Path:

    s3 = boto3.client("s3", region_name=AWS_REGION)
    prefix = f"extensions/{env_name}/"

    resp = s3.list_objects_v2(Bucket=S3_EXTENSIONS_BUCKET, Prefix=prefix)
    contents = resp.get("Contents", [])
    zips = [obj["Key"] for obj in contents if obj["Key"].endswith(".zip")]

    if not zips:
        raise FileNotFoundError(
            f"No extension zip found at s3://{S3_EXTENSIONS_BUCKET}/{prefix}"
        )

    zip_key = zips[0]
    print(f"  Found: s3://{S3_EXTENSIONS_BUCKET}/{zip_key}")

    # Download
    EXTENSIONS_DIR.mkdir(exist_ok=True)
    zip_path = EXTENSIONS_DIR / f"{env_name}.zip"
    s3.download_file(S3_EXTENSIONS_BUCKET, zip_key, str(zip_path))
    print(f"  Downloaded: {zip_path.name}")

    # Extract
    extract_dir = EXTENSIONS_DIR / f"{env_name}"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir()

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)

    # Find the extension folder (contains manifest.json)
    for root, dirs, files in os.walk(extract_dir):
        if "manifest.json" in files:
            ext_path = Path(root)
            print(f"  Extension ready: {ext_path}")
            return ext_path

    raise FileNotFoundError(f"No manifest.json found in {extract_dir}")

# ── S3: Download latest extension (any env) ──────────────
def download_latest_extension(platform_name: str) -> Path:
    """Find the most recently uploaded extension zip in S3 for this platform."""
    s3 = boto3.client("s3", region_name=AWS_REGION)
    prefix = f"extensions/"

    resp = s3.list_objects_v2(Bucket=S3_EXTENSIONS_BUCKET, Prefix=prefix)
    contents = resp.get("Contents", [])

    # Filter zips matching our platform pattern
    pattern = f"ui-test-{platform_name}"
    zips = [obj for obj in contents
            if obj["Key"].endswith(".zip") and pattern in obj["Key"]]

    if not zips:
        raise FileNotFoundError(
            f"No extension found in S3 for {pattern}"
        )

    # Pick the most recent by LastModified
    latest = max(zips, key=lambda obj: obj["LastModified"])
    zip_key = latest["Key"]
    print(f"  Latest in S3: s3://{S3_EXTENSIONS_BUCKET}/{zip_key}")
    print(f"  Uploaded: {latest['LastModified']}")

    # Download
    EXTENSIONS_DIR.mkdir(exist_ok=True)
    zip_path = EXTENSIONS_DIR / f"{platform_name}_latest.zip"
    s3.download_file(S3_EXTENSIONS_BUCKET, zip_key, str(zip_path))
    print(f"  Downloaded: {zip_path.name}")

    # Extract
    extract_dir = EXTENSIONS_DIR / f"{platform_name}_latest"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir()

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)

    # Find manifest.json
    for root, dirs, files in os.walk(extract_dir):
        if "manifest.json" in files:
            ext_path = Path(root)
            print(f"  Extension ready: {ext_path}")
            return ext_path

    raise FileNotFoundError(f"No manifest.json found in {extract_dir}")

# ── Build extension for a platform ───────────────────────
def build_extension(env_name: str, platform_name: str, platform_cfg: dict, branch: str = None, frontend_branch: str = None, force_build: bool = False) -> Path:
    print(f"\n--- Building extension for '{platform_name}' ---")

    # Try downloading existing extension from S3 first (unless forced)
    if not force_build:
        try:
            ext_path = download_extension(env_name, platform_name)
            print(f"  Extension already available in S3, skipping ECS deploy")
            return ext_path
        except FileNotFoundError:
            print(f"  No existing extension in S3, deploying via ECS...")
    else:
        print(f"  Force build requested, deploying via ECS...")

    task_arn = deploy_environment(env_name, platform_cfg, branch, frontend_branch)
    wait_for_task(task_arn)
    ext_path = download_extension(env_name, platform_name)
    return ext_path
