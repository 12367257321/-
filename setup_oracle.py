"""
Oracle 数据库一键初始化脚本（含企业用户）
==========================================================================
【用途】
用法: python setup_oracle.py
一键完成 Oracle 数据库的完整初始化，包括建表、建视图、建存储过程、
插入示范用户和用电数据，以及配置自动预警触发器。

【执行顺序（6 步）】
  Step 1: 清理旧对象 — 删除上次初始化创建的表、序列、视图
  Step 2: 建序列和表 — 创建 4 张核心表 + 4 张辅助表 + 对应序列
  Step 3: 插入规则和数据 — 写入 3 条窃电判定规则 + 25 个演示用户
  Step 4: 生成用电数据 — 为 25 个用户批量生成 30 天的仿真用电记录
  Step 5: 创建触发器 — 3 个触发器保证数据完整性和自动预警
  Step 6: 创建视图和存储过程 — 3 个业务视图 + 4 个存储过程
  收尾: 执行全量稽查 + 输出统计验证

【设计架构说明】
  用户信息(USER_INFO) ← 1对多 → 每日用电(USER_POWER)
       ↓                                 ↓
  判定规则(ILLEGAL_RULE)  —参考—→  预警记录(RISK_WARN) ← 触发器自动生成
       
  视图层: V_USER_POWER_PROFILE(用电画像), V_SUSPECTED_USERS(疑似窃电)
  
【依赖】
- oracledb: Oracle 数据库 Python 驱动
- Oracle XE 数据库已启动并可连接（默认 localhost:1522/XEPDB1）
==========================================================================
"""
import oracledb, random
from datetime import date, timedelta

# Oracle 数据库连接配置（使用 system 管理员账号，拥有完整 DDL 权限）
CONFIG = {"user": "system", "password": "orcl", "dsn": "localhost:1522/XEPDB1"}

def go(cur, sql):
    """
    执行 SQL 的快捷函数。
    
    封装了 cur.execute()，减少代码冗余。
    参数 cur 是 Oracle 游标对象，sql 是待执行的 SQL 字符串。
    """
    cur.execute(sql)


# 建立 Oracle 数据库连接
conn = oracledb.connect(**CONFIG)
cur = conn.cursor()
print(f"[OK] 已连接 Oracle: {CONFIG['dsn']}")

# ============================================================
# Step 1/6: 清理旧数据库对象
# 说明：使用 CASCADE CONSTRAINTS 选项，删除表的同时自动删除相关外键约束
#       尝试删除所有可能存在的旧对象，如果对象不存在则忽略异常（pass）
#       包括：核心业务表、社区/系统辅助表、以及对应的序列
# ============================================================
print("[1/6] 清理...")
for obj in ["risk_warn","user_power","user_info","illegal_rule","absence_record","system_users","vip_record","vip_subscription","community_post","post_like","power_posts"]:
    try: go(cur, f"DROP TABLE {obj} CASCADE CONSTRAINTS")  # CASCADE CONSTRAINTS 级联删除外键约束
    except: pass
for seq in ["seq_user_info","seq_user_power","seq_illegal_rule","seq_risk_warn","seq_absence","seq_sys_users","seq_vip","seq_posts","seq_post_like"]:
    try: go(cur, f"DROP SEQUENCE {seq}")                     # 删除序列，确保 ID 从 1 开始
    except: pass
try: go(cur, "PURGE RECYCLEBIN")                             # 清空 Oracle 回收站，释放表空间
except: pass
print("  完成")

# ============================================================
# Step 2/6: 创建序列和表
# 说明：先创建序列（自增主键生成器），再创建表结构
#       Oracle 没有 MySQL 的 AUTO_INCREMENT，需要用序列 + 触发器或直接 NEXTVAL
# ============================================================
print("[2/6] 建序列和表...")
# --- 创建序列（相当于自增 ID 生成器） ---
go(cur, "CREATE SEQUENCE seq_user_info START WITH 1")    # 用户信息表序列
go(cur, "CREATE SEQUENCE seq_user_power START WITH 1")   # 用电记录表序列
go(cur, "CREATE SEQUENCE seq_illegal_rule START WITH 1") # 判定规则表序列
go(cur, "CREATE SEQUENCE seq_risk_warn START WITH 1")    # 预警记录表序列

# --- 核心表 1: USER_INFO（用户信息表） ---
# 说明：存储所有用电用户的基本档案，包括居民、商业、工业三类
go(cur, "CREATE TABLE user_info (user_id NUMBER PRIMARY KEY, user_name VARCHAR2(50) NOT NULL, addr VARCHAR2(200), meter_code VARCHAR2(20) NOT NULL, phone VARCHAR2(20), user_type VARCHAR2(20), create_time DATE)")
go(cur, "ALTER TABLE user_info ADD CONSTRAINT uk_meter_code UNIQUE (meter_code)")  # 电表编号必须唯一

# --- 核心表 2: USER_POWER（用户每日用电表） ---
# 说明：记录每位用户每天的白昼和夜间用电量，是窃电判定的核心数据源
#       total_power 和 night_ratio 由触发器 trg_power_bi 自动计算
go(cur, "CREATE TABLE user_power (pid NUMBER PRIMARY KEY, user_id NUMBER NOT NULL, record_date DATE NOT NULL, day_power NUMBER(12,2) NOT NULL, night_power NUMBER(12,2) NOT NULL, total_power NUMBER(12,2), night_ratio NUMBER(5,4), create_time DATE)")
go(cur, "ALTER TABLE user_power ADD CONSTRAINT uk_uid_date UNIQUE (user_id, record_date)")              # 同一用户同一天只能有一条记录
go(cur, "ALTER TABLE user_power ADD CONSTRAINT fk_power_uid FOREIGN KEY (user_id) REFERENCES user_info(user_id) ON DELETE CASCADE")  # 外键关联用户表，级联删除

