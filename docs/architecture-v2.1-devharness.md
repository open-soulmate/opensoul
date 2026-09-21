# OpenSoul 架构文档 v2.1 —— DevHarness 插件化设计（提案）

> **版本**：v2.1 Proposal（待用户拍板，未冻结）
> **日期**：2026-09-21
> **前置版本**：v2.0（25组件三层架构 + "一切皆插件、Agent=Model+Harness"原则）
> **上级纲领**：[architecture-thesis.md](architecture-thesis.md)（记忆×知识×技能×进化融合体，2026-09-19拍板）
> **上游规范**：13-Plugin v1.1（插件加载与生命周期）、31-Evolution v1.0（双螺旋自主进化引擎，定稿冻结）
> **调研依据**：《Agent-Harness调研/设计精髓调研报告.md》《源码级设计笔记.md》（8个同类开源项目，源码级）
> **约束**：方案先行，不动代码；Trellis（AGPL-3.0）只偷师设计，零代码引入；出口门禁（Hermes审核后push）不可被自评流程替代

---

## 0. 本版要点

v2.1 回答三个问题：

1. **Trellis 类 Coding Harness 在 25 组件里归谁？** → 第2节：组件归属表（底座层五件套组合包）
2. **调研发现的两块砖（Heredity学习回流 + 任务图层）怎么插件化落地？** → 第3、4节：`gene-loop` 与 `task-graph` 两个插件设计
3. **用户设想（evo=soulmate自身功能、双螺旋互写代码、规则全局化、对所有接入Agent生效）如何用现有规范语言表达？** → 第5、6节：v2.0 原则 + 31-Evolution规范 2.4节的自然延伸，不是新发明

---

## 1. 25组件总表（v2.1 复述，作为映射基准）

> 定义以 knowledge-brain skill（OpenSoulmate生态开发）为准。roadmap 的 39 模块是工程模块，25 组件是架构器官，多对一。

### 1.1 核心主体层（8）

| 组件 | 定位 | 对应工程模块（roadmap） |
|---|---|---|
| Soul | 大脑/中央记忆内核（存储+处理+检索+图谱+权限） | database、vector、knowledge骨架 |
| Cortex | 分析/推理/任务规划/质量评估 | cortex 80% |
| Nerve | 传输信号（事件总线/发布订阅） | nerve 60% |
| Vein | 存储封装/依赖注入 | vein 40% |
| Soma | 连接器+Agent调度+API路由 | a2a/acp/mcp 50% |
| Sense | 感知（OCR/本地采集） | sense 60% |
| Will | 业务工作流（有判断分支的编排） | will 70% |
| Mate | 五官/用户交互端（OpenMate客户端） | OpenMate前端 |

### 1.2 配套底座层（8）

| 组件 | 定位 | 对应工程模块 |
|---|---|---|
| Immune | 免疫/安全门控 | immune 60% + 权限引擎（ASK/hard deny） |
| Vital | 运行时监控/健康 | vital 60% + benchmark 30% |
| Marrow | 骨髓/迁移备份/数据生成 | marrow 50% |
| Gland | 网关/分泌调度（LLM网关） | gland 70% |
| Gene | 基因/技能与规范库（全局作用域） | gene 70% + learn 60%（skill_learner已上报gene） |
| Echo | 反馈/消息广播 | echo 70% |
| Mirror | 镜像/反射自省/元认知 | mirror 40% |
| Link | 连接网关（外部集成） | link 30% |

### 1.3 高级生态层（9）

| 组件 | 定位 | 对应工程模块 |
|---|---|---|
| Hippo | 海马体/记忆固化 | hippocampus 80% + knowledge/learn/timeline/trajectory |
| Reflex | 快速应答（不经深度思考的自动反射） | reflex 40% |
| Heredity | 遗传（经验→下轮生效的固化回流） | heredity 50% ← **v2.1主攻点** |
| Nest | 巢/工作空间 | nest 50% |
| Pulse | 节拍器（定时信号） | pulse 30% + cron |
| Limb | 肢体/RPA执行 | limb 60% |
| Voice | 语音 | voice 60% |
| Vision | 视觉 | vision 50% |
| Mind | 心智/元认知（SoulBrain认知层） | mind 70% |

