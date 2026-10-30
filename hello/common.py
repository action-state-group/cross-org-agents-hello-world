"""What both orgs' services share: evidence sets on disk and the routes that serve them.

An evidence set is what one link opens: the signed records an org holds for one
interaction (its own and any the other org sent it), the wrapped objects it may
disclose, and where each party publishes its key.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from . import evidence

STATIC = Path(__file__).resolve().parent / "static"
SET_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")


class Sets:
    def __init__(self, org: str):
        self.dir = evidence.STATE / org / "sets"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, set_id: str) -> Path:
        if not SET_ID.match(set_id):
            raise HTTPException(404, "no such evidence set")
        return self.dir / f"{set_id}.json"

    def get(self, set_id: str) -> dict:
        path = self._path(set_id)
        if not path.exists():
            raise HTTPException(404, "no such evidence set")
        return json.loads(path.read_text())

    def put(self, set_id: str, value: dict) -> None:
        self._path(set_id).write_text(json.dumps(value, indent=1))

    def create(self, set_id: str, title: str, keys: dict[str, str], note: str = "") -> dict:
        value = {"title": title, "note": note, "records": [], "objects": {}, "keys": keys}
        self.put(set_id, value)
        return value

    def add(self, set_id: str, records: list[dict] = (), objects: list[bytes] = ()) -> dict:
        """Add records, and the wrapped objects some held leg names by digest."""
        value = self.get(set_id)
        held = {r["capsule_id"] for r in value["records"]}
        value["records"] += [r for r in records if r["capsule_id"] not in held]
        named = wrapped_digests(value["records"])
        for octets in objects:
            digest = hashlib.sha256(octets).hexdigest()
            if digest in named:
                value["objects"][digest] = base64.b64encode(octets).decode()
        self.put(set_id, value)
        return value

    def objects(self, set_id: str) -> dict[str, bytes]:
        return {k: base64.b64decode(v) for k, v in self.get(set_id)["objects"].items()}


def capsule_of(record: dict) -> dict:
    return json.loads(base64.b64decode(record["capsule"]))


def settlement_legs(records: list[dict]) -> list[dict]:
    """The records that carry a settlement member."""
    return [r for r in records if "settlement" in capsule_of(r)]


def wrapped_digests(records: list[dict]) -> set[str]:
    return {w["digest"] for r in settlement_legs(records) for w in capsule_of(r)["settlement"].get("wrapped", [])}


async def fetch_key(url: str) -> str | None:
    """The key a party publishes at its /.well-known/evidence-key, or None. Async, so
    a service can fetch its own key while handling a request."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            return (await client.get(url)).raise_for_status().json()["public_key"]
    except (httpx.HTTPError, ValueError, KeyError):
        return None


def mount(app: FastAPI, org: str, sets: Sets, *, key_published=lambda: True) -> None:
    # The verify page on the other org's origin fetches this org's key.
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"])

    @app.get("/.well-known/evidence-key")
    def evidence_key():
        if not key_published():
            raise HTTPException(404, "this org runs no evidence")
        return {"org": org, "public_key": evidence.public_key(org), "alg": "Ed25519"}

    @app.get("/sets/{set_id}")
    def get_set(set_id: str):
        return sets.get(set_id)

    @app.post("/sets/{set_id}/legs")
    async def add_leg(set_id: str, request: Request):
        """The other party adds its own half: a leg sealed under the key it publishes."""
        body = await request.json()
        held = sets.get(set_id)
        record = body.get("record")
        if not isinstance(record, dict) or "capsule_id" not in record:
            raise HTTPException(400, "send {\"record\": <capsulectl artifact record>}")
        if not settlement_legs([record]):
            raise HTTPException(400, "the record carries no settlement leg")
        # Bind both roles to the keys the two orgs publish: a leg sealed under any
        # other key is refused, so nobody can add a half in the other org's name.
        keys = {role: await fetch_key(url) for role, url in held["keys"].items()}
        if not (keys.get("payer") and keys.get("payee")):
            raise HTTPException(409, "the other org publishes no key yet, so its half cannot be checked")
        objects = [base64.b64decode(v) for v in body.get("objects", {}).values()]
        try:
            report = evidence.settlement_status(
                settlement_legs(held["records"]) + [record], keys["payer"], keys["payee"],
                {**sets.objects(set_id), **{hashlib.sha256(o).hexdigest(): o for o in objects}},
            )
        except evidence.EvidenceError as e:
            raise HTTPException(400, str(e))
        if not report["settlement"]["conforming"]:
            raise HTTPException(400, {"refused": "the leg does not verify with the legs held", "report": report["settlement"]})
        sets.add(set_id, [record], objects)
        return {"added": record["capsule_id"], "say": report.get("say"), "settlement": report["settlement"]["settlements"]}

    @app.get("/verify")
    def verify_page():
        return FileResponse(STATIC / "verify.html")
