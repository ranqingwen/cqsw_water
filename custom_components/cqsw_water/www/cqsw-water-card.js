// 重庆水务掌上水务 - 自定义 Lovelace 卡片  v17
// 用法: 集成会自动托管本文件 (无需复制到 /config/www), 在 Lovelace 资源中添加
//       /cqsw_water/www/cqsw-water-card.js (类型: module), 然后卡片类型:
//       type: custom:cqsw-water-card
//       entity: 指向集成主实体 (如 sensor.shui_biao_du_shu)
//       (实体 ID 不确定也没关系, 卡片会自动查找集成主实体)
// 可选配置: hide: ["ladder", "cumulative", ...] 关掉首页某一块 (默认全显示);
//       全部勾选时代码里没有任何显示开关, 和燃气卡一致保持干净
// 旧写法 show_total_fee: false 等仍有效 (等价于 hide 里有对应 key)
// 右上角累计费用口径: cum_scope: year = 本年累计 (默认全部)
// lit 库三重兜底: HA 内置 -> jsdelivr -> unpkg, 任何一个可用即可
let __lit;
try {
  __lit = await import("lit");
} catch (e1) {
  try {
    __lit = await import("https://cdn.jsdelivr.net/npm/lit@2.7.0/index.js?module");
  } catch (e2) {
    __lit = await import("https://unpkg.com/lit@2.7.0/index.js?module");
  }
}
const { LitElement, html, svg, css } = __lit;

const BLUE = "#1e88e5";
const ORANGE = "#f5a623";

// 首页可以被单独关掉的内容块: key 存进 config.hide, 默认全部显示。
// 卡片编辑器的勾选框和卡片渲染共用这一份清单, 改一处两边同步。
const VIS_ITEMS = [
  ["cumulative", "累计费用"],
  ["prev_reading", "上期表读数"],
  ["reading", "本期表读数"],
  ["usage", "本期用水量"],
  ["ladder", "阶梯用量"],
  ["tier_prices", "阶梯单价"],
  ["tier_status", "当前档位"],
  ["name", "户名"],
  ["address", "用水地址"],
  ["last_pay", "最近缴费"],
];

// 旧版 show_xxx 开关 -> 新 hide key 的映射 (老配置仍然生效)
const LEGACY_SHOW_KEYS = {
  cumulative: "show_total_fee",
  prev_reading: "show_last_reading",
  reading: "show_cur_reading",
  usage: "show_usage",
  ladder: "show_ladder",
  name: "show_name",
  address: "show_addr",
  last_pay: "show_pay",
};

class CqswWaterCard extends LitElement {
  static get properties() {
    return {
      hass: { attribute: false },
      config: { attribute: false },
      _view: { state: true },
      _open: { state: true },
    };
  }

  setConfig(config) {
    // entity 未填时卡片会自动查找集成主实体, 不再强制报错
    this.config = config || {};
    this._view = "main";
  }

  // 可视化编辑器: 选择实体(户号) + 勾选显示项
  static async getConfigElement() {
    try { await (window.loadCardHelpers && window.loadCardHelpers()); } catch (e) { /* ignore */ }
    return document.createElement("cqsw-water-card-editor");
  }

  static getStubConfig(hass) {
    let entity = "";
    if (hass && hass.states) {
      for (const eid of Object.keys(hass.states)) {
        const s = hass.states[eid];
        if (s && s.attributes && "usage_list" in s.attributes && "customer_name" in s.attributes) {
          entity = eid;
          break;
        }
      }
    }
    return { entity };
  }

  getCardSize() {
    return 9;
  }

