# Provenance
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/license/mit/)
[![Discord](https://img.shields.io/badge/Discord-Join%20us-5865F2?logo=discord&logoColor=white)](https://discord.gg/8Jm4v89VAu)
[![Telegram](https://img.shields.io/badge/Telegram--T.svg?style=social&logo=telegram)](https://t.me/genlayer)
[![Twitter](https://img.shields.io/twitter/url/https/twitter.com/yeagerai.svg?style=social&label=Follow%20%40GenLayer)](https://x.com/GenLayer)

## About
Provenance is a generic **verified-transfer oracle** - given a transaction
hash and the address it's claimed to be from, validators independently
confirm on any EVM chain's own RPC, with no LLM involved, that a genuine
asset movement actually happened, and attest the real `(asset, amount,
destination)` for any other contract to require before acting on it.

It is extracted from [Salvage Arbiter](https://github.com/2TheMoom/salvage-arbiter)'s
drain-transaction verification, built to satisfy exactly this bar from a
GenLayer steward's own review: *"verify the actual asset and balance
movement"* - not just that a transaction exists, but that it genuinely
moved something, from the address it claims to. Provenance generalizes
that logic so any caller supplies their own `rpc_url` instead of one
hardcoded chain, the same caller-supplies-the-source move
[Concord](https://github.com/2TheMoom/concord) made for price checking.
Anything that needs to gate on "did this specific transfer really happen"
- a cross-chain bridge claim, an escrow release, a reimbursement, a
reputation system - can call this instead of trusting a claimant's word or
reimplementing receipt-parsing from scratch.

`verify_transfer(transfer_id, rpc_url, tx_hash, expected_sender)` fetches
the transaction and its receipt from `rpc_url`, requires the transaction
to be real, successful, and genuinely sent from `expected_sender`, then
looks for a genuine ERC-20/721 `Transfer` log naming that address as
sender - not just any log - falling back to the transaction's own native
value if no such log exists. Validators independently recompute the
identical `(asset, amount, destination)` and must agree byte-for-byte
before consensus is reached; a transaction that doesn't exist, didn't
succeed, wasn't from `expected_sender`, or moved nothing at all reverts
cleanly rather than recording a false attestation. Anyone can independently
re-fetch the same `rpc_url` and confirm the same facts themselves. No
value ever moves through this contract - it only attests that value moved
elsewhere.

## Deployment
Deployed on **GenLayer Bradbury Testnet** (chain ID 4221):
- **Contract:** [`0xf7b62423694Ff62804c612D0c7D6676CAd205D23`](https://explorer-bradbury.genlayer.com/address/0xf7b62423694Ff62804c612D0c7D6676CAd205D23)
- Verified via 16 passing direct-mode tests (`python -m pytest tests/direct/`),
  covering native-value transfers, ERC-20 Transfer logs, ERC-721 Transfer
  logs (tokenId from the third indexed topic, not the data field), a
  genuine log correctly taking priority over incidental native value on
  the same transaction, an unrelated log or a real `Approval` event
  (different topic0, never a `Transfer`) correctly falling back to native
  value instead of being mistaken for one, a wrong-sender transaction, a
  nonexistent transaction, a reverted transaction, a transaction that
  moved nothing at all, a duplicate `transfer_id`, a non-`https://`
  `rpc_url`, a malformed `expected_sender`, and independent transfers
  tracked separately.
- The transaction-fact-gathering and Transfer-log decode logic
  (`_fetch_tx_facts`/`_decode_transfer_out`) is the same code, unchanged
  in its core logic, already live-verified in production inside Salvage
  Arbiter's own deployed contract - this project generalizes its RPC
  endpoint from one hardcoded chain to a caller-supplied `rpc_url` and
  exposes it as a standalone, reusable oracle.
- **Known CLI limitation, not a contract defect:** the bare `genlayer
  write --args` path auto-detects any plain 40-hex-character string
  argument as an `Address` calldata type, even when the target parameter
  is declared `str` - confirmed live via `genlayer trace`, which showed
  `AttributeError: 'Address' object has no attribute 'lower'` when citing
  a real transaction through the CLI directly (the same class of bug
  previously documented in Salvage Arbiter's README for a constructor
  argument). `expected_sender` is exactly a 40-hex-character address
  string, so it collides with this heuristic every time. The fix is the
  same as before: call through `genlayer-js`'s own typed `args: [...]`
  (as `deploy/deployScript.ts` already does for this contract's own
  argument-free constructor) instead of the bare CLI flag - not yet run
  live end-to-end for this exact call as of this writing, since doing so
  needs a signing key this session didn't extract; the 16 direct-mode
  tests independently cover every branch of the same logic deterministically.

## What's included
- `contracts/provenance.py` — the Provenance Intelligent Contract
- `tests/direct/test_provenance.py` — direct-mode tests (in-memory, mocked RPC responses)
- **Contract linting** — static analysis to catch common contract issues before deployment
- **CI pipeline** — GitHub Actions workflow for linting and direct tests
- Configuration file template and a deployment script

This is a contract-only primitive, deliberately without a frontend - the
point is to be called by other contracts and integrators, not to be a
product on its own.

## Requirements
- Python >= 3.12
- [GenLayer CLI](https://github.com/genlayerlabs/genlayer-cli) globally installed: `npm install -g genlayer`
- GenLayer Studio (for integration tests and deployment): Install from [Docs](https://docs.genlayer.com/developers/intelligent-contracts/tooling-setup#using-the-genlayer-studio) or use the hosted [GenLayer Studio](https://studio.genlayer.com/)

## Project Structure

```
contracts/              # Python intelligent contracts
  provenance.py            # Provenance
tests/
  direct/                # Fast in-memory tests (no Studio required)
    test_provenance.py
deploy/                  # TypeScript deployment scripts
gltest.config.yaml       # Test runner network configuration
pyproject.toml           # Python/pytest configuration
.github/workflows/       # CI pipeline
```

## Quick Start

### 1. Set up Python environment

```shell
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Lint the contract

```shell
genvm-lint check contracts/provenance.py
```

### 3. Run direct mode tests

```shell
python -m pytest tests/direct/ -v
```

Use `python -m pytest`, not bare `pytest` - depending on your installed
pytest version, running the bare command can fail to put the project
root on `sys.path`, breaking test discovery with
`ModuleNotFoundError: No module named 'tests'`.

### 4. Deploy the contract

1. Choose your network: `genlayer network`
2. Deploy: `genlayer deploy` (runs the script in `/deploy/deployScript.ts`)

## How Provenance Works

1. **`verify_transfer(transfer_id, rpc_url, tx_hash, expected_sender)`** —
   validators fetch `tx_hash` and its receipt from `rpc_url`, require it
   to be real, successful, and sent from `expected_sender`, then decode a
   genuine Transfer log (ERC-20 or ERC-721) naming that address as
   sender, falling back to native value if none exists. Reverts with "No
   genuine asset movement..." if none of that holds - nothing is recorded
   for a bad citation.
2. **`get_transfer(transfer_id)`** — reads back the full attested record:
   creator, the `rpc_url` and `tx_hash` cited, `expected_sender`, and the
   verified `asset` (`"native"` or a contract address), `amount`,
   `destination`, and `block_number`.
3. **`is_verified(transfer_id)`** — a plain existence check for a
   downstream contract that just needs to know whether a citation has
   been attested at all.

### Example: gating a cross-chain claim

```python
# In some other contract, or from a script:
provenance.verify_transfer("claim-1", "https://rpc.example/v1", tx_hash, claimed_sender)
t = provenance.get_transfer("claim-1")
if t["asset"] == expected_token and t["amount"] >= minimum_amount:
    approve_claim()  # only reachable once Provenance attests a real, matching movement
```

## Testing Strategy

| Test Type | Command | Speed | Requires Studio |
|-----------|---------|-------|-----------------|
| **Lint** | `genvm-lint check contracts/provenance.py` | ~250ms | No |
| **Direct** | `python -m pytest tests/direct/ -v` | ~ms/test | No |

## Community
- **[Discord](https://discord.gg/8Jm4v89VAu)**: Discussions, support, and announcements
- **[Telegram](https://t.me/genlayer)**: Informal chats and quick updates

## Documentation
For detailed information, see our [documentation](https://docs.genlayer.com/).

## License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
