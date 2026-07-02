# 【数据库课设】基于Oracle+Flask的电网异常窃电智能识别系统——从0到1完整开发记录

> 📌 **项目地址**：GitHub（链接待补充）  
> 📅 **开发周期**：2026年6月-7月  
> 👨‍💻 **开发者**：王艺霖、牟研（计机244班）  
> 🎯 **课程**：数据库课程设计  
> 🏷️ **标签**：Oracle 21c、Python Flask、NL2SQL、RAG、Ollama、ChromaDB、BGE-M3、ECharts、Bootstrap 5

---

## 目录

1. [项目概述](#一项目概述)
2. [技术栈选型与理由](#二技术栈选型与理由)
3. [系统架构设计](#三系统架构设计)
4. [数据库设计全过程](#四数据库设计全过程)
5. [后端API开发记录](#五后端api开发记录)
6. [前端开发记录](#六前端开发记录)
7. [核心算法详解](#七核心算法详解)
8. [NL2SQL自然语言查询引擎](#八nl2sql自然语言查询引擎)
9. [遇到的BUG与解决方案（重点）](#九遇到的bug与解决方案重点)
10. [性能优化与测试](#十性能优化与测试)
11. [部署与公网访问](#十一部署与公网访问)
12. [项目总结与反思](#十二项目总结与反思)

---

## 一、项目概述

### 1.1 选题背景

电力反窃电稽查是供电局的核心业务之一。传统稽查模式依赖人工线下排查，存在三大痛点：

- **效率低**：人工逐条比对用电数据耗时耗力
- **覆盖有限**：无法做到全量用户排查
- **误判率高**：正常出差/旅游导致的用电骤降容易被误判为窃电

### 1.2 系统目标

构建一套完整的B/S架构反窃电稽查平台，实现：

- 多维度风险评分算法，自动识别疑似窃电用户
- 触发器驱动的实时预警，录入即检测
- 全量稽查存储过程，一键扫描全部用户
- NL2SQL自然语言查询，降低数据查询门槛
- Word报表自动生成，实现办公自动化

### 1.3 系统规模

| 指标 | 数值 |
|------|------|
| 业务表 | 11张 |
| 视图 | 3个 |
| 触发器 | 3个 |
| 存储过程 | 4个 |
| 自定义函数 | 2个 |
| 序列 | 9个 |
| RESTful API | 53个 |
| 注册用户 | 25人 |
| 用电记录 | 2250条 |
| 预警记录 | 992条 |

---

## 二、技术栈选型与理由

### 2.1 数据库：Oracle 21c XE

**选型理由**：
- 课程指定数据库平台，必须掌握
- PL/SQL能力丰富（游标、异常处理、SYS_REFCURSOR），能充分体现数据库编程深度
- 触发器、存储过程、视图等功能完善，适合构建完整的数据处理流水线

**踩坑记录**：
- 初始安装时服务名搞错，折腾了半天才连上 `localhost:1522/XEPDB1`
- Python连接Oracle需要 `oracledb` 驱动，不是 `cx_Oracle`（已废弃）

### 2.2 后端：Python Flask

**选型理由**：
- 轻量灵活，适合课程设计这种小型项目快速开发
- `@app.route` 装饰器注册路由简洁直观
- Session机制开箱即用，配合 `@login_required` 装饰器实现分层鉴权

**为什么不用Django**：
- Django太重，课程设计不需要ORM、Admin后台等全套功能
- Flask更自由，可以完全掌控SQL语句

### 2.3 前端：Bootstrap 5 + ECharts 5 + 原生JS

**选型理由**：
- Bootstrap响应式布局，一套代码适配桌面和移动端
- ECharts图表库功能强大，双Y轴组合图、饼图、横向柱状图都能轻松实现
- 原生JS避免Vue/React的学习成本，fetch API足够用

**为什么不用Vue/React**：
- 课程设计周期短，没时间学框架
- 原生JS配合Bootstrap已经能实现美观的界面
- 单页应用（SPA）用原生JS也能搞定

### 2.4 AI：Ollama + Qwen2.5:3B + ChromaDB + BGE-M3

**选型理由**：
- Ollama本地部署，不需要付费API，数据安全
- Qwen2.5:3B参数量适中，生成SQL质量够用
- ChromaDB零配置嵌入式，完美契合课程项目
- BGE-M3多语言嵌入模型，中文语义理解优秀

**为什么不用OpenAI API**：
- 需要付费，学生党伤不起
- 数据传到云端有安全隐患
- 本地部署更可控，断网也能用

---

## 三、系统架构设计

### 3.1 B/S三层架构

```
┌─────────────────────────────────────────┐
│        表示层 (templates/)               │
│  HTML5 + Bootstrap 5 + ECharts 5        │
│  user.html(7模块)  admin.html(11模块)    │
└──────────────────┬──────────────────────┘
                   │ 53个 RESTful API (JSON)
┌──────────────────┴──────────────────────┐
│        应用层 (app.py + config.py)       │
│  Python Flask + @login_required          │
│  query() / execute() / call_proc()       │
└──────────────────┬──────────────────────┘
                   │ python-oracledb 驱动
┌──────────────────┴──────────────────────┐
│        数据层 (Oracle 21c XE)            │
│  11张表 + 3个视图 + 4个存储过程           │
│  v_suspected_users(核心评分视图)          │
│  sp_check_all_users(全量稽查)            │
└─────────────────────────────────────────┘
```

### 3.2 数据流向

```
用户录入用电数据
    ↓
触发器 trg_power_bi 自动计算 total_power 和 night_ratio
    ↓
触发器 trg_power_ai 实时检测异常，写入 risk_warn
    ↓
视图 v_suspected_users 计算双维度风险评分
    ↓
存储过程 sp_check_all_users 全量稽查，批量生成预警
    ↓
前端 ECharts 可视化展示
    ↓
管理员处理预警 / 下载Word报表
```

---

## 四、数据库设计全过程

### 4.1 ER模型设计

核心实体：
- **USER_INFO**：用户档案（居民/商业/工业）
- **USER_POWER**：每日用电记录（日间/夜间用电量）
- **RISK_WARN**：预警记录（类型/等级/处理状态）
- **ILLEGAL_RULE**：判定规则（三类用户的差异化阈值）

辅助业务表：
- **SYSTEM_USERS**：系统登录账号
- **COMMUNITY_POST**：社区动态
- **POST_LIKE**：点赞
- **POST_COMMENT**：评论
- **VIP_RECORD**：VIP会员
- **ABSENCE_RECORD**：出远门登记
- **USER_NOTIFICATION**：消息通知

### 4.2 视图设计

#### v_user_power_profile（用户用电画像）

```sql
CREATE VIEW v_user_power_profile AS
SELECT 
    u.user_id,
    u.user_name,
    u.user_type,
    AVG(p.day_power) AS avg_day_power,
    AVG(p.night_power) AS avg_night_power,
    AVG(p.total_power) AS avg_total_power,
    AVG(p.night_ratio) AS avg_night_ratio,
    STDDEV(p.total_power) / NULLIF(AVG(p.total_power), 0) AS fluct_coef,
    CASE 
        WHEN STDDEV(p.total_power) / NULLIF(AVG(p.total_power), 0) < 0.3 THEN '稳定用电'
        WHEN STDDEV(p.total_power) / NULLIF(AVG(p.total_power), 0) > 0.6 THEN '需关注'
        ELSE '正常'
    END AS power_label
FROM user_info u
JOIN user_power p ON u.user_id = p.user_id
WHERE p.record_date >= SYSDATE - 30
GROUP BY u.user_id, u.user_name, u.user_type;
```

**设计思路**：
- 近30天数据计算平均值和标准差
- 波动系数 = 标准差 / 均值，反映用电稳定性
- 根据波动系数动态生成绿色省电标签

#### v_suspected_users（疑似窃电用户 - 核心输出）

```sql
CREATE VIEW v_suspected_users AS
SELECT 
    u.user_id,
    u.user_name,
    u.user_type,
    v.avg_night_ratio,
    v.fluct_coef,
    -- 维度一：夜间占比异常评分（满分40）
    CASE 
        WHEN v.avg_night_ratio < 0.10 THEN 40
        WHEN v.avg_night_ratio < 0.20 THEN 25
        ELSE 0
    END +
    -- 维度二：波动系数异常评分（满分20）
    CASE 
        WHEN v.fluct_coef > 0.60 THEN 20
        WHEN v.fluct_coef > 0.40 THEN 10
        ELSE 0
    END AS risk_score,
    -- 风险等级
    CASE 
        WHEN v.avg_night_ratio < 0.10 THEN '一级(严重)'
        WHEN v.avg_night_ratio < 0.20 THEN '二级(中等)'
        WHEN v.fluct_coef > 0.60 THEN '三级(轻微)'
        ELSE '三级(轻微)'
    END AS warn_level
FROM user_info u
JOIN v_user_power_profile v ON u.user_id = v.user_id
WHERE v.avg_night_ratio < 0.20 OR v.fluct_coef > 0.40
ORDER BY risk_score DESC;
```

**设计思路**：
- 双维度加权评分，满分60分
- 夜间占比<10%得40分（严重异常），<20%得25分（中等异常）
- 波动系数>0.6得20分（严重波动），>0.4得10分（偏高波动）
- 自动标注三级预警等级

### 4.3 触发器设计

#### trg_power_bi（BEFORE INSERT - 自动计算派生字段）

```sql
CREATE OR REPLACE TRIGGER trg_power_bi
BEFORE INSERT ON user_power
FOR EACH ROW
BEGIN
    :NEW.total_power := :NEW.day_power + :NEW.night_power;
    :NEW.night_ratio := :NEW.night_power / :NEW.total_power;
END;
```

**作用**：录入用电数据时自动计算总用电量和夜间占比，无需手动计算。

#### trg_power_ai（AFTER INSERT - 实时预警）

```sql
CREATE OR REPLACE TRIGGER trg_power_ai
AFTER INSERT ON user_power
FOR EACH ROW
DECLARE
    v_threshold NUMBER;
BEGIN
    -- 读取对应类型的夜间占比阈值
    SELECT night_ratio_threshold INTO v_threshold
    FROM illegal_rule
    WHERE user_type = (SELECT user_type FROM user_info WHERE user_id = :NEW.user_id)
    AND is_active = 1;
    
    -- 若夜间占比低于阈值，插入预警
    IF :NEW.night_ratio < v_threshold THEN
        INSERT INTO risk_warn(warn_id, user_id, pid, warn_type, warn_level, warn_desc, night_ratio, is_handled)
        VALUES(seq_risk_warn.NEXTVAL, :NEW.user_id, :NEW.pid, '夜间占比异常', '二级', 
               '夜间占比低于阈值', :NEW.night_ratio, 0);
    END IF;
END;
```

**作用**：录入即检测，实时预警，无需等待定时批处理。

### 4.4 存储过程设计

#### sp_check_all_users（全量稽查 - 最核心）

```sql
CREATE OR REPLACE PROCEDURE sp_check_all_users AS
    CURSOR c_night IS
        SELECT u.user_id, u.user_name, u.user_type, AVG(p.night_ratio) AS avg_night
        FROM user_info u
        JOIN user_power p ON u.user_id = p.user_id
        WHERE p.record_date >= SYSDATE - 30
        GROUP BY u.user_id, u.user_name, u.user_type
        HAVING AVG(p.night_ratio) < (
            SELECT night_ratio_threshold FROM illegal_rule 
            WHERE user_type = u.user_type AND is_active = 1
        );
    
    CURSOR c_fluct IS
        SELECT u.user_id, u.user_name, 
               STDDEV(p.total_power) / NULLIF(AVG(p.total_power), 0) AS fluct
        FROM user_info u
        JOIN user_power p ON u.user_id = p.user_id
        WHERE p.record_date >= SYSDATE - 30
        GROUP BY u.user_id, u.user_name
        HAVING STDDEV(p.total_power) / NULLIF(AVG(p.total_power), 0) > (
            SELECT fluctuation_threshold FROM illegal_rule 
            WHERE user_type = u.user_type AND is_active = 1
        );
BEGIN
    -- 第一道游标：夜间占比异常
    FOR rec IN c_night LOOP
        INSERT INTO risk_warn(warn_id, user_id, warn_type, warn_level, warn_desc, night_ratio, is_handled)
        VALUES(seq_risk_warn.NEXTVAL, rec.user_id, '夜间占比异常', '二级', 
               '夜间占比低于阈值', rec.avg_night, 0);
    END LOOP;
    
    -- 第二道游标：波动系数异常
    FOR rec IN c_fluct LOOP
        INSERT INTO risk_warn(warn_id, user_id, warn_type, warn_level, warn_desc, is_handled)
        VALUES(seq_risk_warn.NEXTVAL, rec.user_id, '用电波动异常', '三级', 
               '波动系数超过阈值', 0);
    END LOOP;
    
    -- 第三道游标：去重合并（按uid+pid或uid+7天窗口去重）
    -- ...（省略去重逻辑）
    
    COMMIT;
END;
```

**设计思路**：
- 三道游标分别处理夜间异常、波动异常、去重合并
- 一键触发即可完成全量扫描
- 耗时控制在2秒以内

---

## 五、后端API开发记录

### 5.1 API分类

| 功能域 | API数量 | 核心接口 |
|--------|---------|---------|
| 认证授权 | 4 | login, register, logout, session |
| 用户管理 | 5 | users增删改查 |
| 用电数据 | 4 | power增删改查 |
| 用电画像 | 2 | profile, trend |
| 窃电预警 | 3 | suspected, warnings, handle |
| 全量稽查 | 3 | check, report, report/download |
| 规则管理 | 2 | rules读取和修改 |
| AI对话 | 1 | chat流式 |
| NL2SQL | 2 | nlquery, nlquery/stream |
| 出远门 | 4 | absence增改查, admin/absence |
| VIP系统 | 5 | status, renew, admin操作 |
| 通知系统 | 3 | 列表, 已读, 添加 |
| 社区动态 | 6 | posts列表, 发帖, 点赞, 删除, 评论 |
| 用电排名 | 1 | ranking |
| Oracle函数 | 2 | risk-score, monthly-saving |

### 5.2 核心封装函数

```python
def query(sql, params=None):
    """查询封装，返回字典列表"""
    cur = conn.cursor()
    if params:
        cur.execute(sql, params)
    else:
        cur.execute(sql)
    columns = [col[0].lower() for col in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]

def execute(sql, params=None):
    """执行封装（INSERT/UPDATE/DELETE）"""
    cur = conn.cursor()
    if params:
        cur.execute(sql, params)
    else:
        cur.execute(sql)
    conn.commit()

def call_proc(proc_name, params=None):
    """存储过程调用封装"""
    cur = conn.cursor()
    if params:
        cur.callproc(proc_name, params)
    else:
        cur.callproc(proc_name)
```

### 5.3 分层鉴权装饰器

```python
from functools import wraps

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session and 'suid' not in session:
            return jsonify({'success': False, 'message': '未登录'}), 401
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') not in ('admin', 'super_admin'):
            return jsonify({'success': False, 'message': '无权限'}), 403
        return f(*args, **kwargs)
    return decorated
```

---

## 六、前端开发记录

### 6.1 响应式布局

**桌面端**：侧边栏导航 + 主内容区  
**移动端**：底部Tab导航 + 全屏内容区

```css
/* 桌面端侧边栏 */
.sidebar {
    width: 240px;
    height: 100vh;
    position: fixed;
    left: 0;
    top: 0;
}

/* 移动端底部Tab */
@media (max-width: 768px) {
    .sidebar { display: none; }
    .bottom-tabs {
        position: fixed;
        bottom: 0;
        width: 100%;
        display: flex;
    }
}
```

### 6.2 ECharts双Y轴组合图

```javascript
var chart = echarts.init(document.getElementById('trendChart'));
chart.setOption({
    xAxis: { type: 'category', data: dates },
    yAxis: [
        { type: 'value', name: '日间用电(kWh)' },
        { type: 'value', name: '夜间占比(%)' }
    ],
    series: [
        { name: '日间用电', type: 'bar', data: dayPower },
        { name: '夜间用电', type: 'line', data: nightPower },
        { name: '夜间占比', type: 'line', yAxisIndex: 1, data: nightRatio }
    ]
});
```

### 6.3 原生JS的fetch API

```javascript
async function apiFetch(url, options = {}) {
    try {
        const r = await fetch(url, {
            headers: { 'Content-Type': 'application/json' },
            ...options
        });
        return await r.json();
    } catch (e) {
        console.error('API请求失败:', e);
        return null;
    }
}
```

---

## 七、核心算法详解

### 7.1 双维度加权风险评分

**算法公式**：

```
risk_score = 维度一评分 + 维度二评分（满分60分）

【维度一：夜间用电占比异常评分】（满分40分）
  avg_night_ratio < 0.10  → 40分（严重异常，白天窃电高度嫌疑）
  avg_night_ratio < 0.20  → 25分（中等异常，需关注）
  avg_night_ratio >= 0.20 →  0分（正常）

【维度二：用电波动性异常评分】（满分20分）
  fluct_coef > 0.60  → 20分（严重波动，疑似人为干预）
  fluct_coef > 0.40  → 10分（偏高波动）
  fluct_coef <= 0.40 →  0分（正常）
```

**设计理由**：
- 维度一针对"窃电者通常在夜间断开电表"的典型特征
- 维度二针对"窃电者用电数据忽高忽低"的行为模式
- 双维度交叉验证，避免单一指标片面性

### 7.2 测试数据验证

设计了5名典型可疑用户：

| 用户 | 异常特征 | 风险评分 |
|------|---------|---------|
| 王五 | 夜间占比仅8% | ~40分 |
| 钱七 | 前20天正常，后10天骤降至30% | ~25分 |
| 周九 | 奇偶日大幅交替，波动系数>0.6 | ~20分 |
| 冯十二 | 骤降+夜间占比极低（双重可疑） | ~40分 |
| 卫十五 | 白天16度，夜间仅1.2度 | ~40分 |

算法准确识别全部5种异常模式。

---

## 八、NL2SQL自然语言查询引擎

### 8.1 RAG架构6步流水线

```
Step 1: RAG检索
  用户中文问题 → BGE-M3嵌入 → 1024维语义向量
  → ChromaDB余弦相似度匹配 → top-5相关Schema文档

Step 2: Prompt构建
  检索文档 + 角色设定 + 字段速查表 + 硬约束 + 用户问题
  → 完整LLM提示词

Step 3: SQL生成
  Ollama Qwen2.5:3B生成 → 正则提取纯SQL

Step 4: SQL校验+自动修正
  安全检查：禁止DROP/DELETE/INSERT/ALTER
  自动修正：night_ratio → avg_night_ratio

Step 5: SQL执行
  Oracle执行 → 列名转小写 → datetime转字符串

Step 6: AI解释
  取前20行数据 → LLM生成自然语言分析报告
```

### 8.2 流式返回（SSE）

```python
@app.route('/api/nlquery/stream', methods=['POST'])
def api_nlquery_stream():
    def generate():
        yield f"event: phase\ndata: 正在检索知识库...\n\n"
        # RAG检索
        docs = rag_retrieve(question)
        
        yield f"event: phase\ndata: 正在生成SQL...\n\n"
        # LLM生成SQL（流式）
        for token in ollama_generate(prompt, stream=True):
            yield f"event: token\ndata: {token}\n\n"
        
        yield f"event: phase\ndata: 正在执行查询...\n\n"
        # 执行SQL
        result = execute_sql(sql)
        
        yield f"event: done\ndata: {json.dumps(result)}\n\n"
    
    return Response(generate(), mimetype='text/event-stream')
```

**前端接收**：

```javascript
const es = new EventSource('/api/nlquery/stream');
es.addEventListener('phase', e => console.log('阶段:', e.data));
es.addEventListener('token', e => sqlText += e.data);
es.addEventListener('done', e => showResult(JSON.parse(e.data)));
```

---

## 九、遇到的BUG与解决方案（重点）

### BUG 1：社区动态发帖失败 - NOT NULL约束

**现象**：  
用户点击"发布"按钮后，前端显示"发布失败：网络错误"，后端日志报错：

```
ORA-01400: 无法将 NULL 插入 ("SYSTEM"."COMMUNITY_POST"."USER_ID")
```

**排查过程**：  
1. 检查前端JS，`publishPost()`函数逻辑没问题
2. 检查后端API，`/api/posts`接口正常
3. 检查数据库，发现`community_post.user_id`列有NOT NULL约束
4. 管理员登录后`session["user_id"]`为None，INSERT时传了NULL

**解决方案**：

```sql
-- 1. 去掉NOT NULL约束
ALTER TABLE community_post MODIFY user_id NULL;

-- 2. 后端SQL改为LEFT JOIN
SELECT p.*, NVL(u.user_name, '系统管理员') as user_name
FROM community_post p
LEFT JOIN user_info u ON p.user_id = u.user_id;
```

**教训**：  
- 数据库设计时要考虑管理员账号没有关联user_id的情况
- 前端要检查`result.success`，失败时弹出具体错误信息

---

### BUG 2：出远门登记提交失败 - SQL bind变量数量不匹配

**现象**：  
用户提交出远门登记后，前端显示"登记失败：网络异常"，后端报错：

```
DPY-4009: 2 positional bind values are required but 1 were provided
```

**排查过程**：  
1. 检查前端`submitTravel()`，逻辑没问题
2. 检查后端`/api/absence`接口，SQL语句：

```python
records = query(
    "SELECT a.*, NVL(u.user_name, '系统管理员') as user_name "
    "FROM absence_record a "
    "LEFT JOIN user_info u ON a.user_id = u.user_id "
    "WHERE a.user_id = :1 OR :1 IS NULL",  # 问题在这里！
    (user_id,)
)
```

3. `python-oracledb`按SQL中`:1`出现的次数计数，`:1`出现了两次，但只传了1个参数

**解决方案**：

```python
records = query(
    "SELECT a.*, NVL(u.user_name, '系统管理员') as user_name "
    "FROM absence_record a "
    "LEFT JOIN user_info u ON a.user_id = u.user_id "
    "WHERE a.user_id = :1 OR :2 IS NULL",  # 改为:1和:2
    (user_id, user_id)  # 传两个参数
)
```

**教训**：  
- Oracle的bind变量按出现次数计数，不是按编号
- 同一个变量出现多次，每个位置都要单独编号

---

### BUG 3：出远门记录不显示 - 前端日期解析NaN

**现象**：  
出远门登记成功后，列表不显示记录，或者状态全乱。

**排查过程**：  
1. 检查后端API，数据正常返回
2. 检查前端`loadTravelList()`，发现日期解析问题：

```javascript
// Oracle返回的日期格式
var endDate = "2026-07-05 00:00:00";

// 错误拼接
var dateStr = endDate + "T00:00:00";  // "2026-07-05 00:00:00T00:00:00"

// new Date()返回NaN
var date = new Date(dateStr);  // Invalid Date
```

**解决方案**：

```javascript
// 先截取日期部分
var endDate = "2026-07-05 00:00:00".split(' ')[0];  // "2026-07-05"

// 再拼接时间
var dateStr = endDate + "T00:00:00";  // "2026-07-05T00:00:00"

// 正确解析
var date = new Date(dateStr);  // 正常
```

**教训**：  
- Oracle返回的日期带时间部分，前端要先截取
- 日期拼接要规范，避免无效格式

---

### BUG 4：管理员端出远门管理无数据 - JOIN导致NULL行丢失

**现象**：  
管理员登录后查看"出远门管理"页面，显示"暂无记录"。

**排查过程**：  
1. 检查数据库，数据存在
2. 检查后端SQL：

```sql
SELECT a.*, u.user_name
FROM absence_record a
JOIN user_info u ON a.user_id = u.user_id;
```

3. 管理员提交的记录`user_id`为NULL，INNER JOIN时这些行被过滤掉了

**解决方案**：

```sql
SELECT a.*, NVL(u.user_name, '系统管理员') as user_name
FROM absence_record a
LEFT JOIN user_info u ON a.user_id = u.user_id;
```

**教训**：  
- 涉及可能为NULL的外键关联，必须用LEFT JOIN
- 用NVL处理NULL值，显示友好文本

---

### BUG 5：ngrok公网访问失败 - 隧道断开

**现象**：  
用户通过ngrok公网URL访问，浏览器显示404。

**排查过程**：  
1. 检查Flask服务，正常运行
2. 检查ngrok进程，已经退出
3. 原因：关闭终端时ngrok进程一起被杀

**解决方案**：  
重新启动ngrok：

```bash
& 'D:\数据库课设\ngrok.exe' http 5000
```

**教训**：  
- ngrok是独立进程，关闭终端会一起退出
- 公网URL不变，但需要重新建立隧道

---

### BUG 6：Python环境找不到 - 路径问题

**现象**：  
VSCode运行`python app.py`报错：

```
Python was not found; run without arguments to install from the Microsoft Store...
```

**排查过程**：  
1. 系统PATH里没有Python路径
2. 实际Python在TRAE内置环境：

```
C:\Users\Lin\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\vm\tools\python\python.exe
```

**解决方案**：  
VSCode选择正确的Python解释器：

1. `Ctrl+Shift+P` → `Python: Select Interpreter`
2. 选择上述路径

**教训**：  
- 多Python环境时要明确指定解释器
- TRAE内置Python路径特殊，需要手动选择

---

## 十、性能优化与测试

### 10.1 性能指标

| 指标 | 目标 | 实测 |
|------|------|------|
| 页面响应时间 | ≤3秒 | ~0.5秒 |
| 全量稽查耗时 | ≤2秒 | ~1.5秒 |
| AI查询响应 | ≤15秒 | 3~15秒 |
| 系统内存占用 | - | ~80MB |
| 并发用户数 | 50 | 支持 |

### 10.2 优化措施

1. **数据库索引**：为`user_power(user_id, record_date)`建立复合索引
2. **视图缓存**：Oracle自动缓存视图查询结果
3. **前端懒加载**：图表数据按需加载，避免一次性渲染
4. **连接池**：Flask使用全局连接，避免频繁创建销毁

---

## 十一、部署与公网访问

### 11.1 本地部署

```bash
# 启动Flask
python app.py

# 访问 http://localhost:5000
```

### 11.2 ngrok公网访问

```bash
# 新开终端，启动ngrok
& 'D:\数据库课设\ngrok.exe' http 5000

# ngrok输出公网URL
# https://xxxx.ngrok-free.dev
```

**首次访问**：  
ngrok会显示安全警告页，点击"Visit Site"跳过。

---

## 十二、项目总结与反思

### 12.1 收获

1. **Oracle数据库编程能力**：深入理解了视图、触发器、存储过程的实战应用
2. **全栈开发经验**：从数据库设计到前端展示，完整走了一遍
3. **BUG排查能力**：6个典型BUG的排查过程锻炼了调试能力
4. **AI工程化落地**：NL2SQL引擎让我理解了RAG架构的实际应用

### 12.2 不足

1. **前端代码冗余**：原生JS导致代码量大，应该考虑用Vue重构
2. **安全机制薄弱**：密码明文存储，应该用bcrypt加密
3. **缺少单元测试**：没有写自动化测试，全靠手动验证
4. **文档不完善**：API文档缺失，应该用Swagger生成

### 12.3 后续优化方向

1. 引入Redis缓存，提升查询性能
2. 升级到Vue 3 + TypeScript，提升前端可维护性
3. 增加定时巡检任务，自动生成日报
4. 完善出远门管理与稽查判定的联动逻辑
5. 增加邮件通知功能，预警自动推送

---

## 附录：项目文件清单

```
d:\数据库课设\
├── app.py                      # Flask主应用（53条路由，约1900行）
├── config.py                   # 全局配置
├── nl2sql.py                   # NL2SQL引擎（约500行）
├── rag_init.py                 # ChromaDB知识库初始化
├── templates/
│   ├── login.html              # 登录页
│   ├── admin.html              # 管理员端（12个Tab）
│   └── user.html               # 用户端（7个Tab）
├── database/
│   ├── 02_tables.sql           # 建表脚本
│   ├── 03_triggers_views.sql   # 触发器+视图
│   └── 04_procedures.sql       # 存储过程
└── README.md                   # 项目说明
```

---

## 写在最后

这个项目从6月初开始，到7月初完成，历时一个月。中间踩了无数坑，尤其是Oracle的bind变量、NOT NULL约束、日期解析这些问题，折腾了很久。但正是这些BUG让我对数据库编程有了更深的理解。

NL2SQL引擎是最大的亮点，也是花精力最多的部分。RAG架构、向量检索、流式返回，这些技术栈组合在一起，最终实现了用中文查询数据库的功能。虽然SQL生成质量还有提升空间，但作为课程设计已经足够了。

最后，感谢指导老师的悉心指导，也感谢自己没有在BUG面前放弃。希望这篇博客能帮助到正在做类似项目的同学！

---

**如果对你有帮助，欢迎点赞、收藏、关注！** 🎉

**有问题欢迎评论区交流！** 💬