# --- 核心表 3: ILLEGAL_RULE（窃电判定规则表） ---
# 说明：存储不同用户类型（居民/商业/工业）的窃电判定阈值标准
#       user_type 列支持按用户类型差异化判定
#       is_active 列支持动态启用/停用规则
go(cur, "CREATE TABLE illegal_rule (rule_id NUMBER PRIMARY KEY, rule_name VARCHAR2(50) NOT NULL, user_type VARCHAR2(20) NOT NULL, night_ratio_threshold NUMBER(5,4) NOT NULL, monthly_drop_threshold NUMBER(5,4) NOT NULL, fluctuation_threshold NUMBER(5,4) NOT NULL, is_active NUMBER(1), create_time DATE, update_time DATE)")

# --- 核心表 4: RISK_WARN（异常窃电预警表） ---
# 说明：触发器自动检测到异常用电后，自动写入预警记录
#       支持按预警类型（夜间比例异常/波动异常）和严重级别（一/二/三级）分类
go(cur, """CREATE TABLE risk_warn (warn_id NUMBER PRIMARY KEY, user_id NUMBER NOT NULL, pid NUMBER, warn_type VARCHAR2(50) NOT NULL, warn_level VARCHAR2(20), warn_desc VARCHAR2(500), night_ratio NUMBER(5,4), month_drop NUMBER(5,4), warn_time DATE DEFAULT SYSDATE, is_handled NUMBER(1) DEFAULT 0 NOT NULL, handle_time DATE, handler VARCHAR2(50), remark VARCHAR2(200))""")
go(cur, "ALTER TABLE risk_warn ADD CONSTRAINT fk_warn_uid FOREIGN KEY (user_id) REFERENCES user_info(user_id) ON DELETE CASCADE")
go(cur, "ALTER TABLE risk_warn ADD CONSTRAINT fk_warn_pid FOREIGN KEY (pid) REFERENCES user_power(pid) ON DELETE SET NULL")  # 用电记录删除时，预警记录保留但 pid 置空

# --- 辅助表 5: absence_record（出远门登记表） ---
# 说明：用户可以登记出差/旅游等长时间外出，系统识别后降低误报率
go(cur, "CREATE TABLE absence_record (id NUMBER PRIMARY KEY, user_id NUMBER NOT NULL, start_date DATE NOT NULL, end_date DATE, reason VARCHAR2(200), is_away NUMBER(1) DEFAULT 1, create_time DATE DEFAULT SYSDATE)")
go(cur, "ALTER TABLE absence_record ADD CONSTRAINT fk_abs_uid FOREIGN KEY (user_id) REFERENCES user_info(user_id) ON DELETE CASCADE")

# --- 辅助表 6: system_users（系统用户登录表） ---
# 说明：供电局工作人员/管理员登录系统的账号表
#       role 字段控制权限: admin(管理员)/worker(检修工人)/viewer(只读查看)
#       parent_admin_id 支持管理员层级关系
go(cur, "CREATE TABLE system_users (suid NUMBER PRIMARY KEY, username VARCHAR2(50) UNIQUE NOT NULL, pwd VARCHAR2(64) NOT NULL, user_id NUMBER, role VARCHAR2(20) NOT NULL, parent_admin_id NUMBER, is_active NUMBER(1) DEFAULT 1, create_time DATE DEFAULT SYSDATE)")

# --- 辅助表 7: vip_record（VIP 订阅表） ---
# 说明：商业/工业用户可订阅增值服务（如详细分析报告、实时预警短信）
go(cur, "CREATE TABLE vip_record (id NUMBER PRIMARY KEY, user_id NUMBER NOT NULL, plan_type VARCHAR2(20) NOT NULL, start_date DATE NOT NULL, end_date DATE NOT NULL, is_active NUMBER(1) DEFAULT 1, create_time DATE DEFAULT SYSDATE)")

# --- 辅助表 8: community_post（用电动态圈帖子表） ---
# 说明：用户可以分享节能心得，形成社区互动
go(cur, "CREATE TABLE community_post (id NUMBER PRIMARY KEY, user_id NUMBER NOT NULL, content VARCHAR2(500), power_saved NUMBER(10,2), create_time DATE DEFAULT SYSDATE)")

# --- 辅助表 9: post_like（动态点赞表） ---
# 说明：记录用户对社区帖子的点赞
go(cur, "CREATE TABLE post_like (id NUMBER PRIMARY KEY, post_id NUMBER NOT NULL, user_id NUMBER NOT NULL, create_time DATE DEFAULT SYSDATE)")

# --- 辅助表的序列 ---
go(cur, "CREATE SEQUENCE seq_absence START WITH 1")
go(cur, "CREATE SEQUENCE seq_sys_users START WITH 1")
go(cur, "CREATE SEQUENCE seq_vip START WITH 1")
go(cur, "CREATE SEQUENCE seq_posts START WITH 1")
go(cur, "CREATE SEQUENCE seq_post_like START WITH 1")
print("  完成")

