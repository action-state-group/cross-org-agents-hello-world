"""What both orgs' services share: evidence sets on disk and the routes that serve them.

An evidence set is what one link opens: the signed records an org holds for one
interaction (its own and any the other org sent it), the wrapped objects it may
disclose, and where each party publishes its key.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

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

    def create(self, set_id: str, title: str, keys: dict[str, str]) -> dict:
        value = {"title": title, "records": [], "objects": {}, "keys": keys}
        self.put(set_id, value)
        return value

    def add(self, set_id: str, records: list[dict] = (), objects: dict[str, bytes] | None = None) -> dict:
        value = self.get(set_id)
        held = {r["capsule_id"] for r in value["records"]}
        value["records"] += [r for r in records if r["capsule_id"] not in held]
        for digest, octets in (objects or {}).items():
            value["objects"][digest] = base64.b64encode(octets).decode()
        self.put(set_id, value)
        return value


def settlement_legs(records: list[dict]) -> list[dict]:
    """The records that carry a settlement member."""
    out = []
    for record in records:
        capsule = json.loads(base64.b64decode(record["capsule"]))
        if "settlement" in capsule:
            out.append(record)
    return out


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
        """The other party adds its own half: a leg it sealed under its own key."""
        body = await request.json()
        held = sets.get(set_id)
        record = body.get("record")
        if not isinstance(record, dict) or "capsule_id" not in record:
            raise HTTPException(400, "send {\"record\": <capsulectl artifact record>}")
        if not settlement_legs([record]):
            raise HTTPException(400, "the record carries no settlement leg")
        try:
            report = evidence.settlement_status(settlement_legs(held["records"]) + [record])
        except evidence.EvidenceError as e:
            raise HTTPException(400, str(e))
        if not report["settlement"]["conforming"]:
            raise HTTPException(400, {"refused": "the leg does not verify with the legs held", "report": report})
        objects = {k: base64.b64decode(v) for k, v in body.get("objects", {}).items()}
        sets.add(set_id, [record], objects)
        return {"added": record["capsule_id"], "say": report.get("say"), "settlement": report["settlement"]["settlements"]}

    @app.get("/verify")
    def verify_page():
        return FileResponse(STATIC / "verify.html")
