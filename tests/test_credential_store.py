from __future__ import annotations

import json
import os

import pytest

from fusion_video_pipeline.credential_store import (
    ApiCredentialStore,
    CredentialStoreError,
)


def test_missing_saved_api_key_is_reported(tmp_path):
    store = ApiCredentialStore(tmp_path / "api-credentials.json")

    assert not store.available()
    with pytest.raises(CredentialStoreError, match="尚未保存"):
        store.load()


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI is only available on Windows")
def test_api_key_round_trips_through_windows_dpapi_without_plaintext(tmp_path):
    store = ApiCredentialStore(tmp_path / "api-credentials.json")
    secret = "test-only-sensitive-value"

    store.save(secret)

    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload["protection"] == "windows-dpapi-current-user"
    assert secret not in store.path.read_text(encoding="utf-8")
    assert store.load() == secret