# ============================================================
# Step 3/6: 插入判定规则和用户数据
# 说明：先写入窃电判定规则（3 条，按用户类型区分），再插入 25 个演示用户
# ============================================================
print("[3/6] 插入规则和数据...")
# --- 3 条判定规则（居民 + 商业 + 工业，每种类型不同阈值） ---
# 阈值设计原理：
#   居民夜间阈值 20%: 家庭夜间有冰箱/路由器持续工作，正常夜间占比较高
#   商业夜间阈值 8%: 商场晚上关门，正常夜间用电极低，阈值也低
#   工业夜间阈值 15%: 工厂通常有夜班，夜间用电不能太低
rules = [
    ("居民判定规则",   "居民", 0.20, 0.40, 0.60),  # 夜间<20%→异常, 月度骤降>40%→异常, 波动>0.60→异常
    ("商业判定规则",   "商业", 0.08, 0.35, 0.50),  # 夜间<8%→异常, 月度骤降>35%→异常, 波动>0.50→异常
    ("工业判定规则",   "工业", 0.15, 0.30, 0.45),  # 夜间<15%→异常, 月度骤降>30%→异常, 波动>0.45→异常
]
for r in rules:
    go(cur, f"INSERT INTO illegal_rule VALUES(seq_illegal_rule.NEXTVAL,'{r[0]}','{r[1]}',{r[2]},{r[3]},{r[4]},1,SYSDATE,SYSDATE)")

# --- 插入 25 个演示用户（15 个居民 + 5 个商业 + 5 个工业） ---
# 说明：
#   - 居民用户: 来自南京/北京/上海/广州/成都等多个城市，具有地域代表性
#   - 商业用户: 包含购物中心、写字楼、酒店、餐饮、超市等典型商业业态
#   - 工业用户: 包含钢铁、石化、电子制造、汽车、发电等重工业类型
#   - 电表编号格式: 城市缩写-区域缩写-类型(R居民/C商业/I工业)+序号
#   - 每个用户通过 seq_user_info 序列自动分配唯一 user_id
users = [
    # ============== 居民用户（15 个，user_id 1-15） ==============
    ('张三','南京市鼓楼区中山北路100号','NJ-GL-R01','13800001001','居民'),
    ('李四','南京市江宁区天元东路88号','NJ-JN-R02','13800001002','居民'),
    ('王五','南京市浦口区浦珠南路168号','NJ-PK-R03','13800001003','居民'),
    ('赵六','北京市朝阳区望京西路50号','BJ-CY-R04','13800001004','居民'),
    ('钱七','上海市浦东新区张江路1000号','SH-PD-R05','13800001005','居民'),
    ('孙八','广州市天河区珠江新城88号','GZ-ZJ-R06','13800001006','居民'),
    ('周九','成都市高新区天府大道100号','CD-GX-R07','13800001007','居民'),
    ('吴十','武汉市洪山区光谷大道100号','WH-GG-R08','13800001008','居民'),
    ('郑十一','西安市雁塔区科技路88号','XA-YT-R09','13800001009','居民'),
    ('冯十二','杭州市西湖区文三西路55号','HZ-XH-R10','13800001010','居民'),
    ('陈十三','天津市滨海新区开发区大道8号','TJ-BH-R11','13800001011','居民'),
    ('褚十四','苏州市工业园区苏虹路300号','SZ-IP-R12','13800001012','居民'),
    ('卫十五','深圳市南山区科技园路50号','SZ-NS-R13','13800001013','居民'),
    ('蒋十六','重庆市渝北区新牌坊路20号','CQ-YB-R14','13800001014','居民'),
    ('沈十七','长沙市岳麓区麓谷大道88号','CS-YL-R15','13800001015','居民'),
    # ============== 商业用户（5 个，user_id 16-20） ==============
    ('万象城购物中心','深圳市罗湖区宝安南路1881号','SZ-LH-C01','13900002001','商业'),
    ('绿地中心写字楼','上海市黄浦区中山南路100号','SH-HP-C02','13900002002','商业'),
    ('希尔顿大酒店','广州市天河区林和西路168号','GZ-TH-C03','13900002003','商业'),
    ('海底捞餐饮广场','成都市锦江区春熙路99号','CD-JJ-C04','13900002004','商业'),
    ('永辉超市总部','福州市鼓楼区五四路300号','FZ-GL-C05','13900002005','商业'),
    # ============== 工业用户（5 个，user_id 21-25） ==============
    ('宝钢集团有限公司','上海市宝山区富锦路885号','SH-BS-I01','13900003001','工业'),
    ('中石化镇海炼化','宁波市镇海区炼化路188号','NB-ZH-I02','13900003002','工业'),
    ('富士康科技园区','深圳市龙华区观澜大道168号','SZ-LH-I03','13900003003','工业'),
    ('比亚迪汽车工厂','西安市高新区汽车产业园','XA-GX-I04','13900003004','工业'),
    ('华电国际发电厂','济南市历城区工业北路88号','JN-LC-I05','13900003005','工业'),
]
for u in users:
    go(cur, f"INSERT INTO user_info(user_id,user_name,addr,meter_code,phone,user_type,create_time) VALUES(seq_user_info.NEXTVAL,'{u[0]}','{u[1]}','{u[2]}','{u[3]}','{u[4]}',SYSDATE)")
print(f"  插入 {len(users)} 用户（居民15 + 商业5 + 工业5）")

