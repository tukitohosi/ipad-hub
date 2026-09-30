# iPadHub 副屏后台

`vendor/ipaddisplay` 是合并项目自己的完整源码副本。旧项目、已安装的 iPad互联、桌面快捷方式和旧用户配置不由这个后台更改。

## 构建

在工作目录执行 `scripts/build-display-engine.ps1`。需要已有的 Visual Studio 2022 C++ x64 工具和 CMake。构建使用独立的 `build/ipadhub-display`，输出 `build/ipadhub-display/Release/iPadHubDisplay.exe`，证据保存在 `build/ipadhub-display/evidence`。使用静态 MSVC CRT，沿用 Windows 系统提供的媒体、图形、输入和网络组件。

后台目标不编译旧 `main.cpp`、`TrayApp.cpp` 或旧应用资源。它复用 `SenderApp`、`DeviceCoordinator`、mDNS、USBMux、私有网络检查、稳定设备身份校验、H.264、输入注入与虚拟屏实现。原十个测试目标继续构建。

## 控制接口

启动参数：

```
iPadHubDisplay.exe --pipe \\.\pipe\<name> --session <id> --parent-pid <pid> --data-dir <absolute-display-directory>
```

后台连接父界面创建的本机双向命名管道，并核对管道服务器 PID 与 `--parent-pid` 一致。使用 UTF-8，每行一个 JSON 消息，最大 256 KiB、最大嵌套 32 层，拒绝重复 JSON 键、无效 UTF-8 和错误会话。

请求：`{v:1,session,request,command,payload:{...}}`。响应：`{v:1,session,request,type:"response",ok,payload,error}`。事件使用 `type:"event"`、空 request 和 `event:"hello|devices|status|released"`。

- `discover`：异步发现局域网与 USB 设备，立即响应已知列表，完成后发 devices。mDNS 搜索有固定时间预算；Apple 本机服务带超时；邻居记录只读，不扫描网段。停止后不继续开始新的发现。
- `configure`：支持 `profile:balanced|sharp|smooth`、`fps:30..120`、`bitrate_mbps:5..100`、`port:1..65535`、`preferred_device_id`、`auto_reconnect`、`require_private_network`，另保留 `taskbar_routing` 和 `launcher_mode:0..2`。传流时拒绝修改配置。
- `start`：使用 `target`、`device_id`、`transport:auto|usb|wifi` 和以上画质设置。target 接受主机名、IPv4、`host:port` 或 `usb:<selector>`。USB 与 Wi-Fi 身份分别处理，名称相同不会建立身份绑定。自动模式保留有界尝试和后备传输；一个尝试完整停止后才开始下一个。
- `status`：返回实际 Sender 快照。只有 `ConnectionPhase::Streaming` 对应 `active`。发现 USB 或记住设备不代表 OpenDisplay 已在前台，`receiver_ready` 仅在真实传流时为 true。
- `stop` / `shutdown`：请求停止、结束输入和辅助窗口、等待 Sender 回收、等待当前有界发现完成、检查自身已认领虚拟屏移除、释放旧托盘互斥锁，随后响应并发送 `released`，退出进程。`clean:false` 或非零退出意味着主界面不得直接开始另一模式。

主界面断开、停止读取管道或父进程死亡会触发同样的清理路径。控制通道不使用 stdout；日志独立写入 `<data-dir>/logs/display-<pid>.log`。启动后台不会自动开始传流。

## 数据与共存

只从 `--data-dir/config.json` / `.bak` 读取设置。保存使用临时文件、原子替换和 `.bak`；保留导入配置里的未知顶层字段和设备字段。后台不自行查找、迁移或覆盖旧应用配置；一次性复制迁移由 iPadHub 主应用负责。

只有新目标定义 `IPADHUB_ENGINE`，显示位置使用 `HKCU\Software\iPadHub\Display`；不会读取或删除旧 `Software\MouseLink` / `Software\opendisplay-win` 条目。原项目副本中的旧目标保持旧命名空间行为。

开始传流前获取 `Local\IpadConnect.Tray.v1`，若旧版 iPad互联已经运行，则拒绝连接并提示用户从其托盘菜单退出。新后台占用时旧托盘也会看到同一锁。原有跨进程设备锁与全局 VDD 锁仍然生效。

新后台不在 Sender 工作线程中自动弹 UAC 对话框，以免无法取消或阻止父程序退出。对于首次使用且尚未注册的 iPad 分辨率，status 提供 width/height 和失败说明。主界面设备工具可由用户点击后提升运行一次性助手：

```
iPadHubDisplay.exe --register-resolution <width> <height>
```

助手仅注册驱动支持的分辨率及旋转尺寸，使用既有规则验证参数，不创建虚拟屏。调用者必须先停止副屏；助手自己也拒绝旧托盘或副屏后台占用期间的注册。Parsec 驱动仍由用户独立安装；不内嵌驱动。

主界面使用 `ipadhub.maintenance.ResolutionTask(executable, width, height, parent)`，在用户明确点击注册后调用 `start()`。UAC 启动在短生命周期工作线程内执行，QTimer 以非阻塞方式等待助手完成，`progress(str)` 提供进度，`finished(bool,str)` 表示实际完成或启动失败。超过 30 秒仅显示仍在等待，不能据此假定已结束、杀死助手或解除设备工具占用。调用者须保留任务对象并在 `finished` 前阻止模式切换和退出。

## 验证边界

新增 `hub-json` 测试覆盖 JSON 语法、重复字段、Unicode 代理对、未知浮点字段往返及嵌套限制；`hub-pipe` 使用真实 Windows 命名管道和独立子进程，覆盖分片、错误会话、配置保存、未知字段保留、范围校验、发现、stop、shutdown、断管道、超大消息和父进程异常退出。

以上测试运行 `--no-device`，这一开关明确拒绝 start，不连接 iPad，不创建虚拟屏，不写旧设置。编译与这些测试不能替代真实设备的 USB/Wi-Fi、50 次切换、60 分钟传流、睡眠恢复与干净 Windows 安装验收。

`tests/test_display_host.py` 进一步使用实际 Qt `EngineProcess` 与编译后的 C++ 程序进行命名管道联调，只发送 status/shutdown，验证 hello、idle、clean release 和正常退出。`tests/test_maintenance.py` 的八项模拟测试验证参数、缺失助手、成功、UAC 取消、占用、超时保持 busy、未知进程状态保持占用与句柄只关闭一次；这些测试不会弹 UAC 或写驱动设置。
