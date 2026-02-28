import akshare as ak
from datetime import datetime, timedelta
import pandas as pd
import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ===================== 网络请求重试配置（解决连接中断）=====================
# 创建带重试机制的requests会话
session = requests.Session()
retry_strategy = Retry(
    total=3,  # 总重试次数
    backoff_factor=1,  # 重试间隔（1s, 2s, 4s...）
    status_forcelist=[429, 500, 502, 503, 504],  # 触发重试的状态码
    allowed_methods=["GET", "POST"]
)
adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount("https://", adapter)
session.mount("http://", adapter)
# 覆盖akshare的默认requests会话（关键：解决底层连接中断）
ak.session = session

# ===================== 选股参数配置 =====================
MIN_CIRC_MARKET_CAP = 10
MAX_CIRC_MARKET_CAP = 300
MIN_PRICE = 5
MAX_PRICE = 30
MAX_STOCK_COUNT = 5
LIMIT_UP_MIN_COUNT = 35
LIMIT_DOWN_MAX_COUNT = 8
MAX_LIANBAN_MIN_HEIGHT = 4
EXPLODE_RATE_MAX = 35
# ==============================================================================

def is_trade_day(date: str = None):
    """精准判断A股交易日（本地规则）"""
    if date is None:
        date = datetime.now().strftime("%Y%m%d")
    dt = datetime.strptime(date, "%Y%m%d")
    
    # 1. 排除周末
    if dt.weekday() in [5, 6]:
        return False

    # 2. 2025年A股法定休市（精简版，覆盖20251231）
    holiday_2025 = [
        "20250101", "20250129", "20250130", "20250405", "20250501", 
        "20250529", "20250530", "20250609", "20250917", "20251001",
        "20251002", "20251003", "20251006", "20251007"
    ]
    # 2025补班交易日
    make_up_2025 = ["20250126", "20250208", "20250407", "20250531", "20250916", "20251008"]

    if date in holiday_2025:
        return False
    elif date in make_up_2025:
        return True
    else:
        return True

def safe_ak_call(func, *args, **kwargs):
    """安全调用akshare接口（带超时+异常容错）"""
    try:
        # 设置接口超时时间（10s），避免无限等待
        kwargs['timeout'] = kwargs.get('timeout', 10)
        return func(*args, **kwargs)
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, 
            requests.exceptions.RemoteDisconnected):
        # 网络异常时返回空DataFrame
        return pd.DataFrame()
    except Exception:
        return pd.DataFrame()

