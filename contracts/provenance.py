# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from genlayer import *

TRANSFER_TOPIC = "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

RPC_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


@allow_storage
@dataclass
class Transfer:
    creator: str
    created_at: u256
    rpc_url: str
    tx_hash: str
    expected_sender: str
    asset: str  # "native", or the ERC-20/721 contract address (lowercase hex, no 0x)
    amount: u256
    destination: str
    block_number: u256


def _decode_transfer_out(logs: list, sender_hex: str) -> dict | None:
    """Genuine ERC-20/721 Transfer log naming sender_hex as sender, or None
    if no such log exists. Handles both ERC-20 (amount in `data`) and
    ERC-721 (tokenId as the third indexed topic)."""
    for log in logs:
        t = log.get("topics") or []
        if len(t) < 3 or str(t[0]).lower().removeprefix("0x") != TRANSFER_TOPIC:
            continue
        if str(t[1]).lower().removeprefix("0x")[-40:] != sender_hex:
            continue
        dst = str(t[2]).lower().removeprefix("0x")[-40:]
        asset = str(log.get("address") or "").lower().removeprefix("0x")
        if len(t) >= 4:  # ERC-721: tokenId is the 3rd indexed topic
            return {"asset": asset, "amount": int(str(t[3]), 16), "destination": dst}
        d = log.get("data") or "0x"
        amt = int(str(d), 16) if d and str(d) != "0x" else 0
        return {"asset": asset, "amount": amt, "destination": dst}
    return None


class Provenance(gl.Contract):
    """A generic verified-transfer oracle: given a transaction hash and the
    address it's claimed to be from, validators independently confirm on
    any EVM chain's own RPC - no LLM - that a genuine asset movement
    actually happened, and attest the real (asset, amount, destination) for
    any other contract to require before acting on it.

    Extracted from Salvage Arbiter's drain-transaction verification (built
    to satisfy exactly this bar: "verify the actual asset and balance
    movement" was the steward feedback that shaped it), generalized so any
    caller supplies their own `rpc_url` instead of one hardcoded chain -
    the same caller-supplies-the-source move Concord made for price
    checking. Anything that needs to gate on "did this specific transfer
    really happen" - a cross-chain bridge claim, an escrow release, a
    reimbursement, a reputation system - can call this instead of trusting
    a claimant's word or rebuilding receipt-parsing from scratch.

    verify_transfer(transfer_id, rpc_url, tx_hash, expected_sender)
    fetches the transaction and its receipt from rpc_url, requires the
    transaction to be real, successful, and genuinely sent from
    expected_sender, then looks for an ERC-20/721 Transfer log naming that
    address as sender - not just any log - falling back to the
    transaction's own native value if no such log exists. Validators
    independently recompute the identical (asset, amount, destination) and
    must agree byte-for-byte before consensus is reached; a transaction
    that doesn't exist, didn't succeed, wasn't from expected_sender, or
    moved nothing at all reverts cleanly rather than recording a false
    attestation. Anyone can independently re-fetch the same rpc_url and
    confirm the same facts themselves. No value ever moves through this
    contract - it only attests that value moved elsewhere.
    """

    transfers: TreeMap[str, Transfer]

    def __init__(self):
        pass

    def _now(self) -> int:
        return int(datetime.now(timezone.utc).timestamp())

    def _fetch_tx_facts(self, rpc_url: str, tx_hash: str, sender_hex: str) -> dict | None:
        tx_body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "eth_getTransactionByHash", "params": [tx_hash]}
        )
        receipt_body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "eth_getTransactionReceipt", "params": [tx_hash]}
        )
        try:
            # Query suffix is inert on a real RPC (POST dispatch is by JSON
            # body, not URL); it only exists so direct-mode tests can mock
            # each call distinctly - same trick proven in Salvage Arbiter.
            r1 = gl.nondet.web.post(rpc_url + "?call=tx", body=tx_body, headers=RPC_HEADERS)
            tx = json.loads((r1.body or b"").decode("utf-8")).get("result")
            if not tx or not tx.get("from"):
                return None
            if str(tx["from"]).lower().removeprefix("0x") != sender_hex:
                return None

            r2 = gl.nondet.web.post(rpc_url + "?call=receipt", body=receipt_body, headers=RPC_HEADERS)
            rc = json.loads((r2.body or b"").decode("utf-8")).get("result")
            if not rc:
                return None
            if rc.get("status") == "0x0":
                return None

            block_hex = tx.get("blockNumber")
            block_number = int(block_hex, 16) if block_hex else 0
            logs = rc.get("logs") or []

            transfer = _decode_transfer_out(logs, sender_hex)
            if transfer is not None:
                return {**transfer, "block_number": block_number}

            value_hex = tx.get("value")
            value_wei = int(value_hex, 16) if value_hex else 0
            if value_wei > 0:
                to_addr = str(tx.get("to") or "").lower().removeprefix("0x")
                return {
                    "asset": "native",
                    "amount": value_wei,
                    "destination": to_addr,
                    "block_number": block_number,
                }
            return None
        except (ValueError, AttributeError, TypeError, KeyError):
            return None

    @gl.public.write
    def verify_transfer(
        self, transfer_id: str, rpc_url: str, tx_hash: str, expected_sender: str
    ) -> None:
        if transfer_id in self.transfers:
            raise gl.vm.UserError(f"Transfer '{transfer_id}' already verified")
        if not rpc_url.startswith("https://"):
            raise gl.vm.UserError("rpc_url must start with https://")

        sender_hex = expected_sender.lower().removeprefix("0x")
        if len(sender_hex) != 40:
            raise gl.vm.UserError("expected_sender must be a 20-byte hex address")

        def leader_fn() -> dict:
            facts = self._fetch_tx_facts(rpc_url, tx_hash, sender_hex)
            if facts is None:
                return {"found": False, "asset": "", "amount": 0, "destination": "", "block_number": 0}
            return {"found": True, **facts}

        def validator_fn(leaders_res) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                return False
            mine = leader_fn()
            theirs = leaders_res.calldata
            return (
                mine["found"] == theirs["found"]
                and mine["asset"] == theirs["asset"]
                and mine["amount"] == theirs["amount"]
                and mine["destination"] == theirs["destination"]
            )

        result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

        if not result["found"]:
            raise gl.vm.UserError(
                f"No genuine asset movement from 0x{sender_hex} found in transaction {tx_hash}"
            )

        self.transfers[transfer_id] = Transfer(
            creator=gl.message.sender_address.as_hex,
            created_at=self._now(),
            rpc_url=rpc_url,
            tx_hash=tx_hash,
            expected_sender=sender_hex,
            asset=result["asset"],
            amount=result["amount"],
            destination=result["destination"],
            block_number=result["block_number"],
        )

    @gl.public.view
    def get_transfer(self, transfer_id: str) -> dict:
        if transfer_id not in self.transfers:
            raise gl.vm.UserError(f"Transfer '{transfer_id}' not found")
        t = self.transfers[transfer_id]
        return {
            "creator": t.creator,
            "created_at": t.created_at,
            "rpc_url": t.rpc_url,
            "tx_hash": t.tx_hash,
            "expected_sender": t.expected_sender,
            "asset": t.asset,
            "amount": t.amount,
            "destination": t.destination,
            "block_number": t.block_number,
        }

    @gl.public.view
    def is_verified(self, transfer_id: str) -> bool:
        return transfer_id in self.transfers
