# Phase 3: 插件系统设计文档

> 版本：v0.1  
> 更新日期：2026-04-15  
> 状态：✅ 已实现

---

## 1. 概述

### 1.1 目标

实现 Sprinkle 的插件系统，支持：
- 插件注册与发现
- 插件生命周期管理（加载/卸载）
- 依赖解析与拓扑排序
- 准热拔插（通过 importlib）
- 插件间通过事件总线通信

### 1.2 范围

在 `src/sprinkle/plugins/` 目录下实现：
- `base.py` - Plugin 基类
- `manager.py` - 插件管理器
- `events.py` - 插件事件总线
- `builtin/` - 内置示例插件

### 1.3 与其他模块的关系

```
┌─────────────────────────────────────────────────────────────┐
│                      Plugin System                           │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ plugins/base.py      - Plugin 基类，定义生命周期钩子     │ │
│  │ plugins/manager.py   - 插件管理器，负责任务调度          │ │
│  │ plugins/events.py    - 事件总线，插件间通信              │ │
│  │ plugins/builtin/     - 内置示例插件                      │ │
│  └────────────────────────────────────────────────────────┘ │
│                            │                                 │
│                            ▼                                 │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ kernel/event.py      - 核心事件总线（依赖）              │ │
│  │ kernel/message.py    - 消息模型（依赖）                  │ │
│  │ config.py            - 配置管理（依赖）                  │ │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 接口设计

### 2.1 Plugin 基类 (plugins/base.py)

```python
class Plugin(ABC):
    """插件基类"""
    
    # 类属性
    name: str = "base-plugin"          # 唯一标识
    version: str = "0.0.0"             # 版本号
    dependencies: List[str] = []       # 依赖列表
    priority: int = 50                 # 优先级 (0-100，越高越先执行)
    
    # 实例方法（生命周期钩子）
    def on_load(self) -> None:
        """插件加载时调用"""
        pass
    
    def on_message(self, message: Message) -> Optional[Message]:
        """
        消息拦截处理
        - return message: 处理后的消息，继续传递
        - return None: 不修改消息，继续传递
        - raise DropMessage: 截断消息，不再传递
        """
        return message
    
    def on_before_send(self, message: Message) -> Message:
        """消息发送前处理"""
        return message
    
    def on_unload(self) -> None:
        """插件卸载时调用"""
        pass


class DropMessage(Exception):
    """抛出此异常以截断消息，不再继续传递"""
    pass
```

### 2.2 PluginManager (plugins/manager.py)

```python
class PluginManager:
    """插件管理器"""
    
    def __init__(self, plugin_dir: Optional[str] = None, timeout: float = 5.0):
        """初始化插件管理器"""
    
    # 注册
    def register_plugin_class(self, plugin_class: Type[Plugin]) -> None:
        """注册插件类"""
    
    def register_plugin_instance(self, plugin: Plugin) -> None:
        """注册插件实例"""
    
    # 查询
    def get_plugin(self, name: str) -> Optional[Plugin]:
        """获取已加载的插件"""
    
    def is_loaded(self, name: str) -> bool:
        """检查插件是否已加载"""
    
    def list_plugins(self) -> List[Dict[str, Any]]:
        """列出所有注册的插件"""
    
    def get_plugin_info(self, name: str) -> Optional[Dict[str, Any]]:
        """获取插件信息"""
    
    # 生命周期
    async def load_plugin(self, name: str, plugin_class: Optional[Type[Plugin]] = None) -> Plugin:
        """加载单个插件"""
    
    async def unload_plugin(self, name: str) -> bool:
        """卸载插件"""
    
    async def reload_plugin(self, name: str) -> Plugin:
        """热重载插件（准热拔插）"""
    
    async def load_all(self) -> List[Plugin]:
        """按依赖顺序加载所有插件"""
    
    async def unload_all(self) -> None:
        """卸载所有插件"""
    
    # 发现
    async def discover_plugins(self, directory: Optional[Path] = None) -> int:
        """从目录发现并自动加载插件"""
    
    # 事件总线集成
    def set_event_bus(self, event_bus: PluginEventBus) -> None:
        """设置事件总线"""
