"""重庆水务(掌上水务) kf.cncqsw.com 数据接口封装。

全链路已用真实账号端到端验证 (2026-10):
  1. 登录  POST /system-service/unAuth/normalLogin?tenantId=
           body: {"authType":4,"source":1,"username":账号,"password":SM2密文,"wxOpenId":""}
           -> data.token (同时下发 Cookie CCS_SESSION_ID)
  2. 鉴权  后续请求带 Cookie: CCS_SESSION_ID=<token>
  3. 规则  GET /system-service/security/rule (带Cookie)
           规则里的 encodeKey/decodeKey 是 SM2 密文, 需用前端硬编码私钥解密
           -> 得到: 数据接口请求体加密公钥 + 响应体解密私钥 (每会话下发)
  4. 数据  GET  /process-center-service/sw/household-user/getCardNo      -> 户号(密文)
           POST /process-center-service/waterDetail/map/<cardNo>          -> 用水明细(密文)
           POST /process-center-service/commonRemote/queryPayRecrod       -> 缴费记录(密文, body也加密)
           GET  /system-service/current                                   -> 用户信息(明文)
  5. 加密  SM2 C1C2C3; 请求体密文 = BASE64_URL_SAFE, Content-Type 必须为 application/json
           响应密文 = BASE64_URL_SAFE

特点: 零第三方依赖, SM2/SM3 用纯标准库实现。
"""

import base64
import json
import logging
import secrets
import urllib.request
import urllib.error
from datetime import datetime

_LOGGER = logging.getLogger(__name__)

BASE = "https://kf.cncqsw.com/sw-cloud"

# 登录密码 SM2 公钥 (前端硬编码, security/rule 解密后确认一致)
LOGIN_SM2_PUBLIC_KEY = (
    "04ad26e36bfac6eb8764e6bf755df2ee626c3fdaf0ba6fd116445a9b84aeaa69f4d47091d5fbfb182c495c31d7aa54ef66d0253521a88c6c03139355bc40c2b6fe"
)

# security/rule 中 encodeKey/decodeKey 的解密私钥 (前端硬编码)
RULE_SM2_PRIVATE_KEY = (
    "70916b0d53ca36798942951c0ca5d0bab35be324f2e66c89246fe9b46c087349"
)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# ====================== 纯标准库 SM2 / SM3 ======================
_p = 0xFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF00000000FFFFFFFFFFFFFFFF
_a = 0xFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF00000000FFFFFFFFFFFFFFFC
_b = 0x28E9FA9E9D9F5E344D5A9E4BCF6509A7F39789F515AB8F92DDBCBD414D940E93
_n = 0xFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFF7203DF6B21C6052B53BBF40939D54123
_Gx = 0x32C4AE2C1F1981195F9904466A39C9948FE30BBFF2660BE1715A4589334C74C7
_Gy = 0xBC3736A2F4F6779C59BDCEE36B692153D0A9877CC62A474002DF32E52139F0A0


def _inv(x, m):
    return pow(x % m, -1, m)


def _rotl(x, k):
    k &= 31
    return ((x << k) | (x >> (32 - k))) & 0xFFFFFFFF


def _ff(x, y, z, j):
    return x ^ y ^ z if j < 16 else (x & y) | (x & z) | (y & z)


def _gg(x, y, z, j):
    return x ^ y ^ z if j < 16 else (x & y) | ((~x & 0xFFFFFFFF) & z)


def _p0(x):
    return x ^ _rotl(x, 9) ^ _rotl(x, 17)


def _p1(x):
    return x ^ _rotl(x, 15) ^ _rotl(x, 23)


