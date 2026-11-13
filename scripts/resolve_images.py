"""Resolve reviewed public image candidates to immutable Linux ARM64 references."""
from __future__ import annotations

import json
import re

from bootstrap import ROOT
from local_runtime import docker

CANDIDATES = {
    "postgres": "pgvector/pgvector:0.8.7-pg17",
    "auth": "supabase/gotrue:v2.197.0",
    "mail": "axllent/mailpit:v1.27.4",
    "python": "python:3.12.10-slim-bookworm",
}


def main() -> None:
    lock = ROOT / "infrastructure/local/images.lock.json"
    images = json.loads(lock.read_text()) if lock.exists() else {}
    for key, candidate in CANDIDATES.items():
        if key not in images:
            result = docker("buildx", "imagetools", "inspect", "--raw", candidate, capture_output=True, text=True)
            value = json.loads(result.stdout)
            descriptors = [m for m in value.get("manifests", [])
                           if m.get("platform", {}).get("os") == "linux"
                           and m.get("platform", {}).get("architecture") == "arm64"
                           and m.get("platform", {}).get("variant", "") in ("", "v8")]
            if len(descriptors) != 1:
                raise RuntimeError(f"No unambiguous ARM64 image for {candidate}")
            digest = descriptors[0]["digest"]
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                raise RuntimeError("Invalid registry digest")
            images[key] = {"candidate": candidate, "reference": candidate.rsplit(":", 1)[0] + "@" + digest,
                           "platform": "linux/arm64"}
            lock.write_text(json.dumps(images, indent=2, sort_keys=True) + "\n")
        reference = images[key]["reference"]
        present = docker("image", "inspect", reference, check=False, capture_output=True, text=True)
        if present.returncode:
            docker("pull", "--platform", "linux/arm64", reference)
        result = docker("image", "inspect", reference, capture_output=True, text=True)
        inspected = json.loads(result.stdout)[0]
        if inspected["Architecture"] != "arm64" or inspected["Os"] != "linux":
            raise RuntimeError("Pulled image platform mismatch")
        print(f"Verified {key}: linux/arm64 {reference}")


if __name__ == "__main__":
    main()