```

### 2.3 PluginEventBus (plugins/events.py)

```python
class PluginEventBus:
    """插件事件总线"""
    
    def __init__(self, max_depth: int = 10, timeout: float = 5.0):
        """初始化事件总线"""
    
    # 订阅/取消订阅
    def on(self, event_name: str, handler: Callable, plugin_name: str, priority: int = 50) -> None:
        """订阅事件"""
    
    def off(self, event_name: str, handler: Callable) -> bool:
        """取消订阅事件"""
    
    def off_all(self, plugin_name: str) -> int:
        """取消订阅插件的所有事件"""
    
    # 发布
    def emit(self, event_name: str, *args, depth: int = 0, **kwargs) -> List[Any]:
        """同步发布事件"""
    
    async def emit_async(self, event_name: str, *args, depth: int = 0, **kwargs) -> List[Any]:
        """异步发布事件"""
    
    # 查询
    def get_handlers(self, event_name: str) -> List[Tuple[int, str]]:
        """获取事件处理器列表"""
    
    def list_events(self) -> List[str]:
        """列出所有已注册的事件"""
    
    def clear(self) -> None:
        """清除所有事件处理器"""
```

---

## 3. 内置插件

### 3.1 HelloWorldPlugin

简单的 Hello World 示例插件：
- 记录所有收到的消息
- 丢弃包含 "bad" 的消息
- 在消息元数据中添加 `processed_by` 标记

```python
class HelloWorldPlugin(Plugin):
    name = "hello-world"
    version = "1.0.0"
    dependencies = []
    priority = 10  # 低优先级，后执行
```

### 3.2 MessageLoggerPlugin

消息日志插件：
- 高优先级执行（priority=100），先捕获原始消息
- 支持配置是否记录输入/输出消息
- 维护最近消息列表（受 `max_entries` 限制）

```python
class MessageLoggerPlugin(Plugin):
    name = "message-logger"
    version = "1.0.0"
    dependencies = []
    priority = 100  # 高优先级，先执行
    
    def __init__(self, log_incoming: bool = True, log_outgoing: bool = True, max_entries: int = 1000):
        ...
```

---

## 4. 实现细节

### 4.1 依赖解析（拓扑排序）

使用 Kahn 算法进行拓扑排序：

```
1. 构建依赖图和入度表
2. 将入度为 0 的节点加入队列
3. 循环处理队列：
   - 弹出节点，加入结果
   - 更新依赖此节点的节点的入度
   - 入度变为 0 的节点加入队列
4. 检测循环：如果结果数量 != 节点数量，则存在循环依赖
```

### 4.2 准热拔插

通过 `importlib.reload()` 实现模块重载：

```
reload_plugin(name):
  1. 卸载旧插件实例
  2. 查找插件对应的模块
  3. importlib.reload(module)
  4. 从重载后的模块找到 Plugin 类
  5. 加载新实例
```

### 4.3 错误隔离

每个插件的事件处理器都包裹在 try-except 中，异常不会传播：

```python
for priority, handler, plugin_name in handlers:
    try:
        result = handler(*args, **kwargs)
        results.append(result)
    except Exception as e:
        logger.error(f"Error in event handler {plugin_name}.{event_name}: {e}")
        results.append(None)  # 继续处理其他处理器
```

### 4.4 循环检测

通过 `depth` 参数和 `max_depth` 限制防止事件链循环：

```python
if depth > self._max_depth:
    logger.warning(f"Event chain exceeded max depth ({self._max_depth}): {event_name}")
    raise RecursionError(f"Event chain exceeded max depth for {event_name}")
```

---

## 5. 测试策略

### 5.1 单元测试

| 模块 | 测试项 | 覆盖率目标 |
|------|--------|-----------|
| base.py | Plugin 生命周期、属性、DropMessage | > 90% |
| manager.py | 注册、加载、卸载、依赖解析、热拔插 | > 85% |
| events.py | 订阅、发布、优先级、错误隔离、循环检测 | > 90% |
| builtin/ | HelloWorldPlugin、MessageLoggerPlugin | > 80% |

### 5.2 集成测试

- 完整的插件生命周期测试
- 事件总线与插件管理器集成测试

---

## 6. 文件结构

```
src/sprinkle/plugins/
├── __init__.py          # 模块导出
├── base.py              # Plugin 基类和 DropMessage
├── manager.py           # PluginManager
├── events.py            # PluginEventBus
└── builtin/
    ├── __init__.py
    ├── hello_world.py   # HelloWorldPlugin
    └── message_logger.py # MessageLoggerPlugin
```

---

## 7. 异常类型

| 异常 | 说明 |
|------|------|
| `DropMessage` | 插件抛出以截断消息 |
| `PluginLoadError` | 插件加载失败 |
| `PluginDependencyError` | 依赖解析失败（循环依赖或缺少依赖）|

---

## 8. 配置项

通过 `PluginManager` 构造函数配置：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `plugin_dir` | str | "./plugins" | 插件目录 |
| `timeout` | float | 5.0 | 插件操作超时(秒) |

通过 `PluginEventBus` 构造函数配置：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_depth` | int | 10 | 事件链最大深度 |
| `timeout` | float | 5.0 | 事件处理器超时(秒) |

---

*设计文档由司康编写~🍪*
