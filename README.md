# astrbot_plugin_newapi

用于在 AstrBot 中只读查询 [new-api](https://github.com/QuantumNous/new-api) 管理信息的插件。支持查看渠道、查询 Codex 订阅用量与可用重置次数，以及将 Dashboard Flow 绘制成适合聊天发送的浅色 Sankey 图。

## 命令

命令仅限 AstrBot 管理员使用：

- `/newapi channel_list`：列出渠道
- `/newapi channel_show <渠道名称或 ID>`：查看渠道详情；Codex 渠道会同时查询订阅限额和重置次数
- `/newapi flow`：按后台配置生成流图并发送图片

插件不会修改渠道、消费重置次数或执行其他写操作。

## 安装与配置

从 AstrBot 插件管理页面安装本仓库，然后填写插件配置：

1. 在 new-api 的个人设置中创建 Access Token。
2. 填写 new-api 实例地址、Access Token 及其所属用户 ID。
3. 选择 Flow 的时间范围、可见阶段、宽度指标、Top N 和溢出处理方式。

插件使用以下请求头访问 new-api：

```http
Authorization: Bearer <access_token>
New-Api-User: <user_id>
```

`channel_*` 和 `/api/data/flow` 需要 new-api 管理员权限。若 Flow 需要显示 `token` 或 `node` 阶段，应使用 Root 用户的 Access Token；普通管理员能够获得的 Flow 维度较少。

## 流图配置

可见阶段按后台列表中的顺序绘制，支持：

`user → node → token → group → model → channel`

默认只显示 `token → model → channel`，以 `quota` 决定流宽，每阶段保留 Top 20，其余数据合并为 Other。也可以改用 `tokens` 或 `requests`，并选择直接隐藏 Top N 以外路径。

绘图依赖 Pillow。插件会依次寻找 Noto Sans CJK、微软雅黑、苹方和 DejaVu Sans；为了正确显示中文，推荐在自定义 AstrBot 镜像中安装 Noto CJK 字体，例如 Debian/Ubuntu 镜像中的 `fonts-noto-cjk`。也可以通过 `font_path` 指向镜像内的 TTF/TTC 字体文件。

## 兼容性

- Python 3.10+
- 支持插件依赖自动安装的 AstrBot 版本
- 需要包含 Dashboard Flow API 和 Codex usage API 的 team-s2/new-api 版本