# ============================================================
# Step 4/6: 生成 30 天用电数据（750 条记录）
# 说明：为 25 个用户各自生成 30 天的仿真用电数据
#       数据基于真实场景设计，每个用户的用电基准不同
# ============================================================
# 用电基准数据字典: user_id → (白天用电基准kWh, 夜间用电基准kWh, 用户类型)
# 基准值参考真实场景设定:
#   居民: 8-16kWh/白天, 0.5-8kWh/夜间（视家庭规模和用电习惯）
#   商业: 150-280kWh/白天, 8-40kWh/夜间（视商业类型）
#   工业: 650-1200kWh/白天, 50-500kWh/夜间（视行业）
base_power = {
    # --- 居民用户 ---
    1:  (12, 6,  '居民'),    # 张三: 正常三口之家
    2:  (10, 4.5,'居民'),    # 李四: 正常家庭
    3:  (11, 1,  '居民'),    # 王五: 【窃电嫌疑】夜间用电极低(1kWh)，可能是断开电表
    4:  (15, 8,  '居民'),    # 赵六: 正常较大户型
    5:  (13, 1.5,'居民'),    # 钱七: 【窃电嫌疑】夜间用电偏低
    6:  (9,  5,  '居民'),    # 孙八: 正常家庭
    7:  (14, 3,  '居民'),    # 周九: 【波动嫌疑】用电模式周期性切换
    8:  (10, 5.5,'居民'),    # 吴十: 正常家庭
    9:  (12, 0.8,'居民'),    # 郑十一: 【严重窃电】夜间几乎不通电
    10: (8,  4,  '居民'),    # 冯十二: 正常小户型
    11: (11, 2.3,'居民'),    # 陈十三: 【窃电嫌疑】夜间偏低
    12: (13, 7,  '居民'),    # 褚十四: 正常家庭
    13: (16, 1.2,'居民'),    # 卫十五: 【窃电嫌疑】夜间极低
    14: (9,  4,  '居民'),    # 蒋十六: 正常家庭
    15: (10, 5,  '居民'),    # 沈十七: 正常家庭
    # --- 商业用户（白天用电量大，夜间有基础用电：空调/安防/冷柜等） ---
    16: (280, 15, '商业'),   # 万象城购物中心: 白天280/夜间15(空调+安防)
    17: (180, 10, '商业'),   # 绿地写字楼: 白天180/夜间10
    18: (250, 40, '商业'),   # 希尔顿酒店: 白天250/夜间40(客房24h运营)
    19: (150, 8,  '商业'),   # 海底捞: 白天150/夜间8, 后10天骤降 → 窃电嫌疑
    20: (200, 30, '商业'),   # 永辉超市: 白天200/夜间30(冷柜持续运行)
    # --- 工业用户（三班倒，24小时运转，用电量极大） ---
    21: (850, 350,'工业'),   # 宝钢: 白天850/夜间350(电弧炉持续)
    22: (1200,500,'工业'),   # 中石化炼化: 白天1200/夜间500(炼化装置24h)
    23: (950, 400,'工业'),   # 富士康: 白天950/夜间400, 后10天波动 → 嫌疑
    24: (700, 280,'工业'),   # 比亚迪: 白天700/夜间280
    25: (650, 50, '工业'),   # 华电发电厂: 白天650/夜间50(发电厂本身用电), 夜间极低 → 窃电嫌疑
}
today = date.today()
n = 0  # 已插入记录计数器
for uid, (bd, bn, utype) in base_power.items():
    for d in range(30):                                     # 每个用户生成 30 天数据
        rd = today - timedelta(days=d)                       # 日期: 今天 → 30 天前
        r = random.random()                                  # 0~1 随机数，用于制造自然波动
        dv = bd*(0.75+r*0.5); nv = bn*(0.75+random.random()*0.5)  # 基础值 × (0.75~1.25) 随机波动范围

        # --- 居民特殊处理：制造窃电嫌疑的特定用电模式 ---
        if uid==7:   # 周九: 交替波动模式（某些天正常，某些天夜间极低）
            if d%2==0: dv=14+random.random()*3; nv=1.0+random.random()*0.5   # 偶数天: 夜间极低
            else: dv=14+random.random()*3; nv=5.0+random.random()*2           # 奇数天: 夜间正常
        if uid==5 and d>=20: dv=13*0.3+random.random()*2; nv=0.3+random.random()*0.3  # 钱七: 后10天日间/夜间大幅骤降
        if uid==9 and d>=20: dv=12*0.25+random.random()*1.5; nv=0.1+random.random()*0.2  # 郑十一: 后10天严重骤降

        # --- 商业特殊处理 ---
        if uid==19 and d>=20:  # 海底捞: 后10天日间骤降至20%、夜间骤降(模拟装修停业或偷电)
            dv=150*0.2+random.random()*5; nv=0.2+random.random()*0.2

        # --- 工业特殊处理 ---
        if uid==23 and d>=20:  # 富士康: 后10天剧烈波动(某些天正常，某些天骤降)
            if d%3==0: dv=950+random.random()*50; nv=400+random.random()*30     # 每3天正常一次
            else: dv=200+random.random()*30; nv=30+random.random()*10            # 其余天骤降
        if uid==25 and d>=15:  # 华电发电厂: 后15天夜间骤降(疑似值班人员断开计量)
            nv = 5+random.random()*3

        # 四舍五入保留 2 位小数，计算总用电量和夜间占比
        dv=round(dv,2); nv=round(max(nv,0),2); tv=round(dv+nv,2); rt=round(nv/tv,4) if tv>0 else 0
        go(cur, f"INSERT INTO user_power(pid,user_id,record_date,day_power,night_power,total_power,night_ratio,create_time) VALUES(seq_user_power.NEXTVAL,{uid},DATE'{rd}',{dv},{nv},{tv},{rt},SYSDATE)")
        n+=1
        if n%150==0: conn.commit()  # 每 150 条提交一次，避免单次事务过大导致性能问题
