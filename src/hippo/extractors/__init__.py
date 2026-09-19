"""OpenHippo extractors — 记忆抽取与准入判定子包。

- gatekeeper: LobeChat memory-user-memory gatekeeper模式 —— 记忆准入判定器，
  "该不该进长期记忆"。不加过滤的记忆=垃圾堆积（26-lobe-chat-source.md §6）。
- deermem_tags: DeerMem记忆抽取安全标签（scope/durability/authority）fail-closed
  + 写侧近重复并入门（fact_dedup）（18-deer-flow-source.md #10/#11）。
"""
