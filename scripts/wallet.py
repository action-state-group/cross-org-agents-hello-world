"""Set up Org A's paying wallet on Base Sepolia (testnet) and print where to fund it.

    uv run scripts/wallet.py            # create a fresh testnet wallet
    uv run scripts/wallet.py --import   # use your own testnet wallet's key

--import asks for the key without echoing it, or reads PAYER_PRIVATE_KEY (prefer the prompt:
an exported variable lands in your shell history). Use a key that holds no real funds on
any chain: an EVM key controls the same address on mainnet too, and this demo pays only on
Base Sepolia. It won't replace a wallet already set up unless you add --force. The key stays
in .state/keys/payer-wallet.json (gitignored, owner-only); delete that file to remove it."""

import getpass
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eth_account import Account  # noqa: E402

from hello import evidence  # noqa: E402

path = evidence.STATE / "keys" / "payer-wallet.json"
path.parent.mkdir(parents=True, exist_ok=True)
if "--import" in sys.argv[1:]:
    if path.exists() and "--force" not in sys.argv[1:]:
        old = json.loads(path.read_text())["address"]
        sys.exit(f"Org A already pays from {old}. Add --force to replace that key (it is not kept).")
    key = os.environ.get("PAYER_PRIVATE_KEY") or getpass.getpass("Testnet-only private key (hex, not echoed): ")
    try:
        account = Account.from_key(key.strip())
    except ValueError:
        sys.exit("That is not a valid private key (64 hex characters, optionally with 0x).")
elif path.exists():
    account = None
else:
    account = Account.create()
if account is not None:
    # Created owner-only from the start: the key is never readable by others, even briefly.
    path.unlink(missing_ok=True)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
        f.write(json.dumps({"address": account.address, "private_key": account.key.hex()}))
address = json.loads(path.read_text())["address"]
print(f"Org A pays from: {address} (Base Sepolia testnet only)")
print("Fund it with Base Sepolia USDC (free): https://faucet.circle.com  (network: Base Sepolia)")
print("No ETH is needed: the x402 facilitator pays the gas. Each booking costs 0.01 testnet USDC.")
