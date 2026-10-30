"""The only place in this hello world that produces or checks evidence.

Every call shells out to `capsulectl` (https://github.com/action-state-group/capsule-cli).
There is no SDK: an org's agent hands capsulectl a capsule-seal-request/v1 file and gets
back a signed artifact record. Each org has its own capsulectl profile and its own
Ed25519 key, created by `scripts/setup.py` under `.state/`.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = Path(os.environ.get("HELLO_STATE", ROOT / ".state"))
# scripts/install-capsulectl.sh puts the pinned release in ./bin.
CAPSULECTL = os.environ.get("CAPSULECTL") or (str(ROOT / "bin" / "capsulectl") if (ROOT / "bin" / "capsulectl").exists() else "capsulectl")


class EvidenceError(RuntimeError):
    pass


def _env() -> dict[str, str]:
    # Profiles live under .state/config, never in the user's own ~/.config.
    return {**os.environ, "XDG_CONFIG_HOME": str(STATE / "config")}


def capsulectl(*args: str) -> dict:
    proc = subprocess.run(
        [CAPSULECTL, *args], capture_output=True, text=True, env=_env(), stdin=subprocess.DEVNULL
    )
    if proc.returncode != 0:
        raise EvidenceError(f"capsulectl {args[0]} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return json.loads(proc.stdout)


def version() -> str:
    try:
        proc = subprocess.run([CAPSULECTL, "--version"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise SystemExit("capsulectl is not on PATH: run scripts/install-capsulectl.sh first (see README)")
    return proc.stdout.strip()


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def json_digest(value) -> str:
    """SHA-256 over the JSON's canonical form (RFC 8785 for the ASCII keys and
    integer and string values this demo uses)."""
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def public_key(org: str) -> str:
    return json.loads((STATE / "keys" / f"{org}.json").read_text())["public_key"]


def seal(
    org: str,
    action_id: str,
    operator: str,
    *,
    settlement: dict | None = None,
    references: list[str] | None = None,
    payload: dict | None = None,
) -> dict:
    """Seal one capsule under `org`'s key and return the artifact record.

    `settlement` is the settlement member of draft-mih-agent-settlement-records-00;
    `references` are capsule IDs of the other party's legs this org holds, cited as
    `counterparty_half`.
    """
    capsule: dict = {
        "ActionID": action_id,
        "ActionType": "fyi",
        "Operator": operator,
        "Developer": "cross-org-agents-hello-world@0.1",
        "Timestamp": now(),
    }
    if settlement is not None:
        capsule["Extensions"] = [{"Name": "settlement", "Value": settlement}]
    if references:
        capsule["References"] = [
            {"Type": "agent-action-capsule", "DigestAlg": "SHA-256", "Digest": ref, "CitationPurpose": "counterparty_half"}
            for ref in references
        ]
    request: dict = {"spec_version": "capsule-seal-request/v1", "capsule": capsule}
    if payload is not None:
        request["payload"] = payload
    with tempfile.TemporaryDirectory() as tmp:
        req, out = Path(tmp, "request.json"), Path(tmp, "record.json")
        req.write_text(json.dumps(request))
        capsulectl("seal", "--profile", org, "--request", str(req), "--output", str(out))
        return json.loads(out.read_text())


def settlement_status(records: list[dict], payer_key: str | None = None, payee_key: str | None = None) -> dict:
    """`capsulectl settlement status` over the legs held: derives the payment and
    delivery state from whichever legs are present. With both keys it also checks
    that each leg is sealed by the party its role names."""
    with tempfile.TemporaryDirectory() as tmp:
        args = ["settlement", "status"]
        if payer_key and payee_key:
            args += ["--payer-key", payer_key, "--payee-key", payee_key]
        paths = {}
        for i, record in enumerate(records):
            path = Path(tmp, f"leg-{i}.json")
            path.write_text(json.dumps(record))
            args += ["--leg", str(path)]
            paths[str(path)] = record["capsule_id"]
        # capsulectl names each leg by its file; name it by its capsule ID instead.
        report = json.dumps(capsulectl(*args))
        for path, capsule_id in paths.items():
            report = report.replace(json.dumps(path), json.dumps(capsule_id))
        return json.loads(report)
