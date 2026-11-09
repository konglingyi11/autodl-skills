# 创建、克隆与保存镜像

下文命令均从 skill 根目录运行。默认使用子账号环境变量登录；主账号在命令前加 `--auth wechat` 或 `--auth sms`，每次执行单独登录。登录步骤见 [README](../README.md)，接口依据见 [HTTP 接口与证据](http-api.md)。

## 1. 查询并准备配置

创建前查询区域、目标主机和镜像。迁移时先运行 `list`，再用 `detail <源实例UUID>` 读取准确的区域、镜像、配置与状态：

```text
python scripts/autodl_instance.py list
python scripts/autodl_instance.py detail <源实例UUID>
python scripts/autodl_instance.py machines --region <区域标识>
python scripts/autodl_instance.py machines --machine-id <目标主机ID>
```

匹配 GPU 显存与数量、CPU 架构、内存、磁盘和框架/CUDA 要求。查询空闲 GPU 与实例槽位，不能仅看主机 GPU 总数。脚本检查空闲 GPU、镜像架构/CUDA 与磁盘容量；工作负载的其他要求、系统盘可用空间和镜像缓存仍需结合实际信息判断。

配置 JSON 只保存资源选择，不保存凭据。使用查询到的实际标识替换以下示例：

```json
{
  "machine_id": 12345,
  "gpu_count": 1,
  "charge_type": "payg",
  "duration": 1,
  "expand_data_disk_gib": 0,
  "base_image_uuid": "<已核验的基础镜像UUID>",
  "instance_name": "experiment"
}
```

复用私有镜像时，用 `private_image_uuid` 替代 `base_image_uuid`，二者必须且只能提供一个。用 `images` 获取私有镜像 UUID；脚本还会检查镜像完成状态及按架构过滤的可用目录。克隆会保留源实例的社区/应用镜像字段，当前配置接口不支持直接新建社区应用。

同区域克隆的配置：

```json
{
  "machine_id": 12345,
  "gpu_count": 1,
  "charge_type": "payg",
  "duration": 1,
  "expand_data_disk_gib": 50,
  "copy_data_disk": true,
  "ft_sparse": false
}
```

`copy_data_disk` 必须显式为 `true` 或 `false`；系统盘始终复制。代码、数据或检查点在数据盘时，应选择复制数据盘；指向其他磁盘的符号链接不会复制其目标。复制数据盘时，目标容量不得小于源实例已分配容量。`expand_data_disk_gib` 是额外扩容量，单位 GiB，不是磁盘总容量。

克隆和保存镜像要求源实例已关机。关机前检查活动任务；不要为了执行本流程自动中断未授权的工作。克隆的镜像字段来自源快照，不允许接管源服务地址或克隆到其他账号。服务端仍可能限制每日克隆次数，遇到配额拒绝时报告原因。

## 2. 生成只读预览

```text
python scripts/autodl_instance.py create --config create.json
python scripts/autodl_instance.py clone <源实例UUID> --config clone.json
python scripts/autodl_instance.py save-image <源实例UUID> --name <唯一镜像名称>
```

未加 `--execute` 时只查询状态并准备请求，输出配置、当前报价或镜像计费规则，以及 `request_sha256`。保存镜像使用 `try_again: false`，名称已存在时拒绝提交，包括仍在保存的同名镜像。

核对源与目标、GPU/显存/数量、CPU/内存、区域、磁盘、镜像、计费周期、实例费用和持续存储费用：

- 价格接口金额单位为千分之一元；`price.display_cny` 按官方前端的逐产品算法转换。
- `data_disk_info.data_disk_deal_daily_price / 1000` 是额外数据盘的日费用，不能与其他周期的金额直接相加。
- 按量计费的 `duration: 1` 仅用于报价，不会在一小时后自动关机。提交或后续开机时的价格可能变化。
- 镜像预览会读取官方计费规则和当前账号用量。新镜像大小与最终费用需保存后才能确定；计费按自然日总容量峰值计算，账号实际额度优先，不能承诺免费。

## 3. 提交一次并保留回执

在用户已有授权覆盖具体操作与费用时，重复同一命令并追加：

```text
--execute --request-sha256 <预览返回的指纹> --receipt <新的绝对回执路径.json>
```

脚本重新查询状态并生成请求；报价、源或镜像身份、主机配置、请求字段变化会使指纹失效。报价变化是否需要新的用户决定取决于已有授权范围。提交前，脚本以独占方式创建回执，记录源和既有资源 UUID，每次执行最多提交一次写请求，不在传输失败后自动重试。

回执所在目录必须已存在；回执文件名不能复用。保留成功和未决回执，不使用新文件名绕过未确认的提交。

| 回执状态 | 含义 | 下一步 |
| --- | --- | --- |
| `SUBMITTING` | 已准备提交，但结果未确认；可能发生超时或进程中断 | 比较远端实例/镜像/订单与回执中原有 UUID，查清结果后再决定是否重试 |
| `ACCEPTED` | 服务端已接受请求 | 使用实际返回的资源标识查询完成状态 |

## 4. 预付费订单单独支付

`charge_type` 可以为 `daily`、`weekly`、`monthly`，`duration` 表示相应周期的正整数数量。创建或克隆先提交订单，不会自动支付。使用实际返回的订单 UUID：

```text
python scripts/autodl_instance.py order <订单UUID>
python scripts/autodl_instance.py pay-order <订单UUID>
```

`pay-order` 默认预览准确的创建/克隆订单及 `deal_price / 1000` 元金额。确认授权覆盖后，用本次支付预览的指纹、新回执和 `--execute` 提交。支付使用 `PUT order/pay`，拒绝已支付或不属于创建/克隆类型的订单；订单有效期和状态仍以服务端为准。

## 5. 核验最终结果

| 操作 | 完成依据 |
| --- | --- |
| 按量创建 / 克隆 / 预付费支付 | 使用实际实例 UUID，通过 `list` / `detail` 核对配置与运行状态；订单 UUID 和接受响应不代表实例已就绪 |
| 保存镜像 | 用 `images` 核对名称、源实例和实际镜像 UUID，状态按服务端的 `saving` / `finished` / `failed` 报告；保存中不要修改或启动源实例 |
| 工作负载迁移 | 基础设施就绪后，在授权范围内检查 GPU、CUDA、文件、检查点、进程与锁，再按对应项目要求恢复工作 |

使用有界查询跟踪状态，仅在有进展、失败或需要用户操作时更新。查询超时不代表任务失败。HTTP 资源状态不能证明磁盘复制完整或训练已恢复。

GPU 不足时，在明确的库存拒绝后刷新主机并在预算内重新选择。不要反复启动缺货源实例、降低显存要求、扩大预算、释放源实例或启动重复训练。跨区域迁移需要私有系统镜像及另外安排的数据传输；本工具不提供跨实例文件传输。保留源实例作为回退时，还需核对实际保留期限。
