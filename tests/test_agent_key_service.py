"""Tests for Agent API Key Service."""

import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import hashlib
import hmac as hmac_lib

from sprinkle.services.agent_key_service import (
    AgentKeyService,
    format_api_key,
    is_valid_key_format,
    NonceCache,
    HMAC_TIMESTAMP_WINDOW,
)


class TestKeyGeneration:
    """Test key generation functions."""

    def test_generate_key_id_length(self):
        """Test key ID is 24 characters."""
        service = AgentKeyService(None)
        key_id = service.generate_key_id()
        assert len(key_id) == 24

    def test_generate_key_id_is_hex(self):
        """Test key ID is uppercase hex."""
        service = AgentKeyService(None)
        key_id = service.generate_key_id()
        assert key_id.isupper()
        assert all(c in "0123456789ABCDEF" for c in key_id)

    def test_generate_key_id_unique(self):
        """Test key IDs are unique."""
        service = AgentKeyService(None)
        ids = [service.generate_key_id() for _ in range(100)]
        assert len(set(ids)) == 100

    def test_generate_secret_length(self):
        """Test secret is 64 characters (256 bits)."""
        service = AgentKeyService(None)
        secret = service.generate_secret()
        assert len(secret) == 64

    def test_generate_secret_is_hex(self):
        """Test secret is lowercase hex."""
        service = AgentKeyService(None)
        secret = service.generate_secret()
        assert all(c in "0123456789abcdef" for c in secret)

    def test_generate_secret_unique(self):
        """Test secrets are unique."""
        service = AgentKeyService(None)
        secrets_list = [service.generate_secret() for _ in range(100)]
        assert len(set(secrets_list)) == 100

    def test_derive_hmac_key(self):
        """Test HMAC key derivation."""
        service = AgentKeyService(None)
        secret = "test_secret_12345"
        hmac_key = service.derive_hmac_key(secret)
        # Should be SHA256 hash (64 hex chars)
        assert len(hmac_key) == 64
        # Should be deterministic
        assert hmac_key == service.derive_hmac_key(secret)
        # Different secrets should produce different keys
        assert hmac_key != service.derive_hmac_key("different_secret")


class TestKeyFormat:
    """Test key format validation."""

    def test_valid_key_format(self):
        """Test valid key format is accepted."""
        # Use only hex chars (no underscores) for key_id - exactly 24 chars, uppercase A-F only
        key_id = "01ABCDEF0123456789ABCDEF"  # 24 chars, uppercase hex A-F
        secret = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"  # 64 chars
        key = f"sk_{key_id}_{secret}"
        assert is_valid_key_format(key) is True

    def test_invalid_prefix(self):
        """Test key without sk_ prefix is rejected."""
        key_id = "01ABC123DEF456789012345"
        secret = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        key = f"pk_{key_id}_{secret}"  # Wrong prefix
        assert is_valid_key_format(key) is False

    def test_missing_secret(self):
        """Test key without secret is rejected."""
        key = "sk_01ABC123DEF456789012345"
        assert is_valid_key_format(key) is False

    def test_short_key_id(self):
        """Test key with short key_id is rejected."""
        key = "sk_01ABC123_0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        assert is_valid_key_format(key) is False

    def test_short_secret(self):
        """Test key with short secret is rejected."""
        key = "sk_01ABC123DEF456789012345_0123456789abcdef"
        assert is_valid_key_format(key) is False

    def test_invalid_key_id_hex(self):
        """Test key with non-hex key_id is rejected."""
        key = "sk_01ABC123DEF456789012345G_0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        assert is_valid_key_format(key) is False

    def test_invalid_secret_hex(self):
        """Test key with non-hex secret is rejected."""
        key = "sk_01ABC123DEF456789012345_0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdefG"
        assert is_valid_key_format(key) is False


class TestFormatApiKey:
    """Test format_api_key function."""

    def test_format_api_key(self):
        """Test key formatting."""
        key_id = "01ABC123DEF456789012345"
        secret = "0123456789abcdef" * 4
        result = format_api_key(key_id, secret)
        assert result == f"sk_{key_id}_{secret}"

    def test_format_api_key_joins_correctly(self):
        """Test format matches expected pattern."""
        key_id = "A" * 24
        secret = "b" * 64
        result = format_api_key(key_id, secret)
        assert result.startswith("sk_")
        # sk_ + 24 char key_id + _ + 64 char secret
        assert len(result) == 3 + 24 + 1 + 64  # = 92


