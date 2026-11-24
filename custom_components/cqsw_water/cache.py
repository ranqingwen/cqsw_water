"""本地缓存文件的读写结构。

设计目标:
  1. 字段名一眼能看懂 (monthList / monthWaterNum / monthWaterCost / rechargeList ...)
  2. 与在线数据"合并"而不是"替换": 在线接口通常只给最近一年,
     更早的月份/缴费记录会一直保留在缓存文件里, 不会因为在线数据变短而丢失
  3. 卡片/实体直接消费缓存文件的数据 (cache -> data), 在线/离线看到的内容一致

缓存文件结构 (示例, 值均为演示数据):
{
  "date": "2026-10-31 10:30:21",          // 本文件最近一次"数据有变化"的时间
  "source": "kf.cncqsw.com",
  "account": "13800000000",                // 登录账号 (多账户区分)
  "card_no": "0000000001",                 // 户号
  "consumer_name": "张三",
  "consumer_addr": "XX小区X-X-X",
  "water_org": "XX市自来水有限公司",
  "customer_type": "居民生活用水",
  "balance": 0.0,
  "reading": 287.0,                        // 最新表读数(止度)
  "begin_scale": 269.0,                    // 本期起度
  "end_scale": 287.0,                      // 本期止度
  "tier_usage": 81.0,                      // 阶梯累计用量
  "now_ladder": "1",
  "ladder": {"ladder1": ..., "ladder2": ..., "ladder3": ...},
  "last_month": "2026年09月",
  "monthList": [
    {"month": "2026年09月", "monthWaterNum": 18.0, "monthWaterCost": 63.0,
     "beginScale": 269.0, "endScale": 287.0, "isPay": true,
     "feeItems": [{"item": "自来水费", "name": "...", "price": 3.5,
                   "amount": 18.0, "money": 63.0}],
     "updated_at": "2026-10-31 10:30:21"}
  ],
  "rechargeList": [
    {"card_no": "0000000001", "consumer_name": "张三", "amount": 63.0,
     "pay_date": "2026/9/20 0:59:41", "pay_mode": "支付宝",
     "period": "2026年09月", "remark": ""}
  ],
  "statistics": {"months": 18, "total_water": 1234.0, "total_cost": 2345.0,
                 "payments": 23}
}
"""

import json
from datetime import datetime


# ---------------------------------------------------------------- 工具
def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _num(v, nd=2):
    """转 float 并保留位数; 失败返回 None。"""
    if v is None or v == "":
        return None
    try:
        return round(float(str(v).replace(",", "").strip()), nd)
    except (TypeError, ValueError):
        return None


def _month_key(month):
    """'2026年09月' -> 202609; 无法解析返回 0 (排序用)。"""
    digits = "".join(ch for ch in str(month or "") if ch.isdigit())
    try:
        return int(digits[:6])
    except ValueError:
        return 0


def _pay_key(rec):
    """缴费记录唯一键: 日期 + 金额 + 方式。"""
    return (
        str(rec.get("pay_date") or "").strip(),
        rec.get("amount"),
        str(rec.get("pay_mode") or "").strip(),
    )


def _better(new, old):
    """新值可用就用新值; 新值被脱敏(*)/为空时保留旧值。"""
    if new in (None, ""):
        return old
    if isinstance(new, str) and "*" in new and isinstance(old, str) and old and "*" not in old:
        return old
    return new