  static get styles() {
    return css`
      :host { --cw-blue: #1677d2; --cw-blue-deep: #0b66c3; }
      .card {
        background: var(--card-background-color, #fff);
        border-radius: 16px; overflow: hidden;
        box-shadow: var(--ha-card-box-shadow, 0 2px 8px rgba(0,0,0,.12));
        font-family: var(--paper-font-body1_-_font-family, sans-serif);
        color: var(--primary-text-color, #222);
      }
      /* ===== 头部 ===== */
      .hd {
        display: flex; align-items: center; gap: 12px; padding: 14px 16px;
      }
      .hd .logo {
        width: 44px; height: 44px; border-radius: 12px; flex: none;
        background: linear-gradient(135deg, #42a5f4, var(--cw-blue-deep));
        display: flex; align-items: center; justify-content: center;
        font-size: 24px; color: #fff;
      }
      .hd .t { flex: 1; min-width: 0; }
      .hd .t .n { font-size: 17px; font-weight: 700; }
      .hd .t .s { font-size: 12px; color: var(--secondary-text-color, #888); margin-top: 2px; }
      .hd .badge {
        flex: none; font-size: 12px; border-radius: 999px; padding: 4px 12px;
      }
      .badge.ok { background: rgba(52,168,83,.15); color: #2e9e57; }
      .badge.bad { background: rgba(229,57,53,.15); color: #e53935; }
      /* ===== 大数字区 ===== */
      .balance { padding: 2px 16px 12px; }
      .balance .lab { font-size: 13px; color: var(--secondary-text-color, #888); }
      .balance .top { display: flex; align-items: flex-end; gap: 10px; }
      .balance .big { font-size: 30px; font-weight: 700; margin-top: 2px; }
      .balance .big .u { font-size: 14px; font-weight: 500; color: var(--secondary-text-color,#888); margin-left: 3px; }
      .balance .top .cum { margin-left: auto; font-size: 18px; font-weight: 600; line-height: 1.1; color: var(--primary-text-color, #333); white-space: nowrap; text-align: right; }
      .balance .top .cum i { display: block; font-size: 11px; font-weight: 400; font-style: normal; color: var(--secondary-text-color, #888); margin-bottom: 2px; letter-spacing: .2px; }
      .balance .sub { font-size: 12px; color: var(--secondary-text-color, #888); margin-top: 4px; }
      .balance .sub b { color: var(--primary-text-color, #333); font-weight: 600; }
      /* 结清状态小标签 */
      .tag {
        display: inline-block; font-size: 11px; font-weight: 600;
        border-radius: 999px; padding: 2px 8px; margin-left: 4px;
      }
      .tag.ok { background: rgba(52,168,83,.15); color: #2e9e57; }
      .tag.wait { background: rgba(245,166,35,.18); color: #b06f00; }
      .balance .top .tag { margin-left: 0; margin-bottom: 6px; }
      /* ===== 磁贴 ===== */
      .tiles { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; padding: 2px 16px 12px; }
      .tiles.three { grid-template-columns: repeat(3, 1fr); }
      .tiles.three .tile { padding: 10px 10px; }
      .tiles.three .tile .v { font-size: 17px; }
      .tile {
        background: rgba(127,127,127,.08);
        border-radius: 12px; padding: 10px 12px;
      }
      .tile .k { font-size: 12px; color: var(--secondary-text-color, #888); }
      .tile .v { font-size: 19px; font-weight: 700; margin-top: 3px; }
      .tile .v .u { font-size: 12px; font-weight: 400; color: var(--secondary-text-color,#888); margin-left: 2px; }
      /* ===== 阶梯 ===== */
      .ladder { margin: 2px 16px 12px; background: rgba(127,127,127,.08); border-radius: 12px; padding: 12px; }
      .ladder .row1 { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; font-size: 13px; font-weight: 600; }
      .ladder .row1 .left { color: var(--primary-text-color); flex: none; }
      .ladder .bar { display: flex; gap: 3px; margin-top: 10px; }
      .ladder .bar .seg {
        flex: 1; position: relative; height: 24px; border-radius: 7px; overflow: hidden;
        display: flex; align-items: center; justify-content: center;
        background: rgba(127,127,127,.14);
      }
      .ladder .bar .fill { position: absolute; left: 0; top: 0; bottom: 0; background: linear-gradient(90deg,#42a5f4,var(--cw-blue-deep)); opacity: .26; }
      .ladder .bar .txt { position: relative; text-align: center; line-height: 1.45; padding: 0 3px; }
      .ladder .bar .txt b { display: block; font-size: 10px; font-weight: 500; color: var(--secondary-text-color,#888); white-space: nowrap; }
      .ladder .prices { display: flex; gap: 3px; margin-top: 6px; }
      .ladder .prices .p { flex: 1; text-align: center; font-size: 10px; color: var(--secondary-text-color,#888); white-space: nowrap; }
      .ladder .note { font-size: 11px; color: var(--secondary-text-color,#999); margin-top: 10px; }
      /* ===== 明细行 ===== */
      .info { padding: 0 16px; }
      .info .row {
        display: flex; justify-content: space-between; align-items: center; font-size: 13px;
        padding: 8px 0; border-bottom: 1px solid var(--divider-color, #f0f0f0);
      }
      .info .row:last-child { border-bottom: none; }
      .info .row .k { color: var(--secondary-text-color, #888); flex: none; }
      .info .row .v { text-align: right; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 62%; }
      /* ===== 按钮区 ===== */
      .actions { display: flex; gap: 10px; padding: 14px 16px 6px; }
      .btn {
        flex: 1; display: flex; align-items: center; justify-content: center; gap: 6px;
        border: none; border-radius: 12px; padding: 11px 0;
        font-size: 14px; font-weight: 600; cursor: pointer; transition: opacity .15s, transform .1s;
      }
      .btn:active { transform: scale(.98); }
      .btn.pri { background: rgba(22,119,210,.32); color: #dbeeff; border: 1.5px solid rgba(22,119,210,.55); }
      .btn.gho { background: rgba(22,119,210,.32); color: #dbeeff; border: 1.5px solid rgba(22,119,210,.55); }
      /* ===== 页脚 ===== */
      .ft {
        display: flex; justify-content: space-between; align-items: center;
        padding: 10px 16px 14px; font-size: 11px; color: var(--secondary-text-color, #999);
      }
      .ft .rf {
        border: 1px solid var(--divider-color,#ddd); border-radius: 999px; background: transparent;
        color: var(--secondary-text-color,#888); font-size: 11px; padding: 4px 12px; cursor: pointer;
      }
      .ft .rf:hover { color: var(--cw-blue); border-color: var(--cw-blue); }
      /* ===== 子页面 ===== */
      .sub .title {
        font-size: 16px; font-weight: 700; padding: 14px 16px 4px;
        display: flex; justify-content: space-between; align-items: center;
      }
      .back { font-size: 13px; color: var(--cw-blue); cursor: pointer; background: none; border: none; }
      .panel { margin: 8px 16px; background: rgba(127,127,127,.08); border-radius: 12px; padding: 12px; }
      .panel .pt { font-size: 14px; font-weight: 600; margin-bottom: 8px; }
      .panel .pu { font-size: 11px; color: var(--secondary-text-color,#999); font-weight: 400; margin-left: 4px; }
      svg text { font-family: sans-serif; }
      .empty { text-align: center; color: var(--secondary-text-color,#999); padding: 20px; font-size: 13px; }
      table { width: 100%; border-collapse: collapse; font-size: 12px; }
      th, td { padding: 7px 6px; text-align: center; }
      th { color: var(--secondary-text-color,#888); font-weight: 500; border-bottom: 1px solid var(--divider-color,#e5e5e5); }
      td { border-bottom: 1px solid var(--divider-color,#f2f2f2); }
      tr:last-child td { border-bottom: none; }
      td.tl, th.tl { text-align: left; }
      td.tr, th.tr { text-align: right; }
      .paid { color: #2e9e57; }
      .unpaid { color: #e53935; }
      .amt-cell { color: var(--cw-blue); font-weight: 700; white-space: nowrap; }
      /* 缴费记录表: 金额列向左收进, 方式列左对齐 */
      .paytable th, .paytable td { padding: 7px 6px; }
      .paytable .amt-cell, .paytable th.amt-th { padding-right: 30px; }
      .paytable .meth { text-align: left; }
      /* 缴费渠道小徽标 */
      .ch {
        display: inline-flex; align-items: center; justify-content: center;
        width: 18px; height: 18px; border-radius: 4px; font-size: 11px;
        color: #fff; flex: none;
      }
      .chwrap { display: inline-flex; align-items: center; gap: 5px; vertical-align: middle; }
      .ch.wx { background: #09bb07; }
      .ch.alipay { background: #1677ff; }
      .ch.bank { background: #5b7a9d; }
      .ch.cash { background: #c0392b; }
      .ch.other { background: #8e8e93; }
      /* 缴费记录行 (费用构成展开里仍在用) */
      .payrow { display: flex; align-items: center; gap: 10px; padding: 9px 0; border-bottom: 1px solid var(--divider-color,#f2f2f2); }
      .payrow:last-child { border-bottom: none; }
      .payrow .ic {
        width: 34px; height: 34px; border-radius: 10px; flex: none;
        background: rgba(30,136,229,.12); display: flex; align-items: center; justify-content: center; font-size: 16px;
      }
      .payrow .m { flex: 1; min-width: 0; }
      .payrow .m .a { font-size: 13px; font-weight: 600; }
      .payrow .m .b { font-size: 11px; color: var(--secondary-text-color,#888); margin-top: 1px; }
      .payrow .amt { font-size: 15px; font-weight: 700; color: var(--cw-blue); flex: none; }
      /* ===== 月份手风琴 (费用构成) ===== */
      tr.mrow { cursor: pointer; }
      tr.mrow:hover td { background: rgba(127,127,127,.12); }
      tr.mrow .chev { float: right; color: var(--secondary-text-color, #888); font-weight: 400; }
      tr.xrow td {
        background: rgba(127,127,127,.14);
        padding: 8px 12px 10px; text-align: left;
      }
      .xrow .payrow { padding: 7px 0; }
      .xrow .payrow .ic { width: 28px; height: 28px; font-size: 13px; border-radius: 8px; }
      .xrow .payrow .m .a { font-size: 12px; }
      .xrow .payrow .amt { font-size: 13px; }
      .xrow .xsum { text-align: right; font-size: 12px; font-weight: 700; padding-top: 4px; }
    `;
  }

