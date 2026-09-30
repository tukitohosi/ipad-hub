# MouseLink 1.0.3 导入与独立构建基线

核验日期：2026-09-30。原目录 `D:\Desktop\本地项目\外设复用` 全程只读。本记录仅证明现有 MouseLink 可在 iPadHub 独立目录中重建，不代表统一后台或真机集成已完成。

## 已完成

- 当前文件系统导入 `vendor/mouselink`：1,170 个原始文件逐一与原目录 SHA-256 相符，另将个人 `open_bridge/device-calibration.json` 替换为原项目自带的空发布模板。原个人校准未修改；其备份由总项目基线保存。
- 原项目没有 `.git`，`git status` 返回“not a git repository”。无法伪造 dirty 状态或 diff；以当前完整应用源码、固件源码、测试、工具、许可证、所需固件资源及文件哈希为基线。`git-state.txt` 记录了这一限制。
- 使用 Python 3.12.10 新建根 `.venv`，安装原有 32 条锁定依赖；全部版本匹配，`pip check` 通过。没有复制旧虚拟环境。
- Python 回归：**166 / 166 通过，0 跳过**。Qt 生命周期：**9 / 9 通过**。这些测试使用临时数据和模拟设备，没有打开真实串口或抓取键鼠。
- 原始固件包 `flash_helper.py --self-check` 与构建后 `MouseLinkFlash.exe --self-check` 均通过。
- 使用原 `tools/MouseLink.spec` 构建 GUI 与刷机助手成功。构建采用独立目录，未重写导入源码、未生成安装包、未重刷开发板。
- 打包 GUI 通过 `--preview --data-dir` 启动，正常截图后退出 0；`preview=True` 不创建 `Worker`，不会发现/控制真实设备。Windows 原生平台截图中文字正常。

## 文件与复现

统一运行 `scripts/build-mouselink-baseline.ps1`。此脚本只使用根 `.venv`，把运行数据、缓存和构建输出放进新项目；不要用导入旧启动脚本来替代本入口。

| 内容 | 新项目内位置 |
|---|---|
| 解释器 | `.venv/Scripts/python.exe` |
| 原始锁定依赖 | `vendor/mouselink/open_bridge/requirements-desktop-lock.txt` |
| 已安装版本记录 | `artifacts/baseline/mouselink/environment-freeze.txt` |
| 最终完整源码及测试夹具 ZIP | `artifacts/baseline/mouselink/MouseLink-1.0.3-current-source-and-fixture.zip` |
| 逐文件来源、排除项、原始哈希 | `artifacts/baseline/mouselink/source-manifest.json` |
| 原始源码哈希清单 | `artifacts/baseline/mouselink/source-SHA256SUMS.txt` |
| 构建 GUI | `artifacts/baseline/mouselink/dist/MouseLink/MouseLink.exe` |
| 构建刷机助手 | `artifacts/baseline/mouselink/dist/MouseLink/MouseLinkFlash.exe` |
| 整个运行目录哈希清单 | `artifacts/baseline/mouselink/built-SHA256SUMS.txt` |
| 完整回归结果 | `artifacts/baseline/mouselink/unittest-complete.log` |
| Qt 生命周期结果 | `artifacts/baseline/mouselink/desktop-regressions.json` |
| 编译日志 | `artifacts/baseline/mouselink/pyinstaller.log` |
| 中文正常的原生预览 | `artifacts/baseline/mouselink/packaged-gui-windows-preview.png` |
| 汇总验证结果 | `artifacts/baseline/mouselink/baseline-verification.json` |

最终源码 ZIP SHA-256：`0e748e715bda98e359a97574936f1153baae8cf309687580aad81dad8b5d6c3a`。

构建 GUI SHA-256：`6ed7c601c1af57dbe2d67a9a190e26705c77a354b997db64cbffc605d5f354e9`（2,047,948 字节）。

构建刷机助手 SHA-256：`7c89ca3ba76825aa6fc7bcaff2367410f52f067a0de7dcb039f7439477563fd0`（6,387,108 字节）。

这两项是本次重建产物，不能要求其哈希等于旧发布 EXE。完整运行目录中 Qt DLL 独立存在。

## 排除与补充资源

排除旧 `release/`、`runtime/`、`test-results/`、Git 元数据、缓存、个人设置和固件备份目录。外部目录链接不跟随；第三方 DShare 参考图标中的符号链接也未复制，清单逐一记录。这些图标不参与 MouseLink 构建。

