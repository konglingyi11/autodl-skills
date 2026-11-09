# HTTP 接口与证据

本文件区分接口来源、登录实测和资源操作实测。公开前端中的方法与字段可以证明客户端契约，不能单独证明服务端接受请求或实际资源创建完成。

## 证据范围

| 时间 / 检查 | 已有证据 | 范围 |
| --- | --- | --- |
| 2026-09-20 公开前端检查 | 普通实例、镜像、订单、主机目录的路由、方法和请求字段 | 未执行真实付费操作；当时未配置子账号凭据 |
| 2026-11-09 微信登录 | 无头浏览器生成二维码，扫码完成登录后主账号实例查询成功，返回空列表 | 未据此验证其他主账号资源接口 |
| 2026-11-09 短信登录 | 用户完成图形验证、短信输入并确认显示实例列表 | 用户确认结果；不记录手机号、验证码或完整响应 |
| 本次流程整理 | 本地合成页面检查输入、缩放坐标、自动关闭、取消和超时；离线测试检查路由和提交约束 | 不访问真实账号，不发送短信，不产生费用 |

## 官方来源

历史资源接口取自 `https://www.autodl.com/assets/` 下的以下前端资源：

| 文件 | 依据 |
| --- | --- |
| `components.36e5184c.js` | HTTP 拦截器、子账号路由重写、Authorization/AppVersion、登录与 ticket 交换 |
| `instance.4daa8be5.js` | 实例列表、开关机、快照、按量/预付费克隆 |
| `service-code.30826265.js` | 主机搜索默认值、区域、基础镜像、创建和订单支付 |
| `myImage.682e0f65.js` | 镜像列表、可用私有镜像、用量、名称检查和保存 |
| `index.39ade2c9.js` | 镜像过滤字段、状态、存储单位和费用展示 |
| `index.6f23b99d.js` | 镜像保存请求、克隆字段和磁盘容量 |
| `index.e8b814c8.js` | 创建/克隆完整请求、主机可用性和价格转换 |
| `getPrice.b8d5d4ee.js` | POST 报价预览 |
| `utils.a30776c6.js` | 可用镜像的架构和芯片过滤 |
| `system-image.vue_vue&type=style&index=0&lang.d3e67626.js` | 镜像查询和元数据 |
| `index.acd728e3.js` | 订单响应层级、deal_price 和 PUT 支付 |

2026-11-09 登录检查涉及 `login.2d3aa78c.js`、`wx-login.0fdffe6c.js`、`components.27efcd29.js` 和 `service-code.bb17a80f.js`，分别用于确认短信验证、微信二维码轮询、登录接口和页面路由。主账号登录继续由官方页面处理 ticket 与令牌交换，不直接重写这些验证请求。