### 1.4 核心差异闭环（纲领2.4，v2.1不变）

```
记忆（Hippo）→ 技能（Gene）→ 遗传（Heredity）→ 进化（Evo）
```

Mem0/Cognee/Zep 只做到 Hippo/Gene 层；**Heredity→Evo 闭环是 OpenSoul 区别于所有记忆/知识库产品的主线**。v2.1 的全部设计都为把这条闭环从中空转变成实心。

---

## 2. Trellis 类 Coding Harness 的组件归属

**定位：DevHarness = 底座层五件套在"AI编码"场景的组合包，按13-Plugin规范做成插件，装入OpenMate后对所有接入Agent生效。**

| Trellis 能力（调研结论） | 归属组件 | OpenSoul 现状 | v2.1 处置 |
|---|---|---|---|
| spec库+按任务注入（`.trellis/spec/`） | **Gene** | ✅ CODING_STANDARDS注入evo + gene skill_learner | 保持；Gene=规范的唯一真相源 |
| 跨会话记忆（journals） | **Hippo** | ✅ agentmemory(54工具)+LTM 472条+evo_feedback.json | 保持；比Trellis强 |
| hooks自动触发+逐轮上下文注入 | **Reflex** | ⚠️ evo为cron轮询式，缺事件驱动 | Phase D评估（非两块砖范围） |
| spec promotion（经验→下轮生效规范） | **Heredity** | ❌ 缺失——"无法自我进化"的结构性原因 | **砖1：gene-loop插件（第3节）** |
| 验证门禁（check子代理+protected files） | **Immune** | ✅ 已手工实现：Hermes审核队列(cron 30min)+KERNEL_FILES三关卡+import一票否决+ruff | 保持；Immune不空缺，出口门禁**不被替代** |
| 平台适配（22+编码Agent） | **Link+Soma** | ✅ ACP/A2A协议+OpenMate接入多Agent+工具网关 | 保持；插件工具注册到Tool网关即全Agent可用 |
| tasks任务目录+PRD前置 | **无对应（缺口）** | ❌ 25组件中Will偏业务工作流，装不下AI编码任务图 | **砖2：task-graph插件（第4节）** |
| workflow三态状态机（Plan/Execute/Finish） | **Mind+Will** | ⚠️ 31-Evolution六阶段(ORPEVL)已覆盖大部分 | 不新增；在六阶段中显式插入PRD与promotion环节（第5节） |

**结论：25组件对Trellis能力覆盖率≈87%（8项中7项有归属），2处无归属恰好=调研发现的两块砖。组件设计的前瞻性被行业反向验证，两块砖也各有一个空位等着。**

行业交叉佐证（调研数据）：Trellis=AGPL仅作设计参照；同类MIT项目（ai-memory 7.3k★/OKF/beads 26k★/mattpocock-skills 266k★）的设计精髓全部进入本方案，产品零AGPL包袱。

---

## 3. 砖1：`gene-loop` 插件 —— Heredity 学习回流管道

### 3.1 要解决的问题

evo_feedback.json 目前是**被动记录**：积累教训，但教训不会结构性地变成下一轮evo自动生效的规范。31-Evolution规范六阶段的 Reflect 产出 `success_patterns`/`improvements`，但没有规定它们的**去向**——这就是闭环中空的精确位置。

### 3.2 管道设计（借鉴三处源码级设计）

```
evo/soulmate任务收尾
      │
      ▼
【Propose】学习提案生成（结构化，含置信度+证据引用）
      │   借鉴：ai-memory auto-improve proposal（confidence+rationale+evidence quotes）
      ▼
【Review】Hermes审核队列（cron 30min，已有）
      │   角色=OKF Trust Tiers：generated:agent（evo提案）→ verified:human（用户/Hermes终审）
      │   权限引擎联动：常规promote自动放行；改核心规范/删除教训 → ASK；伪造证据 → hard deny
      ▼
【Promote】写入Gene（全局作用域规范库）
      │   借鉴：OKF governance默认值——约束类进constraint（强生效），参考类进context（检索时带入）
      │   目标：gene规范库 + knowledge-brain lessons + evo_feedback.json 状态位更新
      ▼
【Inject】下轮evo _plan自动注入（已有机制复用：CODING_STANDARDS注入点）
      │
      ▼
【Measure】生效度量：被注入后同类错误是否下降（Hippo侧取数）
```