  // ============ 数据访问 ============
  _state() {
    if (!this.hass || !this.hass.states) return undefined;
    let st = this.hass.states[this.config.entity];
    if (st) return st;
    for (const eid of Object.keys(this.hass.states)) {
      const s = this.hass.states[eid];
      if (s && s.attributes && "usage_list" in s.attributes && "customer_name" in s.attributes) {
        return s;
      }
    }
    return undefined;
  }

  _attr(name) {
    const s = this._state();
    return s && s.attributes ? s.attributes[name] : undefined;
  }

  _fmt(v, unit) {
    if (v === undefined || v === null || v === "") return "-";
    return unit ? `${v} ${unit}` : `${v}`;
  }

  _money(v) {
    if (v === undefined || v === null || v === "") return "-";
    const n = Number(v);
    return isNaN(n) ? `${v}` : n.toFixed(2);
  }

  // 单价按实际小数位显示: 3.5 就是 3.5, 不强行补零
  _price(v) {
    if (v === undefined || v === null || v === "") return "-";
    const n = Number(v);
    if (!isFinite(n)) return `${v}`;
    return String(parseFloat(n.toFixed(4)));
  }

  // 本期费用构成摘要: "自来水费 ¥45.00 + 污水处理费 ¥18.00"
  _feeBreakdown() {
    const items = this._attr("current_fee_items") || [];
    if (!items.length) return "";
    const map = new Map();
    for (const f of items) {
      const k = String(f.item || "水费");
      map.set(k, (map.get(k) || 0) + (Number(f.money) || 0));
    }
    return [...map.entries()]
      .filter(([, v]) => Number(v) > 0)
      .map(([k, v]) => `${k} ¥${this._money(v)}`)
      .join(" + ");
  }

