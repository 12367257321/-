"""
RAG 知识库初始化 - 将数据库 Schema 向量化存入 ChromaDB
==========================================================================
【用途】
运行一次即可: python rag_init.py
将 Oracle 数据库的所有表结构、视图定义、字段说明、示例 SQL 和业务规则
通过嵌入模型（bge-m3）转换为语义向量，存入 ChromaDB 向量数据库。
后续 NL2SQL 引擎通过向量相似度检索，找到与用户问题最相关的 Schema 知识。

【运行时机】
- 首次部署系统时运行一次
- 数据库表结构或视图变更后重新运行
- 知识库文档内容更新后重新运行

【向量嵌入原理】
嵌入模型（Embedding Model）将一段文本（如"USER_POWER 表存储每日用电数据..."）
映射到高维向量空间（BGE-M3 输出 1024 维向量）。语义相近的文本在向量空间中
距离更近，因此当用户问"哪些用户夜间用电很少"时，包含"night_ratio"和"窃电"
相关描述的文档会被优先检索到。

【依赖】
- chromadb: 向量数据库
- ollama: 本地嵌入模型服务（bge-m3）
==========================================================================
"""
import chromadb, ollama, sys, os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from config import OLLAMA_CONFIG, CHROMA_CONFIG

# ============================================================
# 数据库 Schema 知识文档（按当前 Oracle 表结构编写）
# 说明：SCHEMA_DOCS 是知识库的"原材料"，包含以下四类文档：
#   1. 表级描述 (table_*): 每张 Oracle 表的字段、用途、关联关系
#   2. 视图描述 (view_*): 业务视图的定义和关键字段
#   3. 示例 SQL (example_*): 常见查询的参考 SQL，帮助大模型理解正确的写法
#   4. 关键说明 (key_note_*): 业务规则、判定逻辑等"软知识"
#
#   每个文档有两个字段：
#   - id: 文档的唯一标识符，用于在检索结果中溯源
#   - content: 文档正文，会被 BGE-M3 嵌入模型转为语义向量
# ============================================================
SCHEMA_DOCS = [
    # ================================================================
    # --- 表级描述 (table_*) ---
    # 每张表一个 entry，描述表的用途、所有字段、关联关系
    # ================================================================
    {
        "id": "table_user_info",
        "content": """表名: USER_INFO (用户信息表)
用途: 存储所有用电用户的基本档案信息，包括居民、商业、工业三类用户
字段:
- user_id (NUMBER): 主键，用户编号
- user_name (VARCHAR2): 用户姓名/企业名称
- addr (VARCHAR2): 住址/企业地址
- meter_code (VARCHAR2): 电表编号，唯一
- phone (VARCHAR2): 联系电话
- user_type (VARCHAR2): 用户类型，取值: 居民/商业/工业
- create_time (DATE): 建档时间
关联: 被 USER_POWER 表和 RISK_WARN 表引用""",
    },
    {
        "id": "table_user_power",
        "content": """表名: USER_POWER (用户每日用电表) - 核心数据表
用途: 记录每个用户每天的白昼用电量和夜间用电量，是窃电判定的唯一数据源
字段:
- pid (NUMBER): 主键，记录编号
- user_id (NUMBER): 外键，关联 USER_INFO 的用户编号
- record_date (DATE): 记录日期
- day_power (NUMBER): 白天用电量(单位:kWh)
- night_power (NUMBER): 夜间用电量(单位:kWh)
- total_power (NUMBER): 当日总用电量 = day_power + night_power，由触发器自动计算
- night_ratio (NUMBER): 夜间用电占比 = night_power / total_power，由触发器自动计算。取值范围0~1
- create_time (DATE): 录入时间
关联: 关联 USER_INFO 表。查询时可按时间范围、用户类型、夜间比例等条件筛选
重要: 查询偷电嫌疑用户时，夜间比例(night_ratio)越低越可疑""",
    },
    {
        "id": "table_illegal_rule",
        "content": """表名: ILLEGAL_RULE (窃电判定规则表)
用途: 存储不同用户类型的窃电判定阈值标准。支持按用户类型分别设定阈值
字段:
- rule_id (NUMBER): 主键
- rule_name (VARCHAR2): 规则名称
- user_type (VARCHAR2): 适用的用户类型: 居民/商业/工业
- night_ratio_threshold (NUMBER): 夜间用电比例阈值。居民-0.20(低于20%异常), 商业-0.08(低于8%异常), 工业-0.15(低于15%异常)
- monthly_drop_threshold (NUMBER): 月度用电骤降阈值。居民-0.40, 商业-0.35, 工业-0.30
- fluctuation_threshold (NUMBER): 用电波动系数阈值。居民-0.60, 商业-0.50, 工业-0.45
- is_active (NUMBER): 是否启用 1/0
判定逻辑: 不同用户类型的阈值不同。居民家庭夜间有冰箱路由器等基础用电，阈值较高(20%)；商业场所夜间基本无用电，阈值很低(8%)；工业企业三班倒24h运转，阈值居中(15%)""",
    },
    {
        "id": "table_risk_warn",
        "content": """表名: RISK_WARN (异常窃电预警表)
用途: 系统自动检测到异常用电后，自动存入此表的预警记录。供电局工作人员直接查此表即可获得排查名单
字段:
- warn_id (NUMBER): 主键，预警编号
- user_id (NUMBER): 外键，关联 USER_INFO
- pid (NUMBER): 外键，触发预警的用电记录编号
- warn_type (VARCHAR2): 预警类型: 夜间用电比例异常 / 用电波动异常
- warn_level (VARCHAR2): 预警级别: 一级(严重) / 二级(中等) / 三级(轻微)
- warn_desc (VARCHAR2): 预警详细描述，包含夜间比例具体数值和阈值对比
- night_ratio (NUMBER): 触发预警时的夜间用电比例
- warn_time (DATE): 预警生成时间
- is_handled (NUMBER): 是否已处理 0-未处理 1-已处理
- handle_time (DATE): 处理时间
- handler (VARCHAR2): 处理人姓名""",
    },
    # ================================================================
    # --- 视图描述 (view_*) ---
    # 每个业务视图一个 entry，帮助大模型理解视图包含哪些计算字段
    # ================================================================
    {
        "id": "view_power_profile",
        "content": """视图: V_USER_POWER_PROFILE (用户用电画像视图)
用途: 自动计算每个用户的用电行为画像，包括日均用电、夜间占比、波动系数、用电标签等
关键字段:
- user_id, user_name, addr, meter_code, user_type: 用户基本信息
- avg_day_power, avg_night_power: 日均白昼/夜间用电量(kWh)
- avg_night_ratio: 近30天平均夜间用电占比
- fluct_coef: 用电波动系数(标准差/均值)，反映用电是否忽高忽低
- month_total_power: 本月用电总量
- power_label: 用电标签: 极高风险/高风险/关注/正常（已按user_type差异化判定）
- power_rank: 用电量全量排名
查询时可通过 user_type 筛选居民/商业/工业用户""",
    },
    {
        "id": "view_suspected_users",
        "content": """视图: V_SUSPECTED_USERS (疑似窃电用户视图)
用途: 自动筛选全部疑似窃电用户，供电局直接查此视图即可。
**列名（必须严格使用这些名称）**:
- user_id, user_name, user_type, addr, meter_code: 用户基本信息
- avg_night_ratio: 近30天平均夜间用电占比（不是night_ratio！视图里没有night_ratio列！）
- fluct_coef: 用电波动系数
- power_label: 用电标签: 极高风险/高风险/关注/正常
- risk_level: 风险等级: 一级(严重)/二级(中等)/三级(轻微)
- risk_score: 风险评分(0-60)，越高越可疑
- abnormal_reason: 异常原因说明
WHERE 条件示例: WHERE user_type='商业' AND avg_night_ratio<0.08（不能用night_ratio！）""",
    },
    # ================================================================
    # --- 示例 SQL (example_*) ---
    # 提供常见查询的参考写法，帮助大模型学习正确的 SQL 模式
    # 当用户问题与示例相近时，大模型会参考这些 SQL 结构
    # ================================================================
    {
        "id": "example_resident_abnormal",
        "content": """示例SQL - 查询居民用户中夜间比例异常的窃电嫌疑人:
SELECT user_name, user_type, ROUND(avg_night_ratio*100,1)||'%' as night_pct, risk_level, risk_score
FROM v_suspected_users WHERE user_type='居民' ORDER BY risk_score DESC
或查询全部类型: SELECT * FROM v_suspected_users ORDER BY risk_score DESC""",
    },
    {
        "id": "example_business_user",
        "content": """示例SQL - 查询商业用户用电画像:
SELECT user_name, avg_day_power, avg_night_power, ROUND(avg_night_ratio*100,1)||'%' night_pct, power_label
FROM v_user_power_profile WHERE user_type='商业' ORDER BY avg_total_power DESC""",
    },
    {
        "id": "example_industry_abnormal",
        "content": """示例SQL - 查询工业用户窃电预警:
SELECT u.user_name, w.warn_type, w.warn_level, w.warn_desc, w.warn_time
FROM risk_warn w JOIN user_info u ON w.user_id=u.user_id
WHERE u.user_type='工业' AND w.is_handled=0 ORDER BY w.warn_time DESC""",
    },
    {
        "id": "example_alarm_count",
        "content": """示例SQL - 按用户类型统计预警数量:
SELECT u.user_type, COUNT(*) as warn_count
FROM risk_warn w JOIN user_info u ON w.user_id=u.user_id
WHERE w.is_handled=0 GROUP BY u.user_type ORDER BY warn_count DESC""",
    },
    {
        "id": "example_trend",
        "content": """示例SQL - 查询用户近30天用电趋势:
SELECT record_date, day_power, night_power, total_power, ROUND(night_ratio*100,1)||'%' night_pct
FROM user_power WHERE user_id=3 AND record_date>=TRUNC(SYSDATE)-30 ORDER BY record_date""",
    },
    {
        "id": "example_user_type_stats",
        "content": """示例SQL - 统计各用户类型的平均用电情况:
SELECT user_type, COUNT(*) user_cnt, ROUND(AVG(total_power),2) avg_power
FROM user_power p JOIN user_info u ON p.user_id=u.user_id
WHERE p.record_date>=TRUNC(SYSDATE)-30 GROUP BY u.user_type""",
    },
    # ================================================================
    # --- 关键业务说明 (key_note_*) ---
    # 这类文档不包含表结构，而是描述业务规则和判定逻辑
    # 帮助大模型理解"为什么这样判定"和"各用户类型的区别"
    # ================================================================
    {
        "id": "key_note_threshold",
        "content": """重要说明 - 用户类型与判定阈值:
系统包含三种用户类型，每种类型的窃电判定阈值不同（存在 illegal_rule 表中）：
- 居民: 夜间用电<20%为异常。正常居民夜间有冰箱路由器等基础用电
- 商业: 夜间用电<8%为异常。商场/写字楼晚上关灯，用电极低是正常的，但如果夜间比例过低也可能是偷电
- 工业: 夜间用电<15%为异常。工厂通常三班倒24h运转，夜间比例较高

查询时: 用 user_type 字段过滤用户类型。user_id 是用户编号。"用户"和"企业"都是指 USER_INFO 表中的记录，通过 user_type 区分。""",
    },
    {
        "id": "key_note_night_ratio",
        "content": """核心判定逻辑说明:
正常家庭: 夜间用电占白天用电的30%~80%（冰箱、路由器、夜灯持续运行）
窃电嫌疑: 夜间用电/总用电 < 阈值(居民20%/商业8%/工业15%)，因为偷电者通常夜里断开电表
辅助判定: 月度用电环比骤降超过阈值，或用电波动系数超过阈值

注意: 系统只做疑似标记，不是直接定罪。供电工人拿着名单上门实地核查才能确认是否偷电。""",
    },
]