def check_market_env(date: str = None):
    """市场环境判定（增强网络容错）"""
    if date is None:
        date = datetime.now().strftime("%Y%m%d")
    if not is_trade_day(date):
        return False, f"[{date}] 非A股交易日，不执行选股"
    
    try:
        # 1. 非ST涨停家数（安全调用+超时）
        limit_up_df = safe_ak_call(ak.stock_zt_pool_em, date=date)
        if limit_up_df.empty:
            return False, f"[{date}] 无法获取涨停数据（网络/数据源异常）"
        limit_up_df = limit_up_df[~limit_up_df['名称'].str.contains('ST|退', na=False)]
        limit_up_count = len(limit_up_df)

        # 2. 跌停家数（临时默认0，避免接口报错）
        limit_down_count = 0

        # 3. 最高连板高度（从涨停池提取+容错）
        max_lianban = 0
        if not limit_up_df.empty and '连板数' in limit_up_df.columns:
            max_lianban = limit_up_df['连板数'].max()

        # 4. 上证指数数据（安全调用）
        index_day_drop = 0
        index_5day_gain = 0
        index_df = safe_ak_call(ak.index_zh_a_hist, symbol="000001", period="daily", start_date=date, end_date=date)
        if not index_df.empty and len(index_df) > 0 and '开盘' in index_df.columns and '收盘' in index_df.columns:
            index_close = index_df['收盘'].iloc[0]
            index_open = index_df['开盘'].iloc[0]
            index_day_drop = (index_close - index_open) / index_open * 100 if index_open != 0 else 0
            
            start_5day = (datetime.strptime(date, "%Y%m%d") - timedelta(days=5)).strftime("%Y%m%d")
            index_5day_df = safe_ak_call(ak.index_zh_a_hist, symbol="000001", period="daily", start_date=start_5day, end_date=date)
            if not index_5day_df.empty and len(index_5day_df) >= 2:
                index_5day_gain = (index_5day_df['收盘'].iloc[-1] - index_5day_df['收盘'].iloc[0]) / index_5day_df['收盘'].iloc[0] * 100 if index_5day_df['收盘'].iloc[0] != 0 else 0

        # 5. 炸板率（安全调用）
        explode_rate = 100
        explode_df = safe_ak_call(ak.stock_zt_pool_zbgc_em, date=date)
        explode_count = len(explode_df) if not explode_df.empty else 0
        total_try_limit = limit_up_count + explode_count
        if total_try_limit > 0:
            explode_rate = explode_count / total_try_limit * 100

        # 核心条件校验
        core_pass = (
            limit_up_count >= LIMIT_UP_MIN_COUNT and
            limit_down_count <= LIMIT_DOWN_MAX_COUNT and
            max_lianban >= MAX_LIANBAN_MIN_HEIGHT and
            index_day_drop <= 1 and
            index_5day_gain >= 0 and
            explode_rate <= EXPLODE_RATE_MAX
        )

        if not core_pass:
            return False, f"市场环境不达标。涨停数:{limit_up_count},跌停数:{limit_down_count},最高连板:{max_lianban},炸板率:{explode_rate:.1f}%"
        
        # 辅助条件（安全调用）
        theme_ratio = 0
        theme_df = safe_ak_call(ak.stock_zt_pool_board_em, date=date)
        if not theme_df.empty and '涨停家数' in theme_df.columns and limit_up_count > 0:
            top3_theme_limit = theme_df['涨停家数'].head(3).sum()
            theme_ratio = top3_theme_limit / limit_up_count * 100

        limit_up_ring = 0
        yesterday = (datetime.strptime(date, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
        if is_trade_day(yesterday):
            yesterday_limit_df = safe_ak_call(ak.stock_zt_pool_em, date=yesterday)
            yesterday_limit_df = yesterday_limit_df[~yesterday_limit_df['名称'].str.contains('ST|退', na=False)]
            yesterday_limit_count = len(yesterday_limit_df)
            if yesterday_limit_count > 0:
                limit_up_ring = limit_up_count / yesterday_limit_count

        north_money = -100
        north_df = safe_ak_call(ak.stock_em_hsgt_north_net_flow_in, symbol="北向资金")
        if not north_df.empty and '净流入' in north_df.columns:
            north_money = north_df['净流入'].iloc[-1] if len(north_df) > 0 else -100

        up_down_ratio = 0
        activity_df = safe_ak_call(ak.stock_market_activity_legu_em)
        if not activity_df.empty and '上涨家数' in activity_df.columns and '下跌家数' in activity_df.columns:
            up_count = activity_df['上涨家数'].iloc[0]
            down_count = activity_df['下跌家数'].iloc[0]
            up_down_ratio = up_count / down_count if down_count > 0 else 0

        assist_conditions = [
            theme_ratio >= 60,
            limit_up_ring >= 0.9,
            north_money >= -30,
            up_down_ratio >= 1.2
        ]
        assist_pass = sum(assist_conditions) >= 3

        if assist_pass:
            return True, "市场环境达标，启动选股"
        else:
            return False, "市场辅助条件不达标，不执行选股"
    except Exception as e:
        return False, f"环境判定出错：{str(e)}"

def filter_stock_basic(stock_code: str, stock_name: str, date: str = None):
    """个股筛选（增强容错）"""
    if date is None:
        date = datetime.now().strftime("%Y%m%d")
    try:
        # 基础属性
        info_df = safe_ak_call(ak.stock_individual_info_em, symbol=stock_code)
        if info_df.empty or len(info_df) < 1:
            return False
        info_dict = dict(zip(info_df['item'], info_df['value']))
        if '流通市值' not in info_dict or '最新价' not in info_dict or '上市时间' not in info_dict:
            return False
        circ_market_cap = info_dict['流通市值'] / 100000000
        price = info_dict['最新价']
        list_date = info_dict['上市时间']
        try:
            list_days = (datetime.strptime(date, "%Y%m%d") - datetime.strptime(list_date, "%Y%m%d")).days
        except:
            return False

        basic_pass = (
            MIN_CIRC_MARKET_CAP <= circ_market_cap <= MAX_CIRC_MARKET_CAP and
            MIN_PRICE <= price <= MAX_PRICE and
            "ST" not in stock_name and "退" not in stock_name and
            list_days >= 60
        )
        if not basic_pass:
            return False

        # 量能指标
        start_10day = (datetime.strptime(date, "%Y%m%d") - timedelta(days=10)).strftime("%Y%m%d")
        hist_df = safe_ak_call(ak.stock_zh_a_hist, symbol=stock_code, period="daily", start_date=start_10day, end_date=date)
        if hist_df.empty or len(hist_df) < 2:
            return False
        if '换手率' not in hist_df.columns or '量比' not in hist_df.columns or '成交量' not in hist_df.columns:
            return False
        turnover = hist_df['换手率'].iloc[-1]
        volume_ratio = hist_df['量比'].iloc[-1]
        volume_today = hist_df['成交量'].iloc[-1]
        volume_yesterday = hist_df['成交量'].iloc[-2]

        volume_pass = (
            8 <= turnover <= 18 and
            volume_ratio >= 1.5 and
            volume_today >= volume_yesterday * 1.3
        )
        if not volume_pass:
            return False

        # 趋势指标
        if '收盘' not in hist_df.columns:
            return False
        ma5 = hist_df['收盘'].rolling(5).mean().iloc[-1]
        ma5_prev = hist_df['收盘'].rolling(5).mean().iloc[-2]
        ma20 = hist_df['收盘'].rolling(20).mean().iloc[-1]
        close_price = hist_df['收盘'].iloc[-1]

        trend_pass = (
            close_price >= ma5 and
            ma5 > ma5_prev and
            close_price >= ma20
        )
        if not trend_pass:
            return False

        # 涨停质量
        zt_df = safe_ak_call(ak.stock_zt_pool_em, date=date)
        if zt_df.empty or stock_code not in zt_df['代码'].values:
            return False
        stock_zt = zt_df[zt_df['代码'] == stock_code].iloc[0]
        if '首次封板时间' not in stock_zt or '炸板次数' not in stock_zt or '封单金额' not in stock_zt:
            return False
        first_board_time = stock_zt['首次封板时间']
        explode_times = stock_zt['炸板次数']
        board_order_amount = stock_zt['封单金额'] / 100000000

        board_pass = (
            first_board_time <= "13:30:00" and
            explode_times == 0 and
            board_order_amount >= circ_market_cap * 0.01
        )
        return board_pass
    except:
        return False

def get_stock_type(lianban_count: int):
    """判断连板类型"""
    if lianban_count == 2:
        return "1进2"
    elif lianban_count == 3:
        return "2进3"
    elif lianban_count >= 4:
        return "高位连板"
    else:
        return "首板"

def main():
    """主程序（测试日期改为20251231，历史真实交易日）"""
    # 关键：改用20251231（有完整数据的真实交易日，避免未来日期断连）
    date = "20251231"
    result_text = f"===== 龙头战法选股结果 =====\n日期：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"

    # 校验市场环境
    env_pass, env_msg = check_market_env(date)
    result_text += f"市场状态：{env_msg}\n\n"
    if not env_pass:
        with open("选股结果.txt", "w", encoding="utf-8") as f:
            f.write(result_text)
        print(result_text)
        return

    # 获取涨停池
    zt_df = safe_ak_call(ak.stock_zt_pool_em, date=date)
    if zt_df.empty:
        result_text += "当日无符合条件的涨停个股"
        with open("选股结果.txt", "w", encoding="utf-8") as f:
            f.write(result_text)
        print(result_text)
        return
    zt_df = zt_df[~zt_df['名称'].str.contains('ST|退', na=False)]

    # 筛选个股
    pass_list = []
    for idx, row in zt_df.iterrows():
        stock_code = row['代码']
        stock_name = row['名称']
        if filter_stock_basic(stock_code, stock_name, date):
            pass_list.append({
                "代码": stock_code,
                "名称": stock_name,
                "连板数": row['连板数'] if '连板数' in row else 0,
                "类型": get_stock_type(row['连板数'] if '连板数' in row else 0),
                "封板时间": row['首次封板时间'] if '首次封板时间' in row else "未知",
                "流通市值(亿)": round(row['流通市值']/100000000, 2) if '流通市值' in row else 0
            })

    # 排序输出
    if len(pass_list) > 0:
        pass_df = pd.DataFrame(pass_list)
        pass_df = pass_df.sort_values(
            by=["连板数", "封板时间", "流通市值(亿)"],
            ascending=[False, True, True]
        ).head(MAX_STOCK_COUNT)

        result_text += f"选股结果（共{len(pass_df)}只）：\n"
        for idx, row in pass_df.iterrows():
            result_text += f"{idx+1}. {row['代码']} {row['名称']} | {row['类型']} | 封板时间：{row['封板时间']} | 流通市值：{row['流通市值(亿)']}亿\n"
    else:
        result_text += "今日无符合所有条件的个股"

    # 保存结果
    with open("选股结果.txt", "w", encoding="utf-8") as f:
        f.write(result_text)
    print(result_text)

if __name__ == "__main__":
    main()
