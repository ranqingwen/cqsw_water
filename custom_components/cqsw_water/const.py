"""常量定义。"""

DOMAIN = "cqsw_water"

CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_SCAN_INTERVAL = "scan_interval"  # 单位: 小时
CONF_TIER_PRICES = "tier_prices"      # [一档, 二档, 三档] 阶梯综合单价 (元/m³)

# 默认阶梯综合单价 (元/m³): 重庆中心城区 (主城供水, 一户一表居民, 按年度累计)。
# 一档 自来水2.50+污水1.00=3.50 / 二档 3.22+1.00=4.22 / 三档 4.90+1.00=5.90。
# 仅供参考, 添加/重新配置集成时可修改; 其他区域请按当地水司公示价格填写。
DEFAULT_TIER_PRICES = [3.5, 4.22, 5.9]

DEFAULT_NAME = "重庆水务"
DEFAULT_SCAN_HOURS = 12  # 默认每 12 小时自动登录刷新一次 (可选 1-48)

# 本地缓存文件: 按户号区分, /config/cqsw_water_data_<户号>.json (支持多账户)
CACHE_PREFIX = "cqsw_water_data_"
# 上一版误用的前缀 (兼容读取一次)
CACHE_PREFIX_OLD = "qsw_water_data_"
# 旧版单文件缓存 (兼容读取一次)
LEGACY_CACHE_FILENAME = "cncqsw_water_data.json"

# 实体唯一 id 前缀
ENTITY_PREFIX = "cqsw_water"
