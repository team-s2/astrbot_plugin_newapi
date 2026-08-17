# astrbot_plugin_newapi

用于在 AstrBot 中只读查询 [new-api](https://github.com/QuantumNous/new-api) 管理信息的插件。支持查看渠道、查询 Codex 与智谱 Coding Plan 订阅用量，以及将 Dashboard Flow 绘制成适合聊天发送的浅色 Sankey 图。

## 命令

命令仅限 AstrBot 管理员使用：

- `/newapi channel`：列出所有渠道；订阅渠道会同时查询 Account Info
- `/newapi channel <渠道名称或 ID>`：查看渠道详情和可用的 Account Info
- `/newapi flow [时间范围]`：生成流图并发送图片；支持 `30m`、`1h`、`7d` 等格式，不传时使用后台配置

插件不会修改渠道、消费重置次数或执行其他写操作。

## 安装与配置

从 AstrBot 插件管理页面安装本仓库，然后填写插件配置：

1. 在 new-api 的个人设置中创建 Access Token。
2. 填写 new-api 实例地址、Access Token 及其所属用户 ID。
3. 选择 Flow 的默认时间范围、可见阶段、Top N 和溢出处理方式。

插件使用以下请求头访问 new-api：

```http
Authorization: Bearer <access_token>
New-Api-User: <user_id>
```

`channel` 和 `/api/data/flow` 需要 new-api 管理员权限。若 Flow 需要显示 `token` 或 `node` 阶段，应使用 Root 用户的 Access Token；普通管理员能够获得的 Flow 维度较少。

## 流图配置

可见阶段按后台列表中的顺序绘制，支持：

`user → node → token → group → model → channel`

默认只显示 `token → model → channel`，以 API 返回的实际 `token_used` 决定流宽并在节点标签中显示 token 数。每阶段保留 Top 20，其余数据合并为 Other；也可以选择直接隐藏 Top N 以外路径。

命令行时间范围支持分钟（`m`）、小时（`h`）和天（`d`），最大为 30 天；必须带单位，裸数字不会被接受。

渠道列表中的“计费额度”来自 new-api 的 `used_quota`，并使用 `/api/status` 返回的 `quota_per_unit` 将渠道 USD 余额换算为相同单位。它是 new-api 的内部计费额度，不等同于 Flow 中的实际请求 token 数。

绘图依赖 Pillow。插件会依次寻找 Noto Sans CJK、微软雅黑、苹方和 DejaVu Sans；为了正确显示中文，推荐在自定义 AstrBot 镜像中安装 Noto CJK 字体，例如 Debian/Ubuntu 镜像中的 `fonts-noto-cjk`。也可以通过 `font_path` 指向镜像内的 TTF/TTC 字体文件。

## 兼容性

- Python 3.10+
- 支持插件依赖自动安装的 AstrBot 版本
- 需要包含 Dashboard Flow、Codex usage 和智谱 Coding Plan usage API 的 team-s2/new-api 版本
