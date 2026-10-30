"""Hop 4, run as Org B: add Org B's half to a one-sided record Org A sealed.

Org B ran as a plain x402 endpoint (no evidence) but kept its facilitator's settlement
response. Here it seals its own payee-observed leg under its own key, citing Org A's
legs as counterparty halves, and posts it to the link Org A gave it.

    uv run scripts/add_half.py http://localhost:8001/verify#set=<id>
"""

import base64
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hello import evidence, legs  # noqa: E402
from hello.common import settlement_legs  # noqa: E402

link = urlsplit(sys.argv[1])
set_id = dict(p.split("=", 1) for p in link.fragment.split("&"))["set"]
base = f"{link.scheme}://{link.netloc}"
held = httpx.get(f"{base}/sets/{set_id}").raise_for_status().json()

theirs = [json.loads(base64.b64decode(r["capsule"])) | {"_record": r} for r in settlement_legs(held["records"])]
terms = next(c for c in theirs if c["settlement"]["leg"] == "terms")
receipt = json.loads((evidence.STATE / "org-b" / "payments" / f"{set_id}.json").read_text())
settle_octets = base64.b64decode(receipt["settle_b64"])
settle = json.loads(settle_octets)

leg = legs.payee_observed(terms["capsule_id"], terms["settlement"]["amount"], settle_octets, settle, evidence.now())
record = evidence.seal("org-b", f"paid-{set_id}", "venue.org-b.example", settlement=leg,
                       references=[c["capsule_id"] for c in theirs if c["settlement"]["leg"] != "terms"])
(evidence.STATE / "org-b" / "evidence-adopted").touch()
answer = httpx.post(f"{base}/sets/{set_id}/legs", json={
    "record": record, "objects": {legs.sha256(settle_octets): base64.b64encode(settle_octets).decode()},
})
print(json.dumps(answer.json(), indent=1))
answer.raise_for_status()
