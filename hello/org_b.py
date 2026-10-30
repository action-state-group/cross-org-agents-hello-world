"""Org B: the venue's agent. It quotes, takes an x402 payment on Base Sepolia (testnet),
and confirms the booking.

ORG_B_EVIDENCE=off turns it into a plain x402 endpoint that seals nothing (hop 4).
PAYMENT=none turns payment off (the no-testnet fallback: hops 1 and 3 only).
"""

from __future__ import annotations

import base64
import json
import os
import uuid

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from . import evidence, legs
from .common import Sets, mount
from .x402_testnet import ASSET, NETWORK, PRICE, b64, facilitator_settle, payment_required

ORG = "org-b"
OPERATOR = "venue.org-b.example"
SELF = os.environ.get("ORG_B_URL", "http://localhost:8002")
EVIDENCE = os.environ.get("ORG_B_EVIDENCE", "on") == "on"
PAYMENT = os.environ.get("PAYMENT", "x402")

app = FastAPI(title="Org B: venue agent")
sets = Sets(ORG)
# A plain endpoint publishes a key only once it has added its first half (hop 4).
mount(app, ORG, sets, key_published=lambda: EVIDENCE or (evidence.STATE / ORG / "evidence-adopted").exists())
quotes: dict[str, dict] = {}
# What Org B's own systems hold for each booking, kept even with evidence off, so it
# can add its half later (hop 4).
bookings = evidence.STATE / ORG / "bookings"
bookings.mkdir(parents=True, exist_ok=True)


def pay_to() -> str:
    # ORG_B_PAY_TO receives into your own Base Sepolia address; an address, never a key.
    return os.environ.get("ORG_B_PAY_TO") or json.loads((evidence.STATE / "keys" / "org-b-wallet.json").read_text())["address"]


@app.post("/quote")
async def quote(request: Request):
    body = await request.json()
    ask = {k: body.get(k) for k in ("venue", "date", "people")}
    quote_id = uuid.uuid4().hex[:16]
    price = legs.amount(PRICE, NETWORK, ASSET)
    terms = None
    if EVIDENCE:
        terms = evidence.seal(ORG, f"terms-{quote_id}", OPERATOR, settlement=legs.terms("payee", price, evidence.json_digest(ask)))
        payer_url = str(body.get("payer_url") or "")
        sets.create(
            quote_id,
            f"Booking {quote_id}: {ask['venue']} on {ask['date']}",
            {"payer": f"{payer_url}/.well-known/evidence-key", "payee": f"{SELF}/.well-known/evidence-key"},
        )
        sets.add(quote_id, [terms])
    quotes[quote_id] = {"ask": ask, "terms": terms, "price": price}
    return {"quote_id": quote_id, "price": price, "terms": terms, "book_url": f"{SELF}/book/{quote_id}"}


@app.post("/book/{quote_id}")
async def book(quote_id: str, request: Request):
    q = quotes.get(quote_id)
    if q is None:
        raise HTTPException(404, "no such quote")
    headers = {}
    settle = None
    if PAYMENT == "x402":
        required = payment_required(f"{SELF}/book/{quote_id}", pay_to())
        header = request.headers.get("PAYMENT-SIGNATURE")
        if not header:
            return JSONResponse(required, status_code=402, headers={"PAYMENT-REQUIRED": b64(json.dumps(required).encode())})
        try:
            settle_octets = await facilitator_settle(base64.b64decode(header, validate=True), required["accepts"][0])
        except (httpx.HTTPError, ValueError) as e:  # binascii.Error is a ValueError
            return JSONResponse({**required, "error": str(e)}, status_code=402)
        settle = json.loads(settle_octets)
        headers["PAYMENT-RESPONSE"] = b64(settle_octets)

    confirmation = json.dumps(
        {"booking": quote_id, **q["ask"], "status": "confirmed", "paid_tx": settle["transaction"] if settle else None},
        sort_keys=True,
        separators=(",", ":"),
    )
    receipt = {"price": q["price"], "confirmation": confirmation}
    if settle is not None:
        receipt["settle_b64"] = b64(settle_octets)
    (bookings / f"{quote_id}.json").write_text(json.dumps(receipt))
    sealed = []
    if EVIDENCE:
        terms_id = q["terms"]["capsule_id"]
        at = evidence.now()
        objects = []
        if settle is not None:
            sealed.append(evidence.seal(ORG, f"paid-{quote_id}", OPERATOR, settlement=legs.payee_observed(terms_id, q["price"], settle_octets, settle, at)))
            objects.append(settle_octets)
        sealed.append(evidence.seal(ORG, f"delivered-{quote_id}", OPERATOR, settlement=legs.delivered(terms_id, "payee", confirmation.encode(), at)))
        sets.add(quote_id, sealed, objects)
    return JSONResponse(
        {
            "confirmation": confirmation,
            "legs": sealed,
            "link": f"{SELF}/verify#set={quote_id}" if EVIDENCE else None,
        },
        headers=headers,
    )
