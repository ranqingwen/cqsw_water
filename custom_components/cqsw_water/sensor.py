"""传感器实体: 户号/读数/用量/费用/缴费等数据实体 + 最近刷新时间/登录状态/数据来源。"""

import json
import logging
import os
from datetime import timedelta

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .const import (
    CACHE_PREFIX,
    CACHE_PREFIX_OLD,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_TIER_PRICES,
    CONF_USERNAME,
    DEFAULT_NAME,
    DEFAULT_SCAN_HOURS,
    DEFAULT_TIER_PRICES,
    DOMAIN,
    ENTITY_PREFIX,
    LEGACY_CACHE_FILENAME,
)
from .api import CqSwApi
from .cache import build as build_cache
from .cache import dumps as cache_dumps
from .cache import to_data as cache_to_data
from .cache import upgrade as cache_upgrade

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    uid = f"{ENTITY_PREFIX}_{entry.data[CONF_USERNAME]}"
    device = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"{DEFAULT_NAME} {entry.data.get(CONF_USERNAME, '')}".strip(),
        manufacturer="重庆水务",
        model="掌上水务 kf.cncqsw.com",
    )

    entities = [
        # 数据实体 (按设备页展示顺序)
        CqSwMetricSensor(
            coordinator, device, key="card_no", name="户号",
            unique=f"{uid}_card_no", icon="mdi:card-account-details",
        ),
        CqSwMetricSensor(
            coordinator, device, key="last_reading", name="上期读数",
            unique=f"{uid}_last_reading", device_class=SensorDeviceClass.VOLUME,
            unit=UnitOfVolume.CUBIC_METERS, state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:gauge",
        ),
        CqSwMeterSensor(coordinator, device, entry, f"{uid}_meter"),  # 最新读数
        CqSwMetricSensor(
            coordinator, device, key="current_usage", name="本期水量",
            unique=f"{uid}_current_usage", device_class=SensorDeviceClass.VOLUME,
            unit=UnitOfVolume.CUBIC_METERS, state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:cup-water",
        ),
        CqSwMetricSensor(
            coordinator, device, key="current_fee", name="本期水费",
            unique=f"{uid}_current_fee", device_class=SensorDeviceClass.MONETARY,
            unit="CNY", state_class=SensorStateClass.MEASUREMENT, icon="mdi:currency-cny",
        ),
        CqSwMetricSensor(
            coordinator, device, key="tier_usage", name="本年累计用量",
            unique=f"{uid}_tier_usage", device_class=SensorDeviceClass.VOLUME,
            unit=UnitOfVolume.CUBIC_METERS, state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:chart-bar",
        ),
        CqSwMetricSensor(
            coordinator, device, key="total_fee", name="全部累计费用",
            unique=f"{uid}_total_fee", device_class=SensorDeviceClass.MONETARY,
            unit="CNY", icon="mdi:cash-multiple",
        ),
        CqSwMetricSensor(
            coordinator, device, key="year_total_fee", name="本年累计费用",
            unique=f"{uid}_year_total_fee", device_class=SensorDeviceClass.MONETARY,
            unit="CNY", icon="mdi:cash-register",
        ),
        CqSwMetricSensor(
            coordinator, device, key="last_pay_amount", name="最近缴费金额",
            unique=f"{uid}_last_pay_amount", device_class=SensorDeviceClass.MONETARY,
            unit="CNY", icon="mdi:cash",
        ),
        CqSwMetricSensor(
            coordinator, device, key="last_month", name="账单月份",
            unique=f"{uid}_last_month", icon="mdi:calendar-month-outline",
        ),
        CqSwMetricSensor(
            coordinator, device, key="last_pay_time", name="最近缴费时间",
            unique=f"{uid}_last_pay_time", icon="mdi:clock-outline",
        ),
        CqSwPayStatusSensor(coordinator, device, f"{uid}_pay_status"),
        # 诊断实体
        CqSwLastRefreshSensor(coordinator, device, f"{uid}_last_refresh"),
        CqSwStatusSensor(coordinator, device, entry, f"{uid}_status"),
        CqSwSourceSensor(coordinator, device, entry, f"{uid}_source"),
    ]
    # 清理注册表里已废弃的旧实体 (如曾创建后又被删掉的"全部累计用量"),
    # 避免升级后留下"不可用"的僵尸行; 不在本白名单里的一律移除
    _async_cleanup_stale_entities(hass, entry, {e._attr_unique_id for e in entities})
    async_add_entities(entities, update_before_add=True)