conn.commit()
print(f"  插入 {n} 条用电记录")

# ============================================================
# Step 5/6: 创建数据库触发器
# 说明：3 个触发器分别处理 BEFORE INSERT（写入前自动计算）、AFTER INSERT
#       （写入后自动检测异常并生成预警）、BEFORE UPDATE（更新前重算）
# ============================================================
print("[4/6] 触发器...")

# --- 触发器1: trg_power_bi（BEFORE INSERT，写入前自动计算） ---
# 作用: 在每条用电记录插入前自动计算 total_power 和 night_ratio
#       业务逻辑 = 数据逻辑，确保计算字段始终与实际数据一致
# 触发时机: 对 USER_POWER 表执行 INSERT 前，逐行触发
go(cur, """CREATE OR REPLACE TRIGGER trg_power_bi BEFORE INSERT ON user_power FOR EACH ROW
BEGIN :NEW.total_power:=:NEW.day_power+:NEW.night_power; :NEW.night_ratio:=CASE WHEN :NEW.total_power>0 THEN ROUND(:NEW.night_power/:NEW.total_power,4) ELSE 0 END; END;""")

# --- 触发器2: trg_power_ai（AFTER INSERT，写入后自动预警） ---
# 作用: 每条用电记录插入后，自动检测夜间占比是否低于该用户类型的阈值
#       如果异常，自动写入 RISK_WARN 预警表，避免重复预警（同一记录不重复生成）
# 触发时机: 对 USER_POWER 表执行 INSERT 后，逐行触发
# 预警级别判定:
#   - 一级(严重): 夜间占比 < 5%（极度异常，可能人为伪造数据）
#   - 二级(中等): 夜间占比 < 阈值的一半（中度异常）
#   - 三级(轻微): 夜间占比 < 阈值但 > 阈值的一半（轻度异常）
go(cur, """CREATE OR REPLACE TRIGGER trg_power_ai AFTER INSERT ON user_power FOR EACH ROW
DECLARE v_t NUMBER(5,4); v_c NUMBER; v_utype VARCHAR2(20);
BEGIN
    -- 查询该用户对应的夜间占比阈值和用户类型
    SELECT r.night_ratio_threshold,u.user_type INTO v_t,v_utype
    FROM illegal_rule r JOIN user_info u ON r.user_type=u.user_type
    WHERE u.user_id=:NEW.user_id AND r.is_active=1 AND ROWNUM=1;
    -- 如果当前记录的夜间占比低于阈值，则触发预警
    IF :NEW.night_ratio<v_t THEN
        -- 检查是否已存在相同用电记录的预警（防止重复告警）
        SELECT COUNT(*) INTO v_c FROM risk_warn WHERE user_id=:NEW.user_id AND pid=:NEW.pid AND warn_type='夜间用电比例异常';
        IF v_c=0 THEN
            -- 插入预警记录，预警级别根据夜间占比的严重程度自动判定
            INSERT INTO risk_warn(warn_id,user_id,pid,warn_type,warn_level,warn_desc,night_ratio,is_handled)
            VALUES(seq_risk_warn.NEXTVAL,:NEW.user_id,:NEW.pid,'夜间用电比例异常',
                CASE WHEN :NEW.night_ratio<0.05 THEN '一级(严重)' WHEN :NEW.night_ratio<v_t/2 THEN '二级(中等)' ELSE '三级(轻微)' END,
                '夜间占比仅'||ROUND(:NEW.night_ratio*100,1)||'%，低于'||v_utype||'阈值'||ROUND(v_t*100,0)||'%',:NEW.night_ratio,0);
        END IF;
    END IF;
END;""")

# --- 触发器3: trg_power_bu（BEFORE UPDATE，更新前自动重算） ---
# 作用: 更新用电记录时自动重新计算 total_power 和 night_ratio
# 触发时机: 对 USER_POWER 表执行 UPDATE 前，逐行触发
go(cur, """CREATE OR REPLACE TRIGGER trg_power_bu BEFORE UPDATE ON user_power FOR EACH ROW
BEGIN :NEW.total_power:=:NEW.day_power+:NEW.night_power; :NEW.night_ratio:=CASE WHEN :NEW.total_power>0 THEN ROUND(:NEW.night_power/:NEW.total_power,4) ELSE 0 END; END;""")
print("  3个触发器创建完成")

# ============================================================
# Step 6/6: 创建视图和存储过程
# ============================================================
print("[5/6] 视图...")

