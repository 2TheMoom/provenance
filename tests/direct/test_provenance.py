"""Direct-mode tests for the Provenance contract."""

import json

from tests.direct.conftest import to_hex

CONTRACT = "contracts/provenance.py"
RPC_URL = "https://rpc.example/v1"
TX_HASH = "0x" + "ab" * 32

SENDER = "0x" + "11" * 20
DESTINATION = "0x" + "cd" * 20
TOKEN = "0x" + "ee" * 20

# Must match Provenance.TRANSFER_TOPIC exactly.
TRANSFER_TOPIC = "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def _pad_topic(address: str) -> str:
    return "0x" + "00" * 12 + address.lower().removeprefix("0x")


def _transfer_log(
    from_address: str = SENDER,
    to_address: str = DESTINATION,
    token: str = TOKEN,
    amount: int = 1_000_000,
) -> dict:
    return {
        "address": token,
        "topics": ["0x" + TRANSFER_TOPIC, _pad_topic(from_address), _pad_topic(to_address)],
        "data": hex(amount),
    }


def _nft_transfer_log(
    from_address: str = SENDER,
    to_address: str = DESTINATION,
    token: str = TOKEN,
    token_id: int = 42,
) -> dict:
    """An ERC-721 Transfer log - tokenId as the 3rd indexed topic, no data."""
    return {
        "address": token,
        "topics": [
            "0x" + TRANSFER_TOPIC,
            _pad_topic(from_address),
            _pad_topic(to_address),
            hex(token_id),
        ],
        "data": "0x",
    }


def _unrelated_log() -> dict:
    return {"address": TOKEN, "topics": [], "data": "0x"}


def _approval_log(owner: str = SENDER) -> dict:
    approval_topic = "8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"
    return {
        "address": TOKEN,
        "topics": ["0x" + approval_topic, _pad_topic(owner), _pad_topic(DESTINATION)],
        "data": hex(999),
    }


def _mock_tx(
    vm,
    from_address: str = SENDER,
    found: bool = True,
    to_address: str = DESTINATION,
    value_wei: int = 1,
    status_success: bool = True,
    logs: list | None = None,
):
    tx_result = None
    if found:
        tx_result = {
            "from": from_address,
            "to": to_address,
            "value": hex(value_wei),
            "blockNumber": hex(20738308),
            "hash": TX_HASH,
        }
    vm.mock_web(
        r"call=tx",
        {"method": "POST", "status": 200, "body": json.dumps({"jsonrpc": "2.0", "id": 1, "result": tx_result})},
    )

    receipt_result = None
    if found:
        receipt_result = {
            "status": "0x1" if status_success else "0x0",
            "logs": logs if logs is not None else [],
        }
    vm.mock_web(
        r"call=receipt",
        {"method": "POST", "status": 200, "body": json.dumps({"jsonrpc": "2.0", "id": 1, "result": receipt_result})},
    )