@callback
def _async_cleanup_stale_entities(
    hass: HomeAssistant, entry: ConfigEntry, valid_ids: set
) -> None:
    """删除本集成/本配置条目下 unique_id 不在白名单里的注册表条目。"""
    registry = er.async_get(hass)
    for reg_entry in list(registry.entities.values()):
        if (
            reg_entry.platform != DOMAIN
            or reg_entry.config_entry_id != entry.entry_id
        ):
            continue
        if reg_entry.unique_id not in valid_ids:
            _LOGGER.info(
                "移除已废弃的旧实体: %s (unique_id=%s)",
                reg_entry.entity_id,
                reg_entry.unique_id,
            )
            registry.async_remove(reg_entry.entity_id)


class CqSwDataUpdateCoordinator(DataUpdateCoordinator):
    """抓取重庆水务数据, 并做本地缓存 (缓存格式见 cache.py):

    - 缓存文件按户号区分: /config/cqsw_water_data_<户号>.json (支持多账户)
    - 在线数据与本地缓存"合并"而不是替换: 接口通常只给最近一年,
      更早的月份/缴费记录会一直累积在缓存文件里, 不会丢
    - 卡片/实体数据 = 合并后的缓存数据 (在线/离线看到的都是这一份)
    - 有变化才写盘 (无变化不动文件)
    - 抓取失败: 优先沿用内存上一次数据, 冷启动则读缓存, 保证"未登录也有数据"
    """

    def __init__(self, hass, api: CqSwApi, entry: ConfigEntry):
        self.interval_hours = max(12, entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_HOURS))
        self._interval = timedelta(hours=self.interval_hours)
        # 阶梯综合单价 (元/m³): 配置里留空的档位用默认价 (重庆中心城区, 仅供参考)
        tp = entry.data.get(CONF_TIER_PRICES) or []
        self.tier_prices = [
            float(tp[i]) if i < len(tp) and tp[i] not in (None, "")
            else DEFAULT_TIER_PRICES[i]
            for i in range(3)
        ]
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=self._interval,
        )
        self.api = api
        self.entry = entry
        self._cache_dir = hass.config.path()  # /config

    def _next_refresh_str(self) -> str:
        """下次计划刷新时间 (当前时间 + 刷新间隔)。"""
        return (dt_util.now() + self._interval).strftime("%Y-%m-%d %H:%M:%S")

    # ---- 本地缓存 ----
    def _cache_path_for(self, card_no) -> str:
        safe = "".join(ch for ch in str(card_no) if ch.isalnum()) or "unknown"
        return os.path.join(self._cache_dir, "%s%s.json" % (CACHE_PREFIX, safe))

    def _read_file(self, path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _read_cache(self, card_no=None):
        """读缓存文件, 统一返回新结构 (旧格式自动升级)。

        查找顺序: 指定户号的文件 -> 全部 cqsw_water_data_*.json (按账号/户号匹配)
        -> 旧版单文件。缓存内容为"合并累积"结构, 早于在线可查范围的月份不会丢。
        """
        paths = []
        if card_no:
            paths.append(self._cache_path_for(card_no))
        try:
            for fn in sorted(os.listdir(self._cache_dir)):
                if (
                    fn.startswith(CACHE_PREFIX) or fn.startswith(CACHE_PREFIX_OLD)
                ) and fn.endswith(".json"):
                    paths.append(os.path.join(self._cache_dir, fn))
        except OSError:
            pass
        paths.append(os.path.join(self._cache_dir, LEGACY_CACHE_FILENAME))

        seen = set()
        for path in paths:
            if path in seen:
                continue
            seen.add(path)
            raw = self._read_file(path)
            if not raw:
                continue
            cache = cache_upgrade(raw)
            if not isinstance(cache, dict):
                continue
            if card_no and cache.get("card_no") == card_no:
                return cache
            if cache.get("account") == self.api.username:
                return cache
        return None

    @staticmethod
    def _comparable(cache):
        """比对用: 忽略"抓取时间"字段 (否则每轮都会判定为有变化)。"""
        return cache_dumps({k: v for k, v in (cache or {}).items() if k != "date"})

    def _save_cache_if_changed(self, cache):
        """与本地文件比对, 内容有变化才写入 (原子替换, 缩进美化便于阅读)。"""
        card_no = cache.get("card_no")
        if not card_no:
            return False
        path = self._cache_path_for(card_no)
        old = self._read_file(path)
        if old is not None:
            try:
                if self._comparable(cache_upgrade(old)) == self._comparable(cache):
                    return False
            except Exception:  # 旧文件损坏等情况 -> 直接覆盖
                pass
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2, default=str)
            f.write("\n")
        os.replace(tmp, path)
        return True

    async def _async_update_data(self):
        try:
            online = await self.hass.async_add_executor_job(self.api.fetch_all)
            if not online.get("card_no") or not online.get("usage_list"):
                # 抓取"成功"但数据不完整 (如户号解密偶发失败) -> 视为本次失败,
                # 回退到内存/本地缓存, 保证界面始终有数据可用
                raise UpdateFailed("本次抓取数据不完整 (未获取到户号或用量明细)")
            # 与本地缓存合并 (在线只给最近一年, 历史月份/缴费记录保留在缓存里)
            old = await self.hass.async_add_executor_job(
                self._read_cache, online.get("card_no"))
            cache = build_cache(online, old)
            try:
                await self.hass.async_add_executor_job(self._save_cache_if_changed, cache)
            except Exception as err:
                _LOGGER.warning("写入本地缓存失败: %s", err)
            data = cache_to_data(cache)
            data["from_cache"] = False
            data["available"] = True
            # 真实刷新时间 (区别于 updated_at=数据内容变化时间):
            # 每次成功登录抓取都会更新, 用于判断"自动刷新是否在跑"
            data["last_refresh"] = dt_util.now().strftime("%Y-%m-%d %H:%M:%S")
            data["next_refresh"] = self._next_refresh_str()
            data["ladder_prices"] = list(self.tier_prices)
            return data
        except Exception as err:
            # 内存里还有上一次的数据 -> 沿用 (界面不断档)
            if self.data:
                _LOGGER.warning("重庆水务更新失败, 沿用上一次数据: %s", err)
                data = dict(self.data)
                data["from_cache"] = True
                data["available"] = False
                data["next_refresh"] = self._next_refresh_str()
                data["ladder_prices"] = list(self.tier_prices)
                return data
            # 冷启动 (如 HA 重启后登录失败) -> 读本地缓存 (按户号文件)
            cached = await self.hass.async_add_executor_job(self._read_cache)
            if cached:
                _LOGGER.warning("重庆水务更新失败, 使用本地缓存数据: %s", err)
                data = cache_to_data(cached)
                data["from_cache"] = True
                data["available"] = False
                data["next_refresh"] = self._next_refresh_str()
                data["ladder_prices"] = list(self.tier_prices)
                return data
            raise UpdateFailed(f"重庆水务数据更新失败且无本地缓存: {err}")