  // 各阶梯单价取值优先级:
  //   1. 卡片配置 ladder_prices: [一, 二, 三]
  //   2. 集成配置的阶梯单价 (主实体属性 ladder_prices, 添加集成时填写,
  //      默认为重庆中心城区居民价, 仅供参考)
  //   3. 从费用构成推算 (自来水阶梯价 + 每方固定附加);
  //      接口只返回"用过的档位", 缺的档位从历史月份费用构成里补,
  //      仍缺则显示 "单价 -"
  _tierPrices() {
    const read3 = (arr) => {
      if (!Array.isArray(arr)) return null;
      const a = [null, null, null];
      for (let i = 0; i < 3 && i < arr.length; i++) {
        const v = Number(arr[i]);
        if (!isNaN(v)) a[i] = v;
      }
      return a.some(v => v !== null) ? a : null;
    };
    const cfg = read3((this.config || {}).ladder_prices);
    if (cfg) return cfg;
    const fromAttr = read3(this._attr("ladder_prices"));
    if (fromAttr) return fromAttr;
    const cn = ["一", "二", "三"];
    const scan = (items) => {
      const out = [null, null, null];
      let extra = 0;
      for (const f of items || []) {
        if (f.price === undefined || f.price === null) continue;
        const nm = String(f.name || "");
        const kind = String(f.item || "");
        const txt = nm.indexOf("阶") >= 0 ? nm : kind;
        let idx = -1;
        for (let i = 0; i < 3; i++) {
          if (txt.indexOf("第" + cn[i]) >= 0 || txt.indexOf(cn[i] + "阶") >= 0) { idx = i; break; }
        }
        if (idx < 0) {
          // 无阶梯标记 -> 按方计的固定附加费
          if (kind.indexOf("自来") >= 0) continue;
          extra += Number(f.price) || 0;
        } else if (out[idx] === null) {
          out[idx] = Number(f.price);
        }
      }
      return { out, extra };
    };
    const cur = scan(this._attr("current_fee_items") || []);
    const out = cur.out.slice();
    if (out.filter(v => v !== null).length < 3) {
      const byMonth = this._attr("fee_items_by_month") || {};
      for (const arr of Object.values(byMonth)) {
        const r = scan(arr);
        for (let i = 0; i < 3; i++) if (out[i] === null && r.out[i] !== null) out[i] = r.out[i];
      }
    }
    if (out.every(v => v === null)) return null;
    return out.map(v => v === null ? null : v + cur.extra);
  }

  _connected() {
    const st = this._state();
    return !!(st && st.state !== "unavailable" && st.state !== "unknown");
  }

  // 缴费渠道小徽标 (微信=绿色气泡图标, 支付宝/银行/现金各有标识)
  _channelBadge(channel) {
    const c = String(channel || "");
    if (c.includes("微信")) {
      return html`<span class="ch wx"><svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true">
        <path fill="#fff" d="M9.2 3.5C5.3 3.5 2 6.1 2 9.4c0 1.9 1.1 3.5 2.8 4.6l-.8 2.4 2.8-1.5c.5.1 1 .2 1.6.2h.4c-.2-.6-.3-1.2-.3-1.8 0-3.1 2.9-5.6 6.5-5.6h.4C14.7 5.2 12.2 3.5 9.2 3.5z"/>
        <path fill="#fff" d="M22 13.4c0-2.7-2.6-4.9-5.8-4.9s-5.8 2.2-5.8 4.9 2.6 4.9 5.8 4.9c.7 0 1.3-.1 1.9-.3l2.4 1.3-.7-2.1C21.1 16.3 22 14.9 22 13.4z"/>
        <circle cx="6.8" cy="8.2" r="0.9" fill="#09bb07"/>
        <circle cx="11.4" cy="8.2" r="0.9" fill="#09bb07"/>
        <circle cx="14.6" cy="12.6" r="0.75" fill="#09bb07"/>
        <circle cx="17.8" cy="12.6" r="0.75" fill="#09bb07"/>
      </svg></span>`;
    }
    if (c.includes("支付宝")) return html`<span class="ch alipay">支</span>`;
    if (c.includes("银") || c.includes("卡")) return html`<span class="ch bank">卡</span>`;
    if (c.includes("现金")) return html`<span class="ch cash">¥</span>`;
    return html`<span class="ch other">付</span>`;
  }

  // 阶梯阈值: ladder 值形如 "0-260" / "261-360" / "361"
  _ladderBounds() {
    const l = this._attr("ladder") || {};
    const pick = (v) => {
      if (v === undefined || v === null) return null;
      const nums = String(v).match(/\d+(\.\d+)?/g);
      return nums ? Number(nums[nums.length - 1]) : null;
    };
    return { t1: pick(l.ladder1), t2: pick(l.ladder2), t3: pick(l.ladder3) };
  }

  _go(view) { this._view = view; }

  // 某一块要不要显示: 默认显示, config.hide 里点名了就隐藏;
  // 旧的 show_ladder: false 等写法等同于 hide 里有对应 key
  _show(key) {
    const cfg = this.config || {};
    const legacy = LEGACY_SHOW_KEYS[key];
    if (legacy && cfg[legacy] === false) return false;
    if (Array.isArray(cfg.hide) && cfg.hide.indexOf(key) >= 0) return false;
    return true;
  }

  _refresh() {
    if (!this.hass) return;
    const st = this._state();
    const eid = st ? st.entity_id : this.config.entity;
    this.hass.callService("homeassistant", "update_entity", { entity_id: eid });
  }

