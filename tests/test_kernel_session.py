"""Tests for Session Module (kernel/session.py)."""

import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass

from sprinkle.kernel.session import (
    SessionState,
    SessionData,
    ConnectionPool,
    SessionManager,
    RedisConfig,
)


# ============================================================================
# Test SessionState Enum
# ============================================================================

class TestSessionState:
    """Tests for SessionState enum."""

    def test_session_state_values(self):
        """Test all SessionState enum values."""
        assert SessionState.CONNECTING.value == "connecting"
        assert SessionState.CONNECTED.value == "connected"
        assert SessionState.AUTHENTICATED.value == "authenticated"
        assert SessionState.DISCONNECTING.value == "disconnecting"
        assert SessionState.DISCONNECTED.value == "disconnected"
        assert SessionState.RECONNECTING.value == "reconnecting"

    def test_session_state_count(self):
        """Test that we have expected number of states."""
        assert len(SessionState) == 6


# ============================================================================
# Test SessionData Dataclass
# ============================================================================

class TestSessionData:
    """Tests for SessionData dataclass."""

    def test_session_data_creation_with_defaults(self):
        """Test SessionData creation with default values."""
        session = SessionData(
            session_id="sess_123",
            user_id="user_456",
            connection_id="conn_789",
        )
        assert session.session_id == "sess_123"
        assert session.user_id == "user_456"
        assert session.connection_id == "conn_789"
        assert session.state == SessionState.CONNECTING
        assert session.subscriptions == set()
        assert session.metadata == {}
        assert session.created_at is not None
        assert session.last_ping is not None
        assert session.reconnect_count == 0
        assert session.max_retry == 3

    def test_session_data_creation_with_custom_values(self):
        """Test SessionData creation with custom values."""
        now = time.time()
        session = SessionData(
            session_id="sess_123",
            user_id="user_456",
            connection_id="conn_789",
            state=SessionState.AUTHENTICATED,
            subscriptions={"conv_1", "conv_2"},
            metadata={"key": "value"},
            created_at=now,
            last_ping=now,
            reconnect_count=1,
            max_retry=5,
        )
        assert session.state == SessionState.AUTHENTICATED
        assert session.subscriptions == {"conv_1", "conv_2"}
        assert session.metadata == {"key": "value"}
        assert session.reconnect_count == 1
        assert session.max_retry == 5

    def test_session_data_subscriptions_mutable(self):
        """Test that subscriptions can be modified."""
        session = SessionData(
            session_id="sess_123",
            user_id="user_456",
            connection_id="conn_789",
        )
        session.subscriptions.add("conv_1")
        assert "conv_1" in session.subscriptions

    def test_session_data_metadata_mutable(self):
        """Test that metadata can be modified."""
        session = SessionData(
            session_id="sess_123",
            user_id="user_456",
            connection_id="conn_789",
        )
        session.metadata["key"] = "value"
        assert session.metadata["key"] == "value"


# ============================================================================
# Test ConnectionPool
# ============================================================================

def make_async_mock_redis():
    """Create a properly async mock Redis client."""
    redis_mock = MagicMock()
    redis_mock.get = AsyncMock(return_value=None)
    redis_mock.set = AsyncMock(return_value=True)
    redis_mock.hset = AsyncMock(return_value=1)
    redis_mock.hgetall = AsyncMock(return_value={})
    redis_mock.delete = AsyncMock(return_value=1)
    redis_mock.sadd = AsyncMock(return_value=1)
    redis_mock.srem = AsyncMock(return_value=1)
    redis_mock.expire = AsyncMock(return_value=True)
    redis_mock.close = AsyncMock()
    return redis_mock