class CqSwMeterSensor(CoordinatorEntity, SensorEntity):
    """主实体: 当前读数, 并承载全部属性。"""

    _attr_device_class = SensorDeviceClass.VOLUME
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS

    def __init__(self, coordinator, device: DeviceInfo, entry: ConfigEntry, unique_id):
        super().__init__(coordinator)
        self._device = device
        self._entry = entry
        self._attr_unique_id = unique_id
        self._attr_name = "最新读数"

    @property
    def device_info(self):
        return self._device

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("reading")

    @property
    def available(self):
        # 只要有数据(含本地缓存)就可用, 缓存期间由"登录状态/数据来源"实体区分
        return bool(self.coordinator.data)

    @property
    def extra_state_attributes(self):
        d = self.coordinator.data or {}
        attrs = {}
        for k in (
            "customer_name", "customer_addr", "water_org", "customer_type", "card_no",
            "balance", "account", "current_fee", "current_usage", "last_usage",
            "tier_usage", "last_reading", "begin_scale", "end_scale", "total_fee",
            "year_total_fee",
            "now_ladder", "ladder", "last_pay_time", "last_pay_amount", "updated_at",
            "last_month", "statistics",
            "usage_list", "payment_list", "current_fee_items", "fee_items_by_month",
        ):
            if k in d and d[k] is not None:
                attrs[k] = d[k]
        if d.get("last_refresh"):
            attrs["最后刷新"] = d["last_refresh"]
        if d.get("next_refresh"):
            attrs["下次刷新"] = d["next_refresh"]
        if d.get("ladder_prices"):
            # 英文键供卡片读取, 中文键便于在开发者工具里查看
            attrs["ladder_prices"] = list(d["ladder_prices"])
            attrs["阶梯单价"] = list(d["ladder_prices"])
        if "from_cache" in d:
            attrs["数据来源"] = "本地缓存" if d["from_cache"] else "在线获取"
        return attrs


