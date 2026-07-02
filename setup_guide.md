# ============================================================
# 基于用户用电行为画像的电网异常窃电智能识别系统
# 环境搭建 & 运行说明
# ============================================================

---

## 一、项目简介

本项目是一个**纯 SQL + Web 前端**的数据库课设系统，用于自动识别异常窃电行为。

**核心思路：**
- 正常家庭：夜间用电量占白天用电量的 30%~80%（冰箱、路由器、夜灯持续运行）
- 窃电嫌疑：夜间用电量/白天用电量 < 20%（夜间断开电表，电表不走字）
- 辅以月度用电环比骤降（>40%）和用电波动异常 双重判定

**数据库表（4张）：**
| 表名 | 用途 |
|---|---|
| user_info | 用户基础信息 |
| user_power | 用户每日白天/夜间用电记录 |
| illegal_rule | 窃电判定规则（阈值可配置） |
| risk_warn | 系统自动生成的异常预警记录 |

---

## 二、所需软件（只需 2 个）

### 方案一：本地安装（推荐）

**1. MySQL 8.0**
- 下载地址：https://dev.mysql.com/downloads/mysql/8.0.html
- 安装时设置 root 密码为 `123456`（好记，也可自定义，需同步修改 config.py）
- 安装完成后确保 MySQL 服务已启动

**2. Navicat for MySQL（可视化工具，可选）**
- 用来可视化操作数据库、一键执行 SQL 脚本
- 也可以用 MySQL Workbench、DBeaver 等免费替代

**3. Python 3.9+**
- 下载地址：https://www.python.org/downloads/
- 安装时勾选 "Add Python to PATH"

### 方案二：零安装（在线运行 SQL）
- 打开 https://sqlfiddle.com 或菜鸟教程在线 SQL 工具
- 选择 MySQL 8.0，粘贴 `database/setup.sql` 执行
- 缺点：无前端界面，仅能验证 SQL

---

## 三、搭建步骤（一步步照着做）

### 第1步：初始化 MySQL 数据库

```bash
# 方法A：用 Navicat 执行（推荐新手）
1. 打开 Navicat → 新建连接 → MySQL
2. 连接名随意，主机 localhost，端口 3306，用户名 root，密码 123456
3. 测试连接 → 显示"连接成功"
4. 右键 → 新建数据库 → 数据库名填 power_anti_electricity
5. 双击打开该库 → 点击"查询" → "新建查询"
6. 将 database/setup.sql 全部内容复制粘贴进去 → 点击运行

# 方法B：用命令行执行
# 打开 CMD/PowerShell，进入项目目录
cd d:\数据库课设
mysql -u root -p < database/setup.sql
# 输入密码 123456
```

执行成功后，你应该看到类似输出：
```
用户总数: 15
用电记录总数: 450
预警记录总数: XX
未处理预警: XX
疑似窃电用户: XX
```

**确认数据正确：**
```sql
-- 在 Navicat 查询窗口执行以下语句验证
SELECT * FROM v_suspected_users;     -- 查看疑似窃电用户
SELECT * FROM v_user_power_profile;  -- 查看用电画像
```

---

### 第2步：安装 Python 依赖

```bash
# 打开 CMD/PowerShell，进入项目目录
cd d:\数据库课设
pip install -r requirements.txt

# 如果下载慢，使用国内镜像：
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

---

### 第3步：修改配置（如需要）

打开 `config.py`，确认数据库连接信息与你的 MySQL 一致：

```python
DB_CONFIG = {
    "host": "localhost",       # MySQL 服务器地址
    "port": 3306,              # MySQL 端口
    "user": "root",            # 用户名
    "password": "123456",      # 密码（改成你安装时设置的）
    "database": "power_anti_electricity",  # 数据库名不要改
}
```

---

### 第4步：启动系统

```bash
# 在项目目录下执行
cd d:\数据库课设
python app.py
```

看到以下输出表示启动成功：
```
============================================================
  ⚡ 电网异常窃电智能识别系统 - 后端启动中...
  📍 访问地址: http://localhost:5000
