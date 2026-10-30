"""Settlement leg members (draft-mih-agent-settlement-records-00) for an x402 payment.

capsulectl has no verb that builds a leg from x402 objects yet, so these functions
build the `settlement` member and `evidence.seal` seals it. Wrapped x402 objects are
bound by SHA-256 only; their octets stay with the org that holds them. Account
addresses never enter a leg: the payment is referenced by its transaction hash.
"""

from __future__ import annotations

import hashlib

USDC_DECIMALS = 6


def sha256(octets: bytes) -> str:
    return hashlib.sha256(octets).hexdigest()


def amount(value: str, network: str, asset: str) -> dict:
    # CAIP-19 asset type; x402 amounts are already integer strings in atomic units.
    return {"value": value, "assetCode": f"{network}/erc20:{asset.lower()}", "assetScale": USDC_DECIMALS}


def wrapped(kind: str, octets: bytes) -> dict:
    return {"type": kind, "digest_alg": "SHA-256", "digest": sha256(octets)}


def terms(sealer_role: str, price: dict, deliverable_digest: str) -> dict:
    return {
        "version": "0",
        "leg": "terms",
        "sealer_role": sealer_role,
        "amount": price,
        "deliverable": {"description_digest": deliverable_digest},
    }


def _payment_ref(settle: dict) -> dict:
    return {"type": "x402.transaction", "value": settle["transaction"], "network": settle["network"]}


def _status(settle: dict) -> str:
    return "settled" if settle.get("success") else "failed"


def payer_observed(terms_ref: str, price: dict, payload_octets: bytes, settle_octets: bytes, settle: dict, observed_at: str) -> dict:
    """What the payer's own system saw: the PAYMENT-SIGNATURE it sent and the
    PAYMENT-RESPONSE it got back."""
    return {
        "version": "0",
        "leg": "payer_observed",
        "sealer_role": "payer",
        "terms_ref": terms_ref,
        "payment_ref": _payment_ref(settle),
        "amount": price,
        "status": _status(settle),
        "observed_at": observed_at,
        # x402 exact is gasless for the payer: the facilitator pays gas.
        "routing_fee": {**price, "value": "0"},
        "wrapped": [wrapped("x402.payment-payload", payload_octets), wrapped("x402.settle-response", settle_octets)],
    }


def payee_observed(terms_ref: str, price: dict, settle_octets: bytes, settle: dict, observed_at: str) -> dict:
    """What the payee's own facilitator reported for the payment."""
    return {
        "version": "0",
        "leg": "payee_observed",
        "sealer_role": "payee",
        "terms_ref": terms_ref,
        "payment_ref": _payment_ref(settle),
        "received": price,
        "status": _status(settle),
        "observed_at": observed_at,
        # x402 exact transfers exactly the amount: nothing is deducted on receipt.
        "receive_fee": {**price, "value": "0"},
        "wrapped": [wrapped("x402.settle-response", settle_octets)],
    }


def delivered(terms_ref: str, sealer_role: str, content: bytes, observed_at: str) -> dict:
    return {
        "version": "0",
        "leg": "delivered",
        "sealer_role": sealer_role,
        "terms_ref": terms_ref,
        "observed_at": observed_at,
        "delivery": {"direction": "sent" if sealer_role == "payee" else "received", "content_digest": sha256(content)},
    }
