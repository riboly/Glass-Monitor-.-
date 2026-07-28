# VPS 流量接口 —— 换机器时改哪里

> 行号为 2026-07-27 状态。代码改动后行号会漂，**函数名/字段名才是可靠锚点**，
> 找不到就用「锚点」那列的内容全局搜。
>
> 改完代码可以跑 `py -3.14 _check_doc_lines.py` 校对本文档的行号是否还有效
> —— 它按锚点重新定位并报告对不上的引用。

---

## TL;DR

**换 VPS 首选走界面，不用碰代码也不用改 JSON：**

> 托盘右键 → 设置… → 滚到最下面「流量接口地址」→ 填新地址 → 保存并应用

保存后会立刻重新拉取（`GlassMonitorApp.apply_settings` 调 `traffic.configure()` 热更新），
不需要重启程序。同一处还有「代理地址」，不走代理就留空。

也可以直接改程序目录的 `config.json`（改完**需重启**）：

```jsonc
"traffic": {
  "enabled": true,
  "url": "https://nosla-substore-traffic-api.xxx.workers.dev/?key=xxxx",  // ← 第 28 行
  "proxy": "http://127.0.0.1:7890",                                      // 不走代理填 ""
  "interval_min": 5,
  "interval_sec": 300          // 自动算出来的，别手改
}
```

以上前提是**新接口的返回格式和现在一样**。格式不一样见下面「情况 B」。

> **地址填错/留空不会静默出错**：卡片会明确显示「未配置接口地址」。
> 程序**不存在**任何硬编码的兜底地址，绝不会偷偷回落到上一台 VPS。

---

## 快速定位表

| 要改什么 | 文件 : 行 | 锚点（搜这个） |
|---|---|---|
| **接口地址** | 设置窗或用户配置 | `"url"` |
| **代理地址** | 设置窗或用户配置 | `"proxy"` |
| 轮询间隔（分钟） | 设置窗或用户配置 | `"interval_min"` |
| 流量卡片开关 | 设置窗「显示卡片」或用户配置的 `cards.traffic` | `"cards"` |
| **响应解析（格式变了改这）** | `traffic.py` : **43** | `def parse_traffic_body` |
| 空地址短路（不发请求） | `traffic.py` : **92** | `if not (url or "").strip()` |
| 请求发送 / 代理回退逻辑 | `traffic.py` : **86** | `def fetch_traffic` |
| 数据字段定义 | `traffic.py` : **22** | `class TrafficInfo` |
| 「N 天后重置」计算 | `traffic.py` : **70** | `def format_reset_days` |
| 轮询线程 / 失败缓存 | `traffic.py` : **126** | `class TrafficCollector` |
| 轮询线程里的空地址分支 | `traffic.py` : **260** | `elif not (url or "").strip()` |
| 连续失败几次才报错（默认 10） | `traffic.py` : **134** | `max_fails` |
| GB/TB 单位换算 | `traffic.py` : **290** | `def format_bytes` |
| 进度条颜色阈值（50%/70%） | `traffic.py` : **298** | `def traffic_bar_color` |
| 缺字段时的默认值（**已无硬编码地址**） | `glass_ui.py` : **356 / 357** | `traf.setdefault("url", "")` |
| 采集器初始化 | `glass_ui.py` : **272** | `self.traffic = TrafficCollector(` |
| 保存设置后热更新 | `glass_ui.py` : **572** | `self.traffic.configure(` |
| 卡片绘制（文字/进度条位置） | `glass_ui.py` : **1033** | `def _draw_traffic` |
| 「未配置接口地址」文案 | `glass_ui.py` : **1047** | `elif info.status == "unconfigured"` |
| 设置窗的地址/代理输入框 | `settings_ui.py` : **653 / 674** | `var_traffic_url` |

---

## 当前接口的返回格式

实测样本（2026-07-26 抓的真实响应）：

```
upload=122627453971; download=127052394161; total=536870912000; expire=1786492799
```

规则：

- **分号 `;` 分隔**的 `key=value`，空格无所谓，大小写不敏感
- `upload` / `download` / `total`：**字节数**
- `expire`：**Unix 时间戳（秒）**，流量重置日；大于 `10^10` 会被当毫秒自动 ÷1000
- 已用流量 = `upload + download`（`traffic.py:33` 的 `used` 属性）
- 百分比 = `used / total × 100`（`traffic.py:37` 的 `percent` 属性）
- **`total <= 0` 会被判定为失败**（`traffic.py:63`），所以新接口必须给出总量

多余的字段会被忽略，缺失的字段按 0 处理。

---

## 换 VPS 的三种情况

### 情况 A：新接口格式一样（最常见）

设置窗里填新地址 → 保存并应用。**不用动任何代码，也不用重启。**

### 情况 B：新接口格式不同（比如返回 JSON）

改 `traffic.py:43` 的 `parse_traffic_body`，只要最后返回一个填好
`upload / download / total / expire` 的 `TrafficInfo` 就行，其余全不用动。

比如新接口返回 `{"up":123,"down":456,"quota":1099511627776,"reset":1786492799}`：

```python
def parse_traffic_body(text: str) -> TrafficInfo:
    import json
    d = json.loads(text)
    info = TrafficInfo(
        upload=int(d.get("up", 0)),
        download=int(d.get("down", 0)),
        total=int(d.get("quota", 0)),
        expire=int(d.get("reset", 0)),
        ok=True,
        status="ok",
    )
    if info.total <= 0:                 # 这段判断保留
        info.ok = False
        info.status = "failed"
        info.error = "invalid total"
    return info
```