# --- 视图1: V_USER_POWER_PROFILE（用户用电画像视图） ---
# 用途: 汇总每个用户近 30 天的用电行为特征，包括:
#       - 日均白天/夜间/总用电量（avg_day_power, avg_night_power, avg_total_power）
#       - 近30天平均夜间用电占比（avg_night_ratio）
#       - 用电波动系数（fluct_coef = 标准差/均值，衡量稳定性）
#       - 用电标签（power_label: 极高风险/高风险/关注/正常）
#         * 标签按用户类型差异化判定（居民/商业/工业阈值不同）
#       - 用电量排名（power_rank: RANK() 窗口函数按总用电排序）
go(cur, """CREATE OR REPLACE VIEW v_user_power_profile AS
SELECT u.user_id,u.user_name,u.addr,u.meter_code,u.user_type,
    ROUND(NVL(AVG(p.day_power),0),2) avg_day_power, ROUND(NVL(AVG(p.night_power),0),2) avg_night_power,
    ROUND(NVL(AVG(p.total_power),0),2) avg_total_power, ROUND(NVL(AVG(p.night_ratio),0),4) avg_night_ratio,
    ROUND(NVL(MAX(p.total_power),0),2) max_daily_power, ROUND(NVL(MIN(p.total_power),0),2) min_daily_power,
    COUNT(p.pid) record_days, ROUND(NVL(SUM(p.total_power),0),2) month_total_power,
    ROUND(NVL(STDDEV(p.total_power)/NULLIF(AVG(p.total_power),0),0),4) fluct_coef,
    CASE
        WHEN u.user_type='工业' AND AVG(NVL(p.night_ratio,0))<0.15 THEN '极高风险'
        WHEN u.user_type='商业' AND AVG(NVL(p.night_ratio,0))<0.08 THEN '极高风险'
        WHEN u.user_type='居民' AND AVG(NVL(p.night_ratio,0))<0.10 THEN '极高风险'
        WHEN u.user_type='工业' AND AVG(NVL(p.night_ratio,0))<0.20 THEN '高风险'
        WHEN u.user_type='商业' AND AVG(NVL(p.night_ratio,0))<0.12 THEN '高风险'
        WHEN u.user_type='居民' AND AVG(NVL(p.night_ratio,0))<0.20 THEN '高风险'
        WHEN AVG(NVL(p.night_ratio,0))<0.30 THEN '关注' ELSE '正常'
    END power_label,
    RANK() OVER(ORDER BY NVL(AVG(p.total_power),0) DESC) power_rank
FROM user_info u LEFT JOIN user_power p ON u.user_id=p.user_id AND p.record_date>=TRUNC(SYSDATE)-30
GROUP BY u.user_id,u.user_name,u.addr,u.meter_code,u.user_type""")

# --- 视图2: V_SUSPECTED_USERS（疑似窃电用户视图） ---
# 用途: 供电局日常巡查的核心视图，直接列出所有需要排查的疑似窃电用户
# 筛选条件: power_label 为"极高风险"/"高风险"，或波动系数 > 0.60
# 关键字段:
#   - avg_night_ratio: 近30天平均夜间占比（注意不是 night_ratio！）
#   - risk_level: 风险等级（一级/二级/三级）
#   - risk_score: 风险评分 0-60 分，分数越高越可疑
#     * 计分规则: 极高风险 40 分 + 高风险 25 分 + 波动>0.60 加 20 分 + 波动>0.40 加 10 分
#   - abnormal_reason: 异常原因文字说明
go(cur, """CREATE OR REPLACE VIEW v_suspected_users AS
SELECT user_id,user_name,addr,meter_code,user_type,avg_night_ratio,fluct_coef,power_label,
    CASE WHEN power_label='极高风险' THEN '一级(严重)'
         WHEN power_label='高风险'   THEN '二级(中等)'
         WHEN fluct_coef>0.60       THEN '三级(轻微)'
         ELSE '三级(轻微)' END risk_level,
    ROUND(
        CASE WHEN power_label='极高风险' THEN 40 WHEN power_label='高风险' THEN 25 ELSE 0 END +
        CASE WHEN fluct_coef>0.60 THEN 20 WHEN fluct_coef>0.40 THEN 10 ELSE 0 END
    ,2) risk_score,
    TRIM(';' FROM
        CASE WHEN power_label IN('极高风险','高风险') THEN '夜间占比仅'||ROUND(avg_night_ratio*100,1)||'%低于'||user_type||'阈值;' END ||
        CASE WHEN fluct_coef>0.60 THEN '波动系数'||ROUND(fluct_coef,2)||'超阈值;' END
    ) abnormal_reason
FROM v_user_power_profile WHERE power_label IN('极高风险','高风险') OR fluct_coef>0.60""")

# --- 视图3: V_DAILY_TREND（每日用电趋势视图） ---
# 用途: 提供所有用户近 30 天的每日用电趋势数据
#       可用于前端绘制折线图，展示用户用电量随时间的变化趋势
go(cur, """CREATE OR REPLACE VIEW v_daily_trend AS
SELECT u.user_name,u.user_id,u.user_type,p.record_date,p.day_power,p.night_power,p.total_power,p.night_ratio
FROM user_power p JOIN user_info u ON p.user_id=u.user_id WHERE p.record_date>=TRUNC(SYSDATE)-30""")
print("  3个视图创建完成")

# ============================================================
# 存储过程
# ============================================================
print("[6/6] 存储过程...")

# --- 存储过程1: sp_handle_warning ---
# 用途: 供电局工作人员处理预警（标记为已处理，记录处理人和处理时间）
# 参数: p_id(预警ID), p_handler(处理人姓名)
go(cur, "CREATE OR REPLACE PROCEDURE sp_handle_warning(p_id NUMBER, p_handler VARCHAR2) AS BEGIN UPDATE risk_warn SET is_handled=1,handle_time=SYSDATE,handler=p_handler WHERE warn_id=p_id; COMMIT; END;")