# ---------------------------------------------------------------- 从旧格式升级
def upgrade(raw):
    """把旧版缓存 (直接 dump 的接口数据, 含 usage_list/payment_list) 转成新结构。

    新结构原样返回。
    """
    if not isinstance(raw, dict):
        return None
    if "monthList" in raw:
        return raw
    if "usage_list" not in raw and "payment_list" not in raw:
        return raw  # 未知结构, 原样交给上层

    ts = raw.get("updated_at") or _now()
    months = []
    fee_by_month = raw.get("fee_items_by_month") or {}
    for row in raw.get("usage_list") or []:
        month = row.get("month")
        if not month:
            continue
        item = {
            "month": month,
            "monthWaterNum": _num(row.get("usage")),
            "monthWaterCost": _num(row.get("fee")),
            "isPay": row.get("status") != "未缴费",
            "updated_at": ts,
        }
        if row.get("begin") is not None:
            item["beginScale"] = _num(row.get("begin"))
        if row.get("end") is not None:
            item["endScale"] = _num(row.get("end"))
        items = fee_by_month.get(month)
        if items:
            item["feeItems"] = items
        months.append(item)

    pays = []
    for row in raw.get("payment_list") or []:
        pays.append({
            "card_no": raw.get("card_no"),
            "consumer_name": raw.get("customer_name"),
            "amount": _num(row.get("amount")),
            "pay_date": row.get("time"),
            "pay_mode": row.get("channel"),
            "period": row.get("month") or "",
            "remark": "",
        })

    cache = {
        "date": ts,
        "source": "kf.cncqsw.com",
        "account": raw.get("account"),
        "card_no": raw.get("card_no"),
        "consumer_name": raw.get("customer_name"),
        "consumer_addr": raw.get("customer_addr"),
        "water_org": raw.get("water_org"),
        "customer_type": raw.get("customer_type"),
        "balance": _num(raw.get("balance")),
        "reading": _num(raw.get("reading")),
        "begin_scale": _num(raw.get("begin_scale")),
        "end_scale": _num(raw.get("end_scale")),
        "tier_usage": _num(raw.get("tier_usage")),
        "now_ladder": raw.get("now_ladder"),
        "ladder": raw.get("ladder"),
        "monthList": months,
        "rechargeList": pays,
    }
    return _finalize(cache)


# ---------------------------------------------------------------- 字段顺序 (可读性)
_MONTH_ORDER = ("month", "monthWaterNum", "monthWaterCost",
                "beginScale", "endScale", "isPay", "updated_at", "feeItems")
_PAY_ORDER = ("card_no", "consumer_name", "amount", "pay_date",
              "pay_mode", "period", "remark")


def _ordered_month(item):
    out = {k: item[k] for k in _MONTH_ORDER if item.get(k) is not None}
    for k, v in item.items():          # 未知字段追加在后, 不丢信息
        if k not in out and v is not None:
            out[k] = v
    return out


def _ordered_pay(item):
    out = {k: item[k] for k in _PAY_ORDER if item.get(k) is not None}
    for k, v in item.items():
        if k not in out and v is not None:
            out[k] = v
    return out


# ---------------------------------------------------------------- 合并
def _merge_months(old_list, new_list, stamp):
    """按月合并: 同月以新数据为准 (但保留旧的非空字段/费用构成), 旧独有月份保留。"""
    merged = {}
    for item in old_list or []:
        if item.get("month"):
            merged[item["month"]] = dict(item)
    for item in new_list or []:
        month = item.get("month")
        if not month:
            continue
        prev = merged.get(month) or {}
        out = dict(prev)
        for k, v in item.items():
            if v is None:
                continue
            if k in ("feeItems",) and not v:
                continue
            out[k] = v
        # 起止度: 新数据缺失时沿用旧的
        for k in ("beginScale", "endScale"):
            if out.get(k) is None and prev.get(k) is not None:
                out[k] = prev[k]
        # 只有该月数据真的变化了才更新 updated_at (否则文件内容不变, 不写盘)
        core = ("monthWaterNum", "monthWaterCost", "beginScale", "endScale", "isPay")
        changed = not prev or any(prev.get(k) != out.get(k) for k in core)
        out["updated_at"] = stamp if changed else prev.get("updated_at", stamp)
        merged[month] = out
    return sorted(merged.values(), key=lambda x: _month_key(x.get("month")), reverse=True)


