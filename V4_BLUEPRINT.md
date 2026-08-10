# RootCause Agent v4 重构蓝图

> 基于自研内部溯源 skill（Obsidian 02 文档）的设计思想，
> 把"字段溯源协议"泛化为"根因定位诊断协议"。
> 状态：等待用户提供 log_search.py + 真实 APM 日志后实施。

## 为什么 v3 打不过 RTK（诚实复盘）

v3 在"语法层压缩"（模板化+去重）上和 RTK 竞争：
- RTK: 969 tok / 96.7% 准确率
- ours v3: 1999 tok / 90% 准确率

**结论：语法层是 RTK 主场，不该在那里拼。** RTK 是"压缩器"（去冗余），
我们是"诊断协议"（找根因）。差异必须在诊断架构上体现。

## 自研内部溯源 skill 的 5 个核心设计

### 1. 脚本层 / Agent 层分工（最重要）
```
脚本层(确定性,零LLM): 取数 → 正则抽关键字段 → 裁剪 → 结构化证据
Agent层(大模型): 只读结构化证据 → 语义判断 → 输出报告

自研溯源 skill 具体做法:
  - log_search.py (650行, 零LLM) 用 HMAC-SHA1 鉴权拉 APM
  - 正则抽 5 个字段: request_in / http_calls / resp / errno / latency
  - 单 span 几十 KB → 几行结构化 JSON
  - LLM 从不接触原始日志
```

### 2. 便宜→贵 短路求值（token 节省的真正来源）
```
Step 2.6 读 getter(最便宜) → 命中即收工，不拉 APM(最贵)
同思想: 数据库查询优化器，先用便宜索引过滤

对应到根因定位:
  L0 grep 代码(便宜) → 异常类名/方法名命中 → 收工
  L1 日志结构化提取(中) → 抽 errno/traceId/调用链
  L2 嵌套trace跳转(中) → 发现非当前traceId → 追进去
  L3 LLM 分析(贵) → 只有前面不够才做
```

### 3. 信息裁剪 ≠ 最大压缩
```
自研溯源 skill:
  - 正则抽 5 字段, 不碰原始日志
  - 截断 2000 字符只在 json.loads 失败时触发 (只砍坏数据, 不碰真数据)
  - --summary 只取 uri 骨架 | --filter-uri 只对关心的 span 拉详情

对应到根因定位:
  - 日志压缩改为"字段提取": 抽 traceId/errno/errorMsg/调用链, 不是按行压
  - 合法结构化数据全量保留, 只截断解析失败的残串
```

### 4. 防误判护栏（准确率关键）
```
护栏① 嵌套 traceid 检测 (Step 4.2):
  聚合服务下游断链生成独立 trace → grep -oE '[a-f0-9]{32}' 找非当前 traceId
  真源头在嵌套 trace 里, 不在当前 trace

护栏② errno 语义矩阵 (Step 6):
  errno=0 + 空 → 业务正常 (用户确实没数据)
  errno≠0 + 空 → 接口故障 (鉴权/超时/配置错)
  静默吞 status: HttpUtils 返回空串 ≠ 下游空, 可能 403 被转成空串

护栏③ 全文 grep 预检 (Step 4.1):
  目标值可能在邻居接口 → 先全 trace grep, 别只看一个接口

护栏④ 查询发出 ≠ 查询命中:
  看到发了 ES 查询 ≠ 数据来自它, 必须验证响应实际含目标值
```

### 5. 置信度分级 + 七节报告
```
置信度: 高=有 file:line 证据 | 中低=间接证据必列出 | 无=禁止编造
七节: ①来源类型 ②入口接口 ③下游条件 ④完整规则 ⑤最终值 ⑥DB映射 ⑦置信度
模板化输出 = 可复查、可比对
```

## v4 架构设计

```
输入: 问题描述 + traceId + 日志 + 代码仓库 + (可选)服务拓扑

┌─ 脚本层 (确定性, 零 LLM) ─────────────────────────────┐
│  L0 代码定位    grep 异常类名/方法名 → 定位文件+行号    │
│  L1 日志结构化  抽 traceId/errno/errorMsg/调用链        │
│  L2 嵌套trace   找非当前 traceId → 递归 L1              │
│  L3 护栏检查    errno 语义 / 静默吞status / 邻居grep    │
└───────────────────────────────────────────────────────┘
                  ↓ 只喂结构化证据
┌─ Agent 层 (大模型, 语义判断) ──────────────────────────┐
│  L4 根因分析    综合结构化证据 → 判定根因链             │
│  L5 七节报告    固定模板 + file:line 证据 + 置信度      │
└───────────────────────────────────────────────────────┘

Benchmark 指标升级:
  准确率 (LLM-judge) + 证据引用率(file:line命中) + token 消耗
  对比: baseline / ours / RTK / Drain / headroom
```

## 与开源方案的差异（回答"独到之处"）

