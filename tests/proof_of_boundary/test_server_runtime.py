"""Standalone authentication and explicit STG adapter boundary tests."""

import importlib

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from framework.schemas.trust_level import TrustLevel


def test_external_token_never_promotes_to_internal() -> None:
    from src.api.server import _resolve_standalone_trust

    trust = _resolve_standalone_trust(
        TrustLevel.ANONYMOUS,
        "Bearer external-token",
        "external-token",
        "runner-token",
    )
    assert trust is TrustLevel.VERIFIED_EXTERNAL


def test_runner_token_maps_to_internal() -> None:
    from src.api.server import _resolve_standalone_trust

    trust = _resolve_standalone_trust(
        TrustLevel.ANONYMOUS,
        "Bearer runner-token",
        "external-token",
        "runner-token",
    )
    assert trust is TrustLevel.INTERNAL


def test_invalid_configured_token_is_rejected() -> None:
    from src.api.server import _resolve_standalone_trust

    with pytest.raises(HTTPException) as exc_info:
        _resolve_standalone_trust(
            TrustLevel.ANONYMOUS,
            "Bearer wrong",
            "external-token",
            "runner-token",
        )
    assert exc_info.value.status_code == 401


def test_stage5_mock_mode_runs_full_graph(monkeypatch) -> None:
    monkeypatch.setenv("STG_MOCK_MODE", "true")
    monkeypatch.setenv("INVOKE_AUTH_TOKEN", "external-token")

    import src.api.server as server

    server = importlib.reload(server)
    response = TestClient(server.app).post(
        "/invoke",
        headers={"Authorization": "Bearer external-token"},
        json={
            "input": "Check host health",
            "session_id": "stg-test",
            "input_context": {
                "query_intent": ["cpu", "memory", "disk"],
                "target_context": "baremetal",
                "target_id": "stg-host",
                "output_format": "json",
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["node_history"] == [
        "InitializeNode",
        "PreProcessNode",
        "MainNode",
        "PostProcessNode",
        "FinalizeNode",
    ]
