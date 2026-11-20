"""对话编排子包：意图（intent）、事实层（facts）、结构化块（blocks）、
工具节点（tools）与状态机（graph）。

这一层不依赖 retrieval / LLM；规范问答仍由 RAGService 原有链路完成，
通过 graph.run_dialog 的 spec_answer 回调注入，避免循环依赖。
"""