class CqSwMetricSensor(CoordinatorEntity, SensorEntity):
    """衍生指标实体。"""

    def __init__(self, coordinator, device, key, name, unique,
                 device_class=None, unit=None, state_class=None, icon=None):
        super().__init__(coordinator)
        self._device = device
        self._key = key
        self._attr_unique_id = unique
        self._attr_name = name
        if device_class:
            self._attr_device_class = device_class
        if unit:
            self._attr_native_unit_of_measurement = unit
        if state_class:
            self._attr_state_class = state_class
        if icon:
            self._attr_icon = icon

    @property
    def device_info(self):
        return self._device

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(self._key)

    @property
    def available(self):
        return bool(self.coordinator.data)


class CqSwPayStatusSensor(CoordinatorEntity, SensorEntity):
    """缴费情况: 本期账单的缴费状态 (已缴费 / 待缴费等)。"""

    _attr_icon = "mdi:cash-check"

    def __init__(self, coordinator, device: DeviceInfo, unique_id):
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = unique_id
        self._attr_name = "缴费情况"

    @property
    def device_info(self):
        return self._device

    @property
    def native_value(self):
        ul = (self.coordinator.data or {}).get("usage_list") or []
        return ul[0].get("status") if ul and isinstance(ul[0], dict) else None

    @property
    def available(self):
        return bool(self.coordinator.data)


class CqSwLastRefreshSensor(CoordinatorEntity, SensorEntity):
    """最近刷新时间: 每次成功登录抓取都会更新 (时间戳实体)。

    状态随每次刷新变化, 因此 HA 的"活动"里会像国网集成一样留下
    "最近刷新时间 -> xxx" 的记录, 便于确认自动登录是否在正常运行。
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator, device: DeviceInfo, unique_id):
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = unique_id
        self._attr_name = "最近刷新时间"

    @property
    def device_info(self):
        return self._device

    @property
    def native_value(self):
        s = (self.coordinator.data or {}).get("last_refresh")
        if not s:
            return None
        dt = dt_util.parse_datetime(s)
        if dt is None:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)

    @property
    def available(self):
        return bool(self.coordinator.data)


class CqSwStatusSensor(CoordinatorEntity, SensorEntity):
    """登录状态: 在线 / 离线 (离线=本次数据来自缓存, 非实时)。"""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:lan-disconnect"

    def __init__(self, coordinator, device, entry, unique_id):
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = unique_id
        self._attr_name = "登录状态"

    @property
    def device_info(self):
        return self._device

    @property
    def available(self):
        # 始终可用, 用状态值表达在线/离线, 避免"实体不可用但状态仍是ok"的矛盾
        return self.coordinator.data is not None or self.coordinator.last_update_success

    @property
    def native_value(self):
        d = self.coordinator.data or {}
        return "在线" if d.get("available") else "离线"

    @property
    def icon(self):
        d = self.coordinator.data or {}
        return "mdi:lan-connect" if d.get("available") else "mdi:lan-disconnect"

    @property
    def extra_state_attributes(self):
        d = self.coordinator.data or {}
        attrs = {"刷新间隔(小时)": self.coordinator.interval_hours}
        if d.get("last_refresh"):
            attrs["最后刷新"] = d["last_refresh"]
        if d.get("next_refresh"):
            attrs["下次刷新"] = d["next_refresh"]
        return attrs


class CqSwSourceSensor(CoordinatorEntity, SensorEntity):
    """数据来源: 在线获取 / 本地缓存。"""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:database-outline"

    def __init__(self, coordinator, device, entry, unique_id):
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = unique_id
        self._attr_name = "数据来源"

    @property
    def device_info(self):
        return self._device

    @property
    def available(self):
        return self.coordinator.data is not None or self.coordinator.last_update_success

    @property
    def native_value(self):
        d = self.coordinator.data or {}
        if not d:
            return "未知"
        return "本地缓存" if d.get("from_cache") else "在线获取"

    @property
    def icon(self):
        d = self.coordinator.data or {}
        if d and d.get("from_cache"):
            return "mdi:database-outline"
        return "mdi:cloud-check-outline"

    @property
    def extra_state_attributes(self):
        d = self.coordinator.data or {}
        attrs = {"刷新间隔(小时)": self.coordinator.interval_hours}
        if d.get("last_refresh"):
            attrs["最后刷新"] = d["last_refresh"]
        if d.get("next_refresh"):
            attrs["下次刷新"] = d["next_refresh"]
        return attrs
