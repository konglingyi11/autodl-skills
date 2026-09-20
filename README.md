# AutoDL Instance skill

用于维护 Codex 的 `autodl-instance` skill。仓库根目录即 skill 目录：`SKILL.md` 是入口，`references/` 记录 HTTP 接口、实例创建、镜像和迁移流程，`scripts/` 提供子账号实例与镜像操作、资源查询和离线测试，`agents/` 提供界面元数据。

## 安装与同步

在新环境中，将本仓库克隆到 `$CODEX_HOME/skills/autodl-instance`。如果已安装此 skill，可在本仓库修改和提交后，将 `SKILL.md`、`agents/`、`references/`、`scripts/` 同步到该目录；本仓库和现有安装目录是两个独立副本，修改其中一处不会自动更新另一处。

使用 HTTP 辅助脚本需要 Python 和 `curl_cffi`：

```powershell
python -m pip install curl_cffi
python scripts/autodl_instance.py --help
python -m unittest discover -s scripts -p test_autodl_http.py
```

运行需要登录的 HTTP 操作前，在当前进程环境中配置 `AUTODL_SUB_ACCOUNT` 和 `AUTODL_SUB_PASSWORD`。不要将凭据、实例密码或本地 `.env` 文件提交到仓库。支持的命令、操作授权和预览流程以 [SKILL.md](SKILL.md) 和 [创建与迁移参考](references/create-and-migrate.md) 为准。
