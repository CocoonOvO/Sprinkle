"""Push template engine for Sprinkle notifications."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sprinkle.models.push import PushTemplateModel

logger = logging.getLogger(__name__)


# ============================================================================
# Default Templates
# ============================================================================

DEFAULT_TEMPLATES = {
    "chat.message": {
        "format": "markdown",
        "content": "**{{sender_name}}**: {{content}}",
        "quick_replies": [],
    },
    "chat.message.edited": {
        "format": "markdown",
        "content": "{{sender_name}} 编辑了消息: {{content}}",
        "quick_replies": [],
    },
    "chat.message.deleted": {
        "format": "markdown",
        "content": "{{sender_name}} 删除了消息",
        "quick_replies": [],
    },
    "chat.message.reply": {
        "format": "markdown",
        "content": "**{{sender_name}}** 回复: {{content}}",
        "quick_replies": [],
    },
    "group.member.joined": {
        "format": "markdown",
        "content": "👋 {{actor_name}} 邀请 {{target_name}} 加入了群聊",
        "quick_replies": [],
    },
    "group.member.left": {
        "format": "markdown",
        "content": "👋 {{target_name}} 离开了群聊",
        "quick_replies": [],
    },
    "group.member.kicked": {
        "format": "markdown",
        "content": "⚠️ {{actor_name}} 将 {{target_name}} 移出了群聊",
        "quick_replies": [],
    },
    "group.created": {
        "format": "markdown",
        "content": "✨ 群聊「{{group_name}}」已创建",
        "quick_replies": [],
    },
    "group.disbanded": {
        "format": "markdown",
        "content": "💥 群聊已解散",
        "quick_replies": [],
    },
    "group.info.updated": {
        "format": "markdown",
        "content": "📝 {{actor_name}} 更新了群聊信息",
        "quick_replies": [],
    },
    "mention": {
        "format": "markdown",
        "content": "📌 {{sender_name}} 在消息中提到了你: {{content}}",
        "quick_replies": [],
    },
    "system.notification": {
        "format": "markdown",
        "content": "ℹ️ {{content}}",
        "quick_replies": [],
    },
    "default": {
        "format": "markdown",
        "content": "{{sender_name}}: {{content}}",
        "quick_replies": [],
    },
}


# ============================================================================
# Template Variable Patterns
# ============================================================================

# Pattern to match {{variable}} placeholders
VARIABLE_PATTERN = re.compile(r"\{\{(\w+)\}\}")


# ============================================================================
# PushTemplateEngine
# ============================================================================

class PushTemplateEngine:
    """Renders push notification content using templates.
    
    The template engine loads templates from the database and renders
    them with provided context variables. It supports a simple variable
    substitution syntax: {{variable_name}}.
    
    Example:
        engine = PushTemplateEngine(db_session)
        
        # Render a template
        content = await engine.render("chat.message", {
            "sender_name": "Alice",
            "content": "Hello!",
        })
        # -> "**Alice**: Hello!"
    """
    
    def __init__(self, db_session: AsyncSession):
        """Initialize the template engine.
        
        Args:
            db_session: SQLAlchemy async session
        """
        self._db = db_session
        # In-memory cache for templates
        self._cache: Dict[str, PushTemplateModel] = {}
    
    async def render(
        self,
        template_name: str,
        context: Dict[str, Any],
    ) -> str:
        """Render a template with context variables.
        
        Args:
            template_name: Name of the template to render
            context: Dictionary of variable values
        
        Returns:
            Rendered content string
        """
        template = await self.get_template(template_name)
        
        if template is None:
            # Use default template
            default_content = DEFAULT_TEMPLATES.get(
                template_name,
                DEFAULT_TEMPLATES["default"]
            )["content"]
            return self._render_string(default_content, context)
        
        return self._render_string(template.content, context)
    
    def _render_string(self, content: str, context: Dict[str, Any]) -> str:
        """Render a string with variable substitution.
        
        Args:
            content: Template string with {{variable}} placeholders
            context: Variable values
        
        Returns:
            Rendered string with variables replaced
        """
        def replacer(match):
            var_name = match.group(1)
            return str(context.get(var_name, match.group(0)))
        
        return VARIABLE_PATTERN.sub(replacer, content)
    
    async def get_template(self, name: str) -> Optional[PushTemplateModel]:
        """Get a template by name.
        
        Args:
            name: Template name
        
        Returns:
            Template model if found, None otherwise
        """
        # Check cache first
        if name in self._cache:
            return self._cache[name]
        
        # Query database
        stmt = select(PushTemplateModel).where(
            PushTemplateModel.name == name,
            PushTemplateModel.is_active == True,  # noqa: E712
        )
        result = await self._db.execute(stmt)
        template = result.scalar_one_or_none()
        
        if template:
            self._cache[name] = template
        
        return template
    
    async def list_templates(self, active_only: bool = True) -> List[PushTemplateModel]:
        """List all templates.
        
        Args:
            active_only: If True, only return active templates
        
        Returns:
            List of template models
        """
        stmt = select(PushTemplateModel)
        if active_only:
            stmt = stmt.where(PushTemplateModel.is_active == True)  # noqa: E712
        stmt = stmt.order_by(PushTemplateModel.name)
        
        result = await self._db.execute(stmt)
        return list(result.scalars().all())
    
    async def create_default_templates(self) -> List[PushTemplateModel]:
        """Create default templates if they don't exist.
        
        This is useful for initializing the database with default
        notification templates.
        
        Returns:
            List of created template models
        """
        created = []
        
        for name, config in DEFAULT_TEMPLATES.items():
            # Check if exists
            existing = await self.get_template(name)
            if existing:
                continue
            
            # Create new template
            template = PushTemplateModel(
                id=str(uuid.uuid4()),
                name=name,
                format=config["format"],
                content=config["content"],
                quick_replies=config["quick_replies"],
                is_active=True,
            )
            self._db.add(template)
            created.append(template)
        
        if created:
            await self._db.commit()
            logger.info(f"Created {len(created)} default push templates")
        
        return created
    
    async def create_or_update_template(
        self,
        name: str,
        content: str,
        format: str = "markdown",
        quick_replies: Optional[List[Any]] = None,
        is_active: bool = True,
    ) -> PushTemplateModel:
        """Create or update a template.
        
        Args:
            name: Template name
            content: Template content
            format: Output format (markdown/html/text)
            quick_replies: Optional quick reply definitions
            is_active: Whether the template is active
        
        Returns:
            The created or updated template
        """
        existing = await self.get_template(name)
        
        if existing:
            existing.content = content
            existing.format = format
            existing.quick_replies = quick_replies or []
            existing.is_active = is_active
            await self._db.commit()
            # Update cache
            self._cache[name] = existing
            return existing
        else:
            template = PushTemplateModel(
                id=str(uuid.uuid4()),
                name=name,
                content=content,
                format=format,
                quick_replies=quick_replies or [],
                is_active=is_active,
            )
            self._db.add(template)
            await self._db.commit()
            self._cache[name] = template
            return template
    
    def clear_cache(self) -> None:
        """Clear the template cache."""
        self._cache.clear()
