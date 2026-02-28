def is_trade_day(date: str = None):
    """
    精准判断A股交易日（优先级：接口→本地规则兜底）
    :param date: 日期，格式YYYYMMDD
    :return: True/False
    """
    if date is None:
        date = datetime.now().strftime("%Y%m%d")
    dt = datetime.strptime(date, "%Y%m%d")
    
    # 1. 先通过接口判断（优先）
    try:
        tool_df = ak.tool_trade_date_hist_sina()
        return date in tool_df['trade_date'].astype(str).values
    except:
        # 2. 接口失败时，用本地规则兜底（排除周末+已知节假日）
        # 2026年A股春节休市：20260216-20260222
        holiday_2026 = [f"202602{str(d).zfill(2)}" for d in range(16, 23)]
        # 排除周末
        if dt.weekday() in [5, 6]:
            return False
        # 排除2026春节休市
        if date in holiday_2026:
            return False
        # 其余工作日判定为交易日
        return True