  // ============ 主视图 ============
  _renderMain() {
    const ok = this._connected();
    const type = this._attr("customer_type") || "掌上水务";
    const account = this._attr("account") || "";
    const fee = this._attr("current_fee");
    const usage = this._attr("current_usage");
    const endS = this._attr("end_scale");
    const beginS = this._attr("begin_scale");
    const lastRead = endS !== undefined && endS !== null ? endS : this._attr("reading");
    const prevRead = beginS !== undefined && beginS !== null ? beginS : this._attr("last_reading");
    const tier = this._attr("tier_usage");
    const lb = this._ladderBounds();
    const name = this._attr("customer_name") || "-";
    const cardNo = this._attr("card_no") || "-";
    const addr = this._attr("customer_addr") || "-";
    const balance = this._attr("balance");
    const lastPay = this._attr("last_pay_time");
    const lastPayAmt = this._attr("last_pay_amount");
    const totalFee = this._attr("total_fee");
    const updated = this._attr("last_refresh") || this._attr("updated_at") || "";
    const ul = this._attr("usage_list") || [];
    const month = ul[0] && ul[0].month ? String(ul[0].month).replace(/年|月/g, "-").replace(/-$/, "") : "-";
    const status = ul[0] ? ul[0].status : null;

    // 阶梯进度: 三段等宽 (各段内按各自区间比例填充)
    const t1 = lb.t1 || 260, t2 = lb.t2 || (t1 + 100), t3 = lb.t3 || (t2 + 140);
    const used = Number(tier) || 0;
    const fillPct = Math.min(used, t1) / t1 * 100;
    const fill2Pct = used > t1 ? Math.min(used - t1, t2 - t1) / (t2 - t1) * 100 : 0;
    const fill3Pct = used > t2 ? Math.min(used - t2, t3 - t2) / (t3 - t2) * 100 : 0;
    const remain = Math.max(t1 - used, 0);
    const tierIdx = Math.min(Math.max(Number(this._attr("now_ladder") || 1), 1), 3);
    const stages = [
      { name: "第一阶梯", range: `0-${t1}m³`, fill: fillPct },
      { name: "第二阶梯", range: `${t1 + 1}-${t2}m³`, fill: fill2Pct },
      { name: "第三阶梯", range: `${t2 + 1}m³及以上`, fill: fill3Pct },
    ];
    const tierPrices = this._tierPrices();
    const settled = status === "已缴费" ? true : (status ? false : null);
    const breakdown = this._feeBreakdown();
    const cfg = this.config || {};
    // 累计费用口径: year=本年累计, all=全部累计 (默认); cum_scope 为新写法,
    // 旧的 total_fee_mode: year 也兼容
    const cumYear = cfg.cum_scope === "year" || cfg.total_fee_mode === "year";
    const cumVal = cumYear ? this._attr("year_total_fee") : totalFee;
    const cumLabel = cumYear ? "本年累计" : "累计费用";

    return html`
      <div class="card">
        <div class="hd">
          <div class="logo">💧</div>
          <div class="t">
            <div class="n">重庆水务</div>
            <div class="s">${cardNo ? "户号 " + cardNo : (type || "")}</div>
          </div>
          <div class="badge ${ok ? "ok" : "bad"}">${ok ? "正常" : "异常"}</div>
        </div>

        <div class="balance">
          <div class="lab">本期账单${month && month !== "-" ? `(${month})` : ""}</div>
          <div class="top">
            <div class="big">¥${this._money(fee)}</div>
            ${settled === true ? html`<span class="tag ok">已结清</span>`
              : settled === false ? html`<span class="tag wait">待结清</span>` : ""}
            ${this._show("cumulative") && cumVal !== undefined && cumVal !== null
              ? html`<span class="cum"><i>${cumLabel}</i>¥${this._money(cumVal)}</span>` : ""}
          </div>
          <div class="sub">
            ${breakdown ? breakdown
              : (usage !== undefined && usage !== null ? html`本期用水 <b>${this._fmt(usage)} m³</b>` : "")}
          </div>
        </div>

        ${(() => {
          const tiles = [];
          if (this._show("prev_reading")) tiles.push(html`<div class="tile"><div class="k">上期表读数</div><div class="v">${this._fmt(prevRead)}<span class="u">m³</span></div></div>`);
          if (this._show("reading")) tiles.push(html`<div class="tile"><div class="k">本期表读数</div><div class="v">${this._fmt(lastRead)}<span class="u">m³</span></div></div>`);
          if (this._show("usage")) tiles.push(html`<div class="tile"><div class="k">本期用水量</div><div class="v">${this._fmt(usage)}<span class="u">m³</span></div></div>`);
          if (!tiles.length) return "";
          const cols = Math.min(tiles.length, 3);
          return html`<div class="tiles" style="grid-template-columns:repeat(${cols},1fr)">${tiles}</div>`;
        })()}

        ${this._show("ladder") ? html`
        <div class="ladder">
          <div class="row1">
            <span class="left">阶梯用量</span>
          </div>
          <div class="bar">
            ${stages.map(s => html`
              <div class="seg">
                <div class="fill" style="width:${s.fill}%"></div>
                <div class="txt"><b>${s.name} ${s.range}</b></div>
              </div>`)}
          </div>
          ${this._show("tier_prices") && tierPrices ? html`
          <div class="prices">
            ${tierPrices.map(p => html`<div class="p">${p !== null ? `单价 ¥${this._price(p)}` : "单价 -"}</div>`)}
          </div>` : ""}
          ${this._show("tier_status") ? html`<div class="note">当前档位：${["第一阶梯", "第二阶梯", "第三阶梯"][tierIdx - 1]} · 累计已用 ${this._fmt(used)} m³ · 剩余 ${this._fmt(remain)} m³</div>` : ""}
        </div>` : ""}

        ${(() => {
          const rows = [];
          if (this._show("name")) rows.push(html`<div class="row"><span class="k">户名</span><span class="v">${name}</span></div>`);
          if (this._show("address")) rows.push(html`<div class="row"><span class="k">用水地址</span><span class="v">${addr}</span></div>`);
          if (this._show("last_pay")) rows.push(html`<div class="row"><span class="k">最近缴费</span><span class="v">${lastPay ? `${this._fmt(lastPay)}${lastPayAmt !== undefined && lastPayAmt !== null ? " ¥" + this._money(lastPayAmt) : ""}` : "-"}</span></div>`);
          return rows.length ? html`<div class="info">${rows}</div>` : "";
        })()}

        <div class="actions">
          <button class="btn pri" @click=${() => this._go("usage")}>📊 用水明细</button>
          <button class="btn gho" @click=${() => this._go("payment")}>💳 缴费记录</button>
        </div>

        <div class="ft">
          <span>重庆水务${updated ? " · 最新刷新时间 " + updated.slice(5, 16) : ""}</span>
          <button class="rf" @click=${() => this._refresh()}>⟳ 刷新</button>
        </div>
      </div>
    `;
  }

