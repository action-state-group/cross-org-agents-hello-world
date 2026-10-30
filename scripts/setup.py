"""Create each org's key and capsulectl profile under .state/, plus Org B's testnet
receiving address. Safe to run again: it keeps what already exists."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hello import evidence  # noqa: E402

keys = evidence.STATE / "keys"
keys.mkdir(parents=True, exist_ok=True)
print(evidence.version())

for org in ("org-a", "org-b"):
    meta = keys / f"{org}.json"
    if not meta.exists():
        seed, ck = keys / f"{org}.seed", keys / f"{org}-checkpoint.seed"
        pub = evidence.capsulectl("key", "generate", "--output", str(seed))["public_key"]
        ck_pub = evidence.capsulectl("key", "generate", "--output", str(ck))["public_key"]
        evidence.capsulectl(
            "profile", "create", "--name", org, "--type", "sqlite",
            "--sqlite-path", str(evidence.STATE / f"{org}.db"), "--namespace", org, "--log-id", f"{org}-log",
            "--signing-key-file", str(seed), "--trusted-key", pub,
            "--checkpoint-signing-key-file", str(ck), "--checkpoint-trusted-key", ck_pub,
        )
        meta.write_text(json.dumps({"org": org, "public_key": pub}))
    print(f"{org}: key {json.loads(meta.read_text())['public_key']}")

wallet = keys / "org-b-wallet.json"
if not wallet.exists():
    from eth_account import Account
    # Org B only receives, so only its address is kept.
    wallet.write_text(json.dumps({"address": Account.create().address}))
print(f"org-b receives testnet USDC at {json.loads(wallet.read_text())['address']}")