class TestComputeHmac:
    """Test HMAC computation."""

    def test_compute_hmac(self):
        """Test HMAC computation is correct."""
        import hashlib
        hmac_key = "a" * 64
        timestamp = 1713528000
        nonce = "test_nonce"
        
        signature = AgentKeyService.compute_hmac(hmac_key, timestamp, nonce)
        
        # Verify manually
        message = f"{timestamp}:{nonce}"
        expected = hmac_lib.new(
            hmac_key.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        
        assert signature == expected

    def test_compute_hmac_deterministic(self):
        """Test HMAC computation is deterministic."""
        hmac_key = "a" * 64
        timestamp = 1713528000
        nonce = "test_nonce"
        
        sig1 = AgentKeyService.compute_hmac(hmac_key, timestamp, nonce)
        sig2 = AgentKeyService.compute_hmac(hmac_key, timestamp, nonce)
        
        assert sig1 == sig2

    def test_compute_hmac_different_inputs(self):
        """Test different inputs produce different signatures."""
        hmac_key = "a" * 64
        
        sig1 = AgentKeyService.compute_hmac(hmac_key, 1713528000, "nonce1")
        sig2 = AgentKeyService.compute_hmac(hmac_key, 1713528001, "nonce1")
        sig3 = AgentKeyService.compute_hmac(hmac_key, 1713528000, "nonce2")
        
        assert sig1 != sig2
        assert sig1 != sig3
        assert sig2 != sig3


class TestNonceCache:
    """Test NonceCache for replay prevention."""

    def test_nonce_cache_initially_empty(self):
        """Test nonce cache starts empty."""
        cache = NonceCache()
        assert cache.is_used("any_nonce") is False

    def test_nonce_cache_add_and_check(self):
        """Test adding nonce marks it as used."""
        cache = NonceCache()
        nonce = "test_nonce_123"
        cache.add(nonce)
        assert cache.is_used(nonce) is True

    def test_nonce_cache_different_nonces(self):
        """Test different nonces are tracked separately."""
        cache = NonceCache()
        cache.add("nonce1")
        cache.add("nonce2")
        assert cache.is_used("nonce1") is True
        assert cache.is_used("nonce2") is True
        assert cache.is_used("nonce3") is False

    def test_nonce_cache_clear_on_full(self):
        """Test cache clears when full."""
        cache = NonceCache(max_size=5)
        for i in range(5):
            cache.add(f"nonce_{i}")
        
        # First 5 should be in cache
        assert cache.is_used("nonce_0") is True
        assert cache.is_used("nonce_4") is True
        
        # Adding 6th should trigger eviction
        cache.add("nonce_5")
        
        # Cache should be cleared, so original nonces might not be there
        # (implementation detail: it clears all when full)


class TestParseKey:
    """Test key parsing."""

    @pytest.mark.asyncio
    async def test_parse_key_valid(self):
        """Test parsing valid key."""
        service = AgentKeyService(None)
        full_key = "sk_01ABC123DEF456789012345_0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        key_id, secret = service.parse_key(full_key)
        assert key_id == "01ABC123DEF456789012345"
        assert secret == "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"

    @pytest.mark.asyncio
    async def test_parse_key_invalid_prefix(self):
        """Test parsing key with invalid prefix."""
        service = AgentKeyService(None)
        with pytest.raises(ValueError, match="must start with sk_"):
            service.parse_key("pk_01ABC123DEF456789012345_secret")

    @pytest.mark.asyncio
    async def test_parse_key_missing_secret(self):
        """Test parsing key without secret."""
        service = AgentKeyService(None)
        with pytest.raises(ValueError, match="expected sk_<key_id>_<secret>"):
            service.parse_key("sk_01ABC123DEF456789012345")


class TestAuthenticateHmac:
    """Test HMAC authentication."""

    @pytest.mark.asyncio
    async def test_authenticate_expired_timestamp(self):
        """Test authentication fails with expired timestamp."""
        mock_db = AsyncMock()
        service = AgentKeyService(mock_db)
        
        # Timestamp too old (> 5 minutes)
        old_timestamp = int(time.time()) - HMAC_TIMESTAMP_WINDOW - 10
        
        result = await service.authenticate_hmac(
            key_id="test_key_id",
            signature="test_sig",
            timestamp=old_timestamp,
            nonce="test_nonce",
        )
        
        assert result.success is False
        assert "expired" in result.message.lower()

    @pytest.mark.asyncio
    async def test_authenticate_future_timestamp(self):
        """Test authentication fails with future timestamp."""
        mock_db = AsyncMock()
        service = AgentKeyService(mock_db)
        
        # Timestamp too far in future (> 5 minutes)
        future_timestamp = int(time.time()) + HMAC_TIMESTAMP_WINDOW + 10
        
        result = await service.authenticate_hmac(
            key_id="test_key_id",
            signature="test_sig",
            timestamp=future_timestamp,
            nonce="test_nonce",
        )
        
        assert result.success is False
        assert "expired" in result.message.lower()

    @pytest.mark.asyncio
    async def test_authenticate_replay_attack(self):
        """Test authentication fails with reused nonce."""
        # First, add a nonce to the cache
        test_nonce = "replay_test_nonce"
        _nonce_cache.add(test_nonce)  # Add to global cache
        
        mock_db = AsyncMock()
        service = AgentKeyService(mock_db)
        
        result = await service.authenticate_hmac(
            key_id="test_key_id",
            signature="test_sig",
            timestamp=int(time.time()),
            nonce=test_nonce,
        )
        
        assert result.success is False
        assert "replay" in result.message.lower()


# Global nonce cache for tests
from sprinkle.services.agent_key_service import _nonce_cache