> 注意单位：如果新接口给的是 **GB** 而不是字节，在这里乘 `1024**3` 换算好，
> 否则界面上的 GB 数字和进度条都会错。

### 情况 C：新 VPS 不需要走代理

设置窗的「代理地址」清空（或把用户配置中的 `proxy` 改成 `""`）。

`fetch_traffic`（`traffic.py:86`）本来就是**先试代理、失败再直连**，所以留着旧代理
地址也能跑通，只是每次都要先等代理超时（默认 15 秒），会拖慢刷新。建议清掉。

---

## 数据流向

```
设置窗输入框 / 用户配置      traffic.url / proxy / interval_min
      │
      ▼
glass_ui.py:272   TrafficCollector(url=..., proxy=..., interval_min=...)
      │           （后台线程，不阻塞 UI）
      ▼
traffic.py:260    url 为空？ ──是──► status="unconfigured"，不发请求、不计失败
      │否                            卡片显示「未配置接口地址」
      ▼
traffic.py:86     fetch_traffic()      ← 代理优先，失败回退直连
      ▼
traffic.py:43     parse_traffic_body() ← 【格式变了就改这里】
      ▼
traffic.py:218    _apply_result()      ← 失败时沿用上次数据（status="cached"）
      ▼                                  连续 10 次失败才变 status="failed"
glass_ui.py:1654  self._traffic_info = self.traffic.get()   ← 每秒取一次快照
      ▼
glass_ui.py:1033  _draw_traffic()      ← 画到卡片上
```

## 字段怎么显示到界面上

底部那张流量卡片，四个位置分别对应：

| 界面位置 | 数据来源 |
|---|---|
| 左上「17天后重置」 | `format_reset_days(info.expire)` — 按**北京时间**算天数（`traffic.py:70`） |
| 右上「46.5%」 | `info.percent` |
| 进度条长度 | `info.percent` |
| 进度条颜色 | `traffic_bar_color()` — <50% 绿 / 50–70% 黄 / >70% 红（`traffic.py:298`） |
| 左下「232.62 GB」 | `format_bytes(info.used)` |
| 右下「500.00 GB」 | `format_bytes(info.total)` |
| 中间「缓存」 | 本次拉取失败、正在沿用上次数据 |
| 中间「获取数据失败」 | 连续失败 ≥10 次 —— **接口/网络有问题** |
| 中间「未配置接口地址」 | `url` 为空 —— **是没填地址，不是网络问题** |

最后两条是刻意分开的：一个让你去查网络和接口，一个让你去填地址，不用猜。

---

## 改完怎么验证

不用开程序，直接在项目目录跑（会打印原始响应 + 解析结果）：

```bat
cd C:\GROK\jiankong
py -3.14 -c "import json,sys; sys.path.insert(0,'.'); from app_storage import CONFIG_PATH; from traffic import fetch_traffic; c=json.load(open(CONFIG_PATH,encoding='utf-8'))['traffic']; i=fetch_traffic(c['url'],c['proxy']); print(i); print('used=%s total=%s pct=%.2f%%'%(i.used,i.total,i.percent))"
```

正常输出长这样：

```
TrafficInfo(upload=122627453971, download=127052394161, total=536870912000,
            expire=1786492799, ok=True, error='', status='ok')
used=249679848132 total=536870912000 pct=46.51%
```

`ok=True` 且 `total` 不为 0 就说明接口通了。

地址没填时会直接短路，不会去请求空 URL：

```
TrafficInfo(upload=0, download=0, total=0, expire=0, ok=False,
            error='traffic.url 未配置', status='unconfigured')
```

---

## 几个坑

1. **直接改用户配置要重启；用设置窗则即时生效。**
   用户配置只在启动时读一次；设置窗点「保存并应用」会热更新并立即重新拉取。

2. ~~代码里有硬编码的旧 VPS 地址~~ —— **已于 2026-07-26 移除**。
   现在 `glass_ui.py:356/357` 是 `traf.setdefault("url", "")`，缺字段时留空而不是
   回落到上一台机器。改之前的行为很坑：删掉 `url` 后界面照常显示流量数字，
   但那是旧 VPS 的数据，看不出任何异常，排查起来极费劲。

3. **`interval_sec` 别手改**，它每次启动都会被 `interval_min × 60` 覆盖
   （`glass_ui.py:358`）。要改间隔就改 `interval_min`，或者直接在设置窗里调。

4. **API key 只存在程序目录的 `config.json` 里，源码没有硬编码副本。**
   分享整个程序目录前，手动把 `traffic.url` 和 `traffic.proxy` 清空，同时不要附带
   可能保存旧地址的 `config.json.bak`，也不要分享包含完整地址的设置截图。

5. **关掉流量卡要改 `cards.traffic`，不是 `traffic.enabled`。**
   后者现在只是自动同步的镜像（供外部程序读取），手改会在下次规范化时被覆盖回去。
   早期只有 `traffic.enabled` 的配置在首次启动时会自动迁移到 `cards.traffic`。

6. **`total` 必须有值**。有些面板的接口在流量未初始化时会返回 `total=0`，
   程序会直接判定为失败并显示「获取数据失败」，不是网络问题。
