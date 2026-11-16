"""Build the Lambda code zip and the dependency layer zip into dist/. No Docker needed.

The layer holds the anthropic SDK pinned below 2.0 (Bedrock accepts the 2.0 SDK's API version only for
Claude 4.7 and later) and tzdata, as manylinux wheels for Lambda on arm64 (Graviton).
"""
import hashlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST, BUILD = ROOT / "dist", ROOT / "build"
LAYER_REQS = ["anthropic==1.17.0", "tzdata", "cryptography==45.0.7", "aws-xray-sdk==2.16.0"]  # cryptography: Web Push; X-Ray SDK: traces of every call


def zipdir(src, dest, prefix=""):
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(src.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                z.write(p, Path(prefix) / p.relative_to(src))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def main():
    DIST.mkdir(exist_ok=True)
    code = DIST / "code.zip"
    zipdir(ROOT / "src", code)
    final_code = DIST / f"code-{sha(code)}.zip"
    code.replace(final_code)

    layer_dir = BUILD / "layer" / "python"
    if "--skip-layer" not in sys.argv:
        shutil.rmtree(BUILD / "layer", ignore_errors=True)
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "--target", str(layer_dir),
                               "--platform", "manylinux2014_aarch64", "--implementation", "cp",
                               "--python-version", "3.12", "--only-binary=:all:", *LAYER_REQS])
        # Lambda already ships boto3 and botocore; keep the layer small.
        for p in layer_dir.glob("*"):
            if p.name.split("-")[0] in ("boto3", "botocore", "s3transfer", "jmespath", "urllib3", "dateutil", "python_dateutil"):
                shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink()
        layer = DIST / "layer.zip"
        zipdir(BUILD / "layer", layer)
        final_layer = DIST / f"layer-{sha(layer)}.zip"
        layer.replace(final_layer)
    else:
        final_layer = max(DIST.glob("layer-*.zip"), key=lambda p: p.stat().st_mtime)   # the newest, not the first by name
    print(final_code.name)
    print(final_layer.name)


if __name__ == "__main__":
    main()
