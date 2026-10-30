"""Hop 4, run as Org B: add Org B's half to a one-sided record Org A sealed.

Org B ran as a plain endpoint (no evidence) but kept what its own systems hold for the
booking: the confirmation it sent and, if paid, its facilitator's settlement response.
Here it seals its own legs from those, under its own key, citing Org A's legs as
counterparty halves, and posts them to the link Org A gave it. It signs only what its
own records show: the amount and transaction come from its receipt, not from Org A.

    uv run scripts/add_half.py "http://localhost:8001/verify#set=<id>"
"""

import base64
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hello import evidence, legs  # noqa: E402
from hello.common import capsule_of, settlement_legs  # noqa: E402

link = urlsplit(sys.argv[1])
set_id = dict(p.split("=", 1) for p in link.fragment.split("&"))["set"]
base = f"{link.scheme}://{link.netloc}"
held = httpx.get(f"{base}/sets/{set_id}").raise_for_status().json()
receipt = json.loads((evidence.STATE / "org-b" / "bookings" / f"{set_id}.json").read_text())

theirs = [capsule_of(r) for r in settlement_legs(held["records"])]
terms = next(c for c in theirs if c["settlement"]["leg"] == "terms")
if terms["settlement"]["amount"] != receipt["price"]:
    sys.exit(f"refusing: Org A's terms say {terms['settlement']['amount']}, Org B quoted {receipt['price']}")
cited = [c["capsule_id"] for c in theirs if c["settlement"]["leg"] != "terms"]
at = evidence.now()
mine, objects = [], {}
if "settle_b64" in receipt:
    settle_octets = base64.b64decode(receipt["settle_b64"])
    settle = json.loads(settle_octets)
    paid = [c for c in theirs if c["settlement"]["leg"] == "payer_observed"]
    if any(c["settlement"]["payment_ref"]["value"] != settle["transaction"] for c in paid):
        sys.exit("refusing: Org A's payment leg names a transaction Org B's facilitator did not report")
    mine.append(legs.payee_observed(terms["capsule_id"], receipt["price"], settle_octets, settle, at))
    objects[legs.sha256(settle_octets)] = base64.b64encode(settle_octets).decode()
mine.append(legs.delivered(terms["capsule_id"], "payee", receipt["confirmation"].encode(), at))

# From now on Org B publishes its key, so Org A can check these legs are Org B's.
(evidence.STATE / "org-b" / "evidence-adopted").touch()
for i, leg in enumerate(mine):
    record = evidence.seal("org-b", f"{leg['leg']}-{set_id}", "venue.org-b.example", settlement=leg, references=cited)
    answer = httpx.post(f"{base}/sets/{set_id}/legs", json={"record": record, "objects": objects})
    print(json.dumps(answer.json(), indent=1))
    answer.raise_for_status()