def sm3(msg: bytes) -> bytes:
    IV = 0x7380166F4914B2B9172442D7DA8A0600A96F30BC163138AAE38DEE4DB0FB0E4E
    H = [(IV >> (224 - 32 * i)) & 0xFFFFFFFF for i in range(8)]
    T = (0x79CC4519, 0x7A879D8A)
    m = bytearray(msg)
    bitlen = len(m) * 8
    m.append(0x80)
    while len(m) % 64 != 56:
        m.append(0)
    m += bitlen.to_bytes(8, "big")
    for i in range(0, len(m), 64):
        w = [int.from_bytes(m[i + k:i + k + 4], "big") for k in range(0, 64, 4)]
        for j in range(16, 68):
            w.append(_p1(w[j - 16] ^ w[j - 9] ^ _rotl(w[j - 3], 15)) ^ _rotl(w[j - 13], 7) ^ w[j - 6])
        wp = [w[j] ^ w[j + 4] for j in range(64)]
        A, B, C, D, E, F, G, Hh = H
        for j in range(64):
            tj = T[0] if j < 16 else T[1]
            ss1 = _rotl((_rotl(A, 12) + E + _rotl(tj, j)) & 0xFFFFFFFF, 7)
            ss2 = ss1 ^ _rotl(A, 12)
            tt1 = (_ff(A, B, C, j) + D + ss2 + wp[j]) & 0xFFFFFFFF
            tt2 = (_gg(E, F, G, j) + Hh + ss1 + w[j]) & 0xFFFFFFFF
            D, C, B, A = C, _rotl(B, 9), A, tt1
            Hh, G, F, E = G, _rotl(F, 19), E, _p0(tt2)
        H = [(x ^ y) & 0xFFFFFFFF for x, y in zip(H, [A, B, C, D, E, F, G, Hh])]
    return b"".join(h.to_bytes(4, "big") for h in H)


def _kdf(z: bytes, klen: int) -> bytes:
    out, ct = b"", 1
    while len(out) < klen:
        out += sm3(z + ct.to_bytes(4, "big"))
        ct += 1
    return out[:klen]


def _point_add(P, Q):
    if P is None:
        return Q
    if Q is None:
        return P
    x1, y1 = P
    x2, y2 = Q
    if x1 == x2 and (y1 + y2) % _p == 0:
        return None
    if P == Q:
        lam = (3 * x1 * x1 + _a) * _inv(2 * y1, _p) % _p
    else:
        lam = (y2 - y1) * _inv((x2 - x1) % _p, _p) % _p
    x3 = (lam * lam - x1 - x2) % _p
    y3 = (lam * (x1 - x3) - y1) % _p
    return (x3, y3)


def _scalar_mult(kk, P):
    R = None
    addend = P
    while kk:
        if kk & 1:
            R = _point_add(R, addend)
        addend = _point_add(addend, addend)
        kk >>= 1
    return R


def sm2_encrypt(plaintext: bytes, pub_hex: str = None) -> str:
    """SM2 加密, 返回 hex 字符串 (C1||C2||C3, 无 04 前缀)。"""
    key = pub_hex or LOGIN_SM2_PUBLIC_KEY
    if key.startswith("04"):
        key = key[2:]
    Px = int(key[:64], 16)
    Py = int(key[64:], 16)
    P = (Px, Py)
    klen = len(plaintext)
    while True:
        k = secrets.randbelow(_n - 2) + 1
        C1 = _scalar_mult(k, (_Gx, _Gy))
        x1, y1 = C1
        x2, y2 = _scalar_mult(k, P)
        xb, yb = x2.to_bytes(32, "big"), y2.to_bytes(32, "big")
        t = _kdf(xb + yb, klen)
        if int.from_bytes(t, "big") == 0:
            continue
        C2 = bytes(pt ^ tk for pt, tk in zip(plaintext, t))
        C3 = sm3(xb + plaintext + yb)
        return (x1.to_bytes(32, "big").hex() + y1.to_bytes(32, "big").hex()
                + C2.hex() + C3.hex())