# ============================================================
# 构建知识库
# ============================================================

def get_embedding(text):
    """
    调用 Ollama 嵌入模型将文本转为语义向量。
    
    工作原理：
    BGE-M3 模型将输入文本（如"USER_POWER 表记录每日用电量..."）编码为
    一个 1024 维的浮点数向量。这个向量捕捉了文本的语义含义——"用电量"和
    "电力消耗"的向量会很接近，而"用电量"和"用户姓名"的向量则相距较远。
    
    参数:
        text: 待嵌入的文本内容（SCHEMA_DOCS 中每条文档的 content 字段）
    
    返回:
        list[float]: 1024 维的浮点数向量，用于 ChromaDB 的相似度检索
    """
    # 调用 Ollama API 的 embeddings 端点，使用 BGE-M3 模型
    resp = ollama.embeddings(model=OLLAMA_CONFIG["embedding_model"], prompt=text)
    return resp["embedding"]


def build_knowledge_base():
    """
    构建完整的向量知识库。
    
    执行流程：
    1. 连接 ChromaDB（持久化模式，数据保存在 chroma_db 目录）
    2. 删除旧的知识库集合（确保每次构建都是全新的，不会残留旧数据）
    3. 创建新集合，设置元数据描述
    4. 遍历 SCHEMA_DOCS 中的每一条知识文档：
       a. 调用 get_embedding() 将文档文本转为 1024 维向量
       b. 将文档 ID、向量和原文存入 ChromaDB 集合
    5. 确认集合规模
    
    注意：
    - 此操作会清空旧的知识库，确保 Schema 文档更新后能完全生效
    - 每条文档的嵌入向量生成需要调用 Ollama 模型，13 条文档约需 10-30 秒
    """
    print("🔧 连接 ChromaDB...")
    # 创建 ChromaDB 持久化客户端，数据文件存储在本地磁盘
    client = chromadb.PersistentClient(path=CHROMA_CONFIG["persist_dir"])
    # 删除旧的知识库集合（如果存在），确保重建时不会残留过时数据
    try:
        client.delete_collection(CHROMA_CONFIG["collection_name"])
    except: pass

    # 创建新的向量集合，名称与 nl2sql.py 中引用的 collection_name 一致
    col = client.create_collection(
        name=CHROMA_CONFIG["collection_name"],
        metadata={"description": "窃电稽查系统数据库Schema知识库"},
    )
    # 逐条将知识文档向量化并存入 ChromaDB
    for i, doc in enumerate(SCHEMA_DOCS):
        # 调用嵌入模型将文档文本转为语义向量
        emb = get_embedding(doc["content"])
        # 存入 ChromaDB：一条记录包含唯一 ID、嵌入向量和原始文档文本
        col.add(ids=[doc["id"]], embeddings=[emb], documents=[doc["content"]])
        print(f"  [{i+1}/{len(SCHEMA_DOCS)}] ✅ {doc['id']}")
    print(f"\n✅ 知识库构建完成！共 {col.count()} 条知识")


