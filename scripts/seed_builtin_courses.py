#!/usr/bin/env python3
"""Seed built-in courses for OpenLearn."""
import json, time, uuid, os

COURSES = [
    {
        "course_id": f"course_{uuid.uuid4().hex[:12]}",
        "title": "OpenSoulmate 快速入门",
        "description": "从零开始了解 OpenSoulmate 生态系统，掌握核心概念和基本操作。",
        "tags": ["入门", "教程", "opensoulmate"],
        "topics": ["安装部署", "核心概念", "基本操作"],
        "domain": "platform",
        "knowledge_ids": [],
        "generated_by": "builtin",
        "status": "not_started",
        "created_at": time.time(),
        "updated_at": time.time(),
        "chapters": [
            {
                "chapter_id": f"ch_{uuid.uuid4().hex[:8]}",
                "title": "什么是 OpenSoulmate？",
                "order": 0,
                "completed": False,
                "completed_at": None,
                "content": """# 什么是 OpenSoulmate？

## 核心理念

**"One Soul, Infinite Soma."** — 一个中央记忆大脑，无数分布式感知分身。

OpenSoulmate 是一个 **AI 操作系统**，它不是一个单一的 AI 工具，而是管理所有 AI 工具的平台。

## 架构概览

OpenSoulmate 由 **25 个组件** 组成，分为 4 层：

### 核心主体层（8个）
| 组件 | 隐喻 | 功能 |
|------|------|------|
| **OpenSoul** | 🧠大脑 | 中央记忆内核、RAG、知识图谱 |
| **OpenMate** | 👤共生伙伴 | 多端交互入口（Web/桌面/插件） |
| **OpenCortex** | 🧠皮层 | 高级认知、多Agent协作 |
| **OpenNerve** | ⚡神经 | 事件总线、WebSocket |
| **OpenVein** | 🩸血管 | 大文件分片、缓存 |
| **OpenSoma** | 🤖躯体 | 分布式采集Agent |
| **OpenSense** | 👁感官 | OCR、ASR、多模态 |
| **OpenWill** | ✨意志 | 工作流编排 |

### 配套底座层（8个）
OpenImmune🛡 / OpenVital📊 / OpenMarrow🦴 / OpenGland🧪 / OpenGene🧬 / OpenEcho🔊 / OpenMirror🪞 / OpenLink🔗

### 远期高级生态层（9个）
OpenHippo🧠 / OpenReflex⚡ / OpenHeredity🔗 / OpenNest🏠 / OpenPulse💓 / OpenLimb💪 / OpenVoice🎤 / OpenVision🎨 / OpenMind💭

## 设计原则

1. **一切皆插件** — 配置即组合
2. **松耦合** — 组件独立部署，API通信
3. **即插即用** — 启动自动注册
4. **全链路可追溯** — Trajectory追踪
""",
                "quiz": [
                    {
                        "question": "OpenSoulmate 的核心定位是什么？",
                        "options": ["AI聊天工具", "AI操作系统/管理平台", "代码编辑器", "文档管理系统"],
                        "correct_index": 1,
                        "explanation": "OpenSoulmate 是管理所有 AI 工具的平台，不是单一功能的 AI 工具。"
                    },
                    {
                        "question": "OpenSoulmate 一共有多少个组件？",
                        "options": ["8个", "16个", "25个", "32个"],
                        "correct_index": 2,
                        "explanation": "OpenSoulmate 由 25 个组件组成，分为 4 个层级。"
                    },
                    {
                        "question": "哪个组件负责大文件分片和缓存？",
                        "options": ["OpenNerve", "OpenVein", "OpenSoma", "OpenSense"],
                        "correct_index": 1,
                        "explanation": "OpenVein（血管）负责大文件分片上传、缓存管理和资源同步。"
                    }
                ]
            },
            {
                "chapter_id": f"ch_{uuid.uuid4().hex[:8]}",
                "title": "快速部署与启动",
                "order": 1,
                "completed": False,
                "completed_at": None,
                "content": """# 快速部署与启动

## 系统要求

- Python 3.11+
- Node.js 18+
- PostgreSQL 17（可选，开发可用SQLite）
- 2GB+ RAM

## 启动 OpenSoul（后端）

```bash
cd ~/opensoul
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn src.main:app --host 0.0.0.0 --port 8090
```

## 启动 OpenMate（前端）

```bash
cd ~/openmate
npm install
npm run dev -- --port 3002
```

## 验证部署

1. 访问 http://localhost:3002 打开 OpenMate
2. 访问 http://localhost:8090/api/health 检查后端状态
3. 访问 http://localhost:8090/api/health/all 检查所有器官

## 核心概念

- **器官（Organ）**: 每个组件都是一个"器官"，有独立的 API 和健康检查
- **事件总线**: 组件间通过 OpenNerve 事件总线通信
- **知识库**: 文档采集 → 入库 → RAG 问答 的核心流程
""",
                "quiz": [
                    {
                        "question": "OpenSoul 默认运行在哪个端口？",
                        "options": ["3000", "3002", "8000", "8090"],
                        "correct_index": 3,
                        "explanation": "OpenSoul 后端默认运行在 8090 端口。"
                    },
                    {
                        "question": "如何检查所有器官的健康状态？",
                        "options": ["/api/health", "/api/health/all", "/api/status", "/api/organs"],
                        "correct_index": 1,
                        "explanation": "/api/health/all 会并行检查所有器官的健康端点。"
                    }
                ]
            },
            {
                "chapter_id": f"ch_{uuid.uuid4().hex[:8]}",
                "title": "知识库管理",
                "order": 2,
                "completed": False,
                "completed_at": None,
                "content": """# 知识库管理

## 知识入库流程

```
文档采集 → 预处理 → 分块 → 向量化 → 入库 → RAG检索
```

## 支持的文档格式

- **文本**: Markdown, TXT, JSON, YAML
- **文档**: PDF, DOCX, PPTX
- **代码**: 所有编程语言
- **网页**: URL 抓取
- **多媒体**: 图片(OCR), 音频(ASR)

## 知识库操作

### 创建知识库
通过 OpenMate 的「知识」页面创建，或通过 API：

```bash
curl -X POST http://localhost:8090/api/knowledge \\
  -H "Content-Type: application/json" \\
  -d '{"title": "我的文档", "content": "...", "tags": ["tag1"]}'
```

### RAG 搜索
```bash
curl -X POST http://localhost:8090/api/search \\
  -d '{"query": "如何配置...", "sources": ["knowledge"]}'
```

### 知识图谱
OpenSoul 自动构建实体-关系图谱，支持可视化浏览。

## 最佳实践

1. **文档分块**: 保持 500-1000 字的块大小
2. **标签管理**: 使用有意义的标签便于检索
3. **定期更新**: 过时文档及时更新或删除
4. **质量优先**: 高质量文档优先入库
""",
                "quiz": [
                    {
                        "question": "知识入库的标准流程是什么？",
                        "options": [
                            "采集 → 存储",
                            "采集 → 预处理 → 分块 → 向量化 → 入库",
                            "上传 → 下载",
                            "编辑 → 发布"
                        ],
                        "correct_index": 1,
                        "explanation": "标准流程是：文档采集 → 预处理 → 分块 → 向量化 → 入库 → RAG检索。"
                    }
                ]
            }
        ]
    },
    {
        "course_id": f"course_{uuid.uuid4().hex[:12]}",
        "title": "25组件架构详解",
        "description": "深入理解 OpenSoulmate 的 25 组件架构设计，掌握每个组件的职责和协作方式。",
        "tags": ["架构", "组件", "高级"],
        "topics": ["核心层", "底座层", "高级生态层"],
        "domain": "architecture",
        "knowledge_ids": [],
        "generated_by": "builtin",
        "status": "not_started",
        "created_at": time.time(),
        "updated_at": time.time(),
        "chapters": [
            {
                "chapter_id": f"ch_{uuid.uuid4().hex[:8]}",
                "title": "核心主体层 — 生命体骨架",
                "order": 0,
                "completed": False,
                "completed_at": None,
                "content": """# 核心主体层 — 生命体骨架

## 8 个核心组件

### 🧠 OpenSoul — 大脑
- **职责**: 中央记忆内核、文档解析、RAG多路召回、知识图谱、RBAC权限
- **技术**: PostgreSQL + Qdrant + Meilisearch
- **API**: /api/knowledge, /api/search, /api/graph

### 👤 OpenMate — 共生伙伴
- **职责**: 多端交互入口（Web、Tauri桌面、浏览器插件、MCP客户端）
- **技术**: Next.js 16 + shadcn/ui
- **端口**: 3002

### 🧩 OpenCortex — 大脑皮层
- **职责**: 高级认知、长周期任务规划、CoT思维链、多Agent协作推理
- **API**: /api/cortex

### ⚡ OpenNerve — 神经
- **职责**: 事件总线、WebSocket长连接、分布式节点消息分发
- **API**: /api/nerve

### 🩸 OpenVein — 血管
- **职责**: 大文件分片上传、缓存管理、分布式资源同步
- **特性**: 内容寻址去重、文件版本管理
- **API**: /api/vein

### 🤖 OpenSoma — 躯体
- **职责**: 分布式采集Agent、第三方连接器、多源数据采集
- **原则**: 只读采集，不执行写入操作

### 👁 OpenSense — 感官
- **职责**: OCR图像识别、ASR语音转写、视频抽帧解析
- **API**: /api/sense

### ✨ OpenWill — 意志
- **职责**: 复杂工作流编排、条件触发、多分支定时业务流程
- **API**: /api/will

## 组件间关系

```
👤 OpenMate（用户端）
    ↓
✨ OpenWill（编排）    👁 OpenSense（感知）
    ↓                      ↓
🤖 OpenSoma（采集）    ⚡ OpenNerve（总线）
    ↓                      ↓
🧠 OpenSoul（大脑） ← 🧩 OpenCortex（推理）
    ↑
🩸 OpenVein（文件流转）
```
""",
                "quiz": [
                    {
                        "question": "OpenSoma 的核心原则是什么？",
                        "options": ["读写皆可", "只读采集", "只写执行", "完全隔离"],
                        "correct_index": 1,
                        "explanation": "OpenSoma 遵循只读采集原则，不执行写入操作。写入操作由 OpenLimb 负责。"
                    },
                    {
                        "question": "哪个组件负责文件版本管理和内容寻址去重？",
                        "options": ["OpenSoul", "OpenVein", "OpenMarrow", "OpenNerve"],
                        "correct_index": 1,
                        "explanation": "OpenVein（血管）提供内容寻址文件存储和版本管理功能。"
                    }
                ]
            },
            {
                "chapter_id": f"ch_{uuid.uuid4().hex[:8]}",
                "title": "配套底座层 — 内脏维持系统",
                "order": 1,
                "completed": False,
                "completed_at": None,
                "content": """# 配套底座层 — 内脏维持系统

## 8 个配套组件

### 🛡 OpenImmune — 免疫系统
- **职责**: 内容风控、敏感数据脱敏、访问限流、水印溯源
- **功能**: PII检测、速率限制、IP黑白名单、安全审计
- **API**: /api/immune

### 📊 OpenVital — 生命体征
- **职责**: 全平台指标采集、节点健康状态、性能监控、告警
- **API**: /api/vital

### 🦴 OpenMarrow — 骨髓
- **职责**: 知识库快照、定时备份、灾备恢复、数据迁移
- **功能**: 备份创建/恢复、定时备份调度、数据导入导出
- **API**: /api/marrow

### 🧪 OpenGland — 内分泌腺体
- **职责**: 多LLM统一调度、模型池路由、密钥管理、Token计量
- **API**: /api/gland

### 🧬 OpenGene — 基因
- **职责**: 行业预制模板库、Agent配方、知识库模板、工作流模板
- **内置**: 23个模板（Agent/知识库/工作流/Skill）
- **API**: /api/gene

### 🔊 OpenEcho — 回声
- **职责**: 多渠道消息推送（钉钉/企微/邮件/SMS/Webhook）
- **API**: /api/echo

### 🪞 OpenMirror — 镜像
- **职责**: 隔离测试沙箱，调试工作流/连接器/Agent
- **API**: /api/mirror

### 🔗 OpenLink — 突触
- **职责**: 外部系统双向Webhook、OA/ERP低代码对接
- **功能**: 连接器管理、Webhook收发、事件记录
- **API**: /api/link
""",
                "quiz": [
                    {
                        "question": "OpenGland 的核心职责是什么？",
                        "options": ["文件存储", "多LLM统一调度", "消息推送", "备份恢复"],
                        "correct_index": 1,
                        "explanation": "OpenGland（内分泌腺体）负责多LLM统一调度、模型池路由和Token计量。"
                    },
                    {
                        "question": "OpenGene 内置了多少个模板？",
                        "options": ["8个", "15个", "23个", "25个"],
                        "correct_index": 2,
                        "explanation": "OpenGene 内置了 23 个模板，涵盖 Agent、知识库、工作流和 Skill 四类。"
                    }
                ]
            },
            {
                "chapter_id": f"ch_{uuid.uuid4().hex[:8]}",
                "title": "远期高级生态层",
                "order": 2,
                "completed": False,
                "completed_at": None,
                "content": """# 远期高级生态层 — 高级生命机能

## 9 个远期组件

### 🧠 OpenHippo — 海马体
- **职责**: 短期记忆管理、记忆自动归档、遗忘衰减策略
- **特性**: 访问强化衰减、多策略遗忘、会话生命周期
- **API**: /api/hippo

### ⚡ OpenReflex — 条件反射
- **职责**: 高频问题缓存、短路径快速反射响应
- **特性**: 模糊匹配、相似度阈值、自动过期
- **API**: /api/reflex

### 🔗 OpenHeredity — 遗传链
- **职责**: 平台配置演化、插件版本管理、平滑升级
- **API**: /api/heredity

### 🏠 OpenNest — 细胞巢穴
- **职责**: 租户资源配额、向量空间逻辑隔离、多租户管控
- **API**: /api/nest

### 💓 OpenPulse — 脉搏
- **职责**: 底层高频时钟信号、亚秒级周期性轮询
- **特性**: 漂移校正、精度监控
- **API**: /api/pulse

### 💪 OpenLimb — 四肢
- **职责**: 浏览器自动化、表单填报、外部系统写入
- **API**: /api/limb

### 🎤 OpenVoice — 声带
- **职责**: TTS文字转语音、语音角色管理
- **API**: /api/voice

### 🎨 OpenVision — 视觉成像中枢
- **职责**: 图表生成、思维导图、示意图
- **API**: /api/vision

### 💭 OpenMind — 心智中心
- **职责**: 用户情绪识别、对话人格库、个性化语气
- **API**: /api/mind
""",
                "quiz": [
                    {
                        "question": "哪个组件实现了记忆的遗忘衰减机制？",
                        "options": ["OpenSoul", "OpenHippo", "OpenReflex", "OpenMind"],
                        "correct_index": 1,
                        "explanation": "OpenHippo（海马体）负责短期记忆管理和遗忘衰减策略。"
                    },
                    {
                        "question": "OpenLimb 和 OpenSoma 的核心区别是什么？",
                        "options": [
                            "没有区别",
                            "Soma只读采集，Limb写入执行",
                            "Limb只读，Soma写入",
                            "Soma处理文本，Limb处理图片"
                        ],
                        "correct_index": 1,
                        "explanation": "OpenSoma只读采集数据，OpenLimb负责写入执行操作外部系统。"
                    }
                ]
            }
        ]
    },
    {
        "course_id": f"course_{uuid.uuid4().hex[:12]}",
        "title": "Agent 开发实战",
        "description": "学习如何在 OpenSoulmate 平台上创建、配置和管理 AI Agent。",
        "tags": ["agent", "开发", "实战"],
        "topics": ["Agent配置", "工具注册", "多Agent协作"],
        "domain": "development",
        "knowledge_ids": [],
        "generated_by": "builtin",
        "status": "not_started",
        "created_at": time.time(),
        "updated_at": time.time(),
        "chapters": [
            {
                "chapter_id": f"ch_{uuid.uuid4().hex[:8]}",
                "title": "Agent 基础概念",
                "order": 0,
                "completed": False,
                "completed_at": None,
                "content": """# Agent 基础概念

## 核心公式

```
Agent = Model + Tools + Session + Sandbox
```

- **Model（模型）** — 通过 OpenGland 统一调度
- **Tools（工具）** — 通过 OpenGene 模板库管理
- **Session（会话）** — 通过 OpenNerve 管理
- **Sandbox（沙箱）** — 通过 OpenMirror 隔离

## Agent 类型

### 内置 Agent
- **Hermes**: 全能Agent，可调用所有工具
- **MiMo**: 代码Agent，专注于代码生成

### 自定义 Agent
通过 OpenMate 的「Agent」页面创建，或使用 Gene 模板。

## Agent 配置示例

```yaml
agents:
  - id: "my-agent"
    model: "deepseek-v4"
    tools: ["file_read", "web_search", "code_exec"]
    session: { persist: true }
    sandbox: { enabled: true, type: "docker" }
    harness: { max_steps: 10, timeout: 300 }
```

## 工具系统

Agent 可以使用的工具包括：
- **file_read / file_write**: 文件读写
- **web_search**: 网络搜索
- **code_exec**: 代码执行
- **knowledge_search**: 知识库搜索
- **doc_generate**: 文档生成
""",
                "quiz": [
                    {
                        "question": "Agent 的核心公式是什么？",
                        "options": [
                            "Agent = LLM + Prompt",
                            "Agent = Model + Tools + Session + Sandbox",
                            "Agent = Input + Output",
                            "Agent = Brain + Memory"
                        ],
                        "correct_index": 1,
                        "explanation": "Agent = Model + Tools + Session + Sandbox，这是 OpenSoulmate 的 Agent 定义公式。"
                    }
                ]
            },
            {
                "chapter_id": f"ch_{uuid.uuid4().hex[:8]}",
                "title": "多Agent协作",
                "order": 1,
                "completed": False,
                "completed_at": None,
                "content": """# 多Agent协作

## 协作模式

### 1. 主从模式
一个主 Agent 协调多个从 Agent 执行任务。

### 2. 对等模式
多个 Agent 平等协作，通过事件总线通信。

### 3. 流水线模式
Agent 按顺序执行，前一个的输出是后一个的输入。

## 协作架构

```
Advisor（规划）
    ↓
Executor（执行）→ Verifier（验证）
    ↓
Advisor（审查）
```

## OpenNerve 事件总线

Agent 间通过 OpenNerve 事件总线通信：
- 发布事件: Agent A 完成任务后发布
- 订阅事件: Agent B 收到通知后开始下一步
- 故障隔离: 一个 Agent 挂了不影响其他

## 实战：多Agent文档处理

```yaml
workflow:
  - agent: "parser"
    task: "解析文档"
  - agent: "analyzer"
    task: "分析内容"
  - agent: "summarizer"
    task: "生成摘要"
```

## 最佳实践

1. **明确职责**: 每个 Agent 专注一个领域
2. **松耦合**: 通过事件通信，不直接调用
3. **错误处理**: 设置超时和重试机制
4. **可观测**: 使用 Trajectory 追踪协作过程
""",
                "quiz": [
                    {
                        "question": "Agent 间通信的主要方式是什么？",
                        "options": ["直接函数调用", "共享数据库", "事件总线", "文件交换"],
                        "correct_index": 2,
                        "explanation": "Agent 间通过 OpenNerve 事件总线通信，实现松耦合协作。"
                    },
                    {
                        "question": "哪种协作模式中，前一个Agent的输出是后一个的输入？",
                        "options": ["主从模式", "对等模式", "流水线模式", "网状模式"],
                        "correct_index": 2,
                        "explanation": "流水线模式中，Agent 按顺序执行，输出链式传递。"
                    }
                ]
            }
        ]
    }
]

# Save courses
learn_dir = os.path.expanduser("~/.opensoul/learn")
os.makedirs(learn_dir, exist_ok=True)

for course in COURSES:
    path = os.path.join(learn_dir, f"{course['course_id']}.json")
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(course, f, ensure_ascii=False, indent=2)
    print(f"Created: {course['title']} ({course['course_id']})")

print(f"\nDone! {len(COURSES)} courses created in {learn_dir}")
