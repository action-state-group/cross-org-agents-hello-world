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
def services(tmp_path: Path, *, payment: str = "x402", b_evidence: str = "on", extra_env: dict | None = None):
    a, b, f = _port(), _port(), _port()
    env = {
        **os.environ,
        "HELLO_STATE": str(tmp_path),
        "ORG_A_URL": f"http://127.0.0.1:{a}",
        "ORG_B_URL": f"http://127.0.0.1:{b}",
        "X402_FACILITATOR": f"http://127.0.0.1:{f}",
        "PAYMENT": payment,
        "ORG_B_EVIDENCE": b_evidence,
        **(extra_env or {}),
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
    with services(tmp_path) as (env, a, b):
        held = set_of(links(book(a))[1])
        assert status(held["records"], env)["settlement"]["conforming"]
        # The same flip as the browser page's button: the first record, one bit of its
        # timestamp's first digit.
        record = held["records"][0]
        capsule = bytearray(base64.b64decode(record["capsule"]))
        capsule[capsule.index(b'"timestamp":"') + len(b'"timestamp":"')] ^= 0x01
        tampered = {**record, "capsule": base64.b64encode(bytes(capsule)).decode()}
        # capsulectl reports the set non-conforming: the record's bytes no longer match
        # the Capsule ID its signature covers.
        report = status([tampered, *held["records"][1:]], env)["settlement"]
        assert not report["conforming"]
        assert {f["code"] for f in report["failures"]} & {"capsule_invalid", "envelope_invalid"}, report["failures"]


def test_fallback_without_payment(tmp_path):
    with services(tmp_path, payment="none") as (env, a, b):
        page = book(a)
        held = set_of(links(page)[1])
        settlement = status(held["records"], env)["settlement"]["settlements"][0]
        assert settlement["payment_state"] == "terms_only"
        assert settlement["delivery_state"] == "matched"


def test_upgrade_path_without_payment(tmp_path):
    with services(tmp_path, payment="none", b_evidence="off") as (env, a, b):
        add_half = links(book(a))[-1]
        out = subprocess.run([sys.executable, str(ROOT / "scripts" / "add_half.py"), add_half],
                             env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        assert out.returncode == 0, out.stderr + out.stdout
        settlement = status(set_of(add_half)["records"], env)["settlement"]["settlements"][0]
        assert settlement["payment_state"] == "terms_only"
        assert settlement["delivery_state"] == "matched"


def test_half_under_a_third_key_is_refused(tmp_path):
    with services(tmp_path) as (env, a, b):
        confirmation = links(book(a))[1]
        held = set_of(confirmation)
        from hello import evidence
        from hello.common import capsule_of

        evidence.STATE = tmp_path  # Org C's profile lives in this test's state only
        # Org C copies Org B's delivery leg and seals it under its own key.
        seed = tmp_path / "keys" / "org-c.seed"
        pub = evidence.capsulectl("key", "generate", "--output", str(seed))["public_key"]
        evidence.capsulectl("profile", "create", "--name", "org-c", "--type", "sqlite", "--sqlite-path", str(tmp_path / "c.db"),
                            "--namespace", "c", "--log-id", "c-log", "--signing-key-file", str(seed), "--trusted-key", pub,
                            "--checkpoint-signing-key-file", str(seed), "--checkpoint-trusted-key", pub)
        delivered = next(capsule_of(r)["settlement"] for r in held["records"] if capsule_of(r)["settlement"]["leg"] == "delivered")
        forged = evidence.seal("org-c", "forged", "venue.org-b.example", settlement={**delivered, "observed_at": evidence.now()})
        answer = httpx.post(confirmation.split("/verify")[0] + "/sets/" + confirmation.split("set=")[1] + "/legs", json={"record": forged})
        assert answer.status_code == 400
        codes = {f["code"] for f in answer.json()["detail"]["report"]["failures"]}
        assert "sealer_not_authorized_for_role" in codes, codes
        assert len(set_of(confirmation)["records"]) == len(held["records"])


@pytest.mark.parametrize("header", ["W10=", "eyJhY2NlcHRlZCI6bnVsbH0=", "not base64!"])  # [], {"accepted":null}
def test_malformed_payment_signature_gets_402(tmp_path, header):
    with services(tmp_path) as (env, a, b):
        quote = httpx.post(f"{b}/quote", json={"venue": "Hall 3", "date": "2026-11-04", "people": 1, "payer_url": a}).json()
        answer = httpx.post(quote["book_url"], headers={"PAYMENT-SIGNATURE": header})
        assert answer.status_code == 402, answer.text


def test_payer_refuses_a_402_above_the_quote():
    import asyncio

    from hello import legs
    from hello.x402_testnet import ASSET, NETWORK, pay, payment_required

    required = payment_required("http://localhost/book/x", "0x000000000000000000000000000000000000dEaD")
    with pytest.raises(ValueError, match="different amount"):
        asyncio.run(pay(required, "0x" + "11" * 32, legs.amount("1", NETWORK, ASSET)))


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


def test_own_wallet_import_and_receiving_address(tmp_path):
    from eth_account import Account

    mine = Account.create()
    env = {**os.environ, "HELLO_STATE": str(tmp_path), "PAYER_PRIVATE_KEY": mine.key.hex()}
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "wallet.py"), "--import"],
                         env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert out.returncode == 0 and mine.address in out.stdout, out.stderr
    assert mine.key.hex() not in out.stdout  # the key is never printed
    held = tmp_path / "keys" / "payer-wallet.json"
    assert json.loads(held.read_text())["address"] == mine.address
    assert oct(held.stat().st_mode & 0o777) == "0o600"

    def wallet(*args, key):
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "wallet.py"), "--import", *args],
                              env={**env, "PAYER_PRIVATE_KEY": key}, capture_output=True, text=True, stdin=subprocess.DEVNULL)

    other = Account.create()
    kept = wallet(key=other.key.hex())  # an existing wallet is not replaced without --force
    assert kept.returncode != 0 and mine.address in kept.stderr
    assert json.loads(held.read_text())["address"] == mine.address
    bad = wallet("--force", key="not-a-key")
    assert bad.returncode != 0 and "not a valid private key" in bad.stderr
    assert wallet("--force", key=other.key.hex()).returncode == 0
    assert json.loads(held.read_text())["address"] == other.address
    assert oct(held.stat().st_mode & 0o777) == "0o600"

    typo = subprocess.run([sys.executable, "-c", "import hello.org_b"], cwd=ROOT, capture_output=True, text=True,
                          env={**env, "ORG_B_PAY_TO": "0x1234"}, stdin=subprocess.DEVNULL)
    assert typo.returncode != 0 and "ORG_B_PAY_TO must be" in typo.stderr

    receiver = Account.create().address
    with services(tmp_path / "run", extra_env={"ORG_B_PAY_TO": receiver}) as (env, a, b):
        quote = httpx.post(f"{b}/quote", json={"venue": "Hall 3", "date": "2026-11-04", "people": 1, "payer_url": a}).json()
        required = httpx.post(quote["book_url"])
        assert required.status_code == 402
        assert required.json()["accepts"][0]["payTo"] == receiver
