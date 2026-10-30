"""x402 v2 `exact` on Base Sepolia only. There is no mainnet setting.

The payer signs an EIP-3009 transfer authorization for testnet USDC; the payee's
facilitator (the public x402.org testnet facilitator by default) verifies and settles it.
"""

from __future__ import annotations

import base64
import json
import os

import httpx

NETWORK = "eip155:84532"  # Base Sepolia. Testnet only.
ASSET = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"  # USDC on Base Sepolia
PRICE = "10000"  # 0.01 testnet USDC, in atomic units (6 decimals)
FACILITATOR = os.environ.get("X402_FACILITATOR", "https://x402.org/facilitator")


def payment_required(resource: str, pay_to: str) -> dict:
    return {
        "x402Version": 2,
        "resource": {"url": resource, "description": "Venue booking (testnet)"},
        "accepts": [
            {
                "scheme": "exact",
                "network": NETWORK,
                "asset": ASSET,
                "amount": PRICE,
                "payTo": pay_to,
                "maxTimeoutSeconds": 120,
                "extra": {"name": "USDC", "version": "2"},
            }
        ],
    }


async def facilitator_settle(payload_octets: bytes, requirements: dict) -> bytes:
    """Verify, then settle; returns the settlement response body as the facilitator sent it."""
    payload = json.loads(payload_octets)
    if payload.get("accepted", {}).get("network") != NETWORK:
        raise ValueError("only Base Sepolia (eip155:84532) is accepted")
    body = {"x402Version": 2, "paymentPayload": payload, "paymentRequirements": requirements}
    async with httpx.AsyncClient(timeout=60) as client:
        verified = await client.post(f"{FACILITATOR}/verify", json=body)
        verified.raise_for_status()
        if not verified.json().get("isValid"):
            raise ValueError(f"payment not valid: {verified.json().get('invalidReason')}")
        settled = await client.post(f"{FACILITATOR}/settle", json=body)
        settled.raise_for_status()
        if not settled.json().get("success"):
            raise ValueError(f"payment not settled: {settled.json().get('errorReason')}")
        return settled.content


async def pay(required: dict, private_key: str, quoted: dict) -> bytes:
    """Build the PAYMENT-SIGNATURE payload for a 402 answer; returns its JSON octets.
    Pays only on Base Sepolia, and only the quoted amount of the quoted asset."""
    from eth_account import Account
    from x402.client import x402Client
    from x402.http.utils import encode_payment_signature_header, safe_base64_decode
    from x402.mechanisms.evm.exact.register import register_exact_evm_client
    from x402.schemas.payments import PaymentRequired

    if any(a.get("network") != NETWORK for a in required.get("accepts", [])):
        raise ValueError("refusing to pay on anything but Base Sepolia (eip155:84532)")
    if any(
        a.get("amount") != quoted["value"] or f"{a.get('network')}/erc20:{str(a.get('asset')).lower()}" != quoted["assetCode"]
        for a in required.get("accepts", [])
    ):
        raise ValueError("refusing to pay: the 402 asks for a different amount or asset than the quote")
    client = register_exact_evm_client(x402Client(), Account.from_key(private_key), networks=NETWORK)
    payload = await client.create_payment_payload(PaymentRequired.model_validate(required))
    return safe_base64_decode(encode_payment_signature_header(payload)).encode()


def b64(octets: bytes) -> str:
    return base64.b64encode(octets).decode()
