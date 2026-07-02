"""
NL2SQL 引擎 v2 - 更快 + 更准 + 能解释结果
==========================================================================
【核心功能】
将用户自然语言问题（中文）自动转换为 Oracle SQL 语句并执行查询。
本引擎是"电网异常窃电智能识别系统"的智能查询入口，供电局工作人员通过
自然语言即可查询用电数据、预警记录、窃电嫌疑用户等信息，无需掌握 SQL。

【技术架构】
采用 RAG（检索增强生成）架构：
  1. 用户输入中文问题（如"哪些商业用户有窃电嫌疑"）
  2. RAG 检索：用嵌入模型将问题转为向量，从 ChromaDB 中检索最相关的 Schema 知识
  3. Prompt 构建：将检索到的表结构、视图定义、示例 SQL 拼接进提示词模板
  4. LLM 生成：通过 Ollama 调用本地大模型生成 Oracle SQL 语句
  5. SQL 校验：检查是否有危险操作，并自动修正常见错误（如别名错误、? 占位符）
  6. 执行查询：连接 Oracle 数据库执行 SQL，返回结构化 JSON 结果
  7. AI 解释：让大模型用自然语言解释查询结果，方便非技术人员理解

【依赖】
- ollama: 本地大模型服务（嵌入模型 bge-m3 + 对话模型 qwen2.5:3b）
- chromadb: 向量数据库，存储 Schema 知识库
- oracledb: Oracle 数据库 Python 驱动
==========================================================================
"""
import re, chromadb, ollama, oracledb, sys, os, time
from datetime import datetime

# 将当前目录加入 Python 搜索路径，确保能导入同目录下的 config 模块
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from config import ORACLE_CONFIG, OLLAMA_CONFIG, CHROMA_CONFIG

# ============================================================
# 禁止执行的 SQL 关键字列表（安全机制）
# 说明：NL2SQL 引擎只允许生成 SELECT 查询，任何写操作都会被拦截
#       这是防止模型幻觉或恶意输入导致数据被破坏的重要安全措施
# ============================================================
FORBIDDEN_KW = ["DROP", "DELETE", "INSERT", "ALTER", "TRUNCATE", "CREATE", "GRANT", "REVOKE"]


