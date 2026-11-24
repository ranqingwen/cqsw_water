"""配置流: 用户在 UI 中填写掌上水务账号密码与自动刷新间隔。支持多账户。"""

import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .const import (
    CONF_PASSWORD as C_PASSWORD,
)
from .const import (
    CONF_SCAN_INTERVAL,
    CONF_TIER_PRICES,
    CONF_USERNAME as C_USERNAME,
    DEFAULT_NAME,
    DEFAULT_SCAN_HOURS,
    DEFAULT_TIER_PRICES,
    DOMAIN,
)
from .api import CqSwApi, CqSwLoginError

_LOGGER = logging.getLogger(__name__)

TIER_KEYS = ["tier1_price", "tier2_price", "tier3_price"]


def _tier_values(user_input, defaults=None):
    """把三个单价字段整理成 [一档, 二档, 三档], 留空/未填用默认值。"""
    base = list(defaults or DEFAULT_TIER_PRICES)
    out = []
    for i, key in enumerate(TIER_KEYS):
        v = user_input.get(key) if user_input else None
        out.append(float(v) if v not in (None, "") else base[i])
    return out


def _build_schema(defaults=None):
    defaults = defaults or {}
    price_defaults = [
        defaults.get("tier1_price", DEFAULT_TIER_PRICES[0]),
        defaults.get("tier2_price", DEFAULT_TIER_PRICES[1]),
        defaults.get("tier3_price", DEFAULT_TIER_PRICES[2]),
    ]
    schema = {
        vol.Required(C_USERNAME, default=defaults.get(C_USERNAME, "")): str,
        vol.Required(CONF_PASSWORD, default=defaults.get(CONF_PASSWORD, "")): str,
        vol.Required(
            CONF_SCAN_INTERVAL,
            default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_HOURS),
        ): vol.All(vol.Coerce(int), vol.Range(min=12, max=48)),
    }
    for i, key in enumerate(TIER_KEYS):
        schema[vol.Optional(key, default=price_defaults[i])] = NumberSelector(
            NumberSelectorConfig(
                min=0, max=100, step=0.01,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement="元/m³",
            )
        )
    return vol.Schema(schema)


async def validate_auth(hass, username, password):
    """尝试登录以校验账号密码。返回 (ok, error_key_or_None)。

    - 明确登录失败(如密码错误) -> (False, "invalid_auth")
    - 其他异常(网络/接口波动)   -> (True, None) 仍允许继续(由协调器后续重试)
    """
    api = CqSwApi(username, password)
    try:
        await hass.async_add_executor_job(api.login)
        return True, None
    except CqSwLoginError:
        return False, "invalid_auth"
    except Exception as err:  # 网络/接口异常, 不阻断添加
        _LOGGER.warning("添加集成时登录校验异常(已放行): %s", err)
        return True, None


class CqSwConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """重庆水务配置流 (同一 HA 可添加多个不同账号)。"""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            ok, err = await validate_auth(
                self.hass, user_input[C_USERNAME], user_input[CONF_PASSWORD]
            )
            if not ok and err == "invalid_auth":
                errors["base"] = "invalid_auth"
            else:
                # 每个账号一个条目: 同一账号不允许重复添加, 不同账号可以
                await self.async_set_unique_id(
                    f"{DOMAIN}_{user_input[C_USERNAME]}"
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=DEFAULT_NAME,
                    data={
                        C_USERNAME: user_input[C_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL],
                        CONF_TIER_PRICES: _tier_values(user_input),
                    },
                )
        return self.async_show_form(
            step_id="user", data_schema=_build_schema(), errors=errors
        )

    async def async_step_reconfigure(self, user_input=None):
        """重新配置账号密码与刷新间隔。"""
        errors = {}
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            ok, err = await validate_auth(
                self.hass, user_input[C_USERNAME], user_input[CONF_PASSWORD]
            )
            if not ok and err == "invalid_auth":
                errors["base"] = "invalid_auth"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data={
                        C_USERNAME: user_input[C_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL],
                        CONF_TIER_PRICES: _tier_values(user_input),
                    },
                )
        cur = dict(entry.data) if user_input is None else {}
        if cur:
            tp = cur.get(CONF_TIER_PRICES) or []
            for i, key in enumerate(TIER_KEYS):
                v = tp[i] if i < len(tp) and tp[i] is not None else DEFAULT_TIER_PRICES[i]
                cur.setdefault(key, v)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_build_schema(cur),
            errors=errors,
        )
