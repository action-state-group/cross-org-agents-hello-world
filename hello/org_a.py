"""Org A: the booking agent a person talks to.

Hop 1: a person submits the form; Org A seals its record of the request and gives the
person a copy. Hop 2: Org A asks Org B for a quote and pays it with x402 on Base
Sepolia; each org seals its own legs. Hop 3: the person gets Org B's confirmation link
and verifies it in the browser. Hop 4: if Org B runs no evidence, Org A still seals its
own half and hands Org B an "add your half" link.
"""

from __future__ import annotations

import base64
import html
import json
import os
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from . import evidence, legs
from .common import STATIC, Sets, fetch_key, mount, settlement_legs
from .x402_testnet import pay

ORG = "org-a"
OPERATOR = "booking.org-a.example"
SELF = os.environ.get("ORG_A_URL", "http://localhost:8001")
ORG_B = os.environ.get("ORG_B_URL", "http://localhost:8002")
WALLET = evidence.STATE / "keys" / "payer-wallet.json"

app = FastAPI(title="Org A: booking agent")
sets = Sets(ORG)
mount(app, ORG, sets)


@app.get("/")
def form():
    return FileResponse(STATIC / "form.html")


@app.post("/book", response_class=HTMLResponse)
async def book(venue: str = Form(...), date: str = Form(...), people: int = Form(...)):
    ask = {"venue": venue, "date": date, "people": people}
    result = await run_booking(ask)
    return HTMLResponse(render(result))


async def run_booking(ask: dict) -> dict:
    # Hop 1: Org A's sealed record of what the person asked for: a receipt the person
    # can verify. Only Org A signs it; a half signed by the person is roadmap.
    request_id = uuid.uuid4().hex[:16]
    request_record = evidence.seal(ORG, f"request-{request_id}", OPERATOR, payload=ask)
    sets.create(
        request_id,
        f"Your booking request to Org A ({ask['venue']}, {ask['date']})",
        {"sealer": f"{SELF}/.well-known/evidence-key"},
        note="One-sided: this is Org A's record of your request, signed by Org A only. You did not sign it.",
    )
    sets.add(request_id, [request_record])

    async with httpx.AsyncClient(timeout=90) as client:
        # Hop 2: ask Org B for a quote, then book it, paying if Org B asks with a 402.
        quote = (await client.post(f"{ORG_B}/quote", json={**ask, "payer_url": SELF})).raise_for_status().json()
        booked = await client.post(quote["book_url"])
        payload_octets = settle_octets = None
        if booked.status_code == 402:
            if not WALLET.exists():
                raise HTTPException(402, "Org B asks for an x402 payment, but no testnet wallet is set up. "
                                         "Run `uv run scripts/wallet.py`, or run both orgs with PAYMENT=none.")
            required = json.loads(base64.b64decode(booked.headers["PAYMENT-REQUIRED"]))
            try:
                payload_octets = await pay(required, json.loads(WALLET.read_text())["private_key"], quote["price"])
            except ValueError as e:
                raise HTTPException(502, f"Org A did not pay: {e}")
            booked = await client.post(quote["book_url"], headers={"PAYMENT-SIGNATURE": base64.b64encode(payload_octets).decode()})
        booked.raise_for_status()
        answer = booked.json()
        if "PAYMENT-RESPONSE" in booked.headers:
            settle_octets = base64.b64decode(booked.headers["PAYMENT-RESPONSE"])

        at = evidence.now()
        quote_id = quote["quote_id"]
        their_legs = answer["legs"]
        if quote["terms"] is not None:
            # Org B runs evidence: answer its terms and cite its legs as counterparty halves.
            terms_id = quote["terms"]["capsule_id"]
            sets.create(quote_id, f"Booking {quote_id} (Org A's copy)", _keys())
            sets.add(quote_id, [quote["terms"], *their_legs])
        else:
            # Hop 4: Org B is a plain endpoint. Org A states the terms it accepted itself.
            terms = evidence.seal(ORG, f"terms-{quote_id}", OPERATOR, settlement=legs.terms("payer", quote["price"], evidence.json_digest(ask)))
            terms_id = terms["capsule_id"]
            sets.create(quote_id, f"Booking {quote_id}: begun one-sided by Org A", _keys())
            sets.add(quote_id, [terms])

        cited = [r["capsule_id"] for r in their_legs]
        mine, objects = [], []
        if settle_octets is not None:
            settle = json.loads(settle_octets)
            # The amount is what Org A's wallet signed, not what the quote said.
            paid = json.loads(payload_octets)["accepted"]
            mine.append(evidence.seal(ORG, f"paid-{quote_id}", OPERATOR, references=cited, settlement=legs.payer_observed(
                terms_id, legs.amount(paid["amount"], paid["network"], paid["asset"]), payload_octets, settle_octets, settle, at)))
            objects = [payload_octets, settle_octets]
        mine.append(evidence.seal(ORG, f"received-{quote_id}", OPERATOR, references=cited,
                                  settlement=legs.delivered(terms_id, "payer", answer["confirmation"].encode(), at)))
        sets.add(quote_id, mine, objects)

        if quote["terms"] is not None:
            # Give Org B our half too, so its confirmation link shows both sides.
            sent = {legs.sha256(o): base64.b64encode(o).decode() for o in objects}
            for record in mine:
                (await client.post(f"{ORG_B}/sets/{quote_id}/legs", json={"record": record, "objects": sent})).raise_for_status()

    held = sets.get(quote_id)
    b_key = await fetch_key(f"{ORG_B}/.well-known/evidence-key")
    try:
        status = evidence.settlement_status(settlement_legs(held["records"]), evidence.public_key(ORG), b_key, sets.objects(quote_id))
    except evidence.EvidenceError as e:
        status = {"say": f"capsulectl refused the record: {e}"}
    return {
        "ask": ask,
        "receipt": f"{SELF}/verify#set={request_id}",
        "confirmation": answer["confirmation"],
        "confirmation_link": answer["link"],
        "add_half_link": None if quote["terms"] is not None else f"{SELF}/verify#set={quote_id}",
        "status": status,
    }


def _keys() -> dict:
    return {"payer": f"{SELF}/.well-known/evidence-key", "payee": f"{ORG_B}/.well-known/evidence-key"}


def render(r: dict) -> str:
    e = html.escape
    rows = [
        f'<li><b>Hop 1, your receipt (one-sided).</b> Org A signed its record of your request; you did not sign it. '
        f'<a href="{e(r["receipt"])}">Verify it</a>.</li>',
        f'<li><b>Hop 2.</b> {"Org A sealed its half; Org B sealed nothing" if r["add_half_link"] else "Org A and Org B each sealed their own half"}. '
        f'<code>capsulectl settlement status</code> says: <i>{e(r["status"].get("say", ""))}</i></li>',
    ]
    if r["confirmation_link"]:
        rows.append(f'<li><b>Hop 3.</b> Org B\'s confirmation: <a href="{e(r["confirmation_link"])}">verify it in your browser</a>.</li>')
    if r["add_half_link"]:
        rows.append(f'<li><b>Hop 4.</b> Org B runs no evidence, so the record is one-sided. Org B can verify it and add its half: <a href="{e(r["add_half_link"])}">{e(r["add_half_link"])}</a></li>')
    return (Path(STATIC / "result.html").read_text()
            .replace("{{venue}}", e(r["ask"]["venue"])).replace("{{date}}", e(r["ask"]["date"]))
            .replace("{{items}}", "\n".join(rows)).replace("{{confirmation}}", e(r["confirmation"])))