### 3.3 数据模型（借鉴 ai-memory V22 scheduler schema 的两个核心手法）

**提案表 gene_proposals：**

```json
{
  "proposal_id": "gp-xxxx",
  "source_cycle": "evo-xxxx | session-id",
  "actor": "evo | soulmate | hermes",
  "type": "new_rule | refine_rule | deprecate_rule | lesson",
  "content": "规范/教训正文",
  "governance": "constraint | context",
  "scope": "global | project:{repo}",
  "confidence": 0.0,
  "evidence": [{"quote": "原文引用", "source": "反馈/日志/commit"}],
  "status": "proposed | approved | rejected | promoted",
  "review": {"reviewer": "hermes|user", "reason": "", "at": 0},
  "promoted_to": "gene:{rule-id} | knowledge-brain:{lesson-id}",
  "created_at": 0, "promoted_at": 0
}
```

**schema层不变量（学ai-memory：不变量写进数据库，不靠提示词自觉）：**

1. `evidence` 非空才允许 `status=proposed`（无证据不立案）
2. `promoted_at` 非空 ⟺ `status=promoted` 且 `promoted_to` 非空（溯源闭合）
3. `scope=project:*` 的提案**禁止promote为global规范**（作用域不越级，触发器RAISE(ABORT)级）
4. actor=evo/soulmate 的提案**禁止直接置status=promoted**（必须经review通道，Hermes或user）——出口门禁在数据层锁死

**调度借鉴ai-memory watermark+claims**：watermark标记已处理的cycle/session，按序推进不重不漏；每session恰一次claim（提案去重）。

### 3.4 与用户治理体系的关系

本管道**就是**用户"教学+学习闭环"设想的落地形态：evo产出=教学相长的提案，Hermes审核=过渡期把关，promote进Gene=教材入册，下轮注入=学生学到了。出口门禁不被任何环节替代——promote只是**写入规范库**，evo改代码后push前仍走Hermes审核。

### 3.5 插件声明草案（13-Plugin规范格式）

```json
{
  "id": "gene-loop",
  "name": "基因回流管道",
  "version": "0.1.0",
  "description": "Heredity学习回流：任务收尾生成学习提案，Hermes审核后promote进Gene全局规范库，下轮自动注入",
  "entry": "__init__.py",
  "tools": ["loop_propose", "loop_review_queue", "loop_promote", "loop_status"],
  "frontend": {"enable": true, "entry": "frontend/page.tsx",
    "nav": {"label": "进化回流", "icon": "RefreshCw", "position": "after:dev-specs"}},
  "permissions": ["file:read", "file:write", "storage:read", "storage:write", "eventbus:publish", "eventbus:subscribe", "log:write"],
  "hot_reload": true,
  "protected": true
}
```

- `protected: true`：回流管道本身是免疫相关件，禁止AI擅自修改（对应31规范黑名单精神）
- `frontend.nav`：**实时观察面板**——提案流水、审核状态、promote历史（用户铁律：任何后台系统必须有实时监控面板，不能只说"在运行"）
- 提案写入Gene的操作经Tool网关鉴权，新增权限建议 `gene:write`（仅Core Agent+Hermes审核通道持有）

---

## 4. 砖2：`task-graph` 插件 —— 任务图与PRD前置

### 4.1 要解决的问题

31-Evolution六阶段从Observe起步、Plan阶段直接产出改进清单——**缺"动手前先想清楚"的结构化前置**（evo改坏代码事故`30b5de76`类的根因之一），也缺任务依赖管理（`bd ready`式"图决定下一步"）。

### 4.2 设计（两处源码级借鉴）

