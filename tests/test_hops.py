"""End to end: both orgs as real HTTP services on localhost, sealing with capsulectl.

The payment runs against tests/fake_facilitator.py, so these tests need no funded
testnet wallet. The real testnet run is the README's.
"""

from __future__ import annotations

import base64
import json
import os
import re
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def services(tmp_path: Path, *, payment: str = "x402", b_evidence: str = "on"):
    a, b, f = _port(), _port(), _port()
    env = {
        **os.environ,
        "HELLO_STATE": str(tmp_path),
        "ORG_A_URL": f"http://127.0.0.1:{a}",
        "ORG_B_URL": f"http://127.0.0.1:{b}",
        "X402_FACILITATOR": f"http://127.0.0.1:{f}",
        "PAYMENT": payment,
        "ORG_B_EVIDENCE": b_evidence,
    }
    for script in ("setup.py", "wallet.py"):
        subprocess.run([sys.executable, str(ROOT / "scripts" / script)], env=env, check=True, capture_output=True, stdin=subprocess.DEVNULL)
    procs = [
        subprocess.Popen([sys.executable, "-m", "uvicorn", app, "--port", str(port), "--log-level", "warning"],
                         cwd=cwd, env=env, stdin=subprocess.DEVNULL)
        for app, port, cwd in (("hello.org_a:app", a, ROOT), ("hello.org_b:app", b, ROOT), ("fake_facilitator:app", f, ROOT / "tests"))
    ]
    try:
        for port in (a, b, f):
            for _ in range(100):
                try:
                    httpx.get(f"http://127.0.0.1:{port}/docs", timeout=1)
                    break
                except httpx.HTTPError:
                    time.sleep(0.1)
        yield env, f"http://127.0.0.1:{a}", f"http://127.0.0.1:{b}"
    finally:
        for p in procs:
            p.terminate()
            p.wait(timeout=10)


def book(org_a: str) -> str:
    page = httpx.post(f"{org_a}/book", data={"venue": "Hall 3", "date": "2026-11-04", "people": "40"}, timeout=120)
    assert page.status_code == 200, page.text
    return page.text


def set_of(link: str) -> dict:
    base, set_id = re.match(r"(http://[^/]+)/verify#set=([A-Za-z0-9-]+)", link).groups()
    return httpx.get(f"{base}/sets/{set_id}").json()


def status(records: list[dict], env: dict) -> dict:
    os.environ["HELLO_STATE"] = env["HELLO_STATE"]
    from hello import evidence
    from hello.common import settlement_legs

    evidence.STATE = Path(env["HELLO_STATE"])
    return evidence.settlement_status(settlement_legs(records), evidence.public_key("org-a"), evidence.public_key("org-b"))


def links(page: str) -> list[str]:
    return re.findall(r'href="(http://[^"]+/verify#set=[^"]+)"', page)


def test_x402_two_orgs_agree(tmp_path):
    with services(tmp_path) as (env, a, b):
        page = book(a)
        your_copy, confirmation = links(page)
        assert "Settled: both sides report the same payment." in page

        # Hop 1: the person's copy is Org A's sealed record of the request.
        request = set_of(your_copy)["records"]
        assert len(request) == 1
        capsule = json.loads(base64.b64decode(request[0]["capsule"]))
        assert capsule["model_attestation"]["compute_attestation"]["agent_input_digest"]
        assert [a["name"] for a in request[0]["artifacts"]] == ["payload"]

        # Hop 3: Org B's link holds both orgs' legs, and they join across the two keys.
        held = set_of(confirmation)
        report = status(held["records"], env)["settlement"]
        assert report["conforming"]
        assert report["settlements"][0]["payment_state"] == "agreed"
        assert report["settlements"][0]["delivery_state"] == "matched"
        signers = {leg["authenticated_key"] for leg in report["legs"]}
        assert len(signers) == 2


def test_one_flipped_byte_fails(tmp_path):
    from hello import evidence

    with services(tmp_path) as (env, a, b):
        held = set_of(links(book(a))[1])
        assert status(held["records"], env)["settlement"]["conforming"]
        # The same flip as the browser page's button: one bit of the timestamp's first digit.
        record = held["records"][1]
        capsule = bytearray(base64.b64decode(record["capsule"]))
        capsule[capsule.index(b'"timestamp":"') + len(b'"timestamp":"')] ^= 0x01
        tampered = {**record, "capsule": base64.b64encode(bytes(capsule)).decode()}
        # capsulectl refuses a record whose bytes no longer match its signed Capsule ID.
        with pytest.raises(evidence.EvidenceError):
            status([held["records"][0], tampered, *held["records"][2:]], env)


def test_fallback_without_payment(tmp_path):
    with services(tmp_path, payment="none") as (env, a, b):
        page = book(a)
        held = set_of(links(page)[1])
        settlement = status(held["records"], env)["settlement"]["settlements"][0]
        assert settlement["payment_state"] == "terms_only"
        assert settlement["delivery_state"] == "matched"


def test_upgrade_path_add_your_half(tmp_path):
    with services(tmp_path, b_evidence="off") as (env, a, b):
        page = book(a)
        add_half = links(page)[-1]
        assert "Org B runs no evidence" in page
        assert status(set_of(add_half)["records"], env)["settlement"]["settlements"][0]["payment_state"] == "payer_stated"

        out = subprocess.run([sys.executable, str(ROOT / "scripts" / "add_half.py"), add_half],
                             env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        assert out.returncode == 0, out.stderr + out.stdout
        settlement = status(set_of(add_half)["records"], env)["settlement"]["settlements"][0]
        assert settlement["payment_state"] == "agreed"


@pytest.fixture(autouse=True)
def _need_capsulectl():
    from hello import evidence

    try:
        evidence.version()
    except SystemExit:
        pytest.skip("capsulectl not installed: run scripts/install-capsulectl.sh")