class NL2SQLEngine:
    """
    NL2SQL 引擎主类
    封装了从问题理解到结果解释的完整流水线，对外暴露 query() 和 query_stream() 两个入口。
    采用单例模式（通过 get_engine() 获取），全局复用 ChromaDB 连接和向量集合引用。
    """

    def __init__(self):
        """
        初始化引擎：连接 ChromaDB 并加载知识库集合。
        
        连接过程：
        1. 创建 ChromaDB PersistentClient，数据持久化到 chroma_db 目录
        2. 获取预构建的 Schema 集合（由 rag_init.py 创建）
        3. 打印集合规模确认初始化成功
        """
        # 创建 ChromaDB 持久化客户端，数据存储在配置指定的目录中
        self.chroma_client = chromadb.PersistentClient(path=CHROMA_CONFIG["persist_dir"])
        # 获取已构建好的 Schema 知识库集合（包含表定义、视图、示例 SQL）
        self.col = self.chroma_client.get_collection(CHROMA_CONFIG["collection_name"])
        print(f"✅ 向量库({self.col.count()}条) + Oracle 已连接")

    # ===== RAG 检索（检索增强生成的第一步） =====
    def _rag_retrieve(self, query, top_k=5):
        """
        RAG 检索阶段：将用户问题向量化，从知识库中检索最相关的 Schema 文档。
        
        工作流程：
        1. 用 Ollama 的嵌入模型（bge-m3）将中文问题转为 1024 维语义向量
        2. 在 ChromaDB 中做向量相似度匹配，找出 top_k 条最相关的 Schema 知识
        3. 返回每条知识的 ID、内容和语义相似度分数
        
        参数:
            query: 用户输入的自然语言问题（中文），如"哪些商业用户有窃电嫌疑"
            top_k: 检索返回的文档数量，默认 5 条
        
        返回:
            list[dict]: 每条记录包含 id（文档标识）、content（知识内容）、
                       score（相似度分数，0~1，越大越相关）
        
        向量检索原理：
            ChromaDB 使用余弦相似度衡量两个向量的接近程度。
            距离越近 → 相似度越高 → 说明该知识文档与用户问题语义最相关。
            这些文档会被注入到 Prompt 中，帮助大模型理解数据库结构。
        """
        # 调用 Ollama 嵌入模型，将用户问题转为语义向量
        emb = ollama.embeddings(model=OLLAMA_CONFIG["embedding_model"], prompt=query)["embedding"]
        # 在向量集合中查询与问题向量最相似的 top_k 条文档
        results = self.col.query(query_embeddings=[emb], n_results=top_k)
        # 组装检索结果，将 ChromaDB 距离转换为易读的相似度分数 (1 - 距离)
        return [{"id": results["ids"][0][i], "content": results["documents"][0][i],
                 "score": round(1-results["distances"][0][i],4)} for i in range(len(results["documents"][0]))]

    # ===== 构建 Prompt（将检索结果注入提示词模板） =====
    def _build_prompt(self, question, rag_docs):
        """
        Prompt 构建阶段：将 RAG 检索到的 Schema 知识与系统规则拼接成完整的 LLM 提示词。
        
        Prompt 设计原理：
        1. 角色设定：明确告诉大模型"你是 Oracle SQL 专家"
        2. Schema 注入：将从知识库检索到的表结构、视图、示例 SQL 作为上下文
        3. 字段速查：提供常用表/视图的关键字段列表，确保模型使用正确的列名
        4. 硬约束规则：列出常见错误（如用 ? 占位符、跨表查询时漏 JOIN），强制模型遵守
        5. 格式要求：只输出纯 SQL，不输出 markdown 标记或分号
        
        参数:
            question: 用户输入的自然语言问题
            rag_docs: _rag_retrieve() 返回的 Schema 知识文档列表
        
        返回:
            str: 完整的 LLM 提示词字符串
        """
        # 将多条 Schema 知识文档拼接成一个上下文字段，每条用分隔线和 ID 标注
        ctx = "\n\n".join([f"--- {d['id']} ---\n{d['content']}" for d in rag_docs])
        # 构建完整 Prompt，包含角色、Schema、字段速查、硬性规则和用户问题
        return f"""你是Oracle SQL专家。根据Schema生成一条可执行的Oracle SELECT语句。

## Schema:
{ctx}

## 字段速查（严格使用）:
- USER_INFO: user_id, user_name, user_type, addr, meter_code
- USER_POWER: pid, user_id, record_date, day_power, night_power, total_power, night_ratio
- RISK_WARN: warn_id, user_id, warn_type, warn_level, warn_desc, warn_time, is_handled
- V_SUSPECTED_USERS: user_id, user_name, user_type, avg_night_ratio(不是night_ratio!), risk_level, risk_score, abnormal_reason
- V_USER_POWER_PROFILE: user_id, user_name, user_type, avg_night_ratio(不是night_ratio!), fluct_coef, power_label, power_rank

## 规则（违反即失败）:
1. **禁止**: ? 占位符、参数化语法。所有值必须写死，如 user_id=1 或 user_type='工业'
2. **禁止**: 在 USER_POWER 表上直接写 user_type='工业'。user_type 只在 USER_INFO 表中！跨表筛选必须 JOIN
3. 分类型查用电趋势的正确写法:
   SELECT p.record_date, p.day_power, p.night_power, u.user_name
   FROM user_power p JOIN user_info u ON p.user_id=u.user_id
   WHERE u.user_type='工业' AND p.record_date>=TRUNC(SYSDATE)-30 ORDER BY p.record_date
4. 简单问题写简单SQL，不要无意义子查询或嵌套
5. 视图用 avg_night_ratio 不是 night_ratio
6. 只输出纯SQL，不要markdown，不要分号结尾

## 问题: {question}
## SQL:"""

    # ===== LLM 生成 SQL + 从原始输出中提取纯 SQL =====
    def _generate_sql(self, prompt):
        """
        SQL 生成阶段：调用 Ollama 大模型生成 Oracle SQL 语句。
        
        生成流程：
        1. 将完整的 Prompt 发送给本地大模型（qwen2.5:3b）
        2. 从模型的原始输出中提取纯 SQL 文本
        
        SQL 提取策略（按优先级）：
        - 策略1: 正则匹配 markdown 代码块中的 SQL（如 ```sql ... ```）
        - 策略2: 正则匹配以 SELECT 开头的完整语句
        - 兜底: 如果以上都匹配不到，直接返回原始输出
        
        参数:
            prompt: _build_prompt() 构建的完整提示词
        
        返回:
            str: 提取出的纯 SQL 语句字符串
        """
        # 调用 Ollama 大模型，发送 Prompt 并接收模型生成的文本
        resp = ollama.chat(model=OLLAMA_CONFIG["llm_model"],
                           messages=[{"role": "user", "content": prompt}])
        raw = resp["message"]["content"]
        # 策略1: 提取 markdown 代码块中的 SQL（去除 ```sql 和 ``` 标记）
        m = re.search(r"```(?:sql)?\s*(.*?)\s*```", raw, re.DOTALL | re.IGNORECASE)
        if m: return m.group(1).strip()
        # 策略2: 提取以 SELECT 开头的完整语句（跨行匹配）
        m = re.search(r"(SELECT\b[\s\S]+)", raw, re.IGNORECASE)
        if m: return m.group(1).strip()
        # 兜底: 直接返回原始输出（不做额外处理）
        return raw.strip()

    # ===== SQL 安全校验 + 自动修正 =====
    def _validate_sql(self, sql):
        """
        SQL 校验与自动修正：确保生成的 SQL 是安全的 SELECT 语句，并修正常见错误。
        
        校验规则（按优先级）：
        1. 检查是否包含禁止的 DDL/DML 关键字（如 DROP、DELETE、INSERT），拒绝非 SELECT 操作
        2. 检查是否以 SELECT 开头，确保只执行查询操作
        3. 清理无效字符（分号、markdown 标记）
        
        自动修正规则（针对模型常见幻觉）：
        4. 将 JDBC 风格的 ? 占位符替换为 -1（避免 Oracle 报错）
        5. 将视图中错误列名 night_ratio 修正为 avg_night_ratio（但保留 avg_night_ratio 不变）
        6. 检测 V_SUSPECTED_USERS 视图被额外 JOIN USER_INFO 的错误模式，自动修复
        7. 检测 USER_POWER 表上直接使用 user_type 过滤的错误（提示需要 JOIN USER_INFO）
        
        参数:
            sql: _generate_sql() 提取的原始 SQL 字符串
        
        返回:
            tuple(bool, str): (是否通过校验, 校验/修正后的 SQL 或错误信息)
        """
        upper = sql.upper()
        # 校验1: 检查是否包含禁止的 DDL/DML 关键字
        for kw in FORBIDDEN_KW:
            if re.search(rf"\b{kw}\b", upper): return False, f"禁止操作: {kw}"
        # 校验2: 确保 SQL 以 SELECT 开头（只执行读操作）
        if not upper.strip().startswith("SELECT"): return False, "只允许SELECT"

        # 清理: 去掉可能导致 Oracle 报错的尾部分号和 markdown 标记
        sql = re.sub(r';', '', sql)
        sql = sql.replace("```sql","").replace("```","").strip()

        # 自动修正4: JDBC 占位符 ? → -1（模型可能混淆 SQL 方言）
        sql = re.sub(r'=\s*\?\s', '= -1 ', sql)

        # 自动修正5: 视图列名统一 — 将独立的 night_ratio 替换为 avg_night_ratio
        # 注意: 使用负向前瞻 (?<!avg_) 确保 avg_night_ratio 本身不会被破坏
        sql = re.sub(r'(?<!avg_)night_ratio(?!\w)', 'avg_night_ratio', sql, flags=re.IGNORECASE)

        # 自动修正6: V_SUSPECTED_USERS 视图已包含用户信息列和 avg_night_ratio，
        #   但模型有时会错误地额外 JOIN USER_INFO 表，还把视图独有列(.avg_night_ratio)错写成 u. 前缀。
        #   此段逻辑检测并移除多余 JOIN，同时修正错误别名。
        if "V_SUSPECTED_USERS" in upper and "JOIN USER_INFO" in upper:
            # 6a: 移除多余的 JOIN USER_INFO ... ON ... 子句（支持 LEFT/INNER/FULL/RIGHT/CROSS 各种 JOIN 类型）
            sql = re.sub(r'\s*(LEFT\s+|INNER\s+|FULL\s+|RIGHT\s+|CROSS\s+)?'
                         r'JOIN\s+USER_INFO\s+\w+\s+ON\s+\w?\.\s*user_id\s*=\s*\w?\.\s*user_id\s*',
                         ' ', sql, flags=re.IGNORECASE)
            # 6b: 识别视图的别名和 JOIN 引入的别名
            view_alias = None
            m = re.search(r'FROM\s+V_SUSPECTED_USERS\s+(\w+)', sql, re.IGNORECASE)
            if m:
                view_alias = m.group(1).lower()  # 例如 FROM v_suspected_users v → view_alias = "v"
            join_alias = None
            m2 = re.search(r'(?<=\s)(\w+)\.\w+', sql)
            if m2 and m2.group(1).lower() != view_alias:
                join_alias = m2.group(1)          # 例如 u.user_name → join_alias = "u"
            # 6c: 将错误别名替换为视图的正确别名
            if view_alias and join_alias:
                sql = re.sub(r'\b' + re.escape(join_alias) + r'\.', view_alias + '.', sql, flags=re.IGNORECASE)

        # 自动修正7: user_power 表上没有 user_type 列，必须通过 JOIN user_info 来筛选
        # 如果 SQL 中在 USER_POWER 表上引用了 user_type 但没有 JOIN USER_INFO，则报错并给出修复提示
        if "USER_POWER" in upper and re.search(r'USER_POWER\b.*\bUSER_TYPE', upper) and "JOIN USER_INFO" not in upper:
            return False, "user_power表没有user_type列，需要JOIN user_info表进行筛选"

        return True, sql

    # ===== 执行 SQL =====
    def _execute_sql(self, sql):
        """
        SQL 执行阶段：连接 Oracle 数据库执行校验后的 SQL 语句。
        
        执行流程：
        1. 安全检查：再次确认 SQL 中不含 ? 占位符（模型幻觉的最后一道防线）
        2. 建立 Oracle 连接（使用 oracledb 驱动）
        3. 执行 SQL 并获取列名和数据行
        4. 将结果序列化为 JSON 友好的格式（datetime → 字符串）
        5. 无论成功或失败，确保连接被关闭（finally 块）
        
        参数:
            sql: _validate_sql() 校验/修正后的 SQL 字符串
        
        返回:
            dict: {"success": True/False, "columns": [...], "rows": [...], 
                   "row_count": N} 或 {"success": False, "error": "..."}
        """
        # 最后一道防线：如果 SQL 中仍有 ? 占位符，直接返回错误提示
        if '?' in sql:
            return {"success": False, "error": "SQL包含 ? 占位符（模型幻觉），请换个问题重试"}
        # 建立 Oracle 数据库连接
        conn = oracledb.connect(**ORACLE_CONFIG)
        cur = conn.cursor()
        try:
            # 执行 SQL 查询语句
            cur.execute(sql.strip())
            # 从游标描述中提取列名，统一转为小写以便前端处理
            cols = [d[0].lower() for d in cur.description]
            rows = []
            # 遍历结果集，将每行转为字典格式，datetime 类型转为字符串
            for row in cur.fetchall():
                d = {}
                for i, c in enumerate(cols):
                    v = row[i]
                    # 处理 Oracle DATE/DATETIME 类型，转为标准字符串格式
                    if isinstance(v, datetime): v = v.strftime("%Y-%m-%d %H:%M:%S")
                    elif hasattr(v, 'strftime'): v = v.strftime("%Y-%m-%d")
                    d[c] = v
                rows.append(d)
            # 返回成功结果，包含列名、数据行和总行数
            return {"success": True, "columns": cols, "rows": rows, "row_count": len(rows)}
        except Exception as e:
            # 捕获 Oracle 执行异常，返回错误信息
            return {"success": False, "error": str(e)}
        finally:
            # 无论成功失败，确保关闭游标和连接，避免资源泄漏
            cur.close(); conn.close()

    # ===== AI 解释查询结果 =====
    def _explain_result(self, question, sql, result):
        """
        结果解释阶段：让大模型用通俗易懂的自然语言解释查询结果。
        
        为什么需要这一步：
        数据库返回的原始数据对供电局工作人员不够友好（数字、百分比、风险等级代码）。
        通过 AI 总结，将枯燥的数据转化为可读的分析报告，降低使用门槛。
        
        工作流程：
        1. 从查询结果中取前 20 行作为分析样本
        2. 构造解释 Prompt，包含用户问题、执行的 SQL、查询结果摘要
        3. 让大模型用 2-5 句话总结关键发现，列出可疑用户，控制在 150 字以内
        
        参数:
            question: 用户原始问题
            sql: 执行的 SQL 语句
            result: _execute_sql() 返回的查询结果字典
        
        返回:
            str: AI 生成的自然语言解释文本
        """
        # 查询失败或无数据时，返回简短提示
        if not result["success"] or result["row_count"] == 0:
            return "未查询到相关数据，请换个问法试试。"

        # 取前 20 行数据作为分析样本（避免超出模型上下文窗口）
        rows = result["rows"][:20]
        cols = result["columns"]
        total = result["row_count"]

        # 构造数据摘要：每行格式为 "第1行: user_name=张三, risk_score=45, ..."
        # 最多取前 8 列和前 10 行，防止摘要过长
        summary_lines = [f"第{i+1}行: " + ", ".join([f"{c}={rows[i][c]}" for c in cols[:8]]) for i in range(min(len(rows), 10))]
        summary = "\n".join(summary_lines)

        # 构建解释 Prompt：告诉模型角色（电网稽查系统数据分析助手）和输出要求
        explain_prompt = f"""你是电网窃电稽查系统的数据分析助手。用户问了一个问题，数据库返回了结果。请用简洁的中文解释这个结果，让非技术人员也能看懂。

## 用户问题: {question}
## 执行的SQL: {sql}
## 结果(共{total}条，展示前{min(len(rows),10)}条):
{summary}

## 要求:
1. 用2-5句话总结关键发现
2. 如果涉及窃电嫌疑用户，列出可疑用户姓名和原因
3. 如果涉及统计数字，用百分比或对比让数据更直观
4. 语气专业但易懂
5. 控制在150字以内

## 你的解释:"""

        try:
            # 调用大模型生成自然语言解释
            resp = ollama.chat(model=OLLAMA_CONFIG["llm_model"],
                               messages=[{"role": "user", "content": explain_prompt}])
            return resp["message"]["content"].strip()
        except:
            # 如果解释失败（如模型不可用），返回纯数据摘要作为降级方案
            return f"查询返回 {total} 条记录。{', '.join([rows[i].get('user_name', rows[i].get(list(rows[i].keys())[0], '?')) for i in range(min(len(rows), 5))])}"

    # ===== 主入口：完整流水线（同步版） =====
    def query(self, question):
        """
        主入口方法（同步版）：执行完整的 NL2SQL 流水线。
        
        流水线步骤:
        Step 1: RAG 检索 — 从向量库检索相关表结构知识
        Step 2: Prompt 构建 — 将知识与问题拼接成 LLM 提示词
        Step 3: SQL 生成 — 调用大模型生成 Oracle SQL
        Step 4: SQL 校验 — 安全检查 + 自动修正常见错误
        Step 5: SQL 执行 — 连接 Oracle 执行并返回结果
        Step 6: AI 解释 — 让大模型用自然语言总结结果
        
        参数:
            question: 用户输入的自然语言问题，如"哪些商业用户有窃电嫌疑"
        
        返回:
            dict: {"question": ..., "sql": ..., "result": {...}, 
                   "explanation": ..., "elapsed": 耗时秒数}
        """
        t0 = time.time()                        # 记录开始时间，用于计算总耗时
        rag_docs = self._rag_retrieve(question)  # Step 1: RAG 检索相关 Schema 知识
        prompt = self._build_prompt(question, rag_docs)  # Step 2: 构建 LLM Prompt
        sql_raw = self._generate_sql(prompt)     # Step 3: LLM 生成 SQL
        valid, sql = self._validate_sql(sql_raw) # Step 4: 校验 + 自动修正

        if not valid:
            # 校验失败时直接返回错误，不执行后续步骤
            return {"question": question, "sql": sql_raw, "result": {"success": False, "error": sql},
                    "explanation": "", "elapsed": round(time.time()-t0,2)}

        result = self._execute_sql(sql)          # Step 5: 执行 SQL
        elapsed = round(time.time()-t0, 2)       # 计算总耗时

        # Step 6: AI 解释结果（仅查询成功时解释）
        explanation = self._explain_result(question, sql, result) if result["success"] else ""

        return {"question": question, "sql": sql, "result": result,
                "explanation": explanation, "elapsed": elapsed}

    # ===== 主入口：流式流水线（SSE 版，用于前端实时展示） =====
    def query_stream(self, question):
        """
        流式查询入口（SSE 版）：通过 Server-Sent Events 向浏览器实时推送处理进度。
        
        相比 query() 同步版，流式版的特点：
        - 前端可以实时看到"正在检索知识库..." → "正在生成SQL..." → "正在执行..."
        - LLM 生成的 SQL 逐 token 推送到前端，用户看到模型"逐字写出" SQL
        - 提供更好的用户体验，避免长时间等待无事发生的焦虑感
        
        返回的是一个生成器，通过 yield 逐个发送 SSE 事件：
        - {"event": "phase", ...}: 阶段切换事件（RAG → 生成 → 执行）
        - {"event": "token", ...}: LLM 流式输出的 token（逐字显示 SQL）
        - {"event": "done", ...}: 完成事件（包含最终结果和预览）
        
        参数:
            question: 用户输入的自然语言问题
        
        Yields:
            dict: SSE 事件字典，由 Flask 的 Response 迭代消费
        """
        t0 = time.time()

        # Phase 1: RAG 检索阶段
        yield {"event": "phase", "data": "rag", "text": "🔍 正在从知识库检索相关表和字段..."}
        rag_docs = self._rag_retrieve(question)
        # 提取检索到的表名，用于前端展示"已关联: user_info, user_power, risk_warn"
        rag_tables = [d["id"].replace("table_","") for d in rag_docs if d.get("id","").startswith("table_")]
        rag_info = ", ".join(rag_tables[:3]) if rag_tables else "通用知识库"
        yield {"event": "phase", "data": "rag_done", "text": f"✅ 已关联: {rag_info}"}

        # Phase 2: LLM 流式生成 SQL
        yield {"event": "phase", "data": "generate", "text": "🤔 正在分析问题，生成 SQL...\n"}
        prompt = self._build_prompt(question, rag_docs)

        # 使用 Ollama 的流式调用，设置 stream=True
        stream = ollama.chat(
            model=OLLAMA_CONFIG["llm_model"],
            messages=[{"role": "user", "content": prompt}],
            stream=True
        )
        full_response = ""
        for chunk in stream:
            token = chunk["message"]["content"]       # 每个 chunk 是一个小 token
            full_response += token
            yield {"event": "token", "data": token}   # 逐 token 推送到前端

        yield {"event": "token", "data": "\n"}

        # 从流式输出的完整响应中提取 SQL（与 _generate_sql 逻辑一致但直接内联）
        m = re.search(r"```(?:sql)?\s*(.*?)\s*```", full_response, re.DOTALL | re.IGNORECASE)
        sql_raw = m.group(1).strip() if m else full_response
        if not sql_raw.lower().startswith("select"):
            m2 = re.search(r"(SELECT\b[\s\S]+)", full_response, re.IGNORECASE)
            sql_raw = m2.group(1).strip() if m2 else full_response

        # Phase 3: SQL 校验
        valid, sql = self._validate_sql(sql_raw)
        if not valid:
            yield {"event": "phase", "data": "error", "text": f"❌ {sql}"}
            yield {"event": "done", "data": {"sql": sql_raw, "error": sql, "elapsed": round(time.time()-t0,2)}}
            return

        yield {"event": "phase", "data": "execute", "text": f"\n⚡ 执行 SQL...\n```sql\n{sql}\n```\n"}

        # Phase 4: 执行 SQL 并生成结果预览
        result = self._execute_sql(sql)
        elapsed = round(time.time()-t0, 2)

        if result["success"]:
            cnt = result["row_count"]
            # 构造前端友好的结果预览（表格形式，最多显示 5 行）
            rows = result["rows"]
            preview = ""
            if rows:
                cols = result["columns"]
                preview = "\n📊 结果预览:\n" + " | ".join(cols[:6]) + "\n" + "-"*40 + "\n"
                for r in rows[:5]:
                    preview += " | ".join([str(r.get(c, "")) for c in cols[:6]]) + "\n"
                if cnt > 5:
                    preview += f"... 共 {cnt} 条记录"

            yield {"event": "done", "data": {
                "sql": sql, "result": result, "elapsed": elapsed,
                "preview": f"✅ 查询完成！返回 {cnt} 条记录，耗时 {elapsed}s{preview}"
            }}
        else:
            yield {"event": "done", "data": {
                "sql": sql, "result": result, "elapsed": elapsed,
                "preview": f"❌ 执行失败: {result['error']}"
            }}


# ============================================================
# 全局引擎单例
# 说明：采用模块级懒加载单例模式，整个进程共享一个 NL2SQLEngine 实例
#       避免重复创建 ChromaDB 连接和向量集合引用，提升性能和响应速度
#       Flask 应用通过 get_engine() 获取引擎实例
# ============================================================
_engine = None
def get_engine():
    """
    获取 NL2SQLEngine 全局单例。
    
    首次调用时创建引擎实例（懒加载），后续调用直接返回已创建的实例。
    引擎初始化包括：连接 ChromaDB、加载向量集合。
    """
    global _engine
    if _engine is None: _engine = NL2SQLEngine()
    return _engine