**任务模型（beads frontier + myc继承计数器）：**

```json
{
  "task_id": "beads式hash id（内容派生，多进程并发安全）",
  "type": "bug | feature | task | epic | chore",
  "title": "",
  "prd": {"problem": "", "goal": "", "acceptance": [], "risks": [], "out_of_scope": []},
  "status": "open | in_progress | blocked | closed | gate",
  "priority": 0,
  "deps": [{"to": "task-id", "type": "blocks | parent | discovered-from | relates"}],
  "open_blockers": 0, "anc_blockers": 0,
  "claim": {"holder": "strand_a | strand_b | evo", "at": 0},
  "source": {"actor": "evo | user | soulmate", "session": ""},
  "created_at": 0, "closed_at": 0
}
```

**核心机制：**

1. **frontier计算（学beads `bd ready`）**：`ready = status=open AND open_blockers=0 AND anc_blockers=0`——图而非人决定下一步；myc实测：计数器索引比祖先回溯快4.9倍，`anc_blockers`由触发器随parent_closure维护
2. **PRD前置门（学mattpocock to-spec/grill-me思想）**：type∈{feature, epic}的任务，`prd.acceptance`为空时**不进入ready集合**——大任务先回答"问题/目标/验收/边界"才有施工资格；bug/chore豁免（小事不重罚）
3. **discovered-from边（学beads）**：施工中发现的新任务挂discovered-from边回源任务——evo循环里"改A发现B有问题"的正确记账方式，不静默跑题
4. **双链claim（衔接31规范）**：strand_a/strand_b各自claim任务，互写代码=交叉验证已有机制（31规范2.4：A的改进B验证）在任务图上的显式化
5. **gate状态（学beads gates）**：需人工签章/等待外部事件的任务停在gate态，不阻塞其他分支

### 4.3 与六阶段流水线的衔接（31规范Plan阶段升级）

```
原：Reflect产出improvements → Plan直接产出改进清单 → Execute
新：Reflect产出improvements → [task-graph] 验收标准齐备？──否──▶ 补PRD（不施工）
                                     │是
                                     ▼
                              进入ready frontier → Execute（白名单/语法门控/gitsandbox不变）
```

改动性质：**在冻结规范的Plan与Execute之间插入一个显式检查环节**，不推翻31规范任何强制约束（白名单/语法门控/回滚/双螺旋全部原样）。

### 4.4 插件声明草案

```json
{
  "id": "task-graph",
  "name": "任务图",
  "version": "0.1.0",
  "description": "AI编码任务图：PRD前置门、依赖frontier、双链claim、discovered-from记账",
  "entry": "__init__.py",
  "tools": ["task_create", "task_ready", "task_claim", "task_update", "task_close", "task_graph"],
  "frontend": {"enable": true, "entry": "frontend/page.tsx",
    "nav": {"label": "任务图", "icon": "Workflow", "position": "after:进化回流"}},
  "permissions": ["file:read", "file:write", "storage:read", "storage:write", "eventbus:publish", "log:write"],
  "hot_reload": true,
  "protected": false
}
```

- `frontend.nav`：任务图可视化（G6，knowledge-brain已有技术栈）——节点=任务、边=依赖、frontier高亮、双链claim着色（strand_a蓝/strand_b橙，延续现有配色语义）
- 数据存储：SQLite（opensoul统一，roadmap紧迫问题2"记忆存JSON应存SQLite"的顺势项），CRDT字段（HLC/site_id）预留——两链并发写同一任务图需要（学myc oplog）

---

## 5. 双螺旋与evo身份统一（用户设想的规范语言表达）

### 5.1 三个论断的落位

| 用户设想 | 架构语言 | 依据 |
|---|---|---|
| "evo是soulmate自身的功能，不是外挂" | evo=Heredity组件的执行器，本就在核心差异闭环内；acp-proxy只是当前部署位置 | 纲领2.4闭环 |
| "两个soulmate进程互相写代码" | **已实现**：双螺旋strand_a/strand_b（:8092/:8095）+ 31规范2.4交叉验证（A的改进B验证，失败自动回滚）+ 滚动进化（流量摘除→沙箱→commit→exit42重启，永不停机） | 31规范+evolution-architecture.md |
| "evo开发规则是全局性的" | Gene作用域=global（soulmate=万能助手→基因组不分项目）；OKF global scope、ai-memory global pages行业同款 | gene-loop的scope字段+schema不变量3 |

