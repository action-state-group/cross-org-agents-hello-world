"""Set up Org A's paying wallet on Base Sepolia (testnet) and print where to fund it.

    uv run scripts/wallet.py            # create a fresh testnet wallet
    uv run scripts/wallet.py --import   # use your own testnet wallet's key

--import reads the key from the PAYER_PRIVATE_KEY environment variable, or asks for it
without echoing it, so it never lands in your shell history. Use a wallet that holds
testnet funds only, never a key that controls real (mainnet) funds: this demo pays only on
Base Sepolia, but a key on disk is still a key. The key stays in
.state/keys/payer-wallet.json (gitignored, owner-only); delete that file to remove it."""

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
    path.write_text(json.dumps({"address": account.address, "private_key": account.key.hex()}))
    os.chmod(path, 0o600)
address = json.loads(path.read_text())["address"]
print(f"Org A pays from: {address} (Base Sepolia testnet only)")
print("Fund it with Base Sepolia USDC (free): https://faucet.circle.com  (network: Base Sepolia)")
print("No ETH is needed: the x402 facilitator pays the gas. Each booking costs 0.01 testnet USDC.")