def _merge_pays(old_list, new_list):
    """按 (缴费日期, 金额, 方式) 去重合并, 新的覆盖旧的, 旧的独有记录保留。"""
    merged = {}
    for rec in old_list or []:
        if isinstance(rec, dict):
            merged[_pay_key(rec)] = dict(rec)
    for rec in new_list or []:
        if isinstance(rec, dict):
            merged[_pay_key(rec)] = dict(rec)
    return sorted(
        merged.values(),
        key=lambda x: str(x.get("pay_date") or ""),
        reverse=True,
    )


def _finalize(cache):
    """补齐派生字段与统计, 并统一字段顺序 (方便人眼阅读)。"""
    months = [_ordered_month(m) for m in (cache.get("monthList") or [])]
    cache["monthList"] = months
    cache["rechargeList"] = [_ordered_pay(p) for p in (cache.get("rechargeList") or [])]
    if months:
        latest = months[0]
        cache["last_month"] = latest.get("month")
        if latest.get("endScale") is not None:
            cache["reading"] = latest["endScale"]
            cache["end_scale"] = latest["endScale"]
        if latest.get("beginScale") is not None:
            cache["begin_scale"] = latest["beginScale"]
            cache["last_reading"] = latest["beginScale"]
        if latest.get("monthWaterNum") is not None:
            cache["current_usage"] = latest["monthWaterNum"]
        if latest.get("monthWaterCost") is not None:
            cache["current_fee"] = latest["monthWaterCost"]
        if len(months) > 1 and months[1].get("monthWaterNum") is not None:
            cache["last_usage"] = months[1]["monthWaterNum"]

    total_water = 0.0
    total_cost = 0.0
    year_cost = 0.0
    current_year = datetime.now().year
    for m in months:
        if m.get("monthWaterNum") is not None:
            total_water += m["monthWaterNum"]
        if m.get("monthWaterCost") is not None:
            total_cost += m["monthWaterCost"]
            # 本年累计: 月份属于当前年份才计入
            if _month_key(m.get("month")) // 100 == current_year:
                year_cost += m["monthWaterCost"]
    pays = cache.get("rechargeList") or []
    cache["statistics"] = {
        "months": len(months),
        "total_water": round(total_water, 2),
        "total_cost": round(total_cost, 2),
        "year_cost": round(year_cost, 2),
        "payments": len(pays),
    }
    if pays:
        cache["last_pay_date"] = pays[0].get("pay_date")
        cache["last_pay_amount"] = pays[0].get("amount")
    return cache


def build(online, old_cache=None):
    """在线数据 + 旧缓存 -> 新的缓存结构 (合并, 不丢历史)。"""
    stamp = online.get("updated_at") or _now()
    old_cache = old_cache or {}
    if "monthList" not in old_cache:
        old_cache = upgrade(old_cache) or {}

    fee_by_month = {
        m["month"]: m["feeItems"]
        for m in (old_cache.get("monthList") or [])
        if m.get("month") and m.get("feeItems")
    }
    fee_by_month.update(online.get("fee_items_by_month") or {})

    new_months = []
    for row in online.get("usage_list") or []:
        month = row.get("month")
        if not month:
            continue
        item = {
            "month": month,
            "monthWaterNum": _num(row.get("usage")),
            "monthWaterCost": _num(row.get("fee")),
            "isPay": row.get("status") != "未缴费",
            "updated_at": stamp,
        }
        if row.get("begin") is not None:
            item["beginScale"] = _num(row.get("begin"))
        if row.get("end") is not None:
            item["endScale"] = _num(row.get("end"))
        items = fee_by_month.get(month)
        if items:
            item["feeItems"] = items
        new_months.append(item)

    new_pays = [
        {
            "card_no": online.get("card_no"),
            "consumer_name": online.get("customer_name"),
            "amount": _num(row.get("amount")),
            "pay_date": row.get("time"),
            "pay_mode": row.get("channel"),
            "period": row.get("month") or "",
            "remark": "",
        }
        for row in (online.get("payment_list") or [])
    ]

    cache = {
        "date": stamp,
        "source": "kf.cncqsw.com",
        "account": online.get("account") or old_cache.get("account"),
        "card_no": online.get("card_no") or old_cache.get("card_no"),
        "consumer_name": _better(online.get("customer_name"), old_cache.get("consumer_name")),
        "consumer_addr": _better(online.get("customer_addr"), old_cache.get("consumer_addr")),
        "water_org": _better(online.get("water_org"), old_cache.get("water_org")),
        "customer_type": _better(online.get("customer_type"), old_cache.get("customer_type")),
        "balance": _num(online.get("balance")),
        "reading": _num(online.get("reading")),
        "begin_scale": _num(online.get("begin_scale")),
        "end_scale": _num(online.get("end_scale")),
        "tier_usage": _num(online.get("tier_usage")),
        "now_ladder": online.get("now_ladder"),
        "ladder": online.get("ladder"),
        "monthList": _merge_months(old_cache.get("monthList"), new_months, stamp),
        "rechargeList": _merge_pays(old_cache.get("rechargeList"), new_pays),
    }
    if cache["balance"] is None:
        cache["balance"] = old_cache.get("balance")
    if cache["tier_usage"] is None:
        cache["tier_usage"] = old_cache.get("tier_usage")
    return _finalize(cache)