def sm2_decrypt(priv_hex: str, cipher: bytes) -> bytes:
    """SM2 解密 (C1||C2||C3; C1 兼容带/不带 04 前缀)。"""
    if cipher[:1] == b"\x04" and len(cipher) > 128:
        cipher = cipher[1:]
    d = int(priv_hex, 16)
    C1 = (int.from_bytes(cipher[0:32], "big"), int.from_bytes(cipher[32:64], "big"))
    klen = len(cipher) - 96
    if klen <= 0:
        raise ValueError("密文长度不足")
    C2, C3 = cipher[64:64 + klen], cipher[64 + klen:]
    x2, y2 = _scalar_mult(d, C1)
    xb, yb = x2.to_bytes(32, "big"), y2.to_bytes(32, "big")
    M = bytes(a ^ b for a, b in zip(C2, _kdf(xb + yb, klen)))
    if sm3(xb + M + yb) != C3:
        raise ValueError("SM2 解密 C3 校验失败")
    return M


def b64url_decode(s):
    """BASE64_URL_SAFE -> bytes (兼容标准 base64 / bytes 直通)。"""
    if isinstance(s, bytes):
        return s
    s = str(s).strip().replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s)


def b64url_encode(data: bytes) -> str:
    """bytes -> BASE64_URL_SAFE (无填充)。"""
    return base64.b64encode(data).decode().replace("+", "-").replace("/", "_").rstrip("=")


# ====================== HTTP ======================
def _http(method, path, body=None, cookie_header=None, as_json=True):
    url = BASE + path
    data = None
    headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://kf.cncqsw.com",
        "Referer": "https://kf.cncqsw.com/sw-mobile/",
    }
    if cookie_header:
        headers["Cookie"] = cookie_header
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        else:
            data = body if isinstance(body, bytes) else str(body).encode("utf-8")
        headers["Content-Type"] = "application/json;charset=UTF-8"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=30)
        raw = r.read().decode("utf-8", "ignore")
        set_cookie = r.headers.get("Set-Cookie")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        set_cookie = e.headers.get("Set-Cookie")
    except urllib.error.URLError as e:
        return {"_http_error": True, "_raw": str(e)}, None
    if as_json:
        try:
            return json.loads(raw), set_cookie
        except json.JSONDecodeError:
            return {"_raw": raw, "_http_error": True}, set_cookie
    return raw, set_cookie


# ====================== 通用字段提取 ======================
def dig(obj, *path):
    cur = obj
    for p in path:
        if cur is None:
            return None
        if isinstance(p, int):
            if isinstance(cur, (list, tuple)) and -len(cur) <= p < len(cur):
                cur = cur[p]
            else:
                return None
        elif isinstance(cur, dict):
            cur = cur.get(p)
        else:
            return None
    return cur


def find_first(obj, candidates, default=None):
    if isinstance(obj, dict):
        for c in candidates:
            if c in obj and obj[c] not in (None, ""):
                return obj[c]
    if isinstance(obj, list):
        for item in obj:
            v = find_first(item, candidates)
            if v not in (None, ""):
                return v
    return default


def to_float(v):
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def to_str(v):
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