  // ============ 用水明细 (图表 + 表格) ============
  _chartData() {
    const ul = (this._attr("usage_list") || []).slice(0, 12).filter(r => r.month);
    const asc = ul.slice().reverse();
    return asc.map(r => ({
      label: (String(r.month).match(/(\d{1,2})月/) || [])[1] ? String(r.month).match(/(\d{1,2})月/)[1] + "月" : r.month,
      usage: Number(r.usage) || 0,
      fee: Number(r.fee) || 0,
    }));
  }

  // 组合图: 柱状=用水量(m³, 左轴), 折线=水费(元, 右轴)
  // 注意: 表达式里插入的 SVG 片段必须用 lit 的 svg 标签模板,
  //       否则会被解析成 HTML 命名空间元素导致不渲染!
  _comboChart(data) {
    if (!data.length) return html`<div class="empty">暂无数据</div>`;
    const W = 340, H = 210, padL = 34, padR = 36, padT = 20, padB = 44;
    const iw = W - padL - padR, ih = H - padT - padB;
    const maxU = Math.max(...data.map(d => d.usage), 1) * 1.2;
    const maxF = Math.max(...data.map(d => d.fee), 1) * 1.2;
    const step = iw / data.length;
    const bw = Math.min(step * 0.45, 22);
    const yU = v => padT + ih - (v / maxU) * ih;
    const yF = v => padT + ih - (v / maxF) * ih;
    const ticks = 4;
    const grid = [];
    for (let i = 0; i <= ticks; i++) {
      const vU = maxU * i / ticks, vF = maxF * i / ticks;
      const y = yU(vU);
      grid.push(svg`<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="var(--divider-color,#ddd)" stroke-width="1" stroke-dasharray="${i === 0 ? "" : "3 3"}"/>
        <text x="${padL - 4}" y="${y + 3}" font-size="9" fill="${BLUE}" text-anchor="end">${Math.round(vU)}</text>
        <text x="${W - padR + 4}" y="${y + 3}" font-size="9" fill="${ORANGE}" text-anchor="start">${Math.round(vF)}</text>`);
    }
    const bars = data.map((d, i) => {
      const bh = Math.max((d.usage / maxU) * ih, 1.5);
      const x = padL + step * i + (step - bw) / 2;
      const cx = padL + step * i + step / 2;
      const topY = padT + ih - bh;
      // 柱子够高时数字放柱内(白字), 否则放柱顶上方(蓝字), 避免与水费数字重叠
      const inside = bh > 16;
      // 注意: 不可在 svg 片段里嵌套条件模板, 所有节点必须直接写在这一层
      return svg`<rect x="${x}" y="${topY}" width="${bw}" height="${bh}" rx="2.5" fill="${BLUE}" opacity="0.85"/>
        <text x="${cx}" y="${inside ? topY + 17 : topY - 4}" font-size="9" font-weight="600" fill="${inside ? "#ffffff" : BLUE}" text-anchor="middle">${d.usage}</text>
        <text x="${cx}" y="${H - 26}" font-size="10" fill="var(--secondary-text-color,#888)" text-anchor="middle">${d.label}</text>`;
    });
    const pts = data.map((d, i) => `${padL + step * i + step / 2},${yF(d.fee)}`).join(" ");
    const dots = data.map((d, i) => svg`<circle cx="${padL + step * i + step / 2}" cy="${yF(d.fee)}" r="2.5" fill="var(--card-background-color,#fff)" stroke="${ORANGE}" stroke-width="1.5"/>
      <text x="${padL + step * i + step / 2}" y="${yF(d.fee) - 7}" font-size="9" font-weight="600" fill="${ORANGE}" text-anchor="middle">${Number(d.fee).toFixed(0)}</text>`);
    return html`<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block">
      ${grid}
      ${bars}
      <polyline points="${pts}" fill="none" stroke="${ORANGE}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>
      ${dots}
      <g transform="translate(${W / 2 - 70}, ${H - 7})">
        <rect x="0" y="-8" width="10" height="10" rx="2" fill="${BLUE}"/>
        <text x="14" y="1" font-size="10" fill="var(--secondary-text-color,#888)">用水量 m³</text>
        <line x1="86" y1="-3" x2="106" y2="-3" stroke="${ORANGE}" stroke-width="1.5"/>
        <circle cx="96" cy="-3" r="2.5" fill="var(--card-background-color,#fff)" stroke="${ORANGE}" stroke-width="1.5"/>
        <text x="110" y="1" font-size="10" fill="var(--secondary-text-color,#888)">水费 元</text>
      </g>
    </svg>`;
  }

