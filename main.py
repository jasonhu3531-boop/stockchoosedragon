import akshare as ak
from datetime import datetime, timedelta
import pandas as pd
import os

# ===================== 选股参数配置（已按你的要求设置好）=====================
# 流通市值范围（亿）
MIN_CIRC_MARKET_CAP = 10
MAX_CIRC_MARKET_CAP = 300
# 股价范围（元）
MIN_PRICE = 5
MAX_PRICE = 30
# 每日最多选出的股票数量
MAX_STOCK_COUNT = 5
# 市场环境核心阈值
LIMIT_UP_MIN_COUNT = 35
LIMIT_DOWN_MAX_COUNT = 8
MAX_LIANBAN_MIN_HEIGHT = 4
EXPLODE_RATE_MAX = 35
# ==============================================================================

def is_trade_day(date: str = None):
    """
    强制精准判断A股交易日（弃用接口，用本地规则）
    :param date: 日期，格式YYYYMMDD
    :return: True/False
    """
    if date is None:
        date = datetime.now().strftime("%Y%m%d")
    dt = datetime.strptime(date, "%Y%m%d")
    year = dt.year
    month = dt.month
    day = dt.day

    # 1. 基础规则：排除周末
    if dt.weekday() in [5, 6]:
        return False

    # 2. 2026年A股法定休市（精准罗列）
    holiday_2026 = [
        # 春节休市
        "20260216", "20260217", "20260218", "20260219", "20260220", "20260221", "20260222",
        # 清明节（4月4日-6日）
        "20260404", "20260405", "20260406",
        # 劳动节（5月1日-5日）
        "20260501", "20260502", "20260503", "20260504", "20260505",
        # 端午节（6月19日-21日）
        "20260619", "20260620", "20260621",
        # 中秋节（9月25日-27日）
        "20260925", "20260926", "20260927",
        # 国庆节（10月1日-7日）
        "20261001", "20261002", "20261003", "20261004", "20261005", "20261006", "20261007"
    ]

    # 3. 2026年A股补班交易日（周末补班）
    make_up_2026 = [
        "20260215", "20260407", "20260509", "20260622", "20260928", "20261010"
    ]

    # 最终判断：20260227不在休市清单+是工作日 → 判定为交易日
    if date in holiday_2026:
        return False
    elif date in make_up_2026:
        return True
    else:
        return True

