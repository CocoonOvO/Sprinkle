# 异步文件上传设计

## 背景

当前文件上传是同步的，用户需要等待文件上传完成才能发送消息，体验黏着不流畅。

## 目标

实现异步文件上传：用户发送消息后，文件在后台异步上传，消息状态实时更新，支持多端同步。

## 设计方案

### 1. File 模型变更

新增字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | Enum | uploading/success/failed |
| `error_message` | String | 失败原因（可选） |
| `message_id` | String | 关联消息ID（可选） |

字段变更：
- `file_path` 改为 nullable（上传成功前为空）

### 2. Message 模型变更

在 `message_metadata` 中新增 `file_ids` 字段，类型为 `List[str]`。

### 3. 新增 API

#### 异步上传接口

```
POST /api/v1/files/upload-async
{
  "message_id": "xxx",
  "file_name": "test.pdf",
  "file_size": 1024
}

返回：
{
  "file_id": "xxx",
  "status": "uploading",
  "upload_url": "/api/v1/files/{file_id}/upload-content"
}
```

#### 上传文件内容

```
POST /api/v1/files/{file_id}/upload-content
Content-Type: multipart/form-data

body: file 二进制内容

返回：文件状态更新为 success
```

#### 创建带文件的消息

```
POST /api/v1/conversations/{id}/messages
{
  "content": "看这个文件",
  "content_type": "file",
  "file_names": ["test.pdf", "doc.txt"]
}

返回：消息（file_ids 字段包含关联的文件ID列表，状态均为 uploading）
```

### 4. WebSocket 事件

新增事件：

```python
FILE_UPLOAD_COMPLETED = "file.upload.completed"
FILE_UPLOAD_FAILED = "file.upload.failed"
```

推送数据结构：

```python
{
    "event": "file.upload.completed",
    "data": {
        "message_id": "msg_xxx",
        "file_id": "file_yyy",
        "file_url": "/api/v1/files/file_yyy/download"
    }
}

{
    "event": "file.upload.failed",
    "data": {
        "message_id": "msg_xxx",
        "file_id": "file_yyy",
        "error": "文件过大"
    }
}
```

### 5. 实现步骤

| Step | Task |
|------|------|
| 1 | File 模型增加 status、error_message、message_id 字段 |
| 2 | Message 模型 message_metadata 增加 file_ids 支持 |
| 3 | 添加 WebSocket 事件类型 |
| 4 | 创建异步上传 API（upload-async + upload-content） |
| 5 | 消息 API 支持 file_names 参数 |
| 6 | 实现 WebSocket 推送逻辑 |
| 7 | 迁移脚本更新现有数据 |

### 6. 注意

- 保持现有同步上传接口不变
- 禁止删除或修改已有数据表结构
- 现有数据通过迁移脚本更新 status=success