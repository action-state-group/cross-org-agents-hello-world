"""A test double for the x402 facilitator, so the payment path runs in CI without a
funded testnet wallet. It checks the request shape and answers like a facilitator
whose settlement succeeded. It touches no chain and is never used outside tests."""

import hashlib
import json

from fastapi import FastAPI, Request

app = FastAPI()


def _check(body: dict) -> dict:
    payload = body["paymentPayload"]
    assert body["x402Version"] == 2 and payload["accepted"]["network"] == "eip155:84532"
    assert payload["payload"]["authorization"]["value"] == body["paymentRequirements"]["amount"]
    return payload


@app.post("/verify")
async def verify(request: Request):
    payload = _check(await request.json())
    return {"isValid": True, "payer": payload["payload"]["authorization"]["from"]}


@app.post("/settle")
async def settle(request: Request):
    payload = _check(await request.json())
    tx = "0x" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return {"success": True, "transaction": tx, "network": "eip155:84532", "payer": payload["payload"]["authorization"]["from"]}