def test_verify_transfer_native_value(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    _mock_tx(direct_vm, value_wei=5_000_000_000_000_000)

    contract.verify_transfer("t1", RPC_URL, TX_HASH, SENDER)

    t = contract.get_transfer("t1")
    assert t["creator"] == to_hex(direct_alice)
    assert t["rpc_url"] == RPC_URL
    assert t["tx_hash"] == TX_HASH
    assert t["expected_sender"] == SENDER.removeprefix("0x").lower()
    assert t["asset"] == "native"
    assert t["amount"] == 5_000_000_000_000_000
    assert t["destination"] == DESTINATION.removeprefix("0x").lower()
    assert t["block_number"] == 20738308
    assert contract.is_verified("t1") is True


def test_verify_transfer_erc20_log(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    _mock_tx(direct_vm, value_wei=0, logs=[_transfer_log(amount=250_000)])

    contract.verify_transfer("t1", RPC_URL, TX_HASH, SENDER)

    t = contract.get_transfer("t1")
    assert t["asset"] == TOKEN.removeprefix("0x").lower()
    assert t["amount"] == 250_000
    assert t["destination"] == DESTINATION.removeprefix("0x").lower()


def test_verify_transfer_erc721_log(direct_vm, direct_deploy, direct_alice):
    """An NFT transfer's amount is the tokenId, taken from the 3rd indexed
    topic, not the data field (which is empty for ERC-721)."""
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    _mock_tx(direct_vm, value_wei=0, logs=[_nft_transfer_log(token_id=42)])

    contract.verify_transfer("t1", RPC_URL, TX_HASH, SENDER)

    t = contract.get_transfer("t1")
    assert t["asset"] == TOKEN.removeprefix("0x").lower()
    assert t["amount"] == 42
    assert t["destination"] == DESTINATION.removeprefix("0x").lower()


def test_verify_transfer_prefers_log_over_incidental_native_value(direct_vm, direct_deploy, direct_alice):
    """A token drain can ride along a tx with nonzero native value (e.g. gas
    refund quirks or a bundled call) - the genuine Transfer log must win,
    not be shadowed by treating the tx as a plain native transfer."""
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    _mock_tx(direct_vm, value_wei=1, logs=[_transfer_log(amount=250_000)])

    contract.verify_transfer("t1", RPC_URL, TX_HASH, SENDER)

    t = contract.get_transfer("t1")
    assert t["asset"] == TOKEN.removeprefix("0x").lower()
    assert t["amount"] == 250_000


def test_verify_transfer_ignores_unrelated_log_falls_back_to_native(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    _mock_tx(direct_vm, value_wei=100, logs=[_unrelated_log()])

    contract.verify_transfer("t1", RPC_URL, TX_HASH, SENDER)

    assert contract.get_transfer("t1")["asset"] == "native"


def test_verify_transfer_ignores_approval_log_falls_back_to_native(direct_vm, direct_deploy, direct_alice):
    """An Approval event (different topic0) is a real log from the sender
    but never moves anything - must not be mistaken for a Transfer."""
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    _mock_tx(direct_vm, value_wei=100, logs=[_approval_log()])

    contract.verify_transfer("t1", RPC_URL, TX_HASH, SENDER)

    assert contract.get_transfer("t1")["asset"] == "native"


def test_verify_transfer_wrong_sender_fails(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    other = "0x" + "99" * 20
    _mock_tx(direct_vm, from_address=other, value_wei=100)

    with direct_vm.expect_revert("No genuine asset movement"):
        contract.verify_transfer("t1", RPC_URL, TX_HASH, SENDER)

    assert contract.is_verified("t1") is False


def test_verify_transfer_tx_not_found_fails(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    _mock_tx(direct_vm, found=False)

    with direct_vm.expect_revert("No genuine asset movement"):
        contract.verify_transfer("t1", RPC_URL, TX_HASH, SENDER)


def test_verify_transfer_reverted_tx_fails(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    _mock_tx(direct_vm, value_wei=100, status_success=False)

    with direct_vm.expect_revert("No genuine asset movement"):
        contract.verify_transfer("t1", RPC_URL, TX_HASH, SENDER)


def test_verify_transfer_no_value_no_log_fails(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    _mock_tx(direct_vm, value_wei=0, logs=[])

    with direct_vm.expect_revert("No genuine asset movement"):
        contract.verify_transfer("t1", RPC_URL, TX_HASH, SENDER)


def test_verify_transfer_duplicate_id_fails(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    _mock_tx(direct_vm, value_wei=100)
    contract.verify_transfer("t1", RPC_URL, TX_HASH, SENDER)

    with direct_vm.expect_revert("already verified"):
        contract.verify_transfer("t1", RPC_URL, TX_HASH, SENDER)


def test_verify_transfer_non_https_rpc_fails(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice

    with direct_vm.expect_revert("must start with https://"):
        contract.verify_transfer("t1", "http://rpc.example/v1", TX_HASH, SENDER)


def test_verify_transfer_invalid_sender_format_fails(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice

    with direct_vm.expect_revert("20-byte hex address"):
        contract.verify_transfer("t1", RPC_URL, TX_HASH, "not-an-address")


def test_get_transfer_unknown_fails(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    with direct_vm.expect_revert("not found"):
        contract.get_transfer("nonexistent")


def test_is_verified_unknown_returns_false(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    assert contract.is_verified("nonexistent") is False


def test_independent_transfers_track_separately(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    _mock_tx(direct_vm, value_wei=100)
    contract.verify_transfer("t1", RPC_URL, TX_HASH, SENDER)

    direct_vm.clear_mocks()
    _mock_tx(direct_vm, value_wei=0, logs=[_transfer_log(amount=999)])
    contract.verify_transfer("t2", RPC_URL, TX_HASH, SENDER)

    assert contract.get_transfer("t1")["asset"] == "native"
    assert contract.get_transfer("t2")["amount"] == 999