`open_bridge/firmware/` 的现有 3 个固件镜像与 manifest 是程序刷机能力所需资源，完整保留并通过自检。没有在本轮重建固件。

最初排除整个 `open_bridge/dist/` 使旧 bootloader 兼容测试按其既定规则跳过。已仅补入原始 `open_bridge/dist/bootloader.bin` 作为必要测试夹具，记录来源与哈希，并重跑全部测试获得 166 / 166。初次日志和不含该夹具的早期源码 ZIP 继续保留；应以表中的 `current-source-and-fixture.zip` 与 `unittest-complete.log` 为最终基线。

离屏插件 `QT_QPA_PLATFORM=offscreen` 在本机未显式加载字体时会显示方框，其初次截图只证明进程启动。中文视觉复核使用 `windows` 插件的第二张截图；新界面的离屏 QA 应显式加载所需字体。

## 后续后台接入点

1. `open_bridge/desktop_app.py` 的 `Worker`（第 61 行）包含发现、监测、启动、校准、侧边切换及状态快照。应抽离成无窗口引擎；不要直接实例化 `Window`，否则会产生旧主窗口和托盘。
2. `Worker.stop_bridge()`（第 152 行）只取消当前桥接，`run()` 会再次进入 `_idle()` 并监测串口。切换到副屏必须调用 `request_shutdown()`（第 88 行），等 `finished`/工作线程结束并确认后台进程退出，才允许启动另一引擎。不能把 `running_bridge=False` 当作串口已释放。
3. `KVMController.run()`（`bridge.py` 第 790 行）最终关闭串口并解锁鼠标；`_enter_remote_mode()`（第 923 行）最终停止/等待输入监听、停止 Raw Input、释放远端按键并将光标停回 Windows。已有 session token、command generation 和 fresh readiness 检查应保留。
4. `Worker.snapshot()`（第 292 行）提供 `usb/ble/ready/remote/switching`；结合 `error/busy/phase` 构建协议状态。发现 ESP32-C3 通过 VID `0x303A`、PID `0x1001`，身份包含串口、序列号与位置；不能只按显示名合并。
5. `app_data()`（第 28 行）已支持 `MOUSELINK_DATA_DIR`；后台可指向 `%LOCALAPPDATA%\iPadHub\mouselink`。原默认目录是 `%LOCALAPPDATA%\MouseLink`。`settings.json` 保存模式、速度、热键、`ipad_side`、设备校准；日志为 `bridge.log`。`flash_dialog.py` 的数据根下有 `firmware-backups` 和 `flash-logs`。
6. `app_settings.migrate_settings()`（第 4 行）处理旧 `mixed/edge` 模式、左右位置默认值、速度范围及校准字典。集成迁移应先复制原 JSON，再复用此兼容逻辑，保留额外字段；幂等标记由新应用管理。
7. `flash_dialog.py` 的 `FlashJob` 启动独立 `MouseLinkFlash.exe`（源码时启动 `flash_helper.py`）。整个刷机对话流程先完整关闭 Worker，忙碌时等待完成后才能退出；统一模式控制器应维持这一排他生命周期。
8. 旧 GUI 有数据目录下 `QLockFile(instance.lock)`，并创建 `Local\MouseLink.KVM.Desktop` 命名 mutex。当前代码没有根据该 mutex 的已存在状态拒绝启动，跨数据目录的排他主要依赖串口独占；新后台需显式检测旧 mutex/串口冲突，使用新的 iPadHub 单实例身份并保留共用资源锁。

## 许可与剩余验收

应用的原始许可证、Qt/pynput LGPL 文本、GPL 文本、刷机依赖许可证及 `esptool-5.4.0-source.zip` 都已保留。旧说明将 `MouseLinkFlash.exe` 与其集成源码列为 GPL-2.0-or-later；因此后续产品声明不能只沿用顶层 MIT。打包基线还复制了全部 `licenses/` 和第三方说明。

尚未验证：新的 JSON/管道引擎、统一退出故障恢复、真实数据迁移、新旧应用资源冲突、50 次真机模式切换、USB/Wi-Fi/键鼠各 60 分钟运行、固件重新编译、干净 Windows 安装与新应用升级卸载。上述结果均不得用本次回归/构建成功代替。