class TestConnectionPool:
    """Tests for ConnectionPool class."""

    @pytest.fixture
    def mock_redis_config(self):
        """Create a mock Redis config."""
        config = MagicMock(spec=RedisConfig)
        config.url = "redis://localhost:6379/0"
        return config

    @pytest.fixture
    def connection_pool(self, mock_redis_config):
        """Create a ConnectionPool instance."""
        return ConnectionPool(
            redis_config=mock_redis_config,
            max_connections=50,
            ping_interval=30,
            ping_timeout=10,
            max_retry=3,
        )

    # ========================================================================
    # Lifecycle Tests
    # ========================================================================

    def test_connection_pool_initialization(self, connection_pool, mock_redis_config):
        """Test ConnectionPool initialization with default values."""
        assert connection_pool._redis_config == mock_redis_config
        assert connection_pool._max_connections == 50
        assert connection_pool._ping_interval == 30
        assert connection_pool._ping_timeout == 10
        assert connection_pool._max_retry == 3
        assert connection_pool._memory_store == {}
        assert connection_pool._active_connections == {}
        assert connection_pool._heartbeat_tasks == {}

    def test_connection_pool_initialization_defaults(self):
        """Test ConnectionPool initialization with defaults."""
        config = MagicMock(spec=RedisConfig)
        config.url = "redis://localhost:6379/0"
        pool = ConnectionPool(redis_config=config)
        assert pool._max_connections == 100
        assert pool._ping_interval == 30
        assert pool._ping_timeout == 10
        assert pool._max_retry == 3

    @pytest.mark.asyncio
    async def test_initialize_creates_redis_pool(self, connection_pool):
        """Test that initialize creates Redis connection pool."""
        with patch("sprinkle.kernel.session.redis.ConnectionPool") as mock_pool_class:
            with patch("sprinkle.kernel.session.redis.Redis") as mock_redis_class:
                mock_pool_class.from_url.return_value = MagicMock()
                mock_redis_class.return_value = MagicMock()

                await connection_pool.initialize()

                mock_pool_class.from_url.assert_called_once()
                mock_redis_class.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_handles_no_redis_client(self, connection_pool):
        """Test close when no Redis client exists."""
        connection_pool._redis_client = None
        connection_pool._pool = None
        # Should not raise
        await connection_pool.close()

    # ========================================================================
    # Session Management Tests
    # ========================================================================

    @pytest.mark.asyncio
    async def test_create_session(self, connection_pool):
        """Test creating a new session."""
        redis_mock = make_async_mock_redis()
        connection_pool._redis_client = redis_mock

        session = await connection_pool.create_session(
            session_id="sess_123",
            user_id="user_456",
            connection_id="conn_789",
            metadata={"key": "value"},
        )
        assert session.session_id == "sess_123"
        assert session.user_id == "user_456"
        assert session.connection_id == "conn_789"
        assert session.state == SessionState.CONNECTING
        assert session.metadata == {"key": "value"}
        assert session.max_retry == 3

    @pytest.mark.asyncio
    async def test_get_session_from_memory(self, connection_pool):
        """Test getting session from memory store."""
        session = SessionData(
            session_id="sess_123",
            user_id="user_456",
            connection_id="conn_789",
        )
        connection_pool._memory_store["sess_123"] = session

        result = await connection_pool.get_session("sess_123")

        assert result is not None
        assert result.session_id == "sess_123"

    @pytest.mark.asyncio
    async def test_get_session_from_redis_when_not_in_memory(self, connection_pool):
        """Test getting session from Redis when not in memory."""
        redis_mock = make_async_mock_redis()
        now = time.time()
        redis_data = {
            "session_id": "sess_123",
            "user_id": "user_456",
            "connection_id": "conn_789",
            "state": "connected",
            "subscriptions": "",
            "metadata": "{}",
            "created_at": str(now),
            "last_ping": str(now),
            "reconnect_count": "0",
            "max_retry": "3",
        }
        redis_mock.hgetall = AsyncMock(return_value=redis_data)
        connection_pool._redis_client = redis_mock

        result = await connection_pool.get_session("sess_123")

        assert result is not None
        assert result.session_id == "sess_123"
        assert result.user_id == "user_456"
        # Should also be in memory now
        assert "sess_123" in connection_pool._memory_store

    @pytest.mark.asyncio
    async def test_get_session_not_found(self, connection_pool):
        """Test getting non-existent session returns None."""
        redis_mock = make_async_mock_redis()
        redis_mock.hgetall = AsyncMock(return_value={})
        connection_pool._redis_client = redis_mock

        result = await connection_pool.get_session("nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_delete_session_removes_from_memory(self, connection_pool):
        """Test delete_session removes session from memory."""
        session = SessionData(
            session_id="sess_123",
            user_id="user_456",
            connection_id="conn_789",
        )
        connection_pool._memory_store["sess_123"] = session

        redis_mock = make_async_mock_redis()
        connection_pool._redis_client = redis_mock

        await connection_pool.delete_session("sess_123")

        assert "sess_123" not in connection_pool._memory_store

    @pytest.mark.asyncio
    async def test_get_user_sessions(self, connection_pool):
        """Test getting all sessions for a user."""
        session1 = SessionData(
            session_id="sess_1",
            user_id="user_456",
            connection_id="conn_1",
        )
        session2 = SessionData(
            session_id="sess_2",
            user_id="user_456",
            connection_id="conn_2",
        )
        session3 = SessionData(
            session_id="sess_3",
            user_id="other_user",
            connection_id="conn_3",
        )
        connection_pool._memory_store["sess_1"] = session1
        connection_pool._memory_store["sess_2"] = session2
        connection_pool._memory_store["sess_3"] = session3

        sessions = await connection_pool.get_user_sessions("user_456")

        assert len(sessions) == 2
        assert all(s.user_id == "user_456" for s in sessions)

    # ========================================================================
    # State Management Tests
    # ========================================================================

    @pytest.mark.asyncio
    async def test_set_state(self, connection_pool):
        """Test setting session state."""
        session = SessionData(
            session_id="sess_123",
            user_id="user_456",
            connection_id="conn_789",
        )
        connection_pool._memory_store["sess_123"] = session
        redis_mock = make_async_mock_redis()
        connection_pool._redis_client = redis_mock

        result = await connection_pool.set_state("sess_123", SessionState.CONNECTED)

        assert result is True
        assert connection_pool._memory_store["sess_123"].state == SessionState.CONNECTED

    @pytest.mark.asyncio
    async def test_set_state_triggers_connect_callback(self, connection_pool):
        """Test that CONNECTED state change triggers on_connect callback."""
        session = SessionData(
            session_id="sess_123",
            user_id="user_456",
            connection_id="conn_789",
        )
        connection_pool._memory_store["sess_123"] = session
        redis_mock = make_async_mock_redis()
        connection_pool._redis_client = redis_mock

        callback_called = []

        async def on_connect(sess):
            callback_called.append(sess)

        connection_pool.set_on_connect(on_connect)
        await connection_pool.set_state("sess_123", SessionState.CONNECTED)

        assert len(callback_called) == 1
        assert callback_called[0].session_id == "sess_123"

    @pytest.mark.asyncio
    async def test_set_state_session_not_found(self, connection_pool):
        """Test setting state for non-existent session returns False."""
        result = await connection_pool.set_state("nonexistent", SessionState.CONNECTED)
        assert result is False

    @pytest.mark.asyncio
    async def test_set_disconnect_state_triggers_callback(self, connection_pool):
        """Test that DISCONNECTED state change triggers on_disconnect callback."""
        session = SessionData(
            session_id="sess_123",
            user_id="user_456",
            connection_id="conn_789",
        )
        connection_pool._memory_store["sess_123"] = session
        redis_mock = make_async_mock_redis()
        connection_pool._redis_client = redis_mock

        callback_called = []

        async def on_disconnect(sess):
            callback_called.append(sess)

        connection_pool.set_on_disconnect(on_disconnect)
        await connection_pool.set_state("sess_123", SessionState.DISCONNECTED)

        assert len(callback_called) == 1

    # ========================================================================
    # Subscription Tests
    # ========================================================================

    @pytest.mark.asyncio
    async def test_subscribe(self, connection_pool):
        """Test subscribing to a conversation."""
        session = SessionData(
            session_id="sess_123",
            user_id="user_456",
            connection_id="conn_789",
        )
        connection_pool._memory_store["sess_123"] = session
        redis_mock = make_async_mock_redis()
        connection_pool._redis_client = redis_mock

        result = await connection_pool.subscribe("sess_123", "conv_abc")

        assert result is True
        assert "conv_abc" in session.subscriptions

    @pytest.mark.asyncio
    async def test_unsubscribe(self, connection_pool):
        """Test unsubscribing from a conversation."""
        session = SessionData(
            session_id="sess_123",
            user_id="user_456",
            connection_id="conn_789",
            subscriptions={"conv_abc", "conv_def"},
        )
        connection_pool._memory_store["sess_123"] = session
        redis_mock = make_async_mock_redis()
        connection_pool._redis_client = redis_mock

        result = await connection_pool.unsubscribe("sess_123", "conv_abc")

        assert result is True
        assert "conv_abc" not in session.subscriptions
        assert "conv_def" in session.subscriptions

    @pytest.mark.asyncio
    async def test_subscribe_session_not_found(self, connection_pool):
        """Test subscribe returns False for non-existent session."""
        result = await connection_pool.subscribe("nonexistent", "conv_abc")
        assert result is False

    @pytest.mark.asyncio
    async def test_unsubscribe_session_not_found(self, connection_pool):
        """Test unsubscribe returns False for non-existent session."""
        result = await connection_pool.unsubscribe("nonexistent", "conv_abc")
        assert result is False

    # ========================================================================
    # Heartbeat Tests
    # ========================================================================

    @pytest.mark.asyncio
    async def test_start_heartbeat(self, connection_pool):
        """Test starting heartbeat for a session."""
        await connection_pool.start_heartbeat("sess_123")
        assert "sess_123" in connection_pool._heartbeat_tasks

    @pytest.mark.asyncio
    async def test_stop_heartbeat_nonexistent(self, connection_pool):
        """Test stopping heartbeat for non-existent session does nothing."""
        # Should not raise
        await connection_pool.stop_heartbeat("nonexistent")

    @pytest.mark.asyncio
    async def test_receive_pong(self, connection_pool):
        """Test receiving pong updates last_ping."""
        session = SessionData(
            session_id="sess_123",
            user_id="user_456",
            connection_id="conn_789",
        )
        old_last_ping = session.last_ping
        connection_pool._memory_store["sess_123"] = session
        redis_mock = make_async_mock_redis()
        connection_pool._redis_client = redis_mock

        await connection_pool.receive_pong("sess_123")

        # last_ping should be updated
        assert connection_pool._memory_store["sess_123"].last_ping >= old_last_ping

    # ========================================================================
    # Reconnection Tests
    # ========================================================================

    @pytest.mark.asyncio
    async def test_reconnect_session(self, connection_pool):
        """Test reconnecting a session."""
        session = SessionData(
            session_id="sess_123",
            user_id="user_456",
            connection_id="old_conn",
        )
        connection_pool._memory_store["sess_123"] = session
        redis_mock = make_async_mock_redis()
        connection_pool._redis_client = redis_mock

        result = await connection_pool.reconnect_session("sess_123", "new_conn")

        assert result is not None
        assert result.connection_id == "new_conn"
        assert result.state == SessionState.CONNECTED

    @pytest.mark.asyncio
    async def test_reconnect_session_not_found(self, connection_pool):
        """Test reconnecting non-existent session returns None."""
        result = await connection_pool.reconnect_session("nonexistent", "new_conn")
        assert result is None

    @pytest.mark.asyncio
    async def test_reconnect_disconnected_session_fails(self, connection_pool):
        """Test reconnecting a disconnected session fails."""
        session = SessionData(
            session_id="sess_123",
            user_id="user_456",
            connection_id="old_conn",
            state=SessionState.DISCONNECTED,
        )
        connection_pool._memory_store["sess_123"] = session

        result = await connection_pool.reconnect_session("sess_123", "new_conn")

        assert result is None

    # ========================================================================
    # Redis Persistence Tests
    # ========================================================================

    @pytest.mark.asyncio
    async def test_load_session_from_redis(self, connection_pool):
        """Test loading session from Redis."""
        redis_mock = make_async_mock_redis()
        now = time.time()
        redis_data = {
            "session_id": "sess_123",
            "user_id": "user_456",
            "connection_id": "conn_789",
            "state": "connected",
            "subscriptions": "conv_1,conv_2",
            "metadata": "{}",
            "created_at": str(now),
            "last_ping": str(now),
            "reconnect_count": "2",
            "max_retry": "5",
        }
        redis_mock.hgetall = AsyncMock(return_value=redis_data)
        connection_pool._redis_client = redis_mock

        session = await connection_pool._load_session_redis("sess_123")

        assert session is not None
        assert session.session_id == "sess_123"
        assert session.user_id == "user_456"
        assert session.subscriptions == {"conv_1", "conv_2"}
        assert session.reconnect_count == 2

    @pytest.mark.asyncio
    async def test_load_session_from_redis_empty(self, connection_pool):
        """Test loading non-existent session from Redis returns None."""
        redis_mock = make_async_mock_redis()
        redis_mock.hgetall = AsyncMock(return_value={})
        connection_pool._redis_client = redis_mock

        session = await connection_pool._load_session_redis("nonexistent")

        assert session is None

    @pytest.mark.asyncio
    async def test_handle_disconnect_exceeds_max_retry(self, connection_pool):
        """Test handling disconnect when max retry exceeded."""
        session = SessionData(
            session_id="sess_123",
            user_id="user_456",
            connection_id="conn_789",
            reconnect_count=3,
            max_retry=3,
        )
        redis_mock = make_async_mock_redis()
        connection_pool._redis_client = redis_mock

        callback_called = []

        async def on_disconnect(sess):
            callback_called.append(sess)

        connection_pool.set_on_disconnect(on_disconnect)

        await connection_pool._handle_disconnect(session)

        assert session.state == SessionState.DISCONNECTED
        assert len(callback_called) == 1

    @pytest.mark.asyncio
    async def test_handle_disconnect_triggers_reconnect(self, connection_pool):
        """Test handling disconnect triggers reconnection attempt."""
        session = SessionData(
            session_id="sess_123",
            user_id="user_456",
            connection_id="conn_789",
            reconnect_count=0,
            max_retry=3,
        )
        redis_mock = make_async_mock_redis()
        connection_pool._redis_client = redis_mock

        callback_called = []

        async def on_reconnect(sess):
            callback_called.append(sess)

        connection_pool.set_on_reconnect(on_reconnect)

        await connection_pool._handle_disconnect(session)

        assert session.state == SessionState.RECONNECTING
        assert session.reconnect_count == 1
        assert len(callback_called) == 1

    # ========================================================================
    # Callback Tests
    # ========================================================================

    def test_set_on_connect_callback(self, connection_pool):
        """Test setting on_connect callback."""
        async def callback(session):
            pass

        connection_pool.set_on_connect(callback)
        assert connection_pool._on_connect is callback

    def test_set_on_disconnect_callback(self, connection_pool):
        """Test setting on_disconnect callback."""
        async def callback(session):
            pass

        connection_pool.set_on_disconnect(callback)
        assert connection_pool._on_disconnect is callback

    def test_set_on_reconnect_callback(self, connection_pool):
        """Test setting on_reconnect callback."""
        async def callback(session):
            pass

        connection_pool.set_on_reconnect(callback)
        assert connection_pool._on_reconnect is callback


# ============================================================================
# Test SessionManager
# ============================================================================

class TestSessionManager:
    """Tests for SessionManager class."""

    @pytest.fixture
    def mock_redis_config(self):
        """Create a mock Redis config."""
        config = MagicMock(spec=RedisConfig)
        config.url = "redis://localhost:6379/0"
        return config

    @pytest.fixture
    def session_manager(self, mock_redis_config):
        """Create a SessionManager instance."""
        return SessionManager(
            redis_config=mock_redis_config,
            max_connections=50,
            ping_interval=30,
            ping_timeout=10,
            max_retry=3,
        )

    def test_session_manager_initialization(self, session_manager, mock_redis_config):
        """Test SessionManager initialization."""
        assert session_manager._redis_config == mock_redis_config
        assert isinstance(session_manager._pool, ConnectionPool)
        assert session_manager._pool._max_connections == 50

    def test_session_manager_creates_default_config(self):
        """Test SessionManager creates default RedisConfig if none provided."""
        manager = SessionManager()
        assert manager._redis_config is not None

    @pytest.mark.asyncio
    async def test_session_manager_initialize(self, session_manager):
        """Test SessionManager initialize."""
        with patch.object(session_manager._pool, 'initialize', new_callable=AsyncMock) as mock_init:
            await session_manager.initialize()
            mock_init.assert_called_once()

    @pytest.mark.asyncio
    async def test_session_manager_close(self, session_manager):
        """Test SessionManager close."""
        with patch.object(session_manager._pool, 'close', new_callable=AsyncMock) as mock_close:
            await session_manager.close()
            mock_close.assert_called_once()

    @pytest.mark.asyncio
    async def test_session_manager_create_session(self, session_manager):
        """Test SessionManager create_session."""
        with patch.object(session_manager._pool, 'create_session', new_callable=AsyncMock) as mock_create:
            mock_create.return_value = SessionData(
                session_id="sess_123",
                user_id="user_456",
                connection_id="conn_789",
            )
            session = await session_manager.create_session(
                session_id="sess_123",
                user_id="user_456",
                connection_id="conn_789",
            )
            assert session.session_id == "sess_123"

    @pytest.mark.asyncio
    async def test_session_manager_get_session(self, session_manager):
        """Test SessionManager get_session."""
        with patch.object(session_manager._pool, 'get_session', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = SessionData(
                session_id="sess_123",
                user_id="user_456",
                connection_id="conn_789",
            )
            session = await session_manager.get_session("sess_123")
            assert session.session_id == "sess_123"

    @pytest.mark.asyncio
    async def test_session_manager_delete_session(self, session_manager):
        """Test SessionManager delete_session."""
        with patch.object(session_manager._pool, 'delete_session', new_callable=AsyncMock) as mock_delete:
            await session_manager.delete_session("sess_123")
            mock_delete.assert_called_once_with("sess_123")

    @pytest.mark.asyncio
    async def test_session_manager_get_user_sessions(self, session_manager):
        """Test SessionManager get_user_sessions."""
        with patch.object(session_manager._pool, 'get_user_sessions', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = [
                SessionData(
                    session_id="sess_123",
                    user_id="user_456",
                    connection_id="conn_1",
                ),
            ]
            sessions = await session_manager.get_user_sessions("user_456")
            assert len(sessions) == 1

    @pytest.mark.asyncio
    async def test_session_manager_set_state(self, session_manager):
        """Test SessionManager set_state."""
        with patch.object(session_manager._pool, 'set_state', new_callable=AsyncMock) as mock_set:
            mock_set.return_value = True
            result = await session_manager.set_state("sess_123", SessionState.CONNECTED)
            assert result is True

    @pytest.mark.asyncio
    async def test_session_manager_authenticate(self, session_manager):
        """Test SessionManager authenticate."""
        with patch.object(session_manager._pool, 'set_state', new_callable=AsyncMock) as mock_set:
            mock_set.return_value = True
            result = await session_manager.authenticate("sess_123")
            mock_set.assert_called_once_with("sess_123", SessionState.AUTHENTICATED)
            assert result is True

    @pytest.mark.asyncio
    async def test_session_manager_subscribe(self, session_manager):
        """Test SessionManager subscribe."""
        with patch.object(session_manager._pool, 'subscribe', new_callable=AsyncMock) as mock_sub:
            mock_sub.return_value = True
            result = await session_manager.subscribe("sess_123", "conv_abc")
            assert result is True

    @pytest.mark.asyncio
    async def test_session_manager_unsubscribe(self, session_manager):
        """Test SessionManager unsubscribe."""
        with patch.object(session_manager._pool, 'unsubscribe', new_callable=AsyncMock) as mock_unsub:
            mock_unsub.return_value = True
            result = await session_manager.unsubscribe("sess_123", "conv_abc")
            assert result is True

    @pytest.mark.asyncio
    async def test_session_manager_start_heartbeat(self, session_manager):
        """Test SessionManager start_heartbeat."""
        with patch.object(session_manager._pool, 'start_heartbeat', new_callable=AsyncMock) as mock_start:
            await session_manager.start_heartbeat("sess_123")
            mock_start.assert_called_once_with("sess_123")

    @pytest.mark.asyncio
    async def test_session_manager_stop_heartbeat(self, session_manager):
        """Test SessionManager stop_heartbeat."""
        with patch.object(session_manager._pool, 'stop_heartbeat', new_callable=AsyncMock) as mock_stop:
            await session_manager.stop_heartbeat("sess_123")
            mock_stop.assert_called_once_with("sess_123")

    @pytest.mark.asyncio
    async def test_session_manager_receive_pong(self, session_manager):
        """Test SessionManager receive_pong."""
        with patch.object(session_manager._pool, 'receive_pong', new_callable=AsyncMock) as mock_pong:
            await session_manager.receive_pong("sess_123")
            mock_pong.assert_called_once_with("sess_123")

    @pytest.mark.asyncio
    async def test_session_manager_reconnect(self, session_manager):
        """Test SessionManager reconnect."""
        with patch.object(session_manager._pool, 'reconnect_session', new_callable=AsyncMock) as mock_recon:
            mock_recon.return_value = SessionData(
                session_id="sess_123",
                user_id="user_456",
                connection_id="new_conn",
            )
            session = await session_manager.reconnect("sess_123", "new_conn")
            assert session.connection_id == "new_conn"

    def test_session_manager_on_connect_callback(self, session_manager):
        """Test SessionManager on_connect callback."""
        async def callback(session):
            pass

        session_manager.on_connect(callback)
        assert session_manager._pool._on_connect is callback

    def test_session_manager_on_disconnect_callback(self, session_manager):
        """Test SessionManager on_disconnect callback."""
        async def callback(session):
            pass

        session_manager.on_disconnect(callback)
        assert session_manager._pool._on_disconnect is callback

    def test_session_manager_on_reconnect_callback(self, session_manager):
        """Test SessionManager on_reconnect callback."""
        async def callback(session):
            pass

        session_manager.on_reconnect(callback)
        assert session_manager._pool._on_reconnect is callback