# ====================== 主接口类 ======================
class CqSwApi:
    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.cookie = None
        self.user = {}
        # 会话加密规则 (登录后从 security/rule 解密得到)
        self.body_pub_keys = []     # 数据接口请求体加密公钥列表 (规则 type=1)
        self.resp_priv_keys = []    # 数据接口响应解密私钥列表 (规则 type=3)
        # 兼容属性: 首选密钥
        self.body_pub_key = None
        self.resp_priv_key = None

    # ---------- 基础 ----------
    @staticmethod
    def _dec_rule_key(val):
        """规则密钥密文解码: 优先 HEX, 失败再试 BASE64_URL_SAFE。"""
        try:
            return bytes.fromhex(val.strip())
        except ValueError:
            return b64url_decode(val)

    def _dec_rules(self, rules):
        """用前端硬编码私钥解密 security/rule 里的密钥。"""
        out = {}
        for path, arr in (rules or {}).items():
            rec = {}
            for r in arr or []:
                try:
                    if r.get("encodeKey"):
                        rec.setdefault("encodeKeys", []).append(
                            sm2_decrypt(RULE_SM2_PRIVATE_KEY,
                                        self._dec_rule_key(r["encodeKey"])).decode())
                    if r.get("decodeKey"):
                        rec.setdefault("decodeKeys", []).append(
                            sm2_decrypt(RULE_SM2_PRIVATE_KEY,
                                        self._dec_rule_key(r["decodeKey"])).decode())
                except Exception:  # 单条规则解析失败忽略
                    pass
            out[path] = rec
        return out

    def _load_session_keys(self):
        """登录后拉取并解密会话加密规则。"""
        resp, _ = _http("GET", "/system-service/security/rule",
                        cookie_header=self.cookie)
        rules = dig(resp, "data") or {}
        dec = self._dec_rules(rules)
        for key, rec in dec.items():
            if key.rstrip("*").rstrip("/") in ("/process-center-service", "/process-center-service/**"):
                for ek in rec.get("encodeKeys") or []:
                    if not ek.startswith("04") and len(ek) == 128:
                        ek = "04" + ek
                    if ek not in self.body_pub_keys:
                        self.body_pub_keys.append(ek)
                for dk in rec.get("decodeKeys") or []:
                    if dk not in self.resp_priv_keys:
                        self.resp_priv_keys.append(dk)
        self.body_pub_key = self.body_pub_keys[0] if self.body_pub_keys else None
        self.resp_priv_key = self.resp_priv_keys[0] if self.resp_priv_keys else None
        _LOGGER.debug("会话加密规则: body_pub=%d个 resp_priv=%d个",
                      len(self.body_pub_keys), len(self.resp_priv_keys))

    def _dec_resp(self, resp):
        """响应自适应: 明文 JSON 直接返回; BASE64_URL_SAFE 密文则 SM2 解密。"""
        raw = resp
        if isinstance(resp, dict):
            if resp.get("_http_error") and "_raw" in resp:
                raw = resp["_raw"]
            elif "success" in resp or "errorCode" in resp:
                return resp  # 明文 JSON 响应
            else:
                return resp
        if not isinstance(raw, str):
            return raw
        try:
            j = json.loads(raw)
            if isinstance(j, dict) and ("success" in j or "errorCode" in j):
                return j
        except (json.JSONDecodeError, TypeError):
            pass
        keys = list(self.resp_priv_keys)
        if not keys and self.resp_priv_key:
            keys = [self.resp_priv_key]
        if not keys:
            raise CqSwLoginError("响应为密文但缺少会话解密私钥")
        data = b64url_decode(raw)
        last_err = None
        for k in keys:
            try:
                return json.loads(sm2_decrypt(k, data))
            except Exception as err:
                last_err = err
        raise last_err if last_err else CqSwLoginError("响应解密失败")

    def _enc_body(self, obj) -> str:
        """请求体整体 SM2 加密 -> BASE64_URL_SAFE 字符串。"""
        if not self.body_pub_key:
            raise CqSwLoginError("缺少会话请求加密公钥")
        plain = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        return b64url_encode(bytes.fromhex(sm2_encrypt(plain, self.body_pub_key)))

    def _post_encrypted(self, path, obj):
        resp, _ = _http("POST", path, body=self._enc_body(obj),
                        cookie_header=self.cookie)
        return self._dec_resp(resp)

    def _get_decrypted(self, path):
        resp, _ = _http("GET", path, cookie_header=self.cookie)
        return self._dec_resp(resp)

    def _post_encrypted_raw(self, path):
        """无 body 的 POST (URL 路径带参数), 密文响应解密。"""
        resp, _ = _http("POST", path, body="", cookie_header=self.cookie)
        return self._dec_resp(resp)

    # ---------- 登录 ----------
    def login(self):
        """密码登录 (端点/字段与浏览器逐字一致, 已实测成功)。"""
        enc_pw = sm2_encrypt(self.password.encode("utf-8"), LOGIN_SM2_PUBLIC_KEY)
        body = {
            "authType": 4,           # 4 = 密码登录
            "source": 1,             # 1 = Web
            "username": self.username,
            "password": enc_pw,      # SM2 C1C2C3 hex
            "wxOpenId": "",
        }
        resp, set_cookie = _http("POST", "/system-service/unAuth/normalLogin?tenantId=",
                                 body=body)
        if not resp.get("success"):
            msg = resp.get("errorMessage") or (resp.get("_raw") if isinstance(resp, dict) else None) or str(resp)
            raise CqSwLoginError("登录失败: %s" % msg)
        data = resp.get("data") or {}
        token = data.get("token")
        if not token:
            raise CqSwLoginError("登录响应缺少 token")
        self.cookie = "CCS_SESSION_ID=%s" % token
        self.user = data
        # 加载会话加密规则 (失败不阻断登录本身)
        try:
            self._load_session_keys()
        except Exception as err:
            _LOGGER.warning("加载会话加密规则失败: %s", err)
        return self.cookie, self.user

    # ---------- 数据接口 ----------
    def get_current(self):
        """用户信息 (明文 JSON)。"""
        resp, _ = _http("GET", "/system-service/current", cookie_header=self.cookie)
        return resp

    def get_card_no(self):
        """户号列表 (密文响应)。返回 ["0000000001", ...]。偶发解密失败自动重试一次。"""
        last_err = None
        for attempt in range(2):
            try:
                resp = self._get_decrypted(
                    "/process-center-service/sw/household-user/getCardNo")
                if not resp.get("success"):
                    raise CqSwLoginError("获取户号失败: %s" % resp.get("errorMessage"))
                cards = []
                for row in dig(resp, "data") or []:
                    if isinstance(row, dict) and row.get("cardNo"):
                        cards.append(to_str(row.get("cardNo")))
                return cards
            except Exception as err:
                last_err = err
        raise last_err

    def query_usage(self, card_no):
        """用水明细: POST waterDetail/map/<cardNo> (密文响应)。

        data: waterInfo[{time,amount,money,isPay}] / waterYield[{mouth,amount}]
              / waterRate[{mouth,money}] / waterLadder{ladder1..3,endAmount,nowLadder}
        """
        return self._post_encrypted_raw("/process-center-service/waterDetail/map/%s" % card_no)

    def query_payments(self, card_no):
        """缴费记录: queryPayRecrod (请求体加密, 密文响应)。

        data: [{cardNo,userName,payTime,money,payType,transferTime}]
        """
        today = datetime.now().strftime("%Y%m%d")
        return self._post_encrypted(
            "/process-center-service/commonRemote/queryPayRecrod",
            {"userCode": card_no or "", "startDate": "20200101", "endDate": today})

    def query_month_detail(self, card_no, yyyymm, desensitize=0):
        """单月账单详情 (密文响应): 起度/止度/户名/地址/费用构成。

        data: {time,beginScale,endScale,amount,money,userName,address,
               companyName,natureName,projectInfos[{itemName,feeItemList[]}]}
        desensitize: 0=不脱敏, 1=脱敏 (官方APP用1)
        """
        return self._post_encrypted(
            "/process-center-service/waterDetail/detail?desensitize=%d" % desensitize,
            {"cardNo": card_no, "time": yyyymm})

    # ---------- 总装 ----------
    def fetch_all(self):
        """登录并抓取全部数据, 返回结构化 dict (含 *_raw 诊断字段)。"""
        self.login()
        out = {
            "available": False,
            "updated_at": _now(),
            "account": self.username,
        }

        # 用户信息 (明文)
        try:
            current = self.get_current()
            out["current_raw"] = current
            out.update(self._parse_user(dig(current, "data") or {}))
        except Exception as err:
            _LOGGER.warning("获取用户信息失败: %s", err)

        # 户号
        card_no = None
        try:
            cards = self.get_card_no()
            out["card_list"] = cards
            card_no = cards[0] if cards else None
        except Exception as err:
            _LOGGER.warning("获取户号失败: %s", err)
            card_no = to_str(find_first(out, ["cardNo", "userCode"]))
        out["card_no"] = card_no

        if not card_no:
            out["error"] = "未获取到户号"
            return out

        # 用水明细
        try:
            usage = self.query_usage(card_no)
            out["usage_raw"] = usage
            out.update(self._parse_usage(usage))
        except Exception as err:
            _LOGGER.warning("获取用水明细失败: %s", err)

        # 缴费记录
        try:
            payments = self.query_payments(card_no)
            out["payment_raw"] = payments
            out.update(self._parse_payments(payments))
        except Exception as err:
            _LOGGER.warning("获取缴费记录失败: %s", err)

        # 当前账期详情 (起度/止度/完整户名地址/费用构成) -> 补全各月起止度
        try:
            ul = out.get("usage_list") or []
            ym = None
            if ul and ul[0].get("month"):
                digits = "".join(ch for ch in str(ul[0]["month"]) if ch.isdigit())
                ym = digits[:6] or None
            if ym:
                try:
                    det = self.query_month_detail(card_no, ym, desensitize=0)
                except Exception:
                    det = self.query_month_detail(card_no, ym, desensitize=1)
                out["detail_raw"] = det
                out.update(self._parse_month_detail(det, out))
        except Exception as err:
            _LOGGER.warning("获取当月详情失败: %s", err)

        # 各月费用构成 (卡片点击月份弹窗用, 最近12期, 单月失败跳过)
        try:
            ul = out.get("usage_list") or []
            by_month = {}
            for row in ul[:12]:
                digits = "".join(ch for ch in str(row.get("month") or "") if ch.isdigit())
                ym = digits[:6]
                if not ym:
                    continue
                try:
                    try:
                        det = self.query_month_detail(card_no, ym, desensitize=0)
                    except Exception:
                        det = self.query_month_detail(card_no, ym, desensitize=1)
                    items = self._extract_fee_items(dig(det, "data") or {})
                    if items:
                        by_month[row["month"]] = items
                except Exception:
                    continue
            if by_month:
                out["fee_items_by_month"] = by_month
        except Exception as err:
            _LOGGER.warning("获取各月费用构成失败: %s", err)

        out["available"] = True
        return out

    # ---- 解析 ----
    def _parse_user(self, data):
        if not isinstance(data, dict):
            return {}
        return {
            "customer_name": to_str(find_first(data, ["name", "userName", "realName"])),
            "customer_addr": to_str(find_first(data, ["address", "addr", "customerAddr"])),
            "water_org": to_str(find_first(data, ["orgName", "belongingOrgName", "company"])),
            "balance": to_float(find_first(data, ["balance", "preBalance", "remainMoney"])),
            "phone": to_str(find_first(data, ["phone", "mobile"])),
            "tenant_id": data.get("tenantId"),
        }

    def _parse_usage(self, resp):
        data = dig(resp, "data") or {}
        result = {"usage_list": [], "reading": None, "current_usage": None,
                  "current_fee": None, "tier_usage": None, "last_usage": None,
                  "last_reading": None, "ladder": None, "now_ladder": None}
        info = data.get("waterInfo") or []
        cleaned = []
        for row in info:
            cleaned.append({
                "month": to_str(row.get("time")),           # "2026年09月"
                "usage": to_float(row.get("amount")),        # 吨
                "fee": to_float(row.get("money")),           # 元
                "status": "已缴费" if row.get("isPay") == 1 else "未缴费",
            })
        result["usage_list"] = cleaned
        ladder = data.get("waterLadder") or {}
        if ladder:
            result["tier_usage"] = to_float(ladder.get("endAmount"))
            result["now_ladder"] = ladder.get("nowLadder")
            result["ladder"] = {
                "ladder1": ladder.get("ladder1"),
                "ladder2": ladder.get("ladder2"),
                "ladder3": ladder.get("ladder3"),
            }
        if cleaned:
            latest = cleaned[0]
            result["current_usage"] = latest["usage"]
            result["current_fee"] = latest["fee"]
            result["reading"] = latest["usage"]     # 主实体: 最新账期水量
            if len(cleaned) > 1:
                result["last_usage"] = cleaned[1]["usage"]
                result["last_reading"] = cleaned[1]["usage"]
        return result

    @staticmethod
    def _extract_fee_items(data):
        """从月详情 data.projectInfos[].feeItemList[] 提取费用构成列表。"""
        items = []
        for proj in (data or {}).get("projectInfos") or []:
            for f in (proj.get("feeItemList") or []):
                items.append({
                    "item": to_str(proj.get("itemName")),
                    "name": to_str(f.get("name")),
                    "price": to_float(f.get("price")),
                    "amount": to_float(f.get("amount")),
                    "money": to_float(f.get("money")),
                })
        return items

    def _parse_month_detail(self, det, out):
        """解析当月详情: 起止度 + 链条回推补全各月起止度 + 完整户名地址 + 费用构成。"""
        data = dig(det, "data") or {}
        if not isinstance(data, dict):
            return {}
        res = {}
        begin = to_float(data.get("beginScale"))
        end = to_float(data.get("endScale"))
        if end is not None:
            res["reading"] = end          # 主实体: 本期表读数(止度)
            res["end_scale"] = end
        if begin is not None:
            res["begin_scale"] = begin
            res["last_reading"] = begin   # 上期表读数 = 本期起度

        # 不脱敏的户名/地址/水司/用户类型才覆盖
        for key, field in (("customer_name", "userName"), ("customer_addr", "address"),
                           ("water_org", "companyName"), ("customer_type", "natureName")):
            v = to_str(data.get(field))
            if v and "*" not in v:
                res[key] = v

        # 起止度链条回推: 本月止度-本月水量=本月起度; 上月止度=本月起度, 依次往前
        ul = out.get("usage_list")
        if isinstance(ul, list) and ul and begin is not None and end is not None:
            cur_begin = begin
            for idx, row in enumerate(ul):
                if idx == 0:
                    row["begin"], row["end"] = begin, end
                else:
                    row["end"] = cur_begin
                    u = to_float(row.get("usage")) or 0
                    row["begin"] = round(cur_begin - u, 2)
                cur_begin = row["begin"]

        # 当月费用构成
        items = self._extract_fee_items(data)
        if items:
            res["current_fee_items"] = items

        # 累计水费 (全部账期求和)
        total = 0.0
        has = False
        for row in (out.get("usage_list") or []):
            f = to_float(row.get("fee"))
            if f is not None:
                total += f
                has = True
        if has:
            res["total_fee"] = round(total, 2)
        return res

    def _parse_payments(self, resp):
        data = dig(resp, "data") or []
        result = {"payment_list": [], "last_pay_time": None, "last_pay_amount": None}
        cleaned = []
        for row in data:
            cleaned.append({
                "time": to_str(row.get("payTime")),
                "amount": to_float(row.get("money")),
                "channel": to_str(row.get("payType")),
                "month": to_str(row.get("period")),
            })
        result["payment_list"] = cleaned
        if cleaned:
            result["last_pay_time"] = cleaned[0]["time"]
            result["last_pay_amount"] = cleaned[0]["amount"]
        return result


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class CqSwLoginError(Exception):
    """登录失败 (账号密码错误或网络等)。"""
