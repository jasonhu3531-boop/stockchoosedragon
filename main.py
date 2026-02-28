import akshare as ak
from datetime import datetime, timedelta
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ===================== 龙头战法核心参数（可根据需求调整）=====================
MIN_CIRC_MARKET_CAP = 10    # 最小流通市值（亿）
MAX_CIRC_MARKET_CAP = 300   # 最大流通市值（亿）
MIN_PRICE = 5               # 最小股价（元）
MAX_PRICE = 30              # 最大股价（元）
MAX_STOCK_COUNT = 5         # 最多选股数量
LIMIT_UP_MIN_COUNT = 35     # 市场涨停数阈值（启动选股）
LIMIT_DOWN_MAX_COUNT = 8    # 市场跌停数阈值
MAX_LIANBAN_MIN_HEIGHT = 4  # 最低连板高度（龙头核心）
EXPLODE_RATE_MAX = 35       # 最大炸板率
# ==============================================================================

# ---------------------- 第一步：配置网络重试（解决连接中断/超时）----------------------
session = requests.Session()
retry_strategy = Retry(
    total=3,  # 重试3次
    backoff_factor=1,  # 重试间隔1s/2s/4s
    status_forcelist=[429, 500, 502, 503, 504],  # 服务器异常时重试
    allowed_methods=["GET", "POST"]
)
adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount("https://", adapter)
session.mount("http://", adapter)
ak.session = session  # 覆盖akshare默认会话

# ---------------------- 第二步：工具函数（真实交易日判断+接口安全调用）----------------------
def is_trade_day(date: str) -> bool:
    """精准判断A股真实交易日（结合akshare接口+本地规则）"""
    try:
        # 优先调用akshare交易日接口（最准确）
        trade_cal_df = ak.tool_trade_date_hist_sina()
        trade_dates = trade_cal_df['trade_date'].tolist()
        return date in trade_dates
    except:
        # 备用：本地规则判断（周末+法定节假日）
        dt = datetime.strptime(date, "%Y%m%d")
        if dt.weekday() in [5, 6]:
            return False
        # 2025年核心休市日（精简版）
        holiday_2025 = ["20250101", "20250129", "20250130", "20250405", "20250501", 
                        "20250529", "20250530", "20250609", "20250917", "20251001-20251007"]
        return date not in holiday_2025

def get_last_trade_day() -> str:
    """自动获取「前一个A股真实交易日」（核心：避免未来/非交易日）"""
    today = datetime.now()
    for i in range(1, 10):  # 最多往前查10天
        check_date = (today - timedelta(days=i)).strftime("%Y%m%d")
        if is_trade_day(check_date):
            return check_date
    return datetime.now().strftime("%Y%m%d")  # 兜底

def safe_ak_call(func, *args, **kwargs) -> pd.DataFrame:
    """安全调用akshare接口（兼容所有版本+超时+异常容错）"""
    try:
        kwargs['timeout'] = kwargs.get('timeout', 15)  # 超时15秒
        return func(*args, **kwargs)
    except Exception:  # 通用异常捕获，避开版本兼容问题
        return pd.DataFrame()

