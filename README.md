# Breakline Admin Panel · 断界管理面板

以 MCSManager 新版交互为参照的断界服务器管理工作区，使用 Vue 3、Ant Design Vue、xterm、CodeMirror 与 Python 标准库后端。沿用断界账号、权限、RCON、systemd、数据中心和操作审计；不兼容 MCSManager HTTP/Socket.IO 协议，也不依赖 MCSManager Daemon。

## 功能

- 实例列表、主机信息、四个固定 systemd 实例的状态和在线人数。
- 实时日志控制台：续读、轮转、暂停、自动滚动；受权限限制的指令与预览确认。
- 文件管理：目录、搜索、文本编辑、上传下载、重命名、回收与恢复；文本保存有 SHA-256 冲突检查，上传先暂存再确认。文本上限 8 MiB，流式上传上限 512 MiB。
- 服务器配置编辑、离线世界 ZIP 备份与恢复、持久计划任务。
- 断界业务页面：在线玩家、资产、补偿邮件、语音处罚、截图检查、账号和审计。

Docker、镜像商店、跨机器 Daemon、任意程序部署、主机 shell/PTY 未实现。世界备份与恢复要求实例停止；未知执行结果不会自动重放。

## 构建与测试

需要 Python 3.10+、Node.js 20+；Linux 生产部署还需要 systemd、sudo、acl/libacl。

```sh
cd ui
npm ci
npm test
npm run build
cd ..
python -B -m unittest test_management -v
# Linux 专项：只在临时目录建立隔离文件，需 root 运行
sudo python3 -B -m unittest test_file_owner_helper -v
```

构建生成 `web/mcsm/`。后端无第三方 Python 依赖。测试管理文件、权限、冲突、上传、下载、回收、备份和计划任务使用隔离实例，不连接生产服务。

管理员可使用受限终端：查看经凭据字段过滤的实时日志、查询在线人数，执行公告、保存、警告、封禁等既有授权指令。候选按权限筛选，服务端再次校验；写操作须预览确认并留审计。管理员不获得原始指令、提权、服务器启停、实例文件或库存删除权限。

## 部署

1. 复制 `config.example.json` 到私有 `config.json`，设置域名、管理员数据库、数据中心地址、四个固定实例目录。示例不是完整的生产凭据配置。
2. 在服务环境中提供 `DELTA_SERVICE_TOKEN`、`DELTA_ADMIN_TOKEN` 和配置所指的 `PANEL_RCON_*`；不要把值写进仓库。RCON 仅连接本机。
3. 构建 UI，以独立非 root 账号运行 `python3 panel.py --config /etc/breakline-admin/config.json`。首次添加服主使用 `--bootstrap-owner --username NAME --uuid UUID`，密码交互输入。
4. 面板只绑定回环端口，前置可信 HTTPS 反向代理。代理须正确设置 Host 与 `X-Forwarded-Proto: https`。管理员数据库及管理暂存目录仅供面板账号读写。
5. 面板自带业务页面需要已有断界数据中心、语音/截图服务及相应游戏指令支持；本仓库不含这些私有服务或游戏资产。未配置服务不应当视为已接通。

### Linux 实例文件权限

面板保持非 root 运行。实例目录须授予面板账号必要的 ACL（目录 rwx、现有文件读取、未来文件默认 ACL），systemd 开启 `ProtectSystem=strict` 时，还需在 drop-in 的 `ReadWritePaths=` 追加四个实际目录与管理存储目录。改动前保存原 ACL，勿覆盖已有 drop-in。

若游戏实例由 root 运行，原子文件替换需要保留属主。安装受限辅助程序与 socket activation 模板：

```sh
sudo install -o root -g root -m 0755 file_owner_helper.py /usr/local/libexec/breakline-panel-file-owner
sudo install -o root -g root -m 0644 deploy/breakline-panel-file-owner.socket /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/breakline-panel-file-owner@.service /etc/systemd/system/
# 先核对模板中的 ReadWritePaths 与实际四个实例目录一致
sudo systemctl daemon-reload
sudo systemctl enable --now breakline-panel-file-owner.socket
```

配置必须由 root 持有且不可被组/其他人写入。面板保持非 root 和 NoNewPrivileges，不需要 sudo 权限。socket 权限 0600，仅允许面板账号连接；每个请求都校验 SO_PEERCRED 对端 UID，辅助服务仅保留属主/ACL 操作所需的能力。

辅助程序只接受有大小限制的 JSON，在固定四实例内，为面板新建的常规文件/目录继承同实例参考文件的属主与 ACL，拒绝符号链接、越界和特殊权限位。默认账号与配置位置固定，部署时保持一致。无 socket 的旧部署保留无参数 sudo 调用兼容，但启用 NoNewPrivileges 的生产服务应使用 socket。

回收恢复支持 systemd 绑定挂载导致的跨挂载复制：先在原父目录暂存、恢复属主后再落位。包含不同属主的项目拒绝网页回收，避免丢失属主信息。

详细功能边界、回退说明见 [WORKSPACE.md](WORKSPACE.md)。上线前备份代码、配置、管理员数据库和 ACL；只替换面板无需重启游戏服。回退代码不会撤销已经确认的游戏文件操作。

## 来源与版权

参考上游 [MCSManager](https://github.com/MCSManager/MCSManager)，基线 `aab9f7fd1ff2eb9a38d9107a6c676709320bce9b`。复用并修改的卡片、配色、尺寸和背景素材保留 Apache-2.0 声明，见 `ui/public/MCSManager-LICENSE.txt`、`ui/public/NOTICE.txt` 以及源文件标注。构建会输出运行时第三方许可证汇总。

本项目并非 MCSManager 官方产品。公开仓库不含生产配置、密钥、数据库、玩家数据、截图证据或游戏物品图标；第三方代码各自遵循其许可证。未另行授予本项目原创代码的许可证。

控制台指令输入支持中文/英文搜索、候选列表、参数提示、在线玩家名、目录中的已知子命令、Tab/Enter 选择和历史记录。选中候选不会直接执行；写入仍需预览确认。

玩家资产页支持“仓库物品／身上物品”切换。身上物品读取所选服务器的在线角色，包括快捷栏、口袋、穿戴装备及已装备容器内容；不会把离线或查询失败显示成空背包。服主可以开启删除模式，勾选最多32组，填写原因、预览并确认整组删除；不退款，确认时再次校验完整指纹。安全箱、钥匙包本体及非空容器受保护。

库存功能需要配套断界数据中心 `/v1/admin/panel/inventory-delete` 和游戏端 `deltainventoryadmin` 协议。后端/游戏端未升级时明确拒绝，不提供任意NBT编辑或绕过权限的通用删除。仓库事务保留幂等回执和审计前镜像，游戏端保存独立操作记录；不确定结果不会自动重放。该仓库只包含面板适配器，私有数据中心和游戏模组不是本仓库的一部分。