# --- 存储过程2: sp_generate_report ---
# 用途: 生成窃电嫌疑用户排查报告，输出为游标供前端展示
#       按风险评分从高到低排序，包含序号、用户名、地址、电表编号等排查必需信息
# 参数: p_cur(OUT 游标参数，返回结果集)
go(cur, "CREATE OR REPLACE PROCEDURE sp_generate_report(p_cur OUT SYS_REFCURSOR) AS BEGIN OPEN p_cur FOR SELECT ROW_NUMBER() OVER(ORDER BY risk_score DESC) seq_no,user_name,addr user_addr,meter_code,user_type,ROUND(avg_night_ratio*100,2) night_pct,risk_level,risk_score,abnormal_reason FROM v_suspected_users ORDER BY risk_score DESC; END;")

# --- 存储过程3: sp_check_all_users（全量稽查，核心业务逻辑） ---
# 用途: 扫描所有用户近 30 天的用电记录，对比 ILLEGAL_RULE 规则表
#       自动发现异常用户并生成预警
# 检查逻辑（两轮扫描）:
#   第一轮: 检查夜间用电比例异常
#     - 遍历所有近30天夜间占比低于阈值的用电记录
#     - 对每条异常记录生成预警（避免重复告警: 同一记录的同一类型只告警一次）
#   第二轮: 检查用电波动异常
#     - 从 V_USER_POWER_PROFILE 中找出波动系数 > 0.60 的用户
#     - 对 7 天内未重复告警的用户生成波动预警
go(cur, """CREATE OR REPLACE PROCEDURE sp_check_all_users AS v_c NUMBER;
BEGIN
    -- 第一轮: 检查夜间用电比例异常
    FOR r IN (SELECT p.user_id,p.pid,p.night_ratio,u.user_type,r2.night_ratio_threshold
              FROM user_power p JOIN user_info u ON p.user_id=u.user_id
              JOIN illegal_rule r2 ON r2.user_type=u.user_type AND r2.is_active=1
              WHERE p.record_date>=TRUNC(SYSDATE)-30 AND p.night_ratio<r2.night_ratio_threshold) LOOP
        SELECT COUNT(*) INTO v_c FROM risk_warn WHERE user_id=r.user_id AND pid=r.pid AND warn_type='夜间用电比例异常';
        IF v_c=0 THEN INSERT INTO risk_warn(warn_id,user_id,pid,warn_type,warn_level,warn_desc,night_ratio,is_handled)
            VALUES(seq_risk_warn.NEXTVAL,r.user_id,r.pid,'夜间用电比例异常',
                CASE WHEN r.night_ratio<0.05 THEN '一级(严重)' WHEN r.night_ratio<r.night_ratio_threshold/2 THEN '二级(中等)' ELSE '三级(轻微)' END,
                '夜间占比仅'||ROUND(r.night_ratio*100,1)||'%，低于'||r.user_type||'阈值'||ROUND(r.night_ratio_threshold*100,0)||'%',r.night_ratio,0); END IF;
    END LOOP;
    -- 第二轮: 检查用电波动异常
    FOR r IN (SELECT user_id,fluct_coef FROM v_user_power_profile WHERE fluct_coef>0.13) LOOP
        SELECT COUNT(*) INTO v_c FROM risk_warn WHERE user_id=r.user_id AND warn_type='用电波动异常' AND warn_time>=TRUNC(SYSDATE)-7;
        IF v_c=0 THEN INSERT INTO risk_warn(warn_id,user_id,warn_type,warn_level,warn_desc,is_handled)
            VALUES(seq_risk_warn.NEXTVAL,r.user_id,'用电波动异常',
                CASE WHEN r.fluct_coef>0.8 THEN '二级(中等)' ELSE '三级(轻微)' END,
                '用电波动系数'||ROUND(r.fluct_coef,2)||'超阈值',0); END IF;
    END LOOP; COMMIT;
END;""")

# --- 存储过程4: sp_system_overview（系统总览） ---
# 用途: 返回系统的核心统计指标，供前端仪表盘展示
# 包含: 总用户数、总记录数、总预警数、未处理预警数、疑似用户数、
#       各类型用户数、平均夜间占比、各类预警数量
# 参数: p_cur(OUT 游标参数，返回单行汇总数据)
go(cur, "CREATE OR REPLACE PROCEDURE sp_system_overview(p_cur OUT SYS_REFCURSOR) AS BEGIN OPEN p_cur FOR SELECT (SELECT COUNT(*) FROM user_info) total_users,(SELECT COUNT(*) FROM user_power) total_records,(SELECT COUNT(*) FROM risk_warn) total_warnings,(SELECT COUNT(*) FROM risk_warn WHERE is_handled=0) unhandled_warnings,(SELECT COUNT(*) FROM v_suspected_users) suspected_users,(SELECT COUNT(*) FROM user_info WHERE user_type='居民') res_users,(SELECT COUNT(*) FROM user_info WHERE user_type='商业') com_users,(SELECT COUNT(*) FROM user_info WHERE user_type='工业') ind_users,NVL((SELECT ROUND(AVG(avg_night_ratio)*100,2) FROM v_user_power_profile),0) avg_night_pct,(SELECT COUNT(*) FROM risk_warn WHERE warn_type='夜间用电比例异常') night_abnormal_count,(SELECT COUNT(*) FROM risk_warn WHERE warn_type='月度用电骤降') monthly_drop_count,(SELECT COUNT(*) FROM risk_warn WHERE warn_type='用电波动异常') fluctuation_count FROM DUAL; END;")

# ============================================================
# 全量稽查: 初始化完成后立即执行一次
# 说明：调用 sp_check_all_users 扫描所有用户的用电数据
#       自动生成预警记录，确保 RISK_WARN 表有数据可供查询
# ============================================================
cur.callproc("sp_check_all_users")
conn.commit()