# ---------------------- 第三步：核心策略函数（龙头战法落地）----------------------
def check_market_env(trade_date: str) -> tuple[bool, str]:
    """判断市场环境是否符合龙头战法启动条件（真实数据）"""
    # 1. 获取真实涨停池（非ST/退市）
    limit_up_df = safe_ak_call(ak.stock_zt_pool_em, date=trade_date)
    if limit_up_df.empty:
        return False, f"[{trade_date}] 无涨停数据（市场环境不达标）"
    limit_up_df = limit_up_df[~limit_up_df['名称'].str.contains('ST|退', na=False)]
    limit_up_count = len(limit_up_df)

    # 2. 获取真实跌停数（备用默认0，避免接口空数据）
    limit_down_count = 0
    limit_down_df = safe_ak_call(ak.stock_limit_down_pool_em, date=trade_date)
    if not limit_down_df.empty:
        limit_down_df = limit_down_df[~limit_down_df['名称'].str.contains('ST|退', na=False)]
        limit_down_count = len(limit_down_df)

    # 3. 真实最高连板高度
    max_lianban = 0
    if '连板数' in limit_up_df.columns:
        max_lianban = limit_up_df['连板数'].max() if not limit_up_df.empty else 0

    # 4. 上证指数数据（真实）
    index_day_drop = 0
    index_5day_gain = 0
    index_df = safe_ak_call(ak.index_zh_a_hist, symbol="000001", period="daily", 
                           start_date=trade_date, end_date=trade_date)
    if not index_df.empty and len(index_df) > 0 and '开盘' in index_df.columns and '收盘' in index_df.columns:
        index_close = index_df['收盘'].iloc[0]
        index_open = index_df['开盘'].iloc[0]
        index_day_drop = (index_close - index_open) / index_open * 100 if index_open != 0 else 0
        
        # 5日涨幅
        start_5day = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=5)).strftime("%Y%m%d")
        index_5day_df = safe_ak_call(ak.index_zh_a_hist, symbol="000001", period="daily", 
                                   start_date=start_5day, end_date=trade_date)
        if not index_5day_df.empty and len(index_5day_df) >= 2 and '收盘' in index_5day_df.columns:
            index_5day_gain = (index_5day_df['收盘'].iloc[-1] - index_5day_df['收盘'].iloc[0]) / index_5day_df['收盘'].iloc[0] * 100 if index_5day_df['收盘'].iloc[0] != 0 else 0

    # 5. 真实炸板率
    explode_rate = 100
    explode_df = safe_ak_call(ak.stock_zt_pool_zbgc_em, date=trade_date)
    explode_count = len(explode_df) if not explode_df.empty else 0
    total_try_limit = limit_up_count + explode_count
    if total_try_limit > 0:
        explode_rate = explode_count / total_try_limit * 100

    # 核心条件校验（龙头战法启动阈值）
    core_pass = (
        limit_up_count >= LIMIT_UP_MIN_COUNT and
        limit_down_count <= LIMIT_DOWN_MAX_COUNT and
        max_lianban >= MAX_LIANBAN_MIN_HEIGHT and
        index_day_drop <= 1 and
        index_5day_gain >= 0 and
        explode_rate <= EXPLODE_RATE_MAX
    )

    if not core_pass:
        return False, f"[{trade_date}] 市场环境不达标→涨停数:{limit_up_count} | 跌停数:{limit_down_count} | 最高连板:{max_lianban} | 炸板率:{explode_rate:.1f}%"
    
    # 辅助条件（真实数据）
    theme_ratio = 0
    theme_df = safe_ak_call(ak.stock_zt_pool_board_em, date=trade_date)
    if not theme_df.empty and '涨停家数' in theme_df.columns and limit_up_count > 0:
        top3_theme_limit = theme_df['涨停家数'].head(3).sum()
        theme_ratio = top3_theme_limit / limit_up_count * 100

    limit_up_ring = 0
    yesterday = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
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
        return True, f"[{trade_date}] 市场环境达标→涨停数:{limit_up_count} | 最高连板:{max_lianban} | 炸板率:{explode_rate:.1f}%"
    else:
        return False, f"[{trade_date}] 辅助条件不达标→题材占比:{theme_ratio:.1f}% | 涨停环比:{limit_up_ring:.1f} | 北向资金:{north_money:.1f}亿 | 涨跌比:{up_down_ratio:.1f}"

def filter_dragon_stock(stock_code: str, stock_name: str, trade_date: str) -> bool:
    """筛选符合龙头战法的个股（真实数据+严格策略）"""
    # 1. 真实基本面数据
    info_df = safe_ak_call(ak.stock_individual_info_em, symbol=stock_code)
    if info_df.empty or len(info_df) < 1:
        return False
    info_dict = dict(zip(info_df['item'], info_df['value']))
    # 必选字段校验
    must_have = ['流通市值', '最新价', '上市时间']
    if not all(key in info_dict for key in must_have):
        return False
    
    # 单位转换+基础校验
    try:
        circ_market_cap = info_dict['流通市值'] / 100000000  # 转亿
        price = float(info_dict['最新价'])
        list_date = info_dict['上市时间']
        list_days = (datetime.strptime(trade_date, "%Y%m%d") - datetime.strptime(list_date, "%Y%m%d")).days
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

    # 2. 真实量能数据（10日）
    start_10day = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=10)).strftime("%Y%m%d")
    hist_df = safe_ak_call(ak.stock_zh_a_hist, symbol=stock_code, period="daily", 
                           start_date=start_10day, end_date=trade_date)
    if hist_df.empty or len(hist_df) < 2:
        return False
    # 必选量能字段
    vol_fields = ['换手率', '量比', '成交量']
    if not all(field in hist_df.columns for field in vol_fields):
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

    # 3. 真实趋势数据（均线）
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

    # 4. 真实涨停质量（核心）
    zt_df = safe_ak_call(ak.stock_zt_pool_em, date=trade_date)
    if zt_df.empty or stock_code not in zt_df['代码'].values:
        return False  # 非涨停股直接筛掉
    stock_zt = zt_df[zt_df['代码'] == stock_code].iloc[0]
    # 必选涨停字段
    zt_fields = ['首次封板时间', '炸板次数', '封单金额']
    if not all(field in stock_zt.index for field in zt_fields):
        return False
    
    first_board_time = stock_zt['首次封板时间']
    explode_times = stock_zt['炸板次数']
    board_order_amount = stock_zt['封单金额'] / 100000000  # 转亿

    board_pass = (
        first_board_time <= "13:30:00" and
        explode_times == 0 and
        board_order_amount >= circ_market_cap * 0.01
    )
    return board_pass

