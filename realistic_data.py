"""
为演示账号注入真实可信的用电数据
==========================================================================
【用途】
运行方式: python realistic_data.py
生成 25 个用户 x 90 天 = 2250 条仿真的高真实度每日用电记录，
每条记录包含白天用电量、夜间用电量和触发器的自动计算结果。

【真实度设计思路】
真实用电数据不是简单随机数，而是遵循以下规律：
  1. 工作日 vs 周末差异：居民周末在家用电多，商业周末客流多用电大
  2. 季节性差异：夏冬两季空调用电增加（季节系数 1.25~1.35）
  3. 高斯噪声：用正态分布随机扰动模拟真实世界的随机波动
  4. 用户画像差异：
     - 三口之家：白天10kWh + 夜间4.5kWh（冰箱路由器持续运行）
     - 独居青年：白天6kWh + 夜间7.5kWh（夜猫子，夜间主要用电）
     - 购物中心：白天280kWh + 夜间15kWh（空调安防）
     - 钢铁厂：白天850kWh + 夜间350kWh（三班倒，夜间仍高）

【窃电嫌疑设计（核心）】
为了让演示系统有真实的窃电案例可查，以下用户被注入了窃电特征：
  1. 钱七(uid=5, 居民): 后15天夜间骤降至正常的18%（模拟断开电表）
  2. 陈十三(uid=6, 居民): 后30天夜间骤降至15%（长期偷电嫌疑）
  3. 杨二十(uid=12, 居民): 后20天夜间骤降至20%
  4. 海底捞(uid=19, 商业): 后15天日间骤降至25%、夜间降至20%（装修/偷电）
  5. 发电厂(uid=25, 工业): 后30天夜间骤降至12%（夜间值班断开电表）

【依赖】
- oracledb: Oracle 数据库 Python 驱动
- Oracle 数据库需已由 setup_oracle.py 初始化（包含表和序列）
==========================================================================
"""
import oracledb, random
from datetime import date, timedelta, datetime

# 连接 Oracle 数据库（使用 system 用户，密码默认为 orcl）
conn = oracledb.connect(user='system', password='orcl', dsn='localhost:1522/XEPDB1')
cur = conn.cursor()
today = date.today()


def clear_user_power(uid):
    """
    清空指定用户的所有用电记录。
    
    在注入新数据前先清除旧数据，确保数据干净无冲突。
    使用 WHERE user_id= 精确删除，不影响其他用户。
    
    参数:
        uid: 用户编号 (1~25)
    """
    cur.execute(f"DELETE FROM user_power WHERE user_id={uid}")


def insert_power(uid, rec_date, day_kwh, night_kwh):
    """
    插入一条用电记录。
    
    自动计算 total_power 和 night_ratio（与触发器 trg_power_bi 逻辑一致）。
    每条记录使用序列 seq_user_power 生成唯一主键。
    
    参数:
        uid: 用户编号
        rec_date: 记录日期（date 类型）
        day_kwh: 白天用电量（kWh）
        night_kwh: 夜间用电量（kWh）
    """
    # 总用电量 = 白天 + 夜间
    tv = round(day_kwh + night_kwh, 2)
    # 夜间占比 = 夜间 / 总用电量，总用电为 0 时占比设为 0（防止除零错误）
    rt = round(night_kwh / tv, 4) if tv > 0 else 0
    # 插入记录，pid 由序列自动生成
    cur.execute(f"INSERT INTO user_power(pid,user_id,record_date,day_power,night_power,total_power,night_ratio,create_time) VALUES(seq_user_power.NEXTVAL,{uid},DATE'{rec_date}',{day_kwh},{night_kwh},{tv},{rt},SYSDATE)")


