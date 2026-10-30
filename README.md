# Cross-org agents over HTTP: hello world

Two agents in two organizations book a venue over plain HTTP. Each organization signs
its own half of the booking with its own key, and a person checks the result in a
browser with nothing to install.

- **Org A** is a booking agent that a person talks to through a web form (`hello/org_a.py`, port 8001).
- **Org B** is a venue's agent that quotes, takes payment and confirms (`hello/org_b.py`, port 8002).

The two orgs exchange plain JSON over HTTP. No shared ledger, relay or SDK sits between
them. Each org seals its records by calling [`capsulectl`](https://github.com/action-state-group/capsule-cli)
(`hello/evidence.py` is the only caller). The records are
[Agent Action Capsules](https://github.com/action-state-group/agent-action-capsule), and the
payment halves are settlement legs as defined in `draft-mih-agent-settlement-records-00`.

## The four hops

1. **Person → agent.** You submit the form. Org A seals its record of your request and
   gives it to you as a verifiable receipt. The receipt is one-sided and labeled as such:
   only Org A signs it. A half signed by the person is roadmap. The two-sided record
   starts at hop 2.
2. **Agent → agent, across orgs.** Org A asks Org B for a quote. Org B seals the terms and
   answers `402 Payment Required`. Org A pays with [x402](https://github.com/x402-foundation/x402)
   on Base Sepolia (testnet USDC) and seals what its wallet saw. Org B seals what its
   facilitator reported. Each org signs only its own legs. The legs join across the two keys:
   `capsulectl settlement status` reports *agreed* only when both sides name the same
   payment, amount and status.
3. **Agent → person.** Org B returns a confirmation link. The page checks every record in
   your browser with the Agent Action Capsule TypeScript verifier (the npm package
   `@action-state-group/agent-action-capsule`, loaded from a CDN). It recomputes each
   Capsule ID, verifies each signature and derives the payment state. The **Flip one byte**
   button changes one byte of a record and shows the check fail.
4. **Upgrade path.** Org B runs as a plain x402 endpoint with no evidence. Org A still
   seals its own half, and its link shows *only the payer has stated the payment: a claim,
   not an agreement*. Later, Org B seals its own half with `capsulectl` and posts it to
   that link. The same record then shows *both sides report the same payment*.

## Run it (about 10 minutes)

You need macOS (Apple silicon) or Linux (x86-64 or arm64), `curl`, and
[`uv`](https://docs.astral.sh/uv/getting-started/installation/). There is no prebuilt
`capsulectl` for Intel Macs: there you also need [Go 1.27+](https://go.dev/dl/), and
`install-capsulectl.sh` prints the `go install` command to run. Installing Go adds time
beyond the 10 minutes.

```bash
git clone https://github.com/action-state-group/cross-org-agents-hello-world.git
```

```bash
cd cross-org-agents-hello-world
```

Install the pinned `capsulectl` release into `./bin`, checked against its SHA256SUMS:

```bash
./scripts/install-capsulectl.sh
```

Create both orgs' keys and capsulectl profiles under `.state/`:

```bash
uv run scripts/setup.py
```

### With the testnet payment

Hop 2 pays from a wallet on Base Sepolia, the Base testnet. **Use a key that holds no
real funds on any chain:** an EVM key controls the same address on mainnet too. Either
create a fresh one:

```bash
uv run scripts/wallet.py
```

or use your own testnet wallet. The script asks for its private key without echoing it.
It can also read the key from the `PAYER_PRIVATE_KEY` environment variable, but prefer the
prompt: `export PAYER_PRIVATE_KEY=…` lands in your shell history. The script won't replace
a wallet you already set up unless you add `--force`.

```bash
uv run scripts/wallet.py --import
```

Either way, the script prints the address Org A pays from. The key stays in
`.state/keys/payer-wallet.json`, which is gitignored and readable only by you. Delete that
file to remove the key.

Get free Base Sepolia USDC for that address at <https://faucet.circle.com> (choose Base
Sepolia). You need no ETH, because the x402 facilitator pays the gas. Each booking costs
0.01 testnet USDC, and both orgs refuse any network but Base Sepolia.

By default, Org B receives at an address `setup.py` generated. To receive at your own
Base Sepolia address instead, set `ORG_B_PAY_TO`. That takes an address only, never a
key. Then start both orgs:

```bash
./scripts/run.sh
```

To receive at your own address, start them this way instead:

```bash
ORG_B_PAY_TO=0xYourBaseSepoliaAddress ./scripts/run.sh
```

Open <http://localhost:8001>, press **Book it**, and follow the links.

### Without a payment (fallback)

If the faucet or the testnet is unavailable, run both orgs with payments off. Hops 1 and 3
work the same way. The payment state then reads *no payment is recorded against these terms*.

```bash
PAYMENT=none ./scripts/run.sh
```

### The upgrade path (hop 4)

Stop `run.sh` and start it again with Org B's evidence off:

```bash
ORG_B_EVIDENCE=off ./scripts/run.sh
```

Book again. The result page shows an "add your half" link, and the record behind it is
one-sided. Then, acting as Org B, add Org B's half. Org B signs only what its own records
show: the amount and transaction from its facilitator's settlement response, and the
confirmation it sent. Hop 4 also works with `PAYMENT=none`; Org B then adds only its
delivery half.

```bash
uv run scripts/add_half.py "http://localhost:8001/verify#set=<id from the link>"
```

Reload the link: both halves are there, under two keys. Each org accepts a half only when
it is signed by the key the other org publishes, so nobody else can add one in its name.

## What is checked, and by whom

| Check | Where |
|---|---|
| Each record's Capsule ID matches its content | the browser page (`verifyClass1`), and `capsulectl` |
| Each record is signed by the key it names | the browser page (`verifyProducerEnvelope`), and `capsulectl` |
| Each leg is signed by the party its role names | the browser page, against the key each org publishes at `/.well-known/evidence-key` |
| Both sides report the same payment | `verifySettlements` in the browser, and `capsulectl settlement status` |
| The x402 payment itself | the facilitator, before Org B confirms |

Wrapped x402 objects (the payment payload and the settlement response) are bound into the
legs by SHA-256. Wallet addresses never enter a leg: the payment is referenced by its
transaction hash.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `PAYMENT` | `x402` | `none` turns the payment off (fallback) |
| `ORG_B_EVIDENCE` | `on` | `off` makes Org B a plain x402 endpoint that seals nothing (hop 4) |
| `ORG_A_URL`, `ORG_B_URL` | `http://localhost:8001`, `:8002` | Where each org is reached. Use `https://` URLs when the orgs run on separate hosts. |
| `PAYER_PRIVATE_KEY` | (unset) | Read once by `scripts/wallet.py --import`: a key that holds no real funds on any chain. Prefer the prompt, which keeps the key out of your shell history. |
| `ORG_B_PAY_TO` | the address `setup.py` generated | The Base Sepolia address Org B receives at (`0x` and 40 hex digits; checked at startup) |
| `X402_FACILITATOR` | `https://x402.org/facilitator` | The testnet facilitator Org B uses |
| `HELLO_STATE` | `./.state` | Keys, capsulectl profiles and evidence sets (gitignored) |
| `CAPSULECTL` | `./bin/capsulectl` | The capsulectl binary |

The network is fixed to Base Sepolia (`eip155:84532`) in `hello/x402_testnet.py`. Both
sides refuse any other network. No key is committed: `setup.py` and `wallet.py` create
them locally under `.state/`.

## Tests

```bash
uv run pytest
```

The tests start both orgs as real HTTP services and run hops 1 to 4, the fallback, the
one-byte flip, and the refusals: a half signed by a third key, and a 402 asking for more
than the quote. When Playwright is installed (CI installs it), they also open the verify
pages in headless Chromium and press **Flip one byte**. The payment goes to
`tests/fake_facilitator.py`, a test double that answers like a facilitator whose
settlement succeeded, so the tests need no funded wallet.

`scripts/selftest.sh` runs this README's walkthrough on a fresh clone with payments off,
times it, and fails past 15 minutes. CI runs it on macOS and Linux.

## Roadmap

- **A person's own signature.** In hop 1, the person holds Org A's one-sided receipt. A
  half signed by the person, for example with a key held in the browser, is not built yet.
- **An evidence skill.** `hello/evidence.py` is the one place that calls `capsulectl`. It is
  shaped so it can be packaged as an agent skill that any agent loads, but it isn't one yet.
- **A leg-building verb in capsulectl.** capsulectl seals and checks settlement legs, but
  it has no verb that builds a leg from x402 objects, so `hello/legs.py` builds the leg JSON.
- **Key discovery.** Each org publishes its key at `/.well-known/evidence-key` on its own
  origin. Binding keys to organizations through DNS, DIDs or certificates is not built.
- **Separate hosts with TLS.** The walkthrough runs both orgs on localhost over HTTP. The
  services take `https://` URLs, but this repository doesn't set up TLS.

## License

Apache-2.0. See [LICENSE](LICENSE).