def check_market_env(date: str = None):
    """市场环境判定，返回是否达标+状态说明"""
    if date is None:
        date = datetime.now().strftime("%Y%m%d")
    if not is_trade_day(date):
        return False, f"[{date}] 非A股交易日，不执行选股"
    
    try:
        # 1. 非ST涨停家数
        limit_up_df = ak.stock_zt_pool_em(date=date)
        limit_up_df = limit_up_df[~limit_up_df['名称'].str.contains('ST|退', na=False)]
        limit_up_count = len(limit_up_df)

        # 2. 非ST跌停家数
        limit_down_df = ak.stock_dt_pool_em(date=date)
        limit_down_df = limit_down_df[~limit_down_df['名称'].str.contains('ST|退', na=False)]
        limit_down_count = len(limit_down_df)

        # 3. 市场最高连板高度
        strong_df = ak.stock_zt_pool_strong_em(date=date)
        max_lianban = strong_df['连板数'].max() if not strong_df.empty else 0

        # 4. 上证指数涨跌幅与5日趋势
        index_df = ak.index_zh_a_hist(symbol="000001", period="daily", start_date=date, end_date=date)
        index_close = index_df['收盘'].iloc[0]
        index_open = index_df['开盘'].iloc[0]
        index_day_drop = (index_close - index_open) / index_open * 100
        start_5day = (datetime.strptime(date, "%Y%m%d") - timedelta(days=5)).strftime("%Y%m%d")
        index_5day_df = ak.index_zh_a_hist(symbol="000001", period="daily", start_date=start_5day, end_date=date)
        index_5day_gain = (index_5day_df['收盘'].iloc[-1] - index_5day_df['收盘'].iloc[0]) / index_5day_df['收盘'].iloc[0] * 100

        # 5. 炸板率
        explode_df = ak.stock_zt_pool_zbgc_em(date=date)
        explode_count = len(explode_df)
        total_try_limit = limit_up_count + explode_count
        explode_rate = explode_count / total_try_limit * 100 if total_try_limit > 0 else 100

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
            return False, f"市场环境不达标，不执行选股。涨停数:{limit_up_count},跌停数:{limit_down_count},最高连板:{max_lianban},炸板率:{explode_rate:.1f}%"
        
        # 辅助条件校验（满足≥3个）
        theme_df = ak.stock_zt_pool_board_em(date=date)
        top3_theme_limit = theme_df['涨停家数'].head(3).sum() if not theme_df.empty else 0
        theme_ratio = top3_theme_limit / limit_up_count * 100 if limit_up_count > 0 else 0

        yesterday = (datetime.strptime(date, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
        yesterday_limit_count = 0
        if is_trade_day(yesterday):
            yesterday_limit_df = ak.stock_zt_pool_em(date=yesterday)
            yesterday_limit_df = yesterday_limit_df[~yesterday_limit_df['名称'].str.contains('ST|退', na=False)]
            yesterday_limit_count = len(yesterday_limit_df)
        limit_up_ring = limit_up_count / yesterday_limit_count if yesterday_limit_count > 0 else 0

        north_df = ak.stock_em_hsgt_north_net_flow_in(symbol="北向资金")
        north_money = north_df['净流入'].iloc[-1] if not north_df.empty else -100

        activity_df = ak.stock_market_activity_legu_em()
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
    """个股基础筛选，返回是否通过"""
    if date is None:
        date = datetime.now().strftime("%Y%m%d")
    try:
        # 1. 基础属性筛选（已适配你的参数）
        info_df = ak.stock_individual_info_em(symbol=stock_code)
        info_dict = dict(zip(info_df['item'], info_df['value']))
        circ_market_cap = info_dict['流通市值'] / 100000000
        price = info_dict['最新价']
        list_date = info_dict['上市时间']
        list_days = (datetime.strptime(date, "%Y%m%d") - datetime.strptime(list_date, "%Y%m%d")).days

        basic_pass = (
            MIN_CIRC_MARKET_CAP <= circ_market_cap <= MAX_CIRC_MARKET_CAP and
            MIN_PRICE <= price <= MAX_PRICE and
            "ST" not in stock_name and "退" not in stock_name and
            list_days >= 60
        )
        if not basic_pass:
            return False

        # 2. 量能指标筛选
        start_10day = (datetime.strptime(date, "%Y%m%d") - timedelta(days=10)).strftime("%Y%m%d")
        hist_df = ak.stock_zh_a_hist(symbol=stock_code, period="daily", start_date=start_10day, end_date=date)
        if len(hist_df) < 2:
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

        # 3. 趋势指标筛选
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

        # 4. 涨停质量筛选
        zt_df = ak.stock_zt_pool_em(date=date)
        if stock_code not in zt_df['代码'].values:
            return False
        stock_zt = zt_df[zt_df['代码'] == stock_code].iloc[0]
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
    """主选股程序"""
    # 【20260227测试专用】强制指定日期为昨日（A股正常交易日）
    date = "20260227"
    result_text = f"===== 龙头战法选股结果 =====\n日期：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"

    # 1. 校验市场环境
    env_pass, env_msg = check_market_env(date)
    result_text += f"市场状态：{env_msg}\n\n"
    if not env_pass:
        with open("选股结果.txt", "w", encoding="utf-8") as f:
            f.write(result_text)
        print(result_text)
        return

    # 2. 获取当日涨停池
    try:
        zt_df = ak.stock_zt_pool_em(date=date)
        zt_df = zt_df[~zt_df['名称'].str.contains('ST|退', na=False)]
        if zt_df.empty:
            result_text += "当日无符合条件的涨停个股"
            with open("选股结果.txt", "w", encoding="utf-8") as f:
                f.write(result_text)
            return
    except Exception as e:
        result_text += f"获取涨停池出错：{str(e)}"
        with open("选股结果.txt", "w", encoding="utf-8") as f:
            f.write(result_text)
        return

    # 3. 基础筛选
    pass_list = []
    for idx, row in zt_df.iterrows():
        stock_code = row['代码']
        stock_name = row['名称']
        if filter_stock_basic(stock_code, stock_name, date):
            pass_list.append({
                "代码": stock_code,
                "名称": stock_name,
                "连板数": row['连板数'],
                "类型": get_stock_type(row['连板数']),
                "封板时间": row['首次封板时间'],
                "流通市值(亿)": round(row['流通市值']/100000000, 2)
            })

    # 4. 排序：按连板数降序 > 封板时间升序 > 流通市值升序，取前MAX_STOCK_COUNT只
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

    # 5. 保存结果
    with open("选股结果.txt", "w", encoding="utf-8") as f:
        f.write(result_text)
    print(result_text)

if __name__ == "__main__":
    main()