def realistic_daily(uid, days=90):
    """
    为指定用户生成 90 天的高真实度用电数据。
    
    生成策略（模拟真实世界的用电规律）：
    1. 基础用电量（base_day / base_night）：反映用户类型的典型用电水平
    2. 周末系数（wf: weekend factor）：居民和商业周末用电模式不同
       - 居民：周末在家用电多（wf=1.25~1.5）
       - 商业（商场/餐饮）：周末客流大，用电更猛（wf=1.3~1.5）
    3. 季节系数（sf: seasonal factor）：夏冬季节空调使用增加
       - 1/2月（冬季）、7/8月（夏季）：系数 1.25~1.4（空调高峰期）
       - 3/6/9/12月（过渡季）：系数 1.15~1.2（空调偶尔使用）
       - 其他月份：系数 1.0（舒适天气，无需空调）
    4. 高斯噪声：用 random.gauss(mean, sigma) 添加正态分布随机扰动
       模拟真实生活中的日间波动（如某天多开了洗衣机、某天外出）
    5. 偶尔外出：5% 概率当天用电降至 30%（模拟短期离家）
    
    【窃电嫌疑用户特殊处理】
    以下用户在数据末尾被注入了窃电特征——夜间骤降模式：
    
    钱七(uid=5): 居民独居青年，正常夜间用电约 7.5kWh（夜猫子）。
        后 15 天夜间骤降至 18%，模拟断开电表偷电。
        数据表现: 前 75 天夜间 7-8kWh，后 15 天骤降至约 1.4kWh。
        触发规则: 居民夜间阈值 20%，骤降后约 18% 低于阈值 → 触发预警。
    
    陈十三(uid=6): 居民窃电嫌疑户，正常夜间约 4.5kWh。
        后 30 天夜间骤降至 15%，模拟长期偷电。
        数据表现: 前 60 天夜间 4-5kWh，后 30 天骤降至约 0.7kWh。
        触发规则: 夜间占比极低，可能触发一级严重预警。
    
    杨二十(uid=12): 居民窃电嫌疑，正常夜间约 7kWh。
        后 20 天夜间骤降至 20%，模拟近期开始偷电。
        数据表现: 前 70 天正常，后 20 天夜间骤降至约 1.4kWh。
    
    海底捞(uid=19): 商业餐饮用户，正常白天 150kWh / 夜间 8kWh。
        后 15 天日间骤降至 25%、夜间降至 20%，模拟装修停业或偷电。
        数据表现: 前 75 天日间 150kWh+，后 15 天骤降至 37kWh 左右。
        触发规则: 日间骤降 + 夜间占比异常，双重可疑。
    
    发电厂(uid=25): 工业用户，正常白天 650kWh / 夜间 50kWh。
        但作为发电厂，本身也消费电（办公、照明）。后 30 天夜间骤降至 12%。
        数据表现: 本来夜间用电就不多（50kWh），再降至 12% 约 6kWh。
        触发规则: 工业夜间阈值 15%，骤降至 12% 低于阈值 → 触发预警。
    
    参数:
        uid: 用户编号 (1~25)
        days: 生成天数，默认 90 天
    """
    # 先清空该用户的旧数据
    clear_user_power(uid)
    records = []

    # 逐天生成用电数据（从今天往回推 90 天）
    for d in range(days):
        rd = today - timedelta(days=d)   # 记录日期（今天 → 90 天前）
        weekday = rd.weekday()            # 0=周一, 6=周日，用于工作日/周末判断
        month = rd.month                  # 月份，用于季节性判断

        # ============================================
        # 以下按用户分别设定基础用电模式
        # 每个 if 分支对应一个用户的个性化用电画像
        # ============================================

        if uid == 1:   # 张三 - 南京鼓楼区居民，典型三口之家
            # 基础用电: 白天 10.5kWh（冰箱、电视、洗衣机），夜间 4.5kWh（冰箱、路由器、夜灯）
            base_day = 10.5; base_night = 4.5
            # 周末全家在家，用电增加 25%
            wf = 1.25 if weekday >= 5 else 1.0
            # 冬(1,2)夏(7,8)开空调，用电增加 35%；过渡季(3,6,9,12)偶尔开空调用电增加 15%
            sf = 1.35 if month in (1,2,7,8) else 1.15 if month in (3,6,9,12) else 1.0
            # 高斯噪声: 白天波动约 ±1kWh，夜间波动约 ±0.3kWh
            day = base_day * wf * sf + random.gauss(1, 0.5)
            night = base_night * sf + random.gauss(0, 0.3)
            # 5% 概率模拟全家外出（如旅游），用电大幅下降
            if random.random() < 0.05: day *= 0.3; night *= 0.3

        elif uid == 5:  # 钱七 - 上海浦东居民，独居青年，夜猫子 → 窃电嫌疑
            # 独居青年: 白天上班不在家(6kWh)，晚上回家大量用电(7.5kWh)
            base_day = 6.0; base_night = 7.5
            # 周末白天也在家，用电增加 30%
            wf = 1.3 if weekday >= 5 else 1.0
            # 季节性空调调整
            sf = 1.25 if month in (1,2,7,8) else 1.0
            day = base_day * wf * sf + random.gauss(0, 0.4)
            night = base_night * sf + random.gauss(0, 0.5)
            # 【窃电嫌疑核心逻辑】后15天（d<15 即最近15天）夜间骤降至 18%
            # 模拟断开电表偷电：白天照常用，但夜里把电表拨掉
            if d < 15: night *= 0.18

        elif uid == 16: # 万象城 - 深圳购物中心，大型商业综合体
            # 购物中心用电: 空调、照明、电梯是大头
            base_day = 280; base_night = 15  # 夜间 15kWh 是空调安防监控
            # 周末客流大增，用电增加 40%
            wf = 1.4 if weekday >= 5 else 1.0
            day = base_day * wf + random.gauss(0, 8)
            night = base_night + random.gauss(0, 1.5)
            # 7-8 月酷暑，空调全开，白天用电增加 35%
            if month in (7,8): day *= 1.35; night *= 1.3
            # 1-2 月冬季供暖（深圳虽不冷但暖通系统仍工作），用电增加 20%
            if month in (1,2): day *= 1.2

        elif uid == 19: # 海底捞 - 成都餐饮，后半月骤降 → 窃电嫌疑
            base_day = 150; base_night = 8
            # 周末餐饮高峰，用电增加 30%
            wf = 1.3 if weekday >= 5 else 1.0
            day = base_day * wf + random.gauss(0, 5)
            night = base_night + random.gauss(0, 0.5)
            # 【窃电嫌疑核心逻辑】后15天日间骤降至 25%、夜间降至 20%
            # 模拟装修停业或绕过电表（大量设备仍在使用但电表不走字）
            if d < 15: day *= 0.25; night *= 0.2

        elif uid == 21: # 宝钢 - 上海钢铁厂，三班倒 24h 运转
            # 钢铁厂: 巨大的电弧炉需持续供电，夜间和白天差别不大
            base_day = 850; base_night = 350
            # 工业用电稳定，基本无周末/季节概念，只有轻微高斯噪声
            day = base_day + random.gauss(0, 20)
            night = base_night + random.gauss(0, 15)

        elif uid == 25: # 华电国际 - 济南发电厂，本身用电不多但夜间异常低 → 窃电嫌疑
            # 发电厂: 虽然是发电单位，但办公楼、照明、生产辅助设备仍消费电
            base_day = 650; base_night = 50
            day = base_day + random.gauss(0, 15)
            night = base_night + random.gauss(0, 3)
            # 【窃电嫌疑核心逻辑】后30天夜间骤降至 12%
            # 发电厂夜间值班人员可能断开计量设备
            if d < 30: night *= 0.12

        elif uid == 2:  # 李四 - 南京江宁居民，4口之家
            base_day = 12; base_night = 5   # 人多用电多
            wf = 1.3 if weekday >= 5 else 1.0
            sf = 1.3 if month in (1,2,7,8) else 1.0
            day = base_day * wf * sf + random.gauss(0, 0.6)
            night = base_night * sf + random.gauss(0, 0.3)
            if random.random() < 0.05: day *= 0.3; night *= 0.3  # 偶尔外出

        elif uid == 4:  # 赵六 - 北京朝阳居民，退休老人
            # 退休老人: 整天在家但用电节俭，白天 8kWh，夜间 3kWh
            base_day = 8; base_night = 3
            # 没有周末概念，只有季节性变化
            sf = 1.25 if month in (1,2,7,8) else 1.0
            day = base_day * sf + random.gauss(0, 0.4)
            night = base_night * sf + random.gauss(0, 0.2)

        elif uid == 6:  # 陈十三 - 天津滨海居民，窃电嫌疑户
            base_day = 9; base_night = 4.5
            wf = 1.2 if weekday >= 5 else 1.0
            day = base_day * wf + random.gauss(0, 0.5)
            night = base_night + random.gauss(0, 0.3)
            # 【窃电嫌疑】后30天夜间骤降至15%，长期偷电
            if d < 30: day *= 0.95; night *= 0.15

        elif uid == 7:  # 褚十四 - 苏州工业园区居民，上班族夜猫子
            # 白天上班很少用电(3kWh)，晚上大量娱乐用电(12kWh)
            base_day = 3; base_night = 12
            wf = 1.5 if weekday >= 5 else 1.0   # 周末白天用电也增多
            sf = 1.2 if month in (1,2,7,8) else 1.0
            day = base_day * wf * sf + random.gauss(0, 0.2)
            night = base_night * sf + random.gauss(0, 0.6)

        elif uid == 8:  # 蒋十六 - 重庆渝北居民，大家庭（6口人+老人小孩）
            # 大家庭: 人多电器多，白天 18kWh，夜间 8kWh
            base_day = 18; base_night = 8
            sf = 1.4 if month in (1,2,7,8) else 1.0   # 夏冬重庆酷热酷冷
            wf = 1.25 if weekday >= 5 else 1.0
            day = base_day * wf * sf + random.gauss(0, 0.9)
            night = base_night * sf + random.gauss(0, 0.4)

        elif uid == 10: # 沈十八 - 长沙岳麓居民，独居老人
            # 独居老人: 用电很省的基准线
            base_day = 5; base_night = 2
            sf = 1.3 if month in (1,2,7,8) else 1.0
            day = base_day * sf + random.gauss(0, 0.3)
            night = base_night * sf + random.gauss(0, 0.15)

        elif uid == 11: # 韩十九 - 天津居民，年轻夫妻
            base_day = 11; base_night = 6
            wf = 1.25 if weekday >= 5 else 1.0
            sf = 1.25 if month in (1,2,7,8) else 1.0
            day = base_day * wf * sf + random.gauss(0, 0.6)
            night = base_night * sf + random.gauss(0, 0.3)

        elif uid == 12: # 杨二十 - 深圳南山居民，窃电嫌疑（夜间骤降）
            base_day = 14; base_night = 7
            day = base_day + random.gauss(0, 0.7)
            night = base_night + random.gauss(0, 0.4)
            # 【窃电嫌疑】后20天夜间骤降至20%
            if d < 20: night *= 0.2

        elif uid == 14: # 朱二一 - 居民，合租青年
            base_day = 7; base_night = 6   # 合租: 白天在外上班，晚上多人共用
            sf = 1.2 if month in (1,2,7,8) else 1.0
            day = base_day * sf + random.gauss(0, 0.4)
            night = base_night * sf + random.gauss(0, 0.3)

        elif uid == 17: # 万达广场 - 大型商业综合商场（绿地中心写字楼旁边的商场）
            base_day = 350; base_night = 20  # 更大的商业体，用电更大
            wf = 1.5 if weekday >= 5 else 1.0  # 周末客流翻倍
            day = base_day * wf + random.gauss(0, 10)
            night = base_night + random.gauss(0, 1.5)
            if month in (7,8): day *= 1.35
            if month in (1,2): day *= 1.2

        elif uid == 18: # 沃尔玛超市 - 冷链大型超市
            # 超市特点: 大量冷柜/冰柜 24h 运行，夜间用电也高
            base_day = 300; base_night = 40   # 夜间 40kWh 是冷链系统的持续耗电
            sf = 1.2 if month in (1,2,7,8) else 1.0
            wf = 1.3 if weekday >= 5 else 1.0
            day = base_day * wf * sf + random.gauss(0, 8)
            night = base_night * sf + random.gauss(0, 2)

        elif uid == 22: # 中石化镇海炼化 - 宁波石化厂，超大量工业用电
            base_day = 1200; base_night = 500  # 炼化装置 24h 运转，用电极为巨大
            sf = 1.1 if month in (1,2,7,8) else 1.0   # 工业受季节影响小
            day = base_day * sf + random.gauss(0, 30)
            night = base_night * sf + random.gauss(0, 15)

        elif uid == 24: # 中石油炼化 - 西安化工厂
            base_day = 900; base_night = 450
            sf = 1.15 if month in (1,2,7,8) else 1.0
            day = base_day * sf + random.gauss(0, 25)
            night = base_night * sf + random.gauss(0, 10)

        else:
            # 兜底: 未特别设定的用户，用通用的居民基础模式
            base_day = 10; base_night = 5
            day = base_day + random.gauss(0, 1)
            night = base_night + random.gauss(0, 0.5)

        # 确保用电量不为负数（高斯噪声可能导致负值）
        day = round(max(day, 0.1), 2)
        night = round(max(night, 0.05), 2)
        records.append((rd, day, night))

    # 批量插入（先收集再执行，方便错误处理）
    for rd, day, night in records:
        insert_power(uid, rd, day, night)
    print(f"  ✅ user_id={uid}: {len(records)} 条真实数据")


