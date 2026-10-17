# 新版管理工作区

本次候选以 MCSManager 新版的 Ant Design Vue、卡片、顶部导航/侧栏、深浅配色和 xterm 交互为参照，继续使用断界自己的账号、权限、RCON、systemd、数据中心和审计。它不实现 MCSManager 的 HTTP/Socket.IO 协议，也不需要把游戏实例交给 MCSManager Daemon。

## 已接入的流程

- 实例列表、搜索、四服状态、在线数、启停/重启和既有服主强制操作。
- 总览读取面板主机的 CPU 核数、系统负载、内存（Linux）和数据磁盘；没有采样时明确留空。实例内存和累计 CPU 时间来自固定 systemd 服务。
- xterm 实时读取实例 `logs/latest.log`，游标续读/轮转、暂停、自动滚动、清屏；服务器指令仍使用既有白名单、预览确认、原请求回执与审计。它是日志与受限指令终端，不提供主机 shell 或原始 PTY。
- 文件浏览器：目录浏览、当前目录搜索、文本编辑、文件新建、目录新建、上传下载、重命名、回收与恢复。CodeMirror 编辑器提供行号、JSON 语法着色、搜索替换和撤销重做。上传先写入私有暂存区，确认后原子替换，不把模组二进制写进管理员数据库。文本保存检查源文件 SHA-256；外部改动后拒绝覆盖。流式上传上限 512 MiB，文本编辑上限 8 MiB；下载使用原生流式响应，目录一次最多展示 5,000 条。目录回收及恢复要求实例已停止。
- `server.properties` 的受限设置编辑：保留 RCON 密码及其他未显示字段。更改重启后生效。
- 默认 `world/` 的离线 ZIP 备份、恢复、删除；4 GiB/10 万文件上限。恢复前保留原世界到 `instance-data/restore-rollback/`，不自动清除。
- 持久计划任务：单次/按分钟间隔，保存、公告、启动、空服停止/重启及离线世界备份。执行时重查创建者权限；失败、权限撤销和上次执行不确定时停用，不重放未知结果。
- 在线玩家、玩家资产、补偿邮件、语音/处罚、截图检查、管理员及审计保留原实现，在统一工作区中显示。

尚未实现 MCSManager 的 Docker/镜像商店、跨机器 Daemon 注册、任意程序部署、原始 PTY、任意启动参数编辑和在线世界快照。没有伪造这些页面或把未接入按钮标为可用。

## 本地构建与验收

```powershell
npm.cmd --prefix ui ci --no-audit --no-fund
npm.cmd --prefix ui run build
npm.cmd --prefix ui test
python -B -m unittest test_management -v
python -B -m unittest test_order_clear -q
python -B -m unittest test_workspace_browser.WorkspaceBrowser -v
python -B preview.py --workspace
```

浏览器测试在临时 Java 数据中心、RCON 测试端口及临时实例目录中运行，绝不接触正式资产和四服文件。预览一次性地址仅绑定本机，生产程序不导入预览代码。

## 生产接线

1. 先保存生产面板代码、管理员数据库及现有私有配置。使用候选中的生产 Python 文件和完整 `web/`，不要带入测试账户或预览程序。原有配置与数据库不能被示例覆盖。
2. 每个固定服务可配置 `servers[].directory` 为真实实例目录；未填时只尝试从固定白名单 systemd unit 的 `WorkingDirectory` 读取。找不到时拒绝文件与日志操作，不退回任意主机目录。核对面板服务身份对这些目录的必要读写权限，写入和恢复须能够保留游戏文件属主/属组；不能保留时拒绝提交，不授予任意 shell/sudo。
3. `management.storage` 默认是管理员数据库旁的 `instance-data/`。生产建议 `/var/lib/breakline-admin/instance-data`，保留访问权限；回收、备份、恢复前世界均在此处。不要放进游戏公开文件目录。
4. `workspaceDefault=true` 使 `/` 显示新版；`/workspace` 始终为新版入口。设为 false 可让 `/` 恢复旧界面。不要对外开放原始 RCON、数据中心或面板回环端口。
5. 只需重启面板服务，不需为替换界面重启游戏或数据中心。先做登录、管理员越权拒绝、四服状态、真实日志续读和实例目录只读验收；在测试文件中验证上传/编辑/回收。世界恢复与实际计划启停须选维护窗口另行验收。

Linux 生产使用 `/run/breakline-panel-file-owner.sock` 的 systemd socket activation：面板仍为非 root 且保留 NoNewPrivileges；每个请求由受限 root 辅助服务处理，校验 AF_UNIX 对端 UID，再限定到固定实例内面板新建的文件。辅助程序安装到 `/usr/local/libexec/breakline-panel-file-owner`，配置须 root 持有。模板见公开仓库的 `deploy/`。不需要授予面板 sudo 权限；原 sudo 调用仅作旧部署兼容。

systemd 的实例目录绑定挂载会让跨目录移动退化为复制，虽在同一磁盘也可能如此。因此回收恢复先在原父目录暂存并恢复属主/权限，再原子落位；不同属主混合的项目拒绝网页回收，交由主机管理员处理。部署前须保存 `getfacl -R -p -P` 输出，按目录授予必要 ACL 并在 systemd 的 `ReadWritePaths` 追加四个目录。

只回退界面/面板代码不会回退已提交的服务器配置、文件编辑、回收或世界恢复；文件操作应按操作号和审计记录单独核对。新增表是附加式的，管理员账号、密码和现有资产不迁移。

## 第三方声明

上游基线 `MCSManager/MCSManager@aab9f7fd1ff2eb9a38d9107a6c676709320bce9b`。复用的配色、尺寸及背景素材、改造后的 CardPanel 见 `ui/src/vendor/` 和 `ui/src/CardPanel.vue`；完整许可证和说明保存在 `web/mcsm/MCSManager-LICENSE.txt`、`NOTICE.txt`。断界界面没有冒充 MCSManager 官方产品。
