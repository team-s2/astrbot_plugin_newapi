# astrbot_plugin_newapi

用于在 AstrBot 中只读查询 [new-api](https://github.com/QuantumNous/new-api) 管理信息的插件。支持按会话绑定多个 new-api 实例、查看渠道、查询 Codex 与智谱 Coding Plan 订阅用量，以及将 Dashboard Flow 绘制成适合聊天发送的 Sankey 图。

## 命令

命令不要求 AstrBot 管理员权限，只有绑定了 new-api 实例的会话可以执行：

- `/newapi channel`：列出所有渠道；订阅渠道会同时查询 Account Info
- `/newapi channel <渠道名称或 ID>`：查看渠道详情和可用的 Account Info
- `/newapi quota`：生成额度图，展示全部渠道的周额度、5 小时额度、当前窗口和剩余主动重置次数
- `/newapi flow [时间范围]`：生成流图并发送图片；支持 `30m`、`1h`、`7d` 等格式，不传时使用后台配置

插件不会修改渠道、消费重置次数或执行其他写操作。

## 安装与配置

从 AstrBot 插件管理页面安装本仓库，然后填写插件配置：

1. 在每个 new-api 实例的个人设置中创建 Access Token。
2. 在需要绑定的 AstrBot 会话中使用 `/sid` 获取完整 UMO。
3. 在插件配置的“new-api 实例”中添加实例，填写名称、地址、Access Token、用户 ID、绑定的 UMO 和该实例的 Flow 可见阶段。
4. 选择所有实例共享的 Flow 默认时间范围、Top N 和溢出处理方式。

每个 UMO 只能精确绑定到一个实例。未绑定的会话无法执行查询，也不会回退到其他实例；同一个实例可以绑定多个 UMO。例如：

```yaml
instances:
  - name: 校内 new-api
    base_url: https://new-api.example.com
    access_token: <access_token>
    user_id: 1
    umos:
      - qq:GroupMessage:123456
      - telegram:GroupMessage:789012
    flow_stages:
      - token
      - model
      - channel
```

插件使用以下请求头访问 new-api：

```http
Authorization: Bearer <access_token>
New-Api-User: <user_id>
```

`channel` 和 `/api/data/flow` 需要 new-api 管理员权限。若 Flow 需要显示 `token` 或 `node` 阶段，应为相应实例配置 Root 用户的 Access Token；普通管理员能够获得的 Flow 维度较少。

## 额度图片

`/newapi quota` 查询当前会话绑定实例的全部渠道，不受 `channel_list_limit` 限制。支持 Codex（类型 57）、智谱 Coding Plan（类型 100，以及类型 26 配合 `glm-coding-plan`）；不支持额度查询的渠道不显示。单个渠道认证失败、超时等不会中断其他渠道的展示，错误汇总在图片底部。

图片按上游实际返回的窗口显示周限额、5 小时限额，每行并列显示剩余百分比和重置倒计时，不显示 token 计数；没有的窗口不占位。Codex 和智谱均支持此布局，Codex 附加限额（包括 Spark）不在此图中展示。深色表示已用额度，浅色表示剩余，橙线表示当前时间。周窗口与重置卡共用日期轴，5 小时窗口使用独立的相对小时轴，不显示下方刻度文字。窗口起点按重置时间减去周期推算，不代表历史请求分布。时间统一为 UTC+8；未知用量不视为 0，已到期窗口提示等待上游更新。

主动重置卡仅显示可用张数与到期标记，已使用的重置卡记录不展示，也不参与时间轴范围计算。Codex 显示全额重置卡，智谱分别显示周重置卡和 5 小时重置卡。同一时刻到期的卡合并计数；菱形标记到期，24 小时内到期的卡用红色提醒。日期轴自动扩展以覆盖全部周窗口与可用重置卡到期时间，不按日期范围丢弃数据；卡片标记仅显示「xn」（如 x1、x4），每种重置卡共用一条时间线，标签在上下两侧交替显示，密集时仅增加文字层级以避免重叠。上游只返回张数时不推测有效期；独立重置卡接口失败时可回退到用量接口中的次数，错误显示在底部。查询不会消耗重置卡。

额度图片复用 `font_path` 字体配置，建议安装中文字体。请求支持 `http_proxy` / `https_proxy` 环境变量；每次额度查询最多同时发出 4 个上游请求。

## 流图配置

可见阶段支持：

`user → node → token → group → model → channel`

每个 new-api 实例独立配置可见阶段，图片始终严格按照上述顺序从左向右绘制。默认只显示 `token → model → channel`，以 API 返回的实际 `token_used` 决定流宽并在节点标签中显示 token 数。所有实例共享 Top N 和溢出处理设置；默认每阶段保留 Top 20，其余数据合并为 Other，也可以选择直接隐藏 Top N 以外路径。

流图以 3600 × 2240 为最小画布，并根据实际列数、标签宽度和各列节点数量自动扩大。提高 Top N 会显示更多节点，同时自动增加画布高度，避免标签从上下边缘溢出。

命令行时间范围支持分钟（`m`）、小时（`h`）、天（`d`），最大为 30 天；必须带单位，裸数字不会被接受。

渠道列表中的“计费额度”来自 new-api 的 `used_quota`，并使用 `/api/status` 返回的 `quota_per_unit` 将渠道 USD 余额换算为相同单位。它是 new-api 的内部计费额度，不等同于 Flow 中的实际请求 token 数。

绘图依赖 Pillow。插件会依次寻找 Noto Sans CJK、微软雅黑、苹方和 DejaVu Sans；为了正确显示中文，推荐在自定义 AstrBot 镜像中安装 Noto CJK 字体，例如 Debian/Ubuntu 镜像中的 `fonts-noto-cjk`。也可以通过 `font_path` 指向镜像内的 TTF/TTC 字体文件。

## 兼容性

- Python 3.10+
- 支持插件依赖自动安装的 AstrBot 版本
- 需要包含 Dashboard Flow、Codex usage 和智谱 Coding Plan usage API 的 team-s2/new-api 版本
