# 更新日志

## 1.6.2 - 2026-10-05

- 支持 Grok Subscription 渠道（类型 101），需要 new-api 提供 `/api/channel/:id/grok/usage` 接口。
- `/newapi quota` 额度图显示 Grok 周额度（按上游返回的周期绘制在周轴上）；Grok 没有 5 小时窗口和重置卡，重置卡一列改为显示月度额度的剩余百分比、已用 / 套餐美元额度与重置倒计时。单个窗口查询失败时在该行标注，不影响另一窗口。
- `/newapi channel` 渠道列表与渠道详情显示 Grok Account Info：套餐、周额度与按产品用量、月度额度、按需用量与预付余额。

## 1.6.1 - 2026-10-04

- `/newapi flow` 流图过滤渠道测试产生的用量（new-api 渠道测试日志不带令牌，`token_id` 为 0），不再显示为 `Deleted token #0`，也不再计入模型与渠道的总量。

## 1.6.0 - 2026-10-04

- `/newapi quota` 额度图重新设计。
- `/newapi flow` 流图改进。
- 额度图与流图改用 Skia（`skia-python`）绘制，移除 Pillow 依赖；优先使用 Noto Sans CJK SC 的 Regular / Bold 字重。
- **部署变更**：`skia-python` 在 Linux 上依赖系统库 `libEGL.so.1` 与 `libGL.so.1`，`python:*-slim` 等精简镜像需安装 `libegl1` 与 `libgl1`。

## 1.5.3 - 2026-09-28

- `newapi flow` 时间范围不再有上限，配合 new-api 解除 30 天查询限制后可统计任意历史区间（Sankey 节点数与时间范围无关，图片复杂度不受影响）。
- `flow_hours` 配置项同步取消 720 小时上限提示。

## 1.5.2 - 2026-09-28

- `/newapi quota` 图片中智谱重置卡改为分开显示 5 小时与每周两组剩余次数，不再合并为一个总数。
- 智谱重置卡查询失败时，额度图右列也会显示"未提供"与失败原因（此前仅文本命令可见）。

## 1.5.1 - 2026-09-28

- 智谱重置卡查询失败不再静默：Account Info 与额度图会显示 new-api 返回的失败原因（例如渠道凭据缺少 zcode JWT，需要重新 OAuth 登录）。

## 1.5.0 - 2026-09-28

- 智谱 Coding Plan 渠道类型跟随 new-api 从 62 改为 100（62 已被上游分配给 vLLM）；旧渠道仍可通过类型 26 + `glm-coding-plan` 地址识别。
- `/newapi channel` 的智谱 Account Info 展示 5 小时 / 每周重置卡数量与最早到期时间。
- `/newapi quota` 的智谱行显示可用重置卡总数，不再提示“上游未提供主动重置次数”。

## 1.4.1 - 2026-09-10

- `/newapi quota` 不再要求 AstrBot 管理员权限，与 `/newapi channel`、`/newapi flow` 保持一致，所有绑定实例的会话均可使用。

## 1.4.0 - 2026-09-09

- 新增 `/newapi quota`，生成全部渠道的额度图，突出周额度并展示 5 小时额度、当前窗口和主动重置次数。
- 支持智谱 Coding Plan 类型 62 与旧渠道配置；Codex 配额图忽略 Spark 附加限额。
- 单渠道读取错误或不支持的类型直接标注在图中；缺失字段不会作为零用量展示。
- HTTP 请求支持环境代理并保留 HTTP 错误状态码。

## 1.3.0 - 2026-08-17

- 将流图显示阶段移入各 new-api 实例配置，不同租户可以独立选择阶段。
- Flow 改为在单张 RGB 图片上直接混合绘制，避免多张全尺寸中间图造成内存峰值。
- Flow 渲染改为串行执行，并增加 6400 万像素安全限制和尺寸日志。
- Flow 图片发送完成后由 AstrBot 事件生命周期自动清理，不再残留临时 PNG。

## 1.2.3 - 2026-08-17

- 根据实际列数、标签宽度和每列节点数量动态计算 Flow 画布尺寸。
- Top N 较大时自动增加图片高度，避免标签重叠或从上下边缘溢出。

## 1.2.2 - 2026-08-17

- 将 Flow 最右列标签改为显示在节点右侧，并按实际文字宽度预留画布空间。

## 1.2.1 - 2026-08-17

- Flow 可见阶段固定按 `user → node → token → group → model → channel` 从左向右排列。
- 将 Flow 图片画布扩大为 3600 × 2240。

## 1.2.0 - 2026-08-17

- 支持配置多个 new-api 实例，并将多个 AstrBot UMO 精确绑定到对应实例。
- 未绑定会话不再查询默认实例，重复绑定会在加载配置时报告错误。
- Flow、渠道与 Account Info 查询均按当前会话选择独立客户端。

## 1.1.0 - 2026-08-17

- 将渠道列表与详情合并为 `/newapi channel [名称或 ID]`。
- 在渠道列表中显示 Codex 与智谱 Coding Plan 的 Account Info 套餐余量。
- 将渠道计费额度改为 token-style quota，并按实际 token 数绘制 Flow。
- 支持通过 `/newapi flow [时间范围]` 以 `m`、`h`、`d` 为单位临时指定最近 30 天内的统计范围。

## 1.0.0 - 2026-07-29

- 首次发布，支持查询 new-api 渠道、Codex 用量和 Dashboard Flow。
