-- Migration: Add push notification tables
-- Version: 2.2
-- Description: Creates agent_subscriptions and push_templates tables for the new push notification system

-- ============================================================================
-- Agent Subscriptions Table
-- ============================================================================

CREATE TABLE IF NOT EXISTS agent_subscriptions (
    id VARCHAR(36) PRIMARY KEY,
    agent_id VARCHAR(36) NOT NULL REFERENCES users(id),
    conversation_id VARCHAR(36) NOT NULL REFERENCES conversations(id),
    mode VARCHAR(20) DEFAULT 'mention_only' NOT NULL,
    subscribed_events JSONB DEFAULT '[]' NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    UNIQUE(agent_id, conversation_id)
);

-- Index for querying subscriptions by conversation
CREATE INDEX IF NOT EXISTS idx_agent_subscriptions_conversation 
ON agent_subscriptions(conversation_id);

-- Index for querying subscriptions by agent
CREATE INDEX IF NOT EXISTS idx_agent_subscriptions_agent 
ON agent_subscriptions(agent_id);

-- ============================================================================
-- Push Templates Table
-- ============================================================================

CREATE TABLE IF NOT EXISTS push_templates (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL,
    format VARCHAR(20) DEFAULT 'markdown' NOT NULL,
    content TEXT NOT NULL,
    quick_replies JSONB DEFAULT '[]' NOT NULL,
    is_active BOOLEAN DEFAULT TRUE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

-- Index for active templates lookup
CREATE INDEX IF NOT EXISTS idx_push_templates_active 
ON push_templates(is_active) WHERE is_active = TRUE;

-- ============================================================================
-- Default Template Data
-- ============================================================================

INSERT INTO push_templates (id, name, format, content, quick_replies, is_active) VALUES
    ('00000000-0000-0000-0000-000000000001', 'chat.message', 'markdown', '**{{sender_name}}**: {{content}}', '[]', TRUE),
    ('00000000-0000-0000-0000-000000000002', 'chat.message.edited', 'markdown', '{{sender_name}} 编辑了消息: {{content}}', '[]', TRUE),
    ('00000000-0000-0000-0000-000000000003', 'chat.message.deleted', 'markdown', '{{sender_name}} 删除了消息', '[]', TRUE),
    ('00000000-0000-0000-0000-000000000004', 'chat.message.reply', 'markdown', '**{{sender_name}}** 回复: {{content}}', '[]', TRUE),
    ('00000000-0000-0000-0000-000000000005', 'group.member.joined', 'markdown', '👋 {{actor_name}} 邀请 {{target_name}} 加入了群聊', '[]', TRUE),
    ('00000000-0000-0000-0000-000000000006', 'group.member.left', 'markdown', '👋 {{target_name}} 离开了群聊', '[]', TRUE),
    ('00000000-0000-0000-0000-000000000007', 'group.member.kicked', 'markdown', '⚠️ {{actor_name}} 将 {{target_name}} 移出了群聊', '[]', TRUE),
    ('00000000-0000-0000-0000-000000000008', 'group.created', 'markdown', '✨ 群聊「{{group_name}}」已创建', '[]', TRUE),
    ('00000000-0000-0000-0000-000000000009', 'group.disbanded', 'markdown', '💥 群聊已解散', '[]', TRUE),
    ('00000000-0000-0000-0000-000000000010', 'group.info.updated', 'markdown', '📝 {{actor_name}} 更新了群聊信息', '[]', TRUE),
    ('00000000-0000-0000-0000-000000000011', 'mention', 'markdown', '📌 {{sender_name}} 在消息中提到了你: {{content}}', '[]', TRUE),
    ('00000000-0000-0000-0000-000000000012', 'system.notification', 'markdown', 'ℹ️ {{content}}', '[]', TRUE),
    ('00000000-0000-0000-0000-000000000013', 'default', 'markdown', '{{sender_name}}: {{content}}', '[]', TRUE)
ON CONFLICT (name) DO NOTHING;
