# 视频号加热平台 - 自动化采集流程

## 一、登录后默认状态

登录成功后自动跳转到 **首页**：
- URL: `https://channels.weixin.qq.com/promote/pages/platform/home`
- 页面标题：视频号加热平台

## 二、首页结构

### 左侧导航栏

| 一级菜单 | 二级菜单 | 说明 |
|---------|---------|------|
| 首页 | - | 默认首页 |
| 订单管理 | 短视频订单 | 短视频加热订单列表 |
| 订单管理 | 直播订单 | 直播加热订单列表 |
| 数据分析 | 短视频数据 | 短视频加热数据统计 |
| 数据分析 | 直播标准订单数据 | 直播标准订单数据 |
| 数据分析 | 直播全域订单数据 | 直播全域订单数据 |
| 账户管理 | 账户信息 | 企业账户、视频号信息 |
| 账户管理 | 人员管理 | 运营者管理 |
| 资金管理 | - | 资金相关 |

### 首页主内容区

- 企业账户信息（账户名、视频号）
- 通用余额、优惠券数量
- 批量加热 / 快速加热入口
- 最新通知列表

## 三、关键页面 URL

| 页面 | URL 路径 |
|-----|----------|
| 首页 | `/promote/pages/platform/home` |
| 账户信息 | `/promote/pages/platform/common-account-info` |
| 短视频数据 | `/promote/pages/platform/short-video/promote-statistic` |
| 短视频订单 | 订单管理 -> 短视频订单 |
| 直播订单 | 订单管理 -> 直播订单 |

## 四、短视频数据页（数据分析 -> 短视频数据）

- URL: `.../short-video/promote-statistic`
- 筛选：全部订单、全部作者、日期范围、全部创建人
- 指标：消耗金额、消耗微信豆金额、播放、商品点击数、商品点击率、商品成交数
- 数据明细表头：订单日期、订单号、视频作者、视频、消耗金额、消耗微信豆金额、播放、商品点击数、商品点击率、商品成交数、人群定向
- 支持下载

## 五、我的订单页（直播订单）

- URL: `https://channels.weixin.qq.com/promote/pages/platform/live/order-list`
- 订单接口：
  - `searchLivePromotionOrderList` - 直播推广订单列表（主列表）
  - `searchRoi2PromotionOrderList` - 成交ROI订单列表
  - `getLivePromotionOrderOverview` - 订单概览

## 六、增量采集与断点续传

### 增量采集逻辑（列表倒序）

- 列表按 create_time 倒序，新订单在 page 1
- 从 page 1 开始翻页，每页处理完检查：若本页有订单的 promotion_id 已在 DB，说明到达「已采集边界」
- 停止翻页：后续页都是更老的数据，无需再采
- 不同时间运行：新数据在顶部，遇已存在即停，只采新增部分

### 断点续传

- 每页保存后写入 `data/collect_checkpoint.json`
- 中断后重跑：从 page 1 开始，遇已存在则停，不重复保存（upsert）
- `FULL_RESYNC = True`：全量重采，不检测边界，翻到底

## 七、再来一单页面（create-order）

### URL 结构

```
https://channels.weixin.qq.com/promote/pages/platform/create-order
  ?promoteType=live
  &orderType=standard
  &sourcePromotionId={promotion_id}
```

| 参数 | 说明 | 取值 |
|-----|------|------|
| promoteType | 加热类型 | `live` 直播 / `feed` 短视频 |
| orderType | 订单类型 | `standard` 标准订单 / `full` 全域订单 / `longterm` 长期计划 |
| sourcePromotionId | 源订单 ID | 订单的 promotion_id |

### 跳转逻辑

- **直接 URL 跳转**：无需从订单列表点击「再来一单」，构造上述 URL 即可打开预填页面
- 当前采集的订单来自「标准订单」tab，默认 `orderType=standard`
- 页面调用 `getLivePromotionOrderDetail` 接口（请求体 `{"promotionId":"xxx"}`，MCP 验证）

### 页面内容（预填）

- 加热对象、预计成交金额、订单名称、成交ROI、预算、加热时长、人群定向等

## 八、自动化流程（当前实现）

1. 打开登录页
2. 等待用户扫码登录
3. 登录成功 -> 自动跳转首页
4. 确认首页加载完成
5. 跳转订单列表页，增量采集（遇已存在则停），保存到 SQLite
6. 遍历消耗>500 的订单：先跳转「再来一单」采集配置，再跳转「详情页」

### create-order 页面配置采集

- 接口：`getLivePromotionOrderDetail`，请求体 `{"promotionId":"xxx"}`
- 存储：`order_create_config` 表，`config_json` 为 JSON，按大标题分组：
  - **选择加热类型**：加热对象类型、加热订单类型
  - **选择加热对象**：主播昵称
  - **选择加热方案**：预计带来金额、基础信息（订单名称、加热方式、放量模式、优先提升目标、成交ROI、加热素材）
  - **预算与时间**：订单预算、加热时长
  - **人群定向**：定向类型、观众性别、粉丝层推荐、名单推荐、观众年龄、设备、城市、兴趣
  - **其他**：其他支付方式

### 订单详情页采集

- URL: `https://channels.weixin.qq.com/promote/pages/platform/live/live-promote-order-detail-new?id={promotion_id}`
- 与列表「名称与编号」中的订单 ID 一致，如 `1771344000_728358`
- 再来一单采集完成后，跳转详情页并拦截接口采集数据，存储到 `order_detail_data` 表

**采集内容（均与 promotion_id 关联）：**

1. **加热信息**：订单名称、编号、加热目标、状态、预算、时间、人群定向等
2. **消耗进度**：消耗微信豆、预算微信豆、消耗进度
3. **直播间加热效果**：消耗总金额、曝光总人数、进入总人数、点赞总次数、评论总次数、新增总关注
4. **十分钟级数据**：时间序列，每时间点含 直播间消耗、曝光人数、观看人数、点赞次数、评论次数、新增粉丝数

**接口**（MCP 验证，见 data/api_verified.md）：
- `getLivePromotionOrderDetail`：加热信息（含 orderInfo：订单名称、加热目标、预算、人群定向等）
- `getLivePromotionOrdersTsIndicator`：消耗进度、直播间加热效果汇总、十分钟级时间线