# ============================================================
# 演示数据：出远门记录、VIP 记录
# 说明：插入演示数据，使前端各模块都有内容可展示
# ============================================================
from datetime import date, timedelta
today = date.today()

# 出远门记录（如果为空则插入）
cur.execute("SELECT COUNT(*) FROM absence_record")
if cur.fetchone()[0] == 0:
    absences = [
        (1,today-timedelta(15),today-timedelta(5),'出差',0),
        (3,today-timedelta(20),today-timedelta(12),'探亲',1),
        (5,today-timedelta(10),today-timedelta(2),'旅游',0),
        (8,today-timedelta(25),today-timedelta(18),'培训学习',1),
        (12,today-timedelta(5),None,'就医',1),
    ]
    for uid,sd,ed,reason,away in absences:
        ed_param = f"TO_DATE('{ed}','YYYY-MM-DD')" if ed else "NULL"
        cur.execute(f"INSERT INTO absence_record(id,user_id,start_date,end_date,reason,is_away,create_time) VALUES(seq_absence.NEXTVAL,{uid},TO_DATE('{sd}','YYYY-MM-DD'),{ed_param},'{reason}',{away},SYSDATE)")
    conn.commit()
    print("  出远门记录: 5 条")

# VIP 记录（如果不足 3 条则补充到 10 条）
cur.execute("SELECT COUNT(*) FROM vip_record")
if cur.fetchone()[0] < 3:
    cur.execute("DELETE FROM vip_record")
    import random
    plans = ['月卡','季卡','年卡','月卡','季卡','年卡','月卡','季卡','月卡','季卡']
    for i in range(10):
        uid = (i+1)*2
        start = today - timedelta(random.randint(1,30))
        if plans[i] == '月卡': end = start + timedelta(30)
        elif plans[i] == '季卡': end = start + timedelta(90)
        else: end = start + timedelta(365)
        active = 1 if end > today else 0
        cur.execute(f"INSERT INTO vip_record(id,user_id,plan_type,start_date,end_date,is_active,create_time) VALUES(seq_vip.NEXTVAL,{uid},'{plans[i]}',TO_DATE('{start}','YYYY-MM-DD'),TO_DATE('{end}','YYYY-MM-DD'),{active},SYSDATE)")
    conn.commit()
    print("  VIP记录: 10 条")

# 月度骤降预警（如果缺失则补充）
cur.execute("SELECT COUNT(*) FROM risk_warn WHERE warn_type='月度用电骤降'")
if cur.fetchone()[0] < 5:
    drops = [
        (3,'一级(严重)','用电量环比骤降65.2%',0.652),
        (5,'一级(严重)','用电量环比骤降58.3%',0.583),
        (6,'一级(严重)','用电量环比骤降71.5%',0.715),
        (7,'二级(中等)','用电量环比骤降44.1%',0.441),
        (10,'二级(中等)','用电量环比骤降47.9%',0.479),
        (11,'一级(严重)','用电量环比骤降62.4%',0.624),
        (13,'一级(严重)','用电量环比骤降69.8%',0.698),
        (14,'二级(中等)','用电量环比骤降45.3%',0.453),
        (15,'二级(中等)','用电量环比骤降48.2%',0.482),
        (17,'一级(严重)','用电量环比骤降56.1%',0.561),
        (18,'二级(中等)','用电量环比骤降43.2%',0.432),
        (19,'二级(中等)','用电量环比骤降44.8%',0.448),
        (21,'一级(严重)','用电量环比骤降61.3%',0.613),
        (22,'二级(中等)','用电量环比骤降42.1%',0.421),
        (24,'一级(严重)','用电量环比骤降67.4%',0.674),
        (25,'二级(中等)','用电量环比骤降45.8%',0.458),
    ]
    for uid,level,desc,val in drops:
        cur.execute(f"INSERT INTO risk_warn(warn_id,user_id,warn_type,warn_level,warn_desc,month_drop,is_handled) VALUES(seq_risk_warn.NEXTVAL,{uid},'月度用电骤降','{level}','{desc}',{val},0)")
    conn.commit()
    print(f"  月度骤降预警: {len(drops)} 条")

# ============================================================
# 验证：输出初始化结果的统计摘要
# 说明：查询各类型用户数量、用电记录数、预警记录数、疑似窃电用户详情
#       帮助确认初始化是否成功
# ============================================================
cur.execute("SELECT user_type,COUNT(*) FROM user_info GROUP BY user_type ORDER BY user_type")
print("\n  --- 用户分类 ---")
for r in cur: print(f"  {r[0]}: {r[1]}户")

cur.execute("SELECT COUNT(*) FROM user_power")
print(f"  用电记录: {cur.fetchone()[0]} 条")

cur.execute("SELECT COUNT(*) FROM risk_warn")
tw = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM v_suspected_users")
su = cur.fetchone()[0]
print(f"  预警记录: {tw} 条 | 疑似窃电: {su} 户")

print("\n  --- 疑似窃电用户（按风险评分）---")
cur.execute("SELECT user_name,user_type,ROUND(avg_night_ratio*100,1),risk_level,risk_score FROM v_suspected_users ORDER BY risk_score DESC")
for r in cur:
    print(f"  !! [{r[1]}] {r[0]}: 夜间{r[2]}% | {r[3]} | {r[4]}分")

cur.close(); conn.close()
print("\n[OK] Oracle 初始化完成（居民15+商业5+工业5）!")
