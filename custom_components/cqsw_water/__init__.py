"""重庆水务集成入口 (支持多账户, 每个账户一个配置条目)。

卡片 JS 放在集成目录的 www/ 子目录里, 通过 HA 的静态路径
`/cqsw_water/www/...` 直接对外提供, 不需要再复制到 /config/www:
Lovelace 资源地址填  /cqsw_water/www/cqsw-water-card.js
集成每次启动/重载会自动把资源 URL 更新为 ...?v=<版本-文件哈希>,
覆盖新文件后浏览器会自动加载新版本 (无需手动改资源或强刷)。
"""

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import UpdateFailed

from .api import CqSwApi
from .const import CONF_PASSWORD, CONF_USERNAME, DOMAIN
from .sensor import CqSwDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

CARD_URL_PATH = f"/{DOMAIN}/www"
CARD_JS_PATH = Path(__file__).parent / "www" / "cqsw-water-card.js"
CARD_JS_BASE = f"{CARD_URL_PATH}/cqsw-water-card.js"


def _card_versioned_url() -> str:
    """卡片资源带版本参数: ?v=<manifest版本>-<js内容哈希前8位>。"""
    try:
        version = json.loads(
            (Path(__file__).parent / "manifest.json").read_text(encoding="utf-8")
        ).get("version", "dev")
    except (OSError, ValueError):
        version = "dev"
    try:
        digest = hashlib.sha256(CARD_JS_PATH.read_bytes()).hexdigest()[:8]
    except OSError:
        digest = "0" * 8
    return f"{CARD_JS_BASE}?v={version}-{digest}"


def _lovelace_resources(lovelace: Any) -> Any:
    """兼容不同 HA 版本里 hass.data["lovelace"] 的结构。"""
    if lovelace is None:
        return None
    if isinstance(lovelace, dict):
        if lovelace.get("resources") is not None:
            return lovelace["resources"]
        for dashboard in (lovelace.get("dashboards") or {}).values():
            resources = getattr(dashboard, "resources", None)
            if resources is not None:
                return resources
        return None
    return getattr(lovelace, "resources", None)


async def _async_register_lovelace_resource(hass: HomeAssistant, url: str) -> bool:
    """把卡片资源写进 Lovelace 资源列表; 失败返回 False。

    匹配按去掉 ?v= 后的基础 URL 比对, 用户手动添加的旧资源会被自动升级。
    """
    try:
        resources = _lovelace_resources(hass.data.get("lovelace"))
        if resources is None or not hasattr(resources, "async_create_item"):
            return False
        # 先把资源存储从磁盘载入内存, 否则 async_items() 可能返回空列表
        get_info = getattr(resources, "async_get_info", None)
        if get_info is not None:
            result = get_info()
            if hasattr(result, "__await__"):
                await result
        for item in resources.async_items():
            if str(item.get("url", "")).split("?")[0] != CARD_JS_BASE:
                continue
            if item.get("url") != url:
                await resources.async_update_item(
                    item["id"], {"url": url, "res_type": "module"}
                )
                _LOGGER.info("重庆水务卡片资源已更新为: %s", url)
            return True
        await resources.async_create_item({"res_type": "module", "url": url})
        _LOGGER.info("已自动添加 Lovelace 资源: %s", url)
        return True
    except Exception as err:  # noqa: BLE001 - 资源写入失败不影响集成运行
        _LOGGER.debug("自动写入 Lovelace 资源失败: %s", err)
        return False


@callback
def _add_extra_js_url(hass: HomeAssistant, url: str) -> None:
    """Lovelace 不可写 (YAML 模式等) 时, 通过 frontend 全局注入兜底。"""
    try:
        from homeassistant.components.frontend import add_extra_js_url

        add_extra_js_url(hass, url)
        _LOGGER.info("已通过 frontend 注入卡片资源: %s", url)
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning(
            "卡片资源未能自动加载, 请手动添加资源 %s (类型: JavaScript 模块): %s",
            url, err,
        )


async def _async_setup_card(hass: HomeAssistant) -> None:
    """注册卡片静态路径 + 同步 Lovelace 资源 (多账户只做一次)。"""
    store = hass.data.setdefault(DOMAIN, {})
    if store.get("_card_ready"):
        return
    store["_card_ready"] = True

    await hass.http.async_register_static_paths([
        StaticPathConfig(
            CARD_URL_PATH,
            str(Path(__file__).parent / "www"),
            cache_headers=False,
        )
    ])
    url = _card_versioned_url()

    async def _register(_event: Any = None) -> None:
        if await _async_register_lovelace_resource(hass, url):
            return
        # 资源是 YAML 模式、或 lovelace 还没就绪时, 退回全局注入
        _add_extra_js_url(hass, url)

    if hass.is_running:
        await _register()
    else:
        # HA 启动中: 等 lovelace/frontend 都就绪后再同步
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _register)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # 卡片静态路径 + Lovelace 资源同步只需一次 (多账户共用)
    await _async_setup_card(hass)

    api = CqSwApi(entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
    coordinator = CqSwDataUpdateCoordinator(hass, api, entry)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # 首次刷新放后台: 登录+拉账单是串行网络请求 (常见 10~20s),
    # 若在启动流程里等待会拖慢 HA 启动 (集成启动耗时表里 15s+ 的来源)。
    # 改为后台任务后集成秒完成加载, 实体先短暂显示不可用, 刷完自动恢复。
    async def _first_refresh() -> None:
        try:
            await coordinator.async_config_entry_first_refresh()
        except Exception as err:  # noqa: BLE001 - 失败不中断启动, 周期任务会自动重试
            _LOGGER.warning("重庆水务首次刷新失败(将自动重试): %s", err)

    entry.async_create_background_task(
        hass,
        _first_refresh(),
        f"{DOMAIN}_first_refresh_{entry.entry_id}",
        eager_start=True,
    )

    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor"])
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
