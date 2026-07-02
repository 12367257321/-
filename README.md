电网异常窃电智能识别系统
基于 Oracle 21c + Python Flask 的电力反窃电稽查平台，采用 B/S 三层架构，通过多维度风险评分模型（夜间占比 + 波动系数）自动识别异常用电行为，并集成 NL2SQL 自然语言查询引擎，支持用中文直接查询数据库。

技术栈
数据库：Oracle 21c XE（11张表 / 3个视图 / 3个触发器 / 4个存储过程 / 2个函数）
后端：Python Flask，53个 RESTful API，@login_required / @admin_required 分层鉴权
前端：HTML5 + Bootstrap 5 + ECharts 5，响应式双端适配
AI：Ollama（Qwen2.5:3B）+ ChromaDB + BGE-M3，RAG 架构 NL2SQL 引擎
核心功能
双维度加权风险评分算法，自动排序疑似窃电用户
触发器驱动的实时预警，录入即检测
全量稽查存储过程，一键扫描全部用户
NL2SQL 自然语言查询，中文提问自动生成 SQL
Word 稽查报表一键导出
社区动态圈 + VIP 会员体系


课设哈哈 2026.07.02