  _renderUsage() {
    const data = this._chartData();
    const ul = this._attr("usage_list") || [];

    return html`
      <div class="card sub">
        <div class="title">用水明细<button class="back" @click=${() => this._go("main")}>← 返回</button></div>

        <div class="panel">
          <div class="pt">用水量 / 水费</div>
          ${this._comboChart(data)}
        </div>

        <div class="panel" style="padding:4px 12px 8px">
          <table>
            <thead><tr><th class="tl">月份</th><th>起度</th><th>止度</th><th>水量</th><th>水费</th><th>状态</th></tr></thead>
            <tbody>
              ${ul.map(r => html`
                <tr class="mrow" @click=${() => this._toggleMonth(r)}>
                  <td class="tl">${r.month || "-"}<span class="chev">${this._open === r.month ? "▾" : "›"}</span></td>
                  <td>${this._fmt(r.begin)}</td>
                  <td>${this._fmt(r.end)}</td>
                  <td>${this._fmt(r.usage)}</td>
                  <td>${this._fmt(r.fee)}</td>
                  <td class="${r.status === "已缴费" ? "paid" : "unpaid"}">${r.status || "-"}</td>
                </tr>
                ${this._open === r.month ? this._renderMonthDetail(r) : ""}
              `)}
            </tbody>
          </table>
          ${ul.length === 0 ? html`<div class="empty">暂无数据</div>` : ""}
          <div class="empty" style="padding:8px 0 6px">点击月份展开该期费用构成, 再点一次收起</div>
        </div>
      </div>
    `;
  }

  // ============ 月份费用构成 (手风琴, 展开在该月与上月之间) ============
  _toggleMonth(r) {
    this._open = this._open === r.month ? null : r.month;
  }

  _renderMonthDetail(r) {
    const byMonth = this._attr("fee_items_by_month") || {};
    const items = byMonth[r.month] || [];
    const total = items.reduce((s, f) => s + (Number(f.money) || 0), 0);
    return html`<tr class="xrow"><td colspan="6">
      ${items.length ? html`
        ${items.map(f => html`
          <div class="payrow">
            <div class="ic">🧾</div>
            <div class="m">
              <div class="a">${f.item} · ${f.name}</div>
              <div class="b">单价 ¥${this._money(f.price)}/m³ × ${this._fmt(f.amount)} m³</div>
            </div>
            <div class="amt">¥${this._money(f.money)}</div>
          </div>`)}
        <div class="xsum">合计 ¥${this._money(total)}</div>
      ` : html`<div class="empty">该期费用构成数据未获取, 请点主页"刷新"后重试</div>`}
    </td></tr>`;
  }

  // ============ 缴费记录 (紧凑表格, 与用水明细表格样式一致) ============
  _renderPayment() {
    const list = this._attr("payment_list") || [];
    return html`
      <div class="card sub">
        <div class="title">缴费记录${list.length ? html`<span class="pu" style="font-size:12px;color:var(--secondary-text-color,#888);font-weight:400">${list.length} 笔</span>` : ""}<button class="back" @click=${() => this._go("main")}>← 返回</button></div>
        <div class="panel" style="padding:4px 12px 8px">
          ${list.length === 0 ? html`<div class="empty">暂无数据</div>` : html`
            <table class="paytable">
              <thead><tr><th class="tl">缴费日期</th><th class="tr amt-th">缴费金额</th><th class="meth">缴费方式</th></tr></thead>
              <tbody>
                ${list.map(r => html`
                  <tr>
                    <td class="tl">${r.time || "-"}</td>
                    <td class="tr amt-cell">¥${this._money(r.amount)}</td>
                    <td class="meth"><span class="chwrap">${this._channelBadge(r.channel)}<span>${r.channel || "-"}</span></span></td>
                  </tr>`)}
              </tbody>
            </table>
          `}
        </div>
      </div>
    `;
  }

  render() {
    if (!this.config || !this.hass) return html``;
    if (this._view === "usage") return this._renderUsage();
    if (this._view === "payment") return this._renderPayment();
    return this._renderMain();
  }
}

// 重复定义保护: 资源被加载两次 (如新旧两个资源 URL 并存) 时第二次 define
// 会抛错并中断本文件后续执行, 导致排在后面的编辑器注册不上。
if (!customElements.get("cqsw-water-card")) {
  customElements.define("cqsw-water-card", CqswWaterCard);
}

// ============ 可视化编辑器 ============
// 与燃气卡一致的紧凑样式: 账号下拉 (仅列出集成主实体, 显示"户名 · 户号")
// + 两列勾选框 (默认全勾选; 勾选时代码不写开关, 取消勾选才写 hide: [...])
const EDITOR_TOGGLES = VIS_ITEMS;

class CqswWaterCardEditor extends LitElement {
  static get properties() {
    return { hass: { attribute: false }, config: { attribute: false } };
  }

  setConfig(config) {
    this.config = config || {};
  }

  _emit(cfg) {
    this.dispatchEvent(new CustomEvent("config-changed", {
      detail: { config: cfg }, bubbles: true, composed: true,
    }));
  }

  // 账号下拉选项: 从实体注册表按 platform 过滤, 只列出本集成 (cqsw_water)
  // 的主实体, 不会混入其他集成 (如燃气) 里同名属性的实体。
  // 注册表加载前/失败时退回属性特征粗筛 (usage_list + customer_name)。
  _loadRegistry(hass) {
    if (this._regState) return;
    if (!hass || typeof hass.callWS !== "function") return; // hass 未就绪, 等下次渲染
    this._regState = "loading";
    hass.callWS({ type: "config/entity_registry/list" })
      .then((list) => {
        this._regIds = (list || [])
          .filter((e) => e.platform === "cqsw_water")
          .map((e) => e.entity_id);
        this._regState = "done";
        this.requestUpdate();
      })
      .catch(() => {
        this._regState = "failed";
        this.requestUpdate();
      });
  }

