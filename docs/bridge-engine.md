# iPadHub 键鼠后台

此后台使用新项目 `vendor/mouselink/open_bridge/desktop_app.py` 中的 `Worker`，继续复用原 `KVMController`、串口/BLE 协议、原生边缘与紧急返回实现。后台只创建 `QCoreApplication`，不创建原软件窗口或托盘；未修改外部旧软件、快捷方式、源码或旧数据目录。

## 启动与封装

开发入口为 `.venv\Scripts\python.exe -m ipadhub.engines.bridge`，应用入口调用 `ipadhub.engines.bridge.run(argv)`。参数为：

```
--pipe \\.\pipe\iPadHub-<unique-name>
--session <UUID>
--parent-pid <host-process-id>
--data-dir <LOCALAPPDATA\iPadHub\mouselink>
```

Qt 和原 MouseLink 依赖来自同一锁定环境。PyInstaller 需包含 `desktop_app`、顶层旧 `bridge`、`serial`、`pynput.keyboard._win32`、`pynput.mouse._win32` 及它们引用的模块，搜索路径包含 `vendor/mouselink/open_bridge`。`ipadhub.engines.bridge` 与旧顶层 `bridge` 是不同模块。

`MOUSELINK_DATA_DIR` 被设为传入的新数据目录。诊断单独保存为该目录的 `bridge.log`，不通过控制管道输出。后台只读写这个目录内的设置，保留未知字段和已迁入的校准记录；损坏设置先保留为带时间戳的 `settings.corrupt-*.json`。不会以源码内的示例校准文件替代用户数据。

## 管道协议 v1

本机 Win32 字节管道，UTF-8 JSON 行，单帧最大 256 KiB。客户端使用非阻塞读写，每次轮询的读取量有上限；主进程身份必须与管道服务端 PID 相同。主进程退出、管道断开、异常 JSON 或发送积压均触发关闭设备流程。

请求形态：

```json
{"v":1,"session":"<UUID>","request":"<unique-id>","command":"status","payload":{}}
```

响应形态：

```json
{"v":1,"session":"<UUID>","request":"<unique-id>","type":"response","ok":true,"payload":{},"error":""}
```

事件使用 `type:"event"`、`request:""` 和 `event:"hello|devices|status|calibration|released"`。不同 session 的请求忽略，同一 request 已完成时重发缓存响应而不重复执行操作。首个 `hello` 含 `engine:"bridge"` 和 `version:"0.1.0"`。`released` 事件含 `clean:true,released:true,engine:"bridge"`，主界面以 `clean:true` 确认清理完成。

| 命令 | 作用 |
|---|---|
| `discover` | 启动只监测设备的 Worker；返回 `devices` 与 `settings`，不会启动键鼠接管。 |
| `configure` | 原子保存设置；正在控制时使用原 `request_side_change` 安全重建控制器。启动待完成或校准中拒绝更改。 |
| `start` | 接受配置并等待 USB、BLE、定位通道真实就绪，确认 Worker 已开始控制后才响应成功；20 秒未成功则返回错误。 |
| `status` | 返回当前资源状态、设备信息和设置。 |
| `stop` / `shutdown` | 均调用完整 `request_shutdown`，等待线程真正结束，再发 `released` 并退出进程。 |
| `calibrate` | 运行原七点光标检查；只记录用户明确确认的校准结果。 |

配置字段：`mode:"free|locked"`、`speed:0.25..1.5`、`ipad_side:"left|right"`、`hotkey_return_enabled:bool`。线上速度是倍率，磁盘旧格式仍保存 `speed:25..150`。`start` 可额外传 `port:"COMn"`；实际检测的端口不匹配时拒绝启动。单次仅支持一块 ESP32-C3（VID 303A / PID 1001），沿用原 Worker 的多板安全限制。

设备列表项含 `id`（`bridge:` 加板序列号，无序列号则用端口）、`name`、`port`、`serial`、`vid`、`pid`。不得按名称将板身份与副屏身份合并。

状态含 `state`、`detail`、`running`、`usb`、`ble`、`ready`、`remote`、`busy`、`port`、`device_serial`、`released`、`settings`、`settings_warning`，并保留 Worker 的 `switching` 信息。只有 USB/BLE/定位都准备完成且控制器正在运行时报告 `active`。`remote` 单独表示键鼠当前是否在 iPad 上。

校准请求为 `{"orientation":"portrait|landscape"}`；收到 `calibration` 事件且 `success:true,confirmation_required:true` 仅表示七点检查序列执行完毕。界面让用户确认位置后再次发送 `{"orientation":"portrait|landscape","confirmed":true|false}`，后台才保存当前板的确认记录。切换了板或没有成功检查时拒绝确认。

## 生命周期和资源安全

后台占用与旧程序相同的 `Local\MouseLink.KVM.Desktop` 名称；发现现存句柄即拒绝启用并提示用户从旧软件托盘退出。不会关闭旧程序。旧程序本身并未检查同名 mutex 的占用结果，因此若它在新后台启动后再打开，实际串口独占仍由原实现检查，不能宣称已修改旧程序的互斥行为。

停止不仅是停止控制器，也包括关闭监测串口的 Worker。15 秒未退出时响应 `ok:false,released:false`，保留资源锁并继续等待；不得启动另一功能。界面必须同时确认 `released` **和该后台进程已经退出**，才可开始另一模式或刷机。这也确保原输入监听线程最终随进程退出回收。

自由模式的热键返回选项关闭时，`Ctrl + 左 Alt` 不启用；原实现通用紧急返回是 `Ctrl + Alt + Esc`。状态文案使用通用组合。

## 已验证与待验证

执行：`.venv\Scripts\python.exe -m unittest discover -s tests -p test_bridge_engine.py -v`。

测试使用 fake Worker 验证：等待真实就绪的时序、取消与延迟回调、session/request 去重、完整关闭与停止超时、管道丢失、旧进程占用、端口变化、设置未知字段及损坏保留、运行中位置切换、校准的人为确认边界。另使用真实本机空管道验证 UTF-8 分帧、非阻塞背压、互斥名称检查和后台进程 hello/status/shutdown/退出。空管道进程测试只发 status 与 shutdown，不实例化 Worker，不接管设备。

这些自动化测试不替代真机验收。USB/BLE 真连、50 次双向模式切换、各模式 60 分钟运行、锁屏/睡眠/断连恢复和干净 Windows 安装仍须分别记录实测结果。
