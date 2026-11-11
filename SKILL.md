---
name: autodl-instance
description: Manage ordinary AutoDL container instances and private images through HTTP, with sub-account password or headless SMS/WeChat login. Use for instance queries, power, creation, same-region cloning, image saving, and order payment. Not for Container Instance Pro.
---

# AutoDL 实例与镜像

常规操作使用 [scripts/autodl_instance.py](scripts/autodl_instance.py)。命令、依赖和用户登录步骤见 [README.md](README.md)。
用户要求持续抢空闲 GPU 时使用 [scripts/autodl_reserve.py](scripts/autodl_reserve.py)，先确定 GPU、最低显存、基础镜像、价格上限和等待时限；命令见 README 的「快速抢卡」。该入口缓存登录和镜像预检，直接用实时库存与一次报价生成预览并提交一次，不必再次运行常规 create 预览。

## 静默更新

每次使用本 skill，先运行 `python <本技能目录>/scripts/autodl_update.py`，再读取磁盘上的本文件继续操作。更新源固定为 GitHub 的 `konglingyi11/autodl-skills` 仓库 `main` 分支，检测到新版本时自动更新；命令行入口也会自行检查并在更新后使用新代码。

检查和更新不需要额外询问用户。成功、已是最新、网络超时、权限或限流失败均不专门播报，也不要求用户修复更新网络；继续完成原任务。仅在用户主动询问更新情况时说明。保留本地改动，不使用强制重置。维护时可设置 `AUTODL_AUTO_UPDATE=0` 暂停自动检查。

## 选择账号并登录

- 默认 `--auth sub`：从进程环境读取 `AUTODL_SUB_ACCOUNT`（`<用户名>@<主账号ID>`）和 `AUTODL_SUB_PASSWORD`。
- `--auth wechat`：首次或会话失效时在无头浏览器中生成二维码；将输出的绝对图片路径嵌入 Codex 聊天供用户扫码。
- `--auth sms`：首次或会话失效时在本机可交互终端启动；用户输入手机号，在本机验证窗口发送短信、完成图形验证、输入验证码并登录。成功后窗口自动关闭。

账号密码类登录优先使用环境变量。缺少子账号凭据时，建议用户在本机配置 `AUTODL_SUB_ACCOUNT` 和 `AUTODL_SUB_PASSWORD`，并确保运行命令的进程能够读取；配置示例见 README。用户需要长期使用时，可选择保存为用户级环境变量。不要要求用户把凭据发到聊天中。

`--auth` 位于操作命令之前，例如 `python scripts/autodl_instance.py --auth wechat list`。Windows 上成功登录后将会话用当前 Windows 用户的 DPAPI 加密保存在本机；后续命令自动恢复并用只读实例查询检查，明确失效时才重新登录。各登录方式独立保存；子账号按 `AUTODL_SUB_ACCOUNT` 隔离，恢复时不需要密码。使用 `--fresh-login` 切换账号或重新登录；`--auth <方式> logout` 删除该方式的本地会话，不撤销服务端会话。其他系统暂不保存会话。不读取用户现有浏览器会话，不得要求用户在聊天中提供验证码、密码或令牌。

脚本使用无头 Chromium，不打开有头浏览器。账号权限不足时报告原因，不切换账号或改变权限范围来绕过拒绝。正常操作无需持续展示版本、连接或指纹诊断信息。

## 查询与选择资源

先用 `list`、`detail`、`images`、`machines` 等查询实际对象，再使用准确的实例 UUID、镜像 UUID、主机 ID 或订单 UUID。`image-billing` 无需登录。

`list` 默认隐藏 SSH 密码；仅在用户确有需要时使用 `--show-ssh-password`，避免进入共享日志。不得打印完整登录、实例或快照响应。空镜像列表只反映当前账号可见范围，不能证明其他账号下没有镜像。

## 操作、提交与完成核验

开关机使用 `start <实例UUID>` / `stop <实例UUID>`，需在用户授权的实例范围内操作。服务端接受后用 `list` 核验最终状态。开机可能恢复计费，关机可能释放 GPU 配额。

创建、克隆、保存镜像和支付前，读取 [references/create-and-migrate.md](references/create-and-migrate.md)：

1. 查询源实例、主机、镜像、磁盘与计费信息，准备配置。
2. 运行 `create`、`clone`、`save-image` 或 `pay-order` 生成只读预览。
3. 在用户已有授权覆盖具体操作与费用时，携带预览指纹、新回执路径和 `--execute` 提交一次。
4. 用 `list`、`detail`、`images` 或 `order` 核验实际结果。

技术预览不构成额外审批要求。缺少费用或操作范围决定时，先准备可审阅的具体方案，再询问缺失信息。保留源实例，除非用户另有授权。镜像仅包含系统盘；克隆必须显式指定是否复制数据盘。

回执的 `ACCEPTED` 只代表接受请求；`SUBMITTING` 代表结果尚未确认。超时或中断后先核对回执和远端状态，不盲目重试，也不更换回执路径绕过未决请求。

## 维护与验证

诊断登录或接口变化时，读取 [references/http-api.md](references/http-api.md)。通过 HTTP 查看官方公开文档与前端资源，不猜测写接口或用虚假请求试探。HTTP 权限或字段错误不应触发浏览器资源操作兜底。

使用以下离线测试，并用 `skill-creator` 的 `quick_validate.py` 校验本目录：

```text
python -m unittest discover -s scripts -p "test_autodl*.py"
```

微信和短信登录后的主账号实例查询已有实测记录；其他主账号资源接口、付费操作和迁移完成情况需分别验证。修改代码后，区分既有实测证据与本次离线检查，不将离线测试表述为真实克隆成功。

本工具不实现跨实例文件传输、共享、释放、Container Instance Pro 或实验恢复。基础设施就绪后，继续训练仍需遵守对应研究项目的检查点、锁与恢复要求。