# ---------------------------------------------------------------- 缓存 -> 实体/卡片数据
def to_data(cache):
    """缓存结构 -> 实体属性 + 卡片消费的数据结构 (保持旧字段名不变)。"""
    cache = cache or {}
    months = cache.get("monthList") or []
    usage_list = []
    fee_items_by_month = {}
    for idx, m in enumerate(months):
        usage_list.append({
            "month": m.get("month"),
            "usage": m.get("monthWaterNum"),
            "fee": m.get("monthWaterCost"),
            "status": "已缴费" if m.get("isPay") else "未缴费",
            "begin": m.get("beginScale"),
            "end": m.get("endScale"),
        })
        # 卡片弹窗只用最近一年, 只暴露最近 24 期费用构成, 避免属性过大
        if m.get("feeItems") and idx < 24:
            fee_items_by_month[m.get("month")] = m["feeItems"]

    payment_list = [
        {
            "time": p.get("pay_date"),
            "amount": p.get("amount"),
            "channel": p.get("pay_mode"),
            "month": p.get("period") or "",
        }
        for p in (cache.get("rechargeList") or [])
    ]

    data = {
        "account": cache.get("account"),
        "card_no": cache.get("card_no"),
        "customer_name": cache.get("consumer_name"),
        "customer_addr": cache.get("consumer_addr"),
        "water_org": cache.get("water_org"),
        "customer_type": cache.get("customer_type"),
        "balance": cache.get("balance"),
        "reading": cache.get("reading"),
        "begin_scale": cache.get("begin_scale"),
        "end_scale": cache.get("end_scale"),
        "tier_usage": cache.get("tier_usage"),
        "now_ladder": cache.get("now_ladder"),
        "ladder": cache.get("ladder"),
        "current_usage": cache.get("current_usage"),
        "current_fee": cache.get("current_fee"),
        "last_usage": cache.get("last_usage"),
        "last_reading": cache.get("begin_scale"),
        "total_fee": (cache.get("statistics") or {}).get("total_cost"),
        "year_total_fee": (cache.get("statistics") or {}).get("year_cost"),
        "updated_at": cache.get("date"),
        "last_month": cache.get("last_month"),
        "statistics": cache.get("statistics"),
        "last_pay_time": cache.get("last_pay_date"),
        "last_pay_amount": cache.get("last_pay_amount"),
        "usage_list": usage_list,
        "payment_list": payment_list,
        "current_fee_items": (fee_items_by_month.get(cache.get("last_month"))
                              if cache.get("last_month") else None),
        "fee_items_by_month": fee_items_by_month,
        "cache": cache,
    }
    return data


def dumps(cache):
    """稳定序列化 (比对是否有变化 / 落盘用)。"""
    return json.dumps(cache, sort_keys=True, ensure_ascii=False, default=str)
