"""Create Org A's testnet wallet (Base Sepolia) and print where to fund it.

The key stays in .state/keys/payer-wallet.json (gitignored). It is for testnet USDC only:
never send real funds to it."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eth_account import Account  # noqa: E402

from hello import evidence  # noqa: E402

path = evidence.STATE / "keys" / "payer-wallet.json"
path.parent.mkdir(parents=True, exist_ok=True)
if not path.exists():
    account = Account.create()
    path.write_text(json.dumps({"address": account.address, "private_key": account.key.hex()}))
    os.chmod(path, 0o600)
address = json.loads(path.read_text())["address"]
print(f"Org A's testnet wallet: {address}")
print("Fund it with Base Sepolia USDC (free): https://faucet.circle.com  (network: Base Sepolia)")
print("No ETH is needed: the x402 facilitator pays the gas.")