官方文档：[价格](https://www.autodl.com/docs/price/)、[镜像磁盘范围](https://www.autodl.com/docs/image/)。公开资源文件名会变化；原文件失效时，通过网站 HTML 和导入关系重新定位，不批量猜测 URL 或写接口。

## 登录与账号路由

### 子账号

1. `GET /version.json` 获取 `AppVersion`。
2. `POST /api/v1/sub_user/login` 发送 `sub_name` 和密码的 SHA-1 摘要，取得 ticket。
3. `POST /api/v1/sub_user/passport` 发送 ticket 与设备信息，取得令牌。
4. 后续普通资源接口使用 `/api/v1/sub_user/<resource>`。

凭据来自进程环境变量，令牌仅保存在内存。子账号登录不需要 Playwright，也不读取浏览器会话。

### 主账号

`--auth wechat` 与 `--auth sms` 使用独立的无头 Chromium 上下文，访问官方 `/wx-login` 或 `/login` 页面。用户扫码或在本机窗口完成验证后，脚本在官方源读取此次新会话的 `token` 与 `app_version`，交给 HTTP 客户端；浏览器和临时二维码随即关闭、清理。后续资源接口使用 `/api/v1/<resource>`。

主账号登录实现位于 `scripts/autodl_auth.py`，统一会话交接位于 `Client.login()`。短信窗口检测到有效令牌和版本后自动关闭；取消、超时或失败不继续资源操作。每次新登录会清除当前客户端原有认证头，避免失败后误用旧账号。

无头客户端沿用安装的 Chromium 版本，调整默认用户代理中的 HeadlessChrome 标记，并禁用默认自动化标记；未实现完整设备指纹模拟。官方图形验证由用户完成，登录是否接受仍由服务端决定。

主账号和子账号不得在权限拒绝时相互回退。账号路由根据已选择的登录方式确定；Pro 和其他存储接口的特殊路由不属于本工具范围。非 `Success` API 响应视为错误，不能当作空列表；不打印原始服务端消息或完整响应。

## 普通资源接口

以下为相对于账号资源前缀的路径；子账号前缀为 `/api/v1/sub_user/`，主账号为 `/api/v1/`。主账号当前仅实例列表具有真实账号查询证据，其余按公开客户端路由实现，需分别验证。

| 方法 | 资源 | 请求 / 响应契约 |
| --- | --- | --- |
| POST | `instance` | `page_index`、`page_size`、`status`、`charge_type`、`sub_name`、`unbind_sub_user`；返回 `data.list`、`data.max_page` |
| POST | `instance/power_on`、`instance/power_off` | `instance_uuid` |
| GET | `instance/snapshot` | 查询参数 `instance_uuid`；返回 `data.image`、`data.machine_info_snapshot` |
| POST | `image/private/list` | `order_uuid`、`date_from`、`date_to`、`page_index`、`page_size`；返回分页镜像 |
| POST | `image/private/get` | `cpu_arch`、`chip_corp` 数组；返回 `data` 数组 |
| POST | `image/private/usage` | 无请求体；容量字段 `current_usage`、`max_usage`、`free_size` 单位字节，`to_cost` 单位元 |
| POST | `image/private/check_name` | `image_id`、`image_name`；返回 `data.existed` |
| POST | `instance/image/save` | `image_name`、`instance_uuid`、`try_again: false` |
| GET | `region/list` | 区域目录，仅输出名称和标识 |
| POST | `user/machine/list` | 官方搜索默认字段及可选 `machine_id`、`region_sign_list`、`gpu_type_name`；返回 `list`、`result_total` |
| POST | `image/get` | `image_uuid`；返回 `data` 数组 |
| POST | `order/price/preview` | `machine_id`、`charge_type`、`duration`、`num`、`expand_data_disk`（字节）、`coupon_id_list` |
| POST | `order/instance/create/payg`、`order/instance/create/prepay` | `instance_info` 和对应的 `price_info` |
| POST | `order/instance/clone/payg`、`order/instance/clone/prepay` | 同上，另加源 `instance_uuid` |
| GET | `order` | 查询参数 `order_uuid`；订单在 `data.order` 内 |
| PUT | `order/pay` | `order_uuid`；单独执行创建/克隆订单支付 |

## 字段与单位

创建请求的 `instance_info` 包含 `machine_id`、`charge_type`、`req_gpu_amount`、`instance_name`、`expand_data_disk`，以及选择的 `base_image_uuid` 或 `private_image_uuid` 和前端默认空应用字段。克隆从源实例与快照继承镜像字段，增加 `copy_data_disk_after_clone`、`keep_src_user_service_address_after_clone: false` 和 `ft_sparse`。

仅当 `snapshot.image.image_type == "public"` 时，源基础镜像标识使用 `snapshot.image.image_name`。源数据盘基础配额来自 `snapshot.machine_info_snapshot.max_instance_disk_size`，扩容来自 `source.expand_data_disk_size`，不能替换成未证实的 `instance_disk_size` 字段。

镜像状态为 `saving`、`failed`、`finished`。可用目录不能代替镜像列表的完成状态检查。容量单位为字节；`storage_oss_sign` 表示缓存区域，缺失该字段不能证明镜像可在所有区域即时使用。

价格 API 金额除以 1000 转为元，`usage.to_cost` 已经是元。官方前端遍历 `discount_list`，按 `(origin_price - total_reduction) / 1000` 替换对应产品的展示价格；脚本同时保留安全源字段。数据盘持续费用另取 `data_disk_info.data_disk_deal_daily_price`。缺失字段仍然未知，客户端不能锁定报价后的服务端价格。

## 失败与完成边界

- 分页必须包含已识别的元数据；字段变化时失败，不静默截断。
- 预览只读；执行指纹绑定请求、源/镜像身份、主机配置和报价，并重新检查可用性。指纹不是用户授权证明，也不是服务端报价锁。
- 单次写请求前独占创建回执，超时保留 `SUBMITTING`；更换回执名不能解决未决请求。
- 子账号实时权限、其他主账号资源接口、真实支付、镜像缓存、克隆配额及最终交付仍需各自验证。接口错误应定向检查 HTTP 契约，不通过切换账号、浏览器资源操作或盲目重试处理。
- 完整磁盘复制、源实例保留期限和实验恢复需要另外核验；已有登录实测与本地测试不能代替迁移成功证据。