| 维度 | RTK/Headroom/LogPare | ours v4 |
|------|---------------------|---------|
| 定位 | 压缩器（去冗余） | 诊断协议（找根因） |
| 处理单位 | 每行/每块独立 | traceId 粒度（链路） |
| 上下文 | 无（不懂业务结构） | 服务拓扑 + 代码仓库 |
| 调度 | 一次性压缩 | 便宜→贵短路求值 |
| 防错 | 无 | 嵌套trace/errno语义/静默status |
| 输出 | 压缩文本 | 七节报告 + 置信度 + 证据 |

## 待用户提供

1. ~~log_search.py（5 字段正则提取实现）~~ ✅ 已就位（Obsidian 个人 skill 库）
2. 真实 APM 日志样本（脱敏，几十~几百行）
3. 服务调用关系 / 服务列表（可选）
4. ~~自研溯源 SKILL.md（协议详细步骤）~~ ✅ 已就位（836 行完整版）

---

## 已吸收的实现细节（2026-08-01 从 log_search.py 提取）

### A. 5 字段正则提取（信息裁剪的具体实现）
```python
_BODY_RE = re.compile(r"body=(\{[^|]*\})")
_RESP_RE = re.compile(r"(?:resp|result)=(\{.*?)(?=\|\|httpStatus|\|\|http\b|$)", re.DOTALL)
_INTERFACE_RE = re.compile(r"interface=([^|]+)")
_LATENCY_RE = re.compile(r"latency=(\d+)")
_ERRNO_RE = re.compile(r"\|\|errno=(\d+)")
```
- 触发标记: `_com_request_in`（入参）、`_com_http_success/_com_http_failure/_com_request_out`（下游调用）
- 产出: `request_in{interface, body}` + `http_calls[]{interface, body, resp, latency_ms, errno, status}`

### B. _safe_json_extract（只砍坏数据）
```python
def _safe_json_extract(s, max_len=2000):
    try:    return json.loads(s)   # 合法 JSON → 全量，不截断
    except: return s[:max_len]     # 只有残串才截
```

### C. span 聚合流水线（trace_detail）
```
queryTraceLink(拿到span树) → collect_spans(DFS递归平铺) 
  → 每span调 spanDetail (throttle 0.6s + 指数退避重试 429/503)
  → parse_span_logs(5字段正则) → 结构化输出 {uri, host, request_in, http_calls[]}
```

### D. 双鉴权模式
- Cookie: monitor 日志检索页（无 traceId 时按关键字反查 traceId 列表）
- HMAC-SHA1: observeapi 后端 API（时间窗 300s 对齐，无状态签名不过期）

### E. SKILL.md 反模式速查（16 条踩坑，已并入 v4 护栏设计）
1. 全文 grep 预检（目标值在邻居接口）
2. 嵌套 traceid 检测（真源头在另一条 trace）
3. 业务方法日志 > Filter 日志（Filter 截断 ~1000 字节）
4. errno 语义矩阵（空值 ≠ 故障）
5. 二级索引延迟（刚写入查不到 ≠ 数据不存在）
6. 静默吞 status（HttpUtils 空串 ≠ 下游空，可能 403）
7. 查询发出 ≠ 查询命中（必须验证 resp 实际含目标值）
8. 字段名形状反推业务线是误判
9. 硬编码 Apollo key 是误判（必须数据驱动）
10. LLM 干 grep 的活是浪费（确定性模式必须前置工具）

---

## v4 实施清单（明天拿到真实日志后）

1. 把 `parse_span_logs` 5 字段提取泛化为通用 APM 日志解析器（正则按公司格式定制）
2. 实现 `trace_detail` 聚合流水线（span 树 DFS + 限流 + 重试）
3. 日志压缩 v4 = 5 字段提取 + traceId 链路聚合 + errno/status 语义标注
4. 短路求值调度器（L0 grep → L1 聚合 → L2 LLM）
5. Benchmark 指标加证据引用率（file:line 命中）

---

## 落地状态（2026-08 更新）

蓝图中的护栏设计在 v4 产品（locator/agent.py + common/log_compressor.py）的落地情况：

| 蓝图设计 | 状态 | 落地位置 |
|---|---|---|
| 信号保真压缩（数字/错误码保留） | ✅ 已落地 | common/log_compressor.py（数字保真模板化） |
| 服务级错误分布 | ✅ 已落地 | common/log_compressor.py service_error_distribution |
| 高价值错误过滤 | ✅ 已落地 | common/log_compressor.py LOW_VALUE_RE |
| 短路求值调度器（L0→L1→L3） | ✅ 已落地（2026-08 补回） | locator/agent.py short_circuited（错误信号命中跳过 L2） |
| errno 语义矩阵 | ✅ 已落地（2026-08 补回） | locator/agent.py sys_prompt 第 4 条护栏 |
| 置信度分级 + 七节报告 | ✅ 已落地（2026-08 补回） | locator/agent.py sys_prompt（七节模板 + 高/中/低置信度） |
| 全文 grep 预检 / 嵌套 traceid | ⏳ 待接 APM 数据源后实现（依赖 log_search.py 接入） |
| Benchmark 证据引用率指标 | ⏳ 待实现 |

护栏补回验证：LogDx-CI 0.9296 不变（确定性）、RE3 93.3% 波动内一致（评测链路独立于 agent.py），零影响。
