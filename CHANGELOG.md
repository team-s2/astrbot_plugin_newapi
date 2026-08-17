# 更新日志

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