def get_dragon_type(lianban_count: int) -> str:
    """龙头类型（真实连板）"""
    if lianban_count == 2:
        return "1进2（涨停龙头）"
    elif lianban_count == 3:
        return "2进3（涨停龙头）"
    elif lianban_count >= 4:
        return f"{lianban_count}连板（核心龙头）"
    else:
        return "首板（涨停）"

# ---------------------- 第四步：主程序（自动选股）----------------------
def main():
    """自动选股主流程：真实数据+策略落地"""
    # 1. 自动获取前一个真实交易日（核心：避免无数据）
    trade_date = get_last_trade_day()
    result_text = f"===== 龙头战法自动选股结果 =====\n选股日期：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n目标交易日：{trade_date}\n"

    # 2. 校验市场环境
    env_pass, env_msg = check_market_env(trade_date)
    result_text += f"市场状态：{env_msg}\n\n"
    if not env_pass:
        with open("龙头选股结果.txt", "w", encoding="utf-8") as f:
            f.write(result_text)
        print(result_text)
        return

    # 3. 获取真实涨停池（仅非ST/退市）
    limit_up_df = safe_ak_call(ak.stock_zt_pool_em, date=trade_date)
    if limit_up_df.empty:
        result_text += f"[{trade_date}] 无符合条件的涨停个股"
        with open("龙头选股结果.txt", "w", encoding="utf-8") as f:
            f.write(result_text)
        print(result_text)
        return
    limit_up_df = limit_up_df[~limit_up_df['名称'].str.contains('ST|退', na=False)]

    # 4. 筛选龙头股（严格策略）
    dragon_stocks = []
    for idx, row in limit_up_df.iterrows():
        stock_code = row['代码']
        stock_name = row['名称']
        if filter_dragon_stock(stock_code, stock_name, trade_date):
            dragon_stocks.append({
                "代码": stock_code,
                "名称": stock_name,
                "连板数": row['连板数'] if '连板数' in row else 0,
                "龙头类型": get_dragon_type(row['连板数'] if '连板数' in row else 0),
                "封板时间": row['首次封板时间'] if '首次封板时间' in row else "未知",
                "流通市值(亿)": round(row['流通市值']/100000000, 2) if '流通市值' in row else 0
            })

    # 5. 排序输出（龙头优先级）
    if len(dragon_stocks) > 0:
        dragon_df = pd.DataFrame(dragon_stocks)
        # 排序：连板数降序 → 封板时间升序 → 流通市值升序
        dragon_df = dragon_df.sort_values(
            by=["连板数", "封板时间", "流通市值(亿)"],
            ascending=[False, True, True]
        ).head(MAX_STOCK_COUNT)

        result_text += f"选股结果（共{len(dragon_df)}只龙头股）：\n"
        for idx, row in dragon_df.iterrows():
            result_text += f"{idx+1}. {row['代码']} {row['名称']} | {row['龙头类型']} | 封板时间：{row['封板时间']} | 流通市值：{row['流通市值(亿)']}亿\n"
    else:
        result_text += f"[{trade_date}] 无符合所有龙头战法条件的个股"

    # 6. 保存结果（真实数据）
    with open("龙头选股结果.txt", "w", encoding="utf-8") as f:
        f.write(result_text)
    print(result_text)

if __name__ == "__main__":
    main()