============================================================
```

### 第5步：打开前端页面

打开浏览器，访问：**http://localhost:5000**

你会看到一个完整的管理界面，包含 7 个功能标签页。

---

## 四、功能说明（对应课设要求）

| 标签页 | 功能 | 使用的 SQL 技术 |
|---|---|---|
| 系统概览 | 仪表盘：统计卡片+图表+最近预警 | 聚合查询+存储过程 |
| 用户管理 | 增删改查用户信息 | 单表 CRUD |
| 用电数据 | 录入/编辑每日用电量 | 单表 INSERT/UPDATE，触发器自动计算 |
| 用电画像 | 查看每个用户的用电标签 | VIEW + GROUP BY + 聚合(AVG/MAX/MIN/RANK) + CASE WHEN |
| 窃电预警 | 查看系统自动预警+手动全量稽查 | 多表联查 + 子查询 + 触发器 TRIGGER |
| 稽查报表 | 一键生成嫌疑人表 | 存储过程 PROCEDURE + 开窗函数 |
| 规则配置 | 修改判定阈值 | UPDATE + 动态规则 |

**满足老师「多种查询方式」硬性要求：**
✅ 单表查询（用户管理、用电数据录入）
✅ 多表联查（画像视图 JOIN 3 张表）
✅ 子查询（疑似用户筛选、环比计算）
✅ 聚合函数（AVG/MAX/MIN/SUM/COUNT/STDDEV）
✅ 视图 VIEW（v_user_power_profile、v_suspected_users、v_daily_trend）
✅ 触发器 TRIGGER（自动计算比例+自动预警）
✅ 存储过程 PROCEDURE（sp_generate_report、sp_check_all_users）
✅ 开窗函数（RANK、ROW_NUMBER、LAG）

---

## 五、测试流程（验证系统正确性）

1. 启动系统后进入「系统概览」，确认统计数据正常
2. 进入「用户管理」，查看 15 个测试用户
3. 进入「用电画像」，可以看到王五、钱七、郑十一、卫十五等被标记为"高风险"
4. 进入「窃电预警」，可以看到系统自动生成的预警记录
5. 进入「稽查报表」，点击"生成报表"，可以看到嫌疑人列表
6. **关键验证**：进入「用电数据」，为张三新增一条用电记录（白天=10，夜间=1），保存后立即回到「窃电预警」，应该能看到张三新的预警记录——这证明了触发器自动预警功能正常工作

---

## 六、项目特色（答辩亮点）

1. **选题创新**：不局限于传统的电费计算，而是基于用电行为数据画像进行智能窃电筛查，贴合国家电网真实业务
2. **自动化预警**：触发器实现数据录入即自动检测，无需人工干预
3. **规则可配置**：判定阈值存数据库表，修改规则不用改代码
4. **三级综合判定**：夜间比例 + 月度骤降 + 波动异常，不是单一维度
5. **用户画像标签**：自动为每个用户生成用电行为标签（正常/关注/高风险/极高风险）
6. **前端可视化**：ECharts 图表展示用电趋势和画像分布

---

## 七、文件结构

```
d:\数据库课设\
├── database/
│   └── setup.sql          # 完整的建库SQL（建表+数据+视图+触发器+存储过程）
├── templates/
│   └── index.html         # 前端页面（Bootstrap + ECharts 图表）
├── app.py                 # Flask 后端 API 服务（主入口）
├── config.py              # 配置文件（数据库连接、业务参数）
├── requirements.txt       # Python 依赖
└── setup_guide.md         # 本说明文档
```

---

## 八、常见问题

**Q: MySQL 连接失败 "Access denied for user 'root'"?**
A: 检查 config.py 中的 password 是否正确，MySQL 安装时设置的密码是什么就填什么。

**Q: 端口 5000 被占用？**
A: 修改 config.py 中 FLASK_CONFIG 的 port 值，改成其他端口如 5001。

**Q: 想要更多测试数据？**
A: 修改 database/setup.sql 中 sp_generate_test_data 存储过程的用户数和天数参数。

**Q: 想要导出 Excel 报表？**
A: 安装 openpyxl 库后可以扩展导出功能。

**Q: 能部署到服务器吗？**
A: 可以。修改 config.py 的 host 和 port，用 gunicorn/waitress 替代 Flask 内置服务器。

---

## 九、AI 智能查询（RAG + Ollama + DeepSeek-R1）

### 9.1 这是什么？

系统内置了 **RAG（检索增强生成）** 引擎，让你用**大白话**查询数据库：

```
输入: "哪些商业用户有窃电嫌疑"  →  AI自动生成SQL → 返回结果
输入: "统计各类型预警数量"       →  同上
```

不用写 SQL，像聊天一样问就行。

### 9.2 工作原理

```
你问问题
  → ① RAG检索: 在14条数据库Schema知识中搜最相关的
  → ② 拼Prompt: 知识 + 问题 → 发给Ollama
  → ③ DeepSeek-R1: 生成Oracle SQL
  → ④ 安全校验 + 自动修正列名
  → ⑤ Oracle执行 → 返回结果
```

### 9.3 怎么配（3步）

**第1步：装 Ollama**

去 https://ollama.com 下载安装（和装普通软件一样，下一步下一步）

**第2步：下载两个模型**

打开 CMD/PowerShell，分别执行：

```bash
ollama pull bge-m3              # 嵌入模型（RAG用的，约1.3GB）
ollama pull deepseek-r1:latest  # 大模型（SQL生成用的，约4.7GB）
```

下载完成后 `ollama list` 应该能看到两个模型。

**第3步：初始化 RAG 知识库**

```bash
cd d:\数据库课设
python rag_init.py
```

看到 "✅ 知识库构建完成！共 14 条知识" 就成功了。

然后重启 Flask 即可：`python app.py`

### 9.4 怎么验证 RAG 正常工作

1. 打开 http://localhost:5000
2. 左侧边栏点 **🤖 AI 智能查询**
3. 输入 "统计各类型用户数量" → 点提问
4. 如果返回结果（不是报 Ollama 连接失败），说明 RAG 正常

### 9.5 RAG 知识库包含什么（14条）

| 类别 | 条数 | 内容 |
|---|---|---|
| 表结构 | 4条 | USER_INFO、USER_POWER、ILLEGAL_RULE、RISK_WARN 的完整字段说明 |
| 视图结构 | 2条 | V_USER_POWER_PROFILE、V_SUSPECTED_USERS（含正确列名，防错） |
| 示例SQL | 6条 | 按居民/商业/工业分类的查询示例 |
| 业务知识 | 2条 | 用户类型判定阈值、窃电判定核心逻辑 |

### 9.6 出错了怎么办

| 报错 | 原因 | 解决 |
|---|---|---|
| `Connection refused` | Ollama没启动 | 打开 Ollama 应用，或执行 `ollama serve` |
| `model not found` | 模型没下载 | `ollama pull deepseek-r1:latest` |
| `ORA-00942: 表或视图不存在` | SQL用了错误列名 | 已加自动修正，重新问一次 |
| `查询无结果` | 问题太模糊 | 加具体条件，如"商业用户"、"最近7天" |

### 9.7 配置文件位置

- **`config.py`**：`OLLAMA_CONFIG` 里改模型名、地址
- **`rag_init.py`**：`SCHEMA_DOCS` 里改知识库内容
- **`nl2sql.py`**：`_build_prompt()` 里改 Prompt 模板