# ============================================================
# 主流程：为所有 25 个用户生成 90 天真实数据
# ============================================================
print("正在为所有25个用户注入逼真数据...")
for uid in range(1, 26):
    realistic_daily(uid)

conn.commit()

# ============================================================
# 数据注入后重新执行全量稽查
# 说明：注入新数据后需要重新调用 sp_check_all_users，触发预警判定
#       该存储过程会扫描所有近 30 天用电记录，对比 ILLEGAL_RULE 规则表
#       自动发现异常用户并写入 RISK_WARN 表
# ============================================================
cur.callproc("sp_check_all_users")
conn.commit()

# ============================================================
# 统计验证：输出数据注入后的系统状态概览
# ============================================================
cur.execute("SELECT COUNT(*) FROM user_power")
total = cur.fetchone()[0]                         # 总用电记录数（期望 25 × 90 = 2250 条）
cur.execute("SELECT COUNT(*) FROM v_suspected_users")
sus = cur.fetchone()[0]                           # 疑似窃电用户数（期望 > 0）
cur.execute("SELECT COUNT(*) FROM risk_warn")
warns = cur.fetchone()[0]                         # 总预警记录数（期望 > 0）

print(f"\n总计: {total} 条用电记录 | {warns} 条预警 | {sus} 户疑似窃电")
print("\n疑似用户:")
# 查询疑似窃电用户详情，按风险评分降序排列
cur.execute("SELECT user_name,user_type,ROUND(avg_night_ratio*100,1),risk_level,risk_score FROM v_suspected_users ORDER BY risk_score DESC")
for r in cur:
    print(f"  ⚠ [{r[1]}] {r[0]}: 夜间{r[2]}% | {r[3]} | {r[4]}分")

cur.close(); conn.close()
print("\n✅ 真实数据注入完成！")