def test_search(query="哪些商业用户有窃电嫌疑"):
    """
    测试检索功能：验证知识库是否能正确检索到相关文档。
    
    测试流程：
    1. 连接已构建的 ChromaDB 集合
    2. 将测试查询转为嵌入向量
    3. 在集合中检索 top 3 条最相关的文档
    4. 按相似度从高到低输出结果
    
    通过测试可以验证：
    - 嵌入模型是否正常工作
    - 知识库文档内容是否与预计的查询意图匹配
    - 相似度分数是否合理（通常 > 0.7 为高质量匹配）
    
    参数:
        query: 测试用的自然语言查询，默认为"哪些商业用户有窃电嫌疑"
    """
    client = chromadb.PersistentClient(path=CHROMA_CONFIG["persist_dir"])
    col = client.get_collection(CHROMA_CONFIG["collection_name"])
    # 将测试查询转为嵌入向量
    emb = get_embedding(query)
    # 检索 top 3 条最相似的文档
    results = col.query(query_embeddings=[emb], n_results=3)
    print(f"\n🔍 测试检索: '{query}'")
    for i,(did,doc,dist) in enumerate(zip(results["ids"][0],results["documents"][0],results["distances"][0])):
        # 相似度 = 1 - 距离（距离越小 = 越相似）
        print(f"  Rank{i+1} [{did}] 相似度={1-dist:.4f}: {doc[:80]}...")
    return results


if __name__ == "__main__":
    # 直接执行此脚本时，依次构建知识库并测试检索效果
    build_knowledge_base()
    test_search()
