"""OpenClaw Gateway Client - WebSocket JSON-RPC implementation for communicating with OpenClaw Gateway."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Callable, Optional

from sprinkle.push.events import PushEvent
from sprinkle.services.agent_gateway.base import (
    AgentGatewayClient,
    AgentGatewayError,
    AgentResponse,
    GatewayAuthError,
    GatewayConnectionError,
    GatewayProvider,
    GatewayTimeoutError,
    GatewayValidationError,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Configuration
# ============================================================================

DEFAULT_OPENCLAW_GATEWAY_URL = "ws://127.0.0.1:18789"
DEFAULT_TIMEOUT = 30.0
MAX_RETRIES = 3
RETRY_BACKOFF = [1, 2, 4]


# ============================================================================
# JSON-RPC Message Types
# ============================================================================

class JsonRpcMessage(dict):
    """JSON-RPC message helper."""
    
    @staticmethod
    def request(method: str, params: dict, msg_id: Optional[str] = None) -> "JsonRpcMessage":
        return JsonRpcMessage({
            "type": "req",
            "id": msg_id or str(uuid.uuid4()),
            "method": method,
            "params": params,
        })
    
    @staticmethod
    def response(req_id: str, ok: bool, payload: Any = None, error: str = None) -> "JsonRpcMessage":
        result = {
            "type": "res",
            "id": req_id,
            "ok": ok,
        }
        if ok:
            result["payload"] = payload
        else:
            result["error"] = error
        return JsonRpcMessage(result)
    
    @staticmethod
    def event(event: str, payload: Any = None) -> "JsonRpcMessage":
        result = {
            "type": "event",
            "event": event,
        }
        if payload is not None:
            result["payload"] = payload
        return JsonRpcMessage(result)


# ============================================================================
# OpenClaw Gateway Client (WebSocket + JSON-RPC)
# ============================================================================

class OpenClawGatewayClient(AgentGatewayClient):
    """Client for communicating with OpenClaw Gateway via WebSocket + JSON-RPC.
    
    This client implements the OpenClaw Gateway WebSocket protocol:
    - WebSocket handshake with auth challenge/response
    - JSON-RPC requests/responses over WebSocket
    - Session subscriptions for receiving events
    - Agent message sending via sessions.send
    
    Protocol reference: https://docs.openclaw.ai/gateway/protocol
    
    Session key format (from session-management-compaction.md):
    - Direct chat: agent:<agentId>:<mainKey> (default main)
    - Group: agent:<agentId>:<channel>:group:<id>
    - Room/channel: agent:<agentId>:<channel>:channel:<id>
    
    For QQ private chat: agent:<agentId>:qqbot:c2c:<conversation_id>
    
    Attributes:
        gateway_url: OpenClaw Gateway WebSocket URL.
        api_token: Authentication token for Gateway.
        default_agent_id: Default agent ID to use.
        timeout: Request timeout in seconds.
    """
    
    # Events supported by OpenClaw Gateway
    SUPPORTED_EVENTS = [
        PushEvent.CHAT_MESSAGE,
        PushEvent.CHAT_MESSAGE_REPLY,
        PushEvent.MENTION,
    ]
    
    def __init__(
        self,
        gateway_url: str = DEFAULT_OPENCLAW_GATEWAY_URL,
        api_token: Optional[str] = None,
        default_agent_id: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        """Initialize the OpenClaw Gateway client.
        
        Args:
            gateway_url: OpenClaw Gateway WebSocket URL.
            api_token: Authentication token for Gateway.
            default_agent_id: Default agent ID to use when not specified.
            timeout: Request timeout in seconds.
        """
        self._gateway_url = gateway_url
        self._api_token = api_token
        self._default_agent_id = default_agent_id
        self._timeout = timeout
        
        self._ws: Optional[Any] = None  # WebSocket connection
        self._response_callback: Optional[Callable[[AgentResponse], Any]] = None
        self._initialized = False
        self._connected = False
        
        # Pending requests: message_id -> asyncio.Future
        self._pending_requests: dict[str, asyncio.Future] = {}
        
        # Listen task for WebSocket messages
        self._listen_task: Optional[asyncio.Task] = None
    
    @property
    def provider(self) -> GatewayProvider:
        return GatewayProvider.OPENCLAW
    
    @property
    def supported_events(self) -> list[PushEvent]:
        return self.SUPPORTED_EVENTS
    
    async def initialize(self) -> None:
        """Initialize and connect to the Gateway."""
        if self._initialized:
            return
        
        await self._connect()
        self._initialized = True
        logger.info(f"OpenClaw Gateway client initialized: {self._gateway_url}")
    
    async def close(self) -> None:
        """Close the WebSocket connection."""
        self._connected = False
        
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None
        
        if self._ws:
            await self._ws.close()
            self._ws = None
        
        self._initialized = False
        logger.info("OpenClaw Gateway client closed")
    
    async def _connect(self) -> None:
        """Connect to OpenClaw Gateway with WebSocket handshake."""
        import websockets
        
        try:
            # Connect to Gateway WebSocket
            self._ws = await websockets.connect(
                self._gateway_url,
                ping_interval=None,
            )
            
            # Wait for challenge
            challenge_msg = await asyncio.wait_for(
                self._ws.recv(),
                timeout=self._timeout,
            )
            challenge_data = json.loads(challenge_msg)
            
            if challenge_data.get("event") != "connect.challenge":
                raise GatewayConnectionError("Expected connect.challenge from Gateway")
            
            challenge_payload = challenge_data.get("payload", {})
            nonce = challenge_payload.get("nonce")
            ts = challenge_payload.get("ts")
            
            # Build auth object - token-based auth
            auth = {}
            if self._api_token:
                auth["token"] = self._api_token
            
            # Send connect request - use "gateway-client" for backend mode
            # role must be "operator" for session scopes
            connect_params = {
                "minProtocol": 3,
                "maxProtocol": 3,
                "client": {
                    "id": "gateway-client",  # Required for trusted backend clients
                    "version": "0.1.0",
                    "platform": "linux",
                    "mode": "backend",
                },
                "role": "operator",  # "operator" has session.read/write scopes
                "scopes": [
                    "agent.run",     # Start agent runs
                    "session.read",  # Read session metadata/messages
                    "session.write", # Send messages to sessions
                ],
                "auth": auth,
                "locale": "en-US",
                "userAgent": "sprinkle-agent-gateway/0.1.0",
            }
            
            await self._ws.send(json.dumps(JsonRpcMessage.request("connect", connect_params)))
            
            # Wait for hello-ok
            hello_msg = await asyncio.wait_for(
                self._ws.recv(),
                timeout=self._timeout,
            )
            hello_data = json.loads(hello_msg)
            
            if hello_data.get("type") != "res" or not hello_data.get("ok"):
                error = hello_data.get("error", "Unknown error")
                raise GatewayAuthError(f"Connection failed: {error}")
            
            self._connected = True
            logger.info(f"Connected to OpenClaw Gateway: {self._gateway_url}")
            
            # Start listening for messages
            self._listen_task = asyncio.create_task(self._listen())
            
        except asyncio.TimeoutError:
            raise GatewayTimeoutError(f"Connection timed out to {self._gateway_url}")
        except Exception as e:
            raise GatewayConnectionError(f"Failed to connect: {e}")
    
    async def _listen(self) -> None:
        """Listen for messages from the Gateway."""
        try:
            async for message in self._ws:
                try:
                    data = json.loads(message)
                    await self._handle_message(data)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON from Gateway: {message}")
                except Exception as e:
                    logger.error(f"Error handling message: {e}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            if self._connected:
                logger.error(f"WebSocket listen error: {e}")
                self._connected = False
    
    async def _handle_message(self, data: dict) -> None:
        """Handle incoming message from Gateway.
        
        Args:
            data: Parsed JSON message from Gateway.
        """
        msg_type = data.get("type")
        
        if msg_type == "res":
            # Response to our request
            req_id = data.get("id")
            if req_id in self._pending_requests:
                future = self._pending_requests.pop(req_id)
                if data.get("ok"):
                    future.set_result(data.get("payload"))
                else:
                    future.set_exception(
                        AgentGatewayError(data.get("error", "Unknown error"))
                    )
        
        elif msg_type == "event":
            event_name = data.get("event")
            payload = data.get("payload", {})
            
            # Handle various events
            if event_name == "session.message":
                # Incoming message from agent in session
                await self._handle_session_message(payload)
            elif event_name == "session.message.delta":
                # Streaming message chunk
                await self._handle_session_delta(payload)
            elif event_name == "session.updated":
                logger.debug(f"Session updated: {payload}")
            elif event_name == "run.completed":
                logger.debug(f"Run completed: {payload}")
            elif event_name == "run.failed":
                error = payload.get("error", "Unknown error")
                logger.error(f"Run failed: {error}")
            elif event_name == "run.created":
                logger.debug(f"Run created: {payload}")
        
        elif msg_type == "error":
            logger.error(f"Gateway error: {data}")
    
    async def _handle_session_message(self, payload: dict) -> None:
        """Handle incoming session.message event.
        
        Args:
            payload: Event payload containing agent response.
        """
        if not self._response_callback:
            return
        
        # Parse session message event
        # Payload format: { sessionKey, sessionId, messageId, content, contentType, role, ... }
        response = AgentResponse(
            message_id=payload.get("messageId", ""),
            agent_id=payload.get("agentId", ""),
            conversation_id=payload.get("conversationId", ""),
            content=payload.get("content", ""),
            content_type=payload.get("contentType", "text"),
            stream=False,
            metadata=payload.get("metadata", {}),
        )
        
        try:
            if asyncio.iscoroutinefunction(self._response_callback):
                await self._response_callback(response)
            else:
                self._response_callback(response)
        except Exception as e:
            logger.error(f"Error in response callback: {e}")
    
    async def _handle_session_delta(self, payload: dict) -> None:
        """Handle streaming message delta.
        
        Args:
            payload: Event payload containing chunk data.
        """
        if not self._response_callback:
            return
        
        response = AgentResponse(
            message_id=payload.get("messageId", ""),
            agent_id=payload.get("agentId", ""),
            conversation_id=payload.get("conversationId", ""),
            content=payload.get("delta", ""),  # Chunk content
            content_type="text",
            stream=True,
            metadata=payload.get("metadata", {}),
        )
        
        try:
            if asyncio.iscoroutinefunction(self._response_callback):
                await self._response_callback(response)
            else:
                self._response_callback(response)
        except Exception as e:
            logger.error(f"Error in response callback: {e}")
    
    async def _send_rpc_request(
        self,
        method: str,
        params: dict,
        timeout: Optional[float] = None,
    ) -> Any:
        """Send a JSON-RPC request and wait for response.
        
        Args:
            method: RPC method name.
            params: RPC parameters.
            timeout: Request timeout in seconds.
        
        Returns:
            Response payload.
        
        Raises:
            GatewayTimeoutError: If request times out.
            GatewayConnectionError: If not connected.
            AgentGatewayError: If request fails.
        """
        if not self._ws or not self._connected:
            raise GatewayConnectionError("Not connected to Gateway")
        
        msg_id = str(uuid.uuid4())
        request = JsonRpcMessage.request(method, params, msg_id)
        
        future = asyncio.Future()
        self._pending_requests[msg_id] = future
        
        try:
            await self._ws.send(json.dumps(request))
            
            result = await asyncio.wait_for(
                future,
                timeout=timeout or self._timeout,
            )
            return result
            
        except asyncio.TimeoutError:
            self._pending_requests.pop(msg_id, None)
            raise GatewayTimeoutError(f"Request {method} timed out")
        except Exception as e:
            self._pending_requests.pop(msg_id, None)
            raise AgentGatewayError(f"Request {method} failed: {e}")
    
    async def set_response_callback(
        self,
        callback: Callable[[AgentResponse], Any],
    ) -> None:
        """Set the callback for receiving agent responses.
        
        Args:
            callback: Async callable that accepts AgentResponse.
        """
        self._response_callback = callback
        logger.debug("Response callback set")
    
    def _build_session_key(
        self,
        agent_id: str,
        conversation_id: str,
        is_group: bool = False,
    ) -> str:
        """Build the OpenClaw session key format.
        
        Session key formats (from session-management-compaction.md):
        - Direct chat: agent:<agentId>:<mainKey> (default main)
        - Group: agent:<agentId>:<channel>:group:<id>
        - Room/channel: agent:<agentId>:<channel>:channel:<id>
        
        For QQ:
        - Private chat (C2C): agent:<agentId>:qqbot:c2c:<conversation_id>
        - Group chat: agent:<agentId>:qqbot:group:<group_id>
        
        Args:
            agent_id: Target agent ID.
            conversation_id: Conversation ID.
            is_group: Whether this is a group conversation.
        
        Returns:
            Session key string.
        """
        if is_group:
            return f"agent:{agent_id}:qqbot:group:{conversation_id}"
        else:
            return f"agent:{agent_id}:qqbot:c2c:{conversation_id}"
    
    async def send_message(
        self,
        agent_id: str,
        conversation_id: str,
        content: str,
        content_type: str = "text",
        metadata: Optional[dict[str, Any]] = None,
        is_group: bool = False,
    ) -> str:
        """Send a message to an agent through OpenClaw Gateway.
        
        Args:
            agent_id: Target agent ID.
            conversation_id: Associated conversation ID.
            content: Message content.
            content_type: Content type (text/markdown/image/file).
            metadata: Additional metadata.
            is_group: Whether this is a group conversation.
        
        Returns:
            message_id: Unique ID for tracking the request.
        
        Raises:
            GatewayConnectionError: If connection fails.
            GatewayTimeoutError: If request times out.
            GatewayValidationError: If request is invalid.
        """
        if not self._initialized:
            await self.initialize()
        
        message_id = str(uuid.uuid4())
        target_agent_id = agent_id or self._default_agent_id
        
        if not target_agent_id:
            raise GatewayValidationError(
                "agent_id is required when no default_agent_id is set"
            )
        
        # Build session key
        session_key = self._build_session_key(target_agent_id, conversation_id, is_group)
        
        logger.debug(
            f"Sending message to OpenClaw Gateway: "
            f"agent={target_agent_id}, session_key={session_key}, msg={message_id}"
        )
        
        # Retry logic
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                # First, resolve the session to get canonical session key
                # This ensures we're sending to the right session
                try:
                    session_info = await self._send_rpc_request(
                        "sessions.resolve",
                        {"key": session_key},
                    )
                    # Use the resolved session key if returned
                    if session_info and isinstance(session_info, dict):
                        resolved = session_info.get("key") or session_info.get("sessionKey")
                        if resolved:
                            session_key = resolved
                except Exception as e:
                    logger.debug(f"Session resolve returned: {e}")
                    # Session might not exist, that's OK
                
                # Send message via JSON-RPC sessions.send
                result = await self._send_rpc_request(
                    "sessions.send",
                    {
                        "key": session_key,
                        "message": content,
                        "messageId": message_id,
                    },
                )
                
                logger.debug(f"Message sent successfully: {result}")
                return message_id
                
            except GatewayTimeoutError as e:
                last_error = e
                logger.warning(f"Timeout (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
                
            except GatewayConnectionError as e:
                last_error = e
                logger.warning(f"Connection error (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
                
                # Try to reconnect
                try:
                    self._connected = False
                    await self._connect()
                except Exception:
                    pass
                
            except Exception as e:
                last_error = AgentGatewayError(f"Unexpected error: {e}")
                logger.error(f"Error sending message: {e}")
                raise
            
            # Exponential backoff
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_BACKOFF[attempt])
        
        # All retries exhausted
        raise last_error or AgentGatewayError("Failed to send message after retries")
    
    async def abort(
        self,
        agent_id: str,
        conversation_id: str,
        is_group: bool = False,
    ) -> bool:
        """Abort an ongoing agent session.
        
        Args:
            agent_id: Target agent ID.
            conversation_id: Associated conversation ID.
            is_group: Whether this is a group conversation.
        
        Returns:
            True if abort was successful, False otherwise.
        """
        if not self._initialized:
            await self.initialize()
        
        session_key = self._build_session_key(agent_id, conversation_id, is_group)
        
        try:
            await self._send_rpc_request(
                "sessions.abort",
                {"key": session_key},
            )
            return True
        except Exception as e:
            logger.error(f"Error aborting session: {e}")
            return False
    
    async def set_context(
        self,
        agent_id: str,
        conversation_id: str,
        context: dict[str, Any],
        is_group: bool = False,
    ) -> None:
        """Set conversation context for an agent session.
        
        This sets the conversation prompt or system context for the session.
        
        Args:
            agent_id: Target agent ID.
            conversation_id: Associated conversation ID.
            context: Context data containing keys like:
                - conversation_prompt: The conversation prompt to set
                - system_prompt: System-level prompt
            is_group: Whether this is a group conversation.
        """
        if not self._initialized:
            await self.initialize()
        
        session_key = self._build_session_key(agent_id, conversation_id, is_group)
        
        try:
            await self._send_rpc_request(
                "sessions.patch",
                {
                    "key": session_key,
                    "context": context,
                },
            )
            logger.debug(f"Context set for session {session_key}")
        except Exception as e:
            logger.error(f"Error setting context: {e}")
            raise AgentGatewayError(f"Failed to set context: {e}")
    
    async def subscribe_to_session(
        self,
        agent_id: str,
        conversation_id: str,
        is_group: bool = False,
    ) -> None:
        """Subscribe to session events for receiving agent responses.
        
        Args:
            agent_id: Target agent ID.
            conversation_id: Associated conversation ID.
            is_group: Whether this is a group conversation.
        """
        if not self._initialized:
            await self.initialize()
        
        session_key = self._build_session_key(agent_id, conversation_id, is_group)
        
        try:
            await self._send_rpc_request(
                "sessions.subscribe",
                {"key": session_key},
            )
            logger.debug(f"Subscribed to session: {session_key}")
        except Exception as e:
            logger.error(f"Error subscribing to session: {e}")
            raise AgentGatewayError(f"Failed to subscribe: {e}")
    
    async def unsubscribe_from_session(
        self,
        agent_id: str,
        conversation_id: str,
        is_group: bool = False,
    ) -> None:
        """Unsubscribe from session events.
        
        Args:
            agent_id: Target agent ID.
            conversation_id: Associated conversation ID.
            is_group: Whether this is a group conversation.
        """
        if not self._initialized:
            await self.initialize()
        
        session_key = self._build_session_key(agent_id, conversation_id, is_group)
        
        try:
            await self._send_rpc_request(
                "sessions.unsubscribe",
                {"key": session_key},
            )
            logger.debug(f"Unsubscribed from session: {session_key}")
        except Exception as e:
            logger.error(f"Error unsubscribing from session: {e}")
            raise AgentGatewayError(f"Failed to unsubscribe: {e}")


# ============================================================================
# Exports
# ============================================================================

__all__ = [
    "OpenClawGatewayClient",
]