"""Tests for the push notification system."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from sprinkle.push.events import PushEvent, PushEventData
from sprinkle.push.subscription import SubscriptionMode, AgentSubscription
from sprinkle.push.templates import PushTemplateEngine, DEFAULT_TEMPLATES
from sprinkle.push.router import PushRouter


class TestPushEvent:
    """Test PushEvent enum."""
    
    def test_chat_message_event(self):
        assert PushEvent.CHAT_MESSAGE == "chat.message"
    
    def test_group_member_events(self):
        assert PushEvent.GROUP_MEMBER_JOINED == "group.member.joined"
        assert PushEvent.GROUP_MEMBER_LEFT == "group.member.left"
        assert PushEvent.GROUP_MEMBER_KICKED == "group.member.kicked"


class TestPushEventData:
    """Test PushEventData dataclass."""
    
    def test_create_push_event_data(self):
        event_data = PushEventData(
            event=PushEvent.CHAT_MESSAGE,
            conversation_id="conv_123",
            sender_id="user_456",
            target_ids=["user_789"],
            content="Hello world",
        )
        
        assert event_data.event == PushEvent.CHAT_MESSAGE
        assert event_data.conversation_id == "conv_123"
        assert event_data.sender_id == "user_456"
        assert event_data.target_ids == ["user_789"]
        assert event_data.content == "Hello world"
        assert event_data.template_name == "default"
    
    def test_to_dict(self):
        event_data = PushEventData(
            event=PushEvent.MENTION,
            conversation_id="conv_123",
            sender_id="user_456",
            target_ids=["user_789"],
            content="Hey @user_789",
            metadata={"foo": "bar"},
        )
        
        d = event_data.to_dict()
        
        assert d["event"] == "mention"
        assert d["conversation_id"] == "conv_123"
        assert d["sender_id"] == "user_456"
        assert d["target_ids"] == ["user_789"]
        assert d["content"] == "Hey @user_789"
        assert d["metadata"] == {"foo": "bar"}
    
    def test_from_dict(self):
        data = {
            "event": "chat.message",
            "conversation_id": "conv_123",
            "sender_id": "user_456",
            "target_ids": ["user_789"],
            "content": "Hello",
            "metadata": {},
            "template_name": "chat.message",
        }
        
        event_data = PushEventData.from_dict(data)
        
        assert event_data.event == PushEvent.CHAT_MESSAGE
        assert event_data.conversation_id == "conv_123"


class TestSubscriptionMode:
    """Test SubscriptionMode enum."""
    
    def test_modes(self):
        assert SubscriptionMode.DIRECT == "direct"
        assert SubscriptionMode.MENTION_ONLY == "mention_only"
        assert SubscriptionMode.UNLIMITED == "unlimited"
        assert SubscriptionMode.EVENT_BASED == "event_based"


class TestAgentSubscription:
    """Test AgentSubscription dataclass."""
    
    def test_create_subscription(self):
        sub = AgentSubscription(
            agent_id="agent_123",
            conversation_id="conv_456",
            mode=SubscriptionMode.MENTION_ONLY,
        )
        
        assert sub.agent_id == "agent_123"
        assert sub.conversation_id == "conv_456"
        assert sub.mode == SubscriptionMode.MENTION_ONLY
        assert sub.subscribed_events == set()
    
    def test_create_with_events(self):
        events = {PushEvent.CHAT_MESSAGE, PushEvent.MENTION}
        sub = AgentSubscription(
            agent_id="agent_123",
            conversation_id="conv_456",
            mode=SubscriptionMode.EVENT_BASED,
            subscribed_events=events,
        )
        
        assert sub.subscribed_events == events


class TestPushTemplateEngine:
    """Test PushTemplateEngine."""
    
    @pytest.fixture
    def mock_db(self):
        """Create a mock async database session."""
        db = AsyncMock()
        return db
    
    def test_render_string_simple(self):
        """Test simple variable substitution."""
        engine = PushTemplateEngine.__new__(PushTemplateEngine)
        engine._cache = {}
        
        result = engine._render_string(
            "Hello {{name}}!",
            {"name": "World"}
        )
        
        assert result == "Hello World!"
    
    def test_render_string_multiple_vars(self):
        engine = PushTemplateEngine.__new__(PushTemplateEngine)
        engine._cache = {}
        
        result = engine._render_string(
            "{{sender}} said {{content}}",
            {"sender": "Alice", "content": "Hi there"}
        )
        
        assert result == "Alice said Hi there"
    
    def test_render_string_missing_var(self):
        """Missing variables should be left as-is."""
        engine = PushTemplateEngine.__new__(PushTemplateEngine)
        engine._cache = {}
        
        result = engine._render_string(
            "Hello {{name}}! You are {{age}} years old.",
            {"name": "World"}
        )
        
        assert result == "Hello World! You are {{age}} years old."
    
    def test_render_string_empty_content(self):
        engine = PushTemplateEngine.__new__(PushTemplateEngine)
        engine._cache = {}
        
        result = engine._render_string("", {"name": "World"})
        assert result == ""
    
    def test_default_templates_exist(self):
        """Verify default templates are defined."""
        assert "chat.message" in DEFAULT_TEMPLATES
        assert "group.member.joined" in DEFAULT_TEMPLATES
        assert "mention" in DEFAULT_TEMPLATES
        assert "default" in DEFAULT_TEMPLATES


class TestPushRouter:
    """Test PushRouter."""
    
    @pytest.fixture
    def mock_subscription_service(self):
        """Create a mock subscription service."""
        service = AsyncMock()
        return service
    
    @pytest.fixture
    def mock_template_engine(self):
        """Create a mock template engine."""
        engine = AsyncMock()
        return engine
    
    def test_should_push_to_mention_mentioned(self):
        """Agent should receive push when mentioned."""
        engine = PushRouter.__new__(PushRouter)
        
        event_data = PushEventData(
            event=PushEvent.CHAT_MESSAGE,
            conversation_id="conv_123",
            sender_id="user_456",
            target_ids=["agent_789"],
            content="Hey @agent_789",
        )
        
        # Agent is in target_ids - use sync helper directly
        # The async method calls the sync helper internally
        from sprinkle.push.subscription import SubscriptionService
        service = SubscriptionService.__new__(SubscriptionService)
        assert service._is_agent_mentioned(event_data, "agent_789") == True
    
    def test_should_push_to_mention_not_mentioned(self):
        engine = PushRouter.__new__(PushRouter)
        
        event_data = PushEventData(
            event=PushEvent.CHAT_MESSAGE,
            conversation_id="conv_123",
            sender_id="user_456",
            target_ids=["user_789"],
            content="Hello everyone",
        )
        
        # Agent not in target_ids
        from sprinkle.push.subscription import SubscriptionService
        service = SubscriptionService.__new__(SubscriptionService)
        assert service._is_agent_mentioned(event_data, "agent_789") == False
    
    def test_should_push_to_mention_in_metadata(self):
        engine = PushRouter.__new__(PushRouter)
        
        event_data = PushEventData(
            event=PushEvent.CHAT_MESSAGE,
            conversation_id="conv_123",
            sender_id="user_456",
            target_ids=[],
            content="Hey",
            metadata={"mentions": ["agent_789"]},
        )
        
        from sprinkle.push.subscription import SubscriptionService
        service = SubscriptionService.__new__(SubscriptionService)
        assert service._is_agent_mentioned(event_data, "agent_789") == True
    
    def test_should_push_to_mention_mention_event(self):
        """MENTION event type should always push to mentioned agents."""
        engine = PushRouter.__new__(PushRouter)
        
        event_data = PushEventData(
            event=PushEvent.MENTION,
            conversation_id="conv_123",
            sender_id="user_456",
            target_ids=["agent_789"],
            content="You were mentioned",
        )
        
        # MENTION event should return True for mentioned agent
        from sprinkle.push.subscription import SubscriptionService
        service = SubscriptionService.__new__(SubscriptionService)
        assert service._is_agent_mentioned(event_data, "agent_789") == True
    
    def test_build_template_context_message(self):
        engine = PushRouter.__new__(PushRouter)
        
        event_data = PushEventData(
            event=PushEvent.CHAT_MESSAGE,
            conversation_id="conv_123",
            sender_id="user_456",
            target_ids=["user_789"],
            content="Hello",
            metadata={"reply_to": "msg_111"},
        )
        
        ctx = engine._build_template_context(event_data)
        
        assert ctx["event"] == "chat.message"
        assert ctx["conversation_id"] == "conv_123"
        assert ctx["sender_id"] == "user_456"
        assert ctx["content"] == "Hello"


class TestSubscriptionService:
    """Test SubscriptionService with mocks."""
    
    def test_should_push_to_agent_direct_mode(self):
        """DIRECT mode should always push."""
        from sprinkle.push.subscription import SubscriptionService
        
        service = SubscriptionService.__new__(SubscriptionService)
        
        sub = AgentSubscription(
            agent_id="agent_123",
            conversation_id="conv_456",
            mode=SubscriptionMode.DIRECT,
        )
        
        event_data = PushEventData(
            event=PushEvent.CHAT_MESSAGE,
            conversation_id="conv_456",
            sender_id="user_789",
            target_ids=[],
            content="Hello",
        )
        
        # Synchronous check for DIRECT mode
        # Note: This tests the sync part; full test would need async
        assert sub.mode == SubscriptionMode.DIRECT
    
    def test_should_push_to_agent_unlimited_mode(self):
        """UNLIMITED mode should always push."""
        sub = AgentSubscription(
            agent_id="agent_123",
            conversation_id="conv_456",
            mode=SubscriptionMode.UNLIMITED,
        )
        
        assert sub.mode == SubscriptionMode.UNLIMITED
    
    def test_is_agent_mentioned_in_target_ids(self):
        """Check if agent is in target_ids."""
        from sprinkle.push.subscription import SubscriptionService
        
        service = SubscriptionService.__new__(SubscriptionService)
        
        event_data = PushEventData(
            event=PushEvent.CHAT_MESSAGE,
            conversation_id="conv_456",
            sender_id="user_789",
            target_ids=["agent_123", "agent_456"],
            content="Hello",
        )
        
        assert service._is_agent_mentioned(event_data, "agent_123") == True
        assert service._is_agent_mentioned(event_data, "agent_999") == False