  _accountOptions() {
    const states = this.hass && this.hass.states ? this.hass.states : {};
    const label = (eid) => {
      const s = states[eid];
      if (!s || !s.attributes) return eid;
      const a = s.attributes;
      const nm = a.customer_name || a.friendly_name || eid;
      const no = a.card_no || "";
      return no ? `${nm} · ${no}` : nm;
    };
    const fromAttr = () => Object.keys(states).filter((eid) => {
      const a = (states[eid] || {}).attributes || {};
      return "usage_list" in a && "customer_name" in a;
    });

    let ids;
    if (this._regState === "done") {
      // 只保留"主实体": 集成下每个账号只有主实体带 usage_list 属性,
      // 其余 (本期水费/户号/登录状态...) 不会出现在账号下拉里
      ids = (this._regIds || []).filter((eid) => {
        const a = (states[eid] || {}).attributes || {};
        return "usage_list" in a;
      });
    } else {
      if (!this._regState) this._loadRegistry(this.hass);
      ids = fromAttr(); // 加载中/失败时的兜底
    }

    const out = ids.map((eid) => ({ eid, label: label(eid) }));
    const cfgEntity = (this.config || {}).entity;
    if (cfgEntity && !out.some(o => o.eid === cfgEntity)) {
      out.unshift({ eid: cfgEntity, label: cfgEntity });
    }
    return out;
  }

  _setEntity(eid) {
    const cfg = { ...(this.config || {}) };
    if (eid) cfg.entity = eid;
    else delete cfg.entity;
    this._emit(cfg);
  }

  // 勾选/取消某一块: 没勾就进 hide 列表, 全勾上时把 hide 整个删掉 (代码保持干净)
  _toggle(key, on) {
    const cfg = { ...(this.config || {}) };
    const hide = new Set(Array.isArray(cfg.hide) ? cfg.hide : []);
    if (on) hide.delete(key);
    else hide.add(key);
    if (hide.size) cfg.hide = Array.from(hide);
    else delete cfg.hide;
    // 旧的 show_xxx 开关与新的 hide 会打架, 动过勾选就以新的为准
    for (const k of Object.values(LEGACY_SHOW_KEYS)) delete cfg[k];
    this._emit(cfg);
  }

  // 右上角「累计费用」的口径: year = 本年 (只算今年账期), all = 全部 (默认, 不写代码)
  _setScope(scope) {
    const cfg = { ...(this.config || {}) };
    if (scope === "year") cfg.cum_scope = "year";
    else delete cfg.cum_scope;
    delete cfg.total_fee_mode; // 旧键, 以新键为准
    this._emit(cfg);
  }

  // 编辑器勾选框状态: 与卡片 _show 同一套规则 (hide + 旧 show_xxx 兼容)
  _visible(key) {
    const cfg = this.config || {};
    const legacy = LEGACY_SHOW_KEYS[key];
    if (legacy && cfg[legacy] === false) return false;
    if (Array.isArray(cfg.hide) && cfg.hide.indexOf(key) >= 0) return false;
    return true;
  }

  static get styles() {
    return css`
      :host { display: block; }
      .sec { font-size: 12px; font-weight: 600; color: var(--primary-text-color, #333); margin: 4px 0 8px; }
      .sec.later { margin-top: 16px; }
      select.acct {
        width: 100%; box-sizing: border-box;
        appearance: auto; -webkit-appearance: auto;
        padding: 9px 12px; font-size: 14px; color: var(--primary-text-color, #222);
        background: var(--card-background-color, #fff);
        border: 1px solid var(--divider-color, #ccc); border-radius: 8px;
        cursor: pointer;
      }
      .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 2px 12px; }
      .item { display: flex; align-items: center; gap: 8px; padding: 6px 0; cursor: pointer; font-size: 14px; color: var(--primary-text-color, #222); }
      .item input { width: 16px; height: 16px; margin: 0; cursor: pointer; accent-color: var(--primary-color, #1e88e5); }
      .hint { font-size: 12px; color: var(--secondary-text-color, #888); margin-top: 10px; }
    `;
  }

  render() {
    if (!this.hass) return html``; // hass 未就绪, 等 HA 赋值后重渲染
    const cfg = this.config || {};
    const accounts = this._accountOptions();
    return html`
      <div class="sec">账号</div>
      ${accounts.length ? html`
        <select class="acct" @change=${(ev) => this._setEntity(ev.target.value)}>
          ${accounts.map((o) => html`<option value=${o.eid} ?selected=${cfg.entity === o.eid}>${o.label}</option>`)}
        </select>` : ""}
      <div class="sec later">显示内容</div>
      <div class="grid">
        ${EDITOR_TOGGLES.map(([key, label]) => html`
          <label class="item">
            <input
              type="checkbox"
              ?checked=${this._visible(key)}
              @change=${(ev) => this._toggle(key, ev.target.checked)}
            />
            <span>${label}</span>
          </label>`)}
      </div>
      <div class="sec later">累计费用</div>
      <select class="acct" @change=${(ev) => this._setScope(ev.target.value)}>
        <option value="all" ?selected=${cfg.cum_scope !== "year" && cfg.total_fee_mode !== "year"}>全部累计</option>
        <option value="year" ?selected=${cfg.cum_scope === "year" || cfg.total_fee_mode === "year"}>本年累计</option>
      </select>
      <div class="hint">右上角那个金额的算法：本年 = 只加今年账期；全部 = 所有账期加起来（含往年）。</div>
      <div class="hint" style="margin-top:4px">默认全部勾选；取消勾选的块不会出现在首页卡片上。</div>
    `;
  }
}

if (!customElements.get("cqsw-water-card-editor")) {
  customElements.define("cqsw-water-card-editor", CqswWaterCardEditor);
}

window.customCards = window.customCards || [];
window.customCards.push({
  type: "cqsw-water-card",
  name: "重庆水务水表卡片",
  description: "重庆水务水表读数 / 阶梯 / 用水明细图表 / 缴费记录",
});
