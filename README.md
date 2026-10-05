# LEO 卫星遥测网关

低轨卫星地面站遥测信道的 TCP 接入网关。多个 TCP 连接可能送达同一信道的数据，
网关在任意拆包、粘包、并发重传与乱序下，为每个信道重建一致的连续载荷摘要。

## 帧格式（大端）

| 字段 | 长度 | 说明 |
| --- | --- | --- |
| 同步字 | 4 | `1A CF FC 1D` |
| 信道号 | 1 | `0..255` |
| 序号 | 4 | uint32，每信道从 0 开始 |
| 载荷长度 | 2 | uint16，`1..1024` |
| 载荷 | N | 1–1024 字节 |
| CRC32 | 4 | 覆盖信道号至载荷（IEEE CRC32，与 zlib 一致） |

## 信道规则

- 序号自 0 起连续计序；领先 `nextSequence` 不超过 **31** 的帧暂存，缺口补齐后按序并入摘要。
- 同序号**同载荷**重传：忽略（无论帧是否已交付）。
- 同序号**内容冲突**，或序号领先超过 31（窗口越界）：信道永久标记 `failed`，
  后续帧全部忽略，状态冻结。
- 非法长度或 CRC 错误：只丢弃该候选帧，从其后每个字节重新寻找同步字
  （同步字可能恰好出现在坏帧载荷内），不影响任何信道状态。

## HTTP API

`GET /api/channels/{id}`

```json
{
  "channelId": 1,
  "status": "active",
  "nextSequence": 6,
  "seenSequences": 6,
  "contiguousPayloadBytes": 912,
  "sha256": "…连续载荷 SHA-256…",
  "failureReason": null
}
```

- `status`：`active` 或 `failed`；失败时 `failureReason` 给出明确原因。
- 未知信道返回 `404`。
- `GET /healthz` 返回 200，供容器健康检查使用。

## 配置

环境变量（HTTP/TCP 宿主机端口均可配置）：

| 变量 | 默认值 |
| --- | --- |
| `HTTP_PORT` | `8080` |
| `TCP_PORT` | `9100` |
| `HTTP_HOST` / `TCP_HOST` | `0.0.0.0` |

## Docker

```bash
# 自定义端口
HTTP_PORT=18080 TCP_PORT=19100 docker compose up --build gateway

# 运行一次性校验服务（健康检查通过后自动执行；退出码即其结论）
docker compose up --build --abort-on-container-exit --exit-code-from verify
```

`verify` 服务依次执行，任一步失败即以非零码退出：

1. 镜像内项目构建（setuptools 构建并安装）；
2. 代码测试（重组器/状态机单元测试 + 真实 socket 集成测试）；
3. 真实 TCP 写入与 HTTP 查询冒烟（多连接乱序、单字节拆包、坏帧粘连、冲突信道）。

## 本地开发（仅标准库；测试需要 pytest）

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install pytest -e .
python -m pytest -q
HTTP_PORT=8080 TCP_PORT=9100 python -m app.main   # 另开终端
GATEWAY_HOST=127.0.0.1 python scripts/smoke.py
```

## 项目结构

```
app/framing.py     流式帧重组（拆包/粘包/坏帧重同步）
app/channels.py    信道状态机（窗口、去重、冲突、连续摘要）
app/gateway.py     跨连接共享的信道注册表
app/server.py      TCP 接入与 HTTP 查询（标准库）
app/main.py        入口与环境变量配置
scripts/smoke.py   真实 TCP/HTTP 冒烟
scripts/verify.sh  构建 + 测试 + 冒烟三阶段（verify 服务入口）
tests/             单元与真实 socket 集成测试
```