### 5.2 v2.1带来的唯一新东西

双螺旋互写代码、六阶段、白名单、交叉验证——31规范全都有，**不需要发明**。v2.1新增的只有两个环节：Plan前的PRD门（task-graph）、Learn后的promotion去向（gene-loop→Gene）。闭环由"中空"变"实心"，其余一寸不动。

### 5.3 Trellis类能力作为OpenMate插件的全Agent生效机制

按13-Plugin规范§4：插件工具统一注册至全局Tool网关，调用链 `Skill/Agent → 网关鉴权 → 插件执行 → 日志埋点`。gene-loop/task-graph注册后，**OpenMate接入的所有Agent自动可用**：evo/soulmate（原生）、Hermes（微信通道）、Claude Code/OpenCode/Codex（经ACP/A2A）、未来任何Agent——"一切皆插件、Agent=Model+Harness"原则的直接兑现，无需逐Agent适配。

---

## 6. 治理与安全（不可动项复述）

1. **出口门禁不变**：evo/soulmate任何代码push前仍经Hermes审核队列（cron 30min）——gene-loop的Review环节与出口门禁是同一批准源的两次把关（一次管"写什么规范"，一次管"改什么代码"），互不替代
2. **权限引擎联动**：promote操作常规放行（review通过即合法）；改核心规范/deprecate规则→ASK用户；伪造evidence/越权scope→hard deny+审计
3. **信任分层**：`verified:human`（用户拍板、Hermes审核通过）与`generated:agent`（evo/soulmate提案）分列，Gene规范库只收verified——学OKF："Agent永不覆盖人类定的架构法"
4. **AGPL合规**：Trellis零代码引入；ai-memory/OKF/beads/mattpocock均为MIT，设计思想自由使用，自研实现版权归Open-Soulmate
5. **可观测性**：两插件均带frontend.nav实时面板（提案流水/任务图/审核状态），对应用户铁律"必须有实时监控面板"

---

## 7. 施工分期提案（每期走用户治理流程：方案→拍板→evo在门禁下施工→Hermes把关→用户验收）

| 期 | 内容 | 交付物 | 依赖 |
|---|---|---|---|
| **A** | gene-loop插件：proposal schema+Review接线+promote写Gene+下轮注入 | 插件+面板+schema迁移；evo循环试运行1轮出真实提案 | 无 |
| **B** | task-graph插件：任务模型+frontier+PRD门+SQLite存储 | 插件+G6可视化；evo下一个真实任务走PRD门 | 无（可与A并行开发，A先上线） |
| **C** | 双链互写强化：31规范2.4交叉验证接入task-graph claim+gene-loop度量 | "被注入后同类错误率"数字（Measure环节首个benchmark） | A+B |
| **D** | 对全Agent开放：Tool网关注册+ACP/A2A侧调用验证 | 微信端/外部Agent可调用task_create等工具 | A+B |

先行地基（纲领第一阶段，不在本插件包内但影响Measure取数）：qdrant启动、RAG分块管道、hippo三因子检索——决定promotion生效度量的数据质量。

---

## 8. 版本信息

- 文档版本：architecture v2.1 Proposal
- 状态：**待用户拍板**（拍板前不动任何代码；拍板后按第7节分期，evo施工，Hermes门禁把关）
- 依据文档：纲领architecture-thesis.md（2026-09-19）、roadmap.md（39模块）、13-Plugin v1.1（2026-09-03冻结）、31-Evolution v1.0（2026-09-10冻结）、Agent-Harness调研两份（2026-09-21，含源码级笔记）
- 本地调研资产：`~/Documents/Hermes/Agent-Harness调研/`（报告+笔记+6个MIT/AGPL仓库clone）
