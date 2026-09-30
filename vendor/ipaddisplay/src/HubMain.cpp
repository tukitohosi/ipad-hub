#include <winsock2.h>
#include <windows.h>
#include <mfapi.h>
#include <shellapi.h>
#include <fcntl.h>
#include <io.h>

#include "app/Config.h"
#include "app/DeviceCoordinator.h"
#include "app/DisplayLauncher.h"
#include "app/HubJson.h"
#include "app/SenderApp.h"
#include "app/TaskbarRouter.h"
#include "app/UiModel.h"
#include "display/VirtualDisplay.h"
#include "net/Mdns.h"
#include "net/NeighborDiscovery.h"
#include "net/UsbMux.h"

#include <chrono>
#include <filesystem>
#include <fstream>
#include <future>
#include <memory>
#include <thread>

namespace {
using namespace od::hub;
using Clock = std::chrono::steady_clock;
constexpr size_t MaxFrame = 256 * 1024;
std::string Utf8(std::wstring_view text) {
    const int count = WideCharToMultiByte(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), nullptr, 0, nullptr, nullptr);
    std::string out(count, '\0');
    WideCharToMultiByte(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), out.data(), count, nullptr, nullptr);
    return out;
}
struct Handle {
    HANDLE value = nullptr;
    ~Handle() { if (value && value != INVALID_HANDLE_VALUE) CloseHandle(value); }
};

// The pipe is a client of the UI. Reads only consume bytes PeekNamedPipe already
// observed; overlapped writes have a deadline so an unresponsive UI cannot hold
// input/virtual-display resources hostage during shutdown.
class Pipe {
public:
    Handle handle;
    HANDLE parent = nullptr;
    bool alive = true;
    std::string buffered;
    std::string session;
    bool ParentAlive() const { return parent && WaitForSingleObject(parent, 0) == WAIT_TIMEOUT; }
    bool Send(Json::Object message) {
        if (!alive || !ParentAlive()) return false;
        message["v"] = N(1); message["session"] = S(session);
        const std::string bytes = Dump(O(std::move(message))) + '\n';
        if (bytes.size() > MaxFrame) return alive = false;
        Handle event{CreateEventW(nullptr, TRUE, FALSE, nullptr)};
        OVERLAPPED op{}; op.hEvent = event.value;
        DWORD written = 0;
        BOOL ok = WriteFile(handle.value, bytes.data(), static_cast<DWORD>(bytes.size()), &written, &op);
        if (!ok && GetLastError() == ERROR_IO_PENDING) {
            HANDLE waits[] = {event.value, parent};
            if (WaitForMultipleObjects(2, waits, FALSE, 1000) != WAIT_OBJECT_0) {
                CancelIoEx(handle.value, &op);
                GetOverlappedResult(handle.value, &op, &written, TRUE);
                return alive = false;
            }
            ok = GetOverlappedResult(handle.value, &op, &written, FALSE);
        }
        if (!ok || written != bytes.size()) return alive = false;
        return true;
    }
    bool Event(const std::string& event, Json payload) {
        return Send({{"request", S("")}, {"type", S("event")}, {"event", S(event)}, {"payload", std::move(payload)}});
    }
    bool Response(const std::string& request, bool ok, Json payload = O(), std::string error = {}) {
        return Send({{"request", S(request)}, {"type", S("response")}, {"ok", B(ok)},
                     {"payload", std::move(payload)}, {"error", S(std::move(error))}});
    }
    std::optional<std::string> Read() {
        if (!alive || !ParentAlive()) { alive = false; return {}; }
        auto line = buffered.find('\n');
        if (line == std::string::npos) {
            DWORD available = 0;
            if (!PeekNamedPipe(handle.value, nullptr, 0, nullptr, &available, nullptr)) { alive = false; return {}; }
            if (available) {
                char block[16384]; DWORD read = 0;
                Handle event{CreateEventW(nullptr, TRUE, FALSE, nullptr)};
                OVERLAPPED op{}; op.hEvent = event.value;
                BOOL ok = ReadFile(handle.value, block, std::min<DWORD>(available, sizeof(block)), &read, &op);
                if (!ok && GetLastError() == ERROR_IO_PENDING) {
                    if (WaitForSingleObject(event.value, 1000) != WAIT_OBJECT_0) CancelIoEx(handle.value, &op);
                    ok = GetOverlappedResult(handle.value, &op, &read, TRUE);
                }
                if (!ok || !read) { alive = false; return {}; }
                buffered.append(block, read);
                line = buffered.find('\n');
            }
        }
        if ((line == std::string::npos && buffered.size() > MaxFrame) || (line != std::string::npos && line > MaxFrame)) {
            alive = false; return {};
        }
        if (line == std::string::npos) return {};
        auto result = buffered.substr(0, line); buffered.erase(0, line + 1);
        return result;
    }
};

void RedirectLog(const std::filesystem::path& directory) {
    std::filesystem::create_directories(directory / L"logs");
    const auto path = directory / L"logs" / (L"display-" + std::to_wstring(GetCurrentProcessId()) + L".log");
    FILE* file = nullptr; freopen_s(&file, "NUL", "w", stdout); freopen_s(&file, "NUL", "w", stderr);
    HANDLE output = CreateFileW(path.c_str(), GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
                               nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (output == INVALID_HANDLE_VALUE) return;
    int fd = _open_osfhandle(reinterpret_cast<intptr_t>(output), _O_WRONLY | _O_TEXT);
    if (fd == -1) { CloseHandle(output); return; }
    _dup2(fd, _fileno(stdout)); _dup2(fd, _fileno(stderr)); _close(fd);
    setvbuf(stdout, nullptr, _IONBF, 0); setvbuf(stderr, nullptr, _IONBF, 0);
}

std::optional<Json> ReadJson(const std::filesystem::path& path) {
    std::error_code error;
    if (std::filesystem::file_size(path, error) > 4 * 1024 * 1024 || error) return {};
    std::ifstream file(path, std::ios::binary);
    std::string text((std::istreambuf_iterator<char>(file)), {});
    if (text.starts_with("\xef\xbb\xbf")) text.erase(0, 3);
    auto parsed = JsonParser(text).Parse();
    return parsed && std::holds_alternative<Json::Object>(parsed->value) ? parsed : std::nullopt;
}

class Engine {
public:
    Pipe& pipe;
    std::filesystem::path configPath;
    od::Config config;
    Json::Object rawConfig;
    od::SenderApp sender;
    od::DeviceCoordinator coordinator;
    od::DiscoveryCache cache;
    std::vector<od::UsbMuxDevice> usb;
    std::vector<od::NeighborRecord> neighbors;
    od::DisplayLauncherManager launcher{GetModuleHandleW(nullptr)};
    od::TaskbarRouter taskbar;
    bool taskbarStarted = false;
    bool noDevice = false;
    bool closing = false;
    bool started = false;
    bool everStreaming = false;
    bool settingsDirty = false;
    std::string lastStatus;
    std::string forcedFailure;
    Handle oldTrayGate;
    struct Discovery { std::vector<od::MdnsReceiver> wifi; std::vector<od::UsbMuxDevice> usb; od::NeighborSnapshot neighbors; };
    std::future<Discovery> discovery;
    struct Attempt { std::string target, id, transport; uint16_t port = 9000; std::vector<std::string> candidates; };
    std::vector<Attempt> attempts;
    size_t attemptIndex = 0;
    Clock::time_point attemptAt{};
    Clock::time_point lastDiscovery{};
    bool fallbackStopping = false;

    Engine(Pipe& channel, std::filesystem::path directory, bool test)
        : pipe(channel), configPath(directory / L"config.json"), noDevice(test) {
        auto raw = ReadJson(configPath);
        if (!raw) raw = ReadJson(configPath.wstring() + L".bak");
        if (raw) rawConfig = std::get<Json::Object>(std::move(raw->value));
        // The legacy Config parser deliberately only accepts integral JSON.
        // Pass its known schema, keeping future/decimal fields in rawConfig.
        Json::Object known;
        for (const char* key : {"version", "devices", "ip", "port", "autoReconnect", "fps", "bitrateMbps", "streamProfile",
                "requirePrivateNetwork", "showLauncher", "launcherMode", "darkTheme", "taskbarRouting", "preferredDeviceId",
                "lastConnectionTransport", "lastWifiAddress", "lastUsbTarget"}) {
            if (const auto* value = Field(rawConfig, key)) known[key] = *value;
        }
        if (auto entry = known.find("devices"); entry != known.end()) if (auto* devices = std::get_if<Json::Array>(&entry->second.value)) {
            for (auto& device : *devices) if (auto* object = std::get_if<Json::Object>(&device.value)) {
                Json::Object selected;
                for (const char* key : {"id", "name", "lastIpv4", "bonjourHost", "port", "preferredTransport", "priority",
                        "autoConnect", "lastSeen", "macHint", "macMatchingEnabled", "macBindings"}) {
                    if (const auto* value = Field(*object, key)) selected[key] = *value;
                }
                if (auto bindings = selected.find("macBindings"); bindings != selected.end()) if (auto* array = std::get_if<Json::Array>(&bindings->second.value)) {
                    for (auto& binding : *array) if (const auto* source = std::get_if<Json::Object>(&binding.value)) {
                        binding = O({{"mac", S(StringField(*source, "mac"))}, {"networkScope", S(StringField(*source, "networkScope"))}});
                    }
                }
                *object = std::move(selected);
            }
        }
        config = od::Config::Parse(Dump(O(known)));
    }
    ~Engine() { Stop(); }

    bool Save() {
        auto parsed = JsonParser(config.Serialize()).Parse();
        auto fields = std::get<Json::Object>(std::move(parsed->value));
        // Preserve unknown top-level and per-device fields imported from newer
        // configs. The baseline snapshot and old app's file remain untouched.
        if (const auto* original = Field(rawConfig, "devices")) {
            if (const auto* oldArray = std::get_if<Json::Array>(&original->value)) {
                auto& newArray = std::get<Json::Array>(fields["devices"].value);
                for (auto& item : newArray) {
                    auto& object = std::get<Json::Object>(item.value);
                    for (const auto& old : *oldArray) if (const auto* prev = std::get_if<Json::Object>(&old.value)) {
                        const auto id = StringField(object, "id"), target = StringField(object, "lastIpv4");
                        if ((!id.empty() && id == StringField(*prev, "id")) || (!target.empty() && target == StringField(*prev, "lastIpv4"))) {
                            for (const auto& [key, value] : *prev) object.try_emplace(key, value);
                            break;
                        }
                    }
                }
            }
        }
        for (auto& [key, value] : fields) rawConfig[key] = std::move(value);
        const auto bytes = Dump(O(rawConfig));
        const auto temporary = configPath.wstring() + L".tmp";
        Handle file{CreateFileW(temporary.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS,
                                FILE_ATTRIBUTE_NORMAL | FILE_FLAG_WRITE_THROUGH, nullptr)};
        if (file.value == INVALID_HANDLE_VALUE) return false;
        DWORD count = 0;
        bool ok = WriteFile(file.value, bytes.data(), static_cast<DWORD>(bytes.size()), &count, nullptr) && count == bytes.size() && FlushFileBuffers(file.value);
        CloseHandle(file.value); file.value = nullptr;
        if (!ok) return false;
        const auto backup = configPath.wstring() + L".bak";
        if (std::filesystem::exists(configPath)) {
            DeleteFileW(backup.c_str());
            ok = ReplaceFileW(configPath.c_str(), temporary.c_str(), backup.c_str(), REPLACEFILE_WRITE_THROUGH, nullptr, nullptr) != FALSE;
        } else ok = MoveFileExW(temporary.c_str(), configPath.c_str(), MOVEFILE_WRITE_THROUGH) != FALSE;
        settingsDirty = !ok;
        return ok;
    }

    Json Devices() {
        Json::Array devices;
        for (const auto& record : cache.Records()) {
            if (record.address.empty()) continue;
            devices.push_back(O({{"id", S(record.id.empty() ? "wifi:" + record.address : record.id)},
                {"receiver_id", S(record.id)}, {"name", S(record.name.empty() ? record.host : record.name)},
                {"target", S(record.address)}, {"port", N(record.port ? record.port : config.port)},
                {"transport", S("wifi")}, {"online", B(record.online)}, {"receiver_ready", B(false)}}));
        }
        for (const auto& device : config.devices) {
            if (od::IsUsbMuxTarget(device.lastIpv4)) continue;
            if (!device.id.empty() && cache.FindById(device.id)) continue;
            const auto targets = od::BuildDeviceCandidates(device, cache.Records(), neighbors);
            const auto target = targets.empty() ? device.lastIpv4 : targets.front();
            if (target.empty()) continue;
            const bool duplicate = std::any_of(devices.begin(), devices.end(), [&](const Json& item) { return StringField(std::get<Json::Object>(item.value), "target") == target; });
            if (duplicate) continue;
            devices.push_back(O({{"id", S(device.id.empty() ? "wifi:" + target : device.id)},
                {"receiver_id", S(device.id)}, {"name", S(device.name.empty() ? "已保存的 iPad" : device.name)},
                {"target", S(target)}, {"port", N(device.port)}, {"transport", S("wifi")},
                {"online", B(false)}, {"remembered", B(true)}, {"receiver_ready", B(false)}}));
        }
        size_t i = 0;
        for (const auto& device : usb) {
            const auto target = od::MakeUsbMuxTarget(device.udid);
            devices.push_back(O({{"id", S(target)}, {"name", S("USB iPad " + std::to_string(++i))},
                {"target", S(target)}, {"port", N(config.port)}, {"transport", S("usb")},
                {"online", B(true)}, {"receiver_ready", B(false)}}));
        }
        return O({{"devices", A(std::move(devices))}});
    }

    void Discover() {
        if (discovery.valid()) return;
        lastDiscovery = Clock::now();
        discovery = std::async(std::launch::async, [test = noDevice] {
            if (test) return Discovery{};
            Discovery result;
            result.wifi = od::BrowseReceivers(800);
            result.usb = od::ListUsbMuxDevices();
            result.neighbors = od::ReadNeighborSnapshot({}, false);
            return result;
        });
    }

    Json Status() {
        const auto snapshot = sender.Snapshot();
        std::string state = "idle", detail = "选择 iPad 后连接；请让 OpenDisplay 保持前台";
        if (discovery.valid() && !started) { state = "discovering"; detail = "正在查找 USB 和局域网 iPad"; }
        else if (!started && (!cache.Records().empty() || !usb.empty() || !config.devices.empty())) {
            state = "ready"; detail = "已找到设备或保存的记录；OpenDisplay 是否就绪将在连接时确认";
        }
        if (started) {
            state = "connecting"; detail = Utf8(od::ConnectionPhaseTitle(snapshot.phase));
            if (snapshot.phase == od::ConnectionPhase::Streaming) { state = "active"; detail = "正在将 Windows 桌面传送到 iPad"; }
            else if (snapshot.phase == od::ConnectionPhase::Blocked || snapshot.phase == od::ConnectionPhase::UnsafeNetwork || snapshot.phase == od::ConnectionPhase::Failed) state = "failed";
            if (snapshot.failure != od::FailureReason::None) detail = Utf8(od::FailureHelpText(snapshot.failure, snapshot.detail));
            if (snapshot.failure == od::FailureReason::DriverUnavailable) state = "missing";
            if (snapshot.failure == od::FailureReason::ResolutionUnavailable) detail += "；可在设备工具中注册 iPad 原生分辨率后重试";
        }
        if (!forcedFailure.empty()) { state = "failed"; detail = forcedFailure; }
        if (closing) { state = "stopping"; detail = "正在结束传流并回收虚拟屏和输入状态"; }
        const auto transport = snapshot.connectedAddress.empty() ? snapshot.transport : (od::IsUsbMuxTarget(snapshot.connectedAddress) ? "usb" : "wifi");
        return O({{"state", S(state)}, {"detail", S(detail)}, {"running", B(sender.IsRunning())},
            {"receiver_ready", B(snapshot.phase == od::ConnectionPhase::Streaming)},
            {"transport", S(transport)}, {"device_id", S(snapshot.deviceId)},
            {"width", N(snapshot.width)}, {"height", N(snapshot.height)},
            {"failure", N(static_cast<int64_t>(snapshot.failure))}, {"attempt", N(attemptIndex + 1)},
            {"settings_saved", B(!settingsDirty)}});
    }

    bool Configure(const Json::Object& payload, std::string& error) {
        if (sender.IsRunning()) { error = "请先停止副屏再修改连接设置"; return false; }
        auto proposed = config;
        int64_t number = 0;
        const auto profile = StringField(payload, "profile");
        if (!profile.empty()) {
            if (profile == "balanced") { proposed.streamProfile = od::StreamProfile::Balanced; proposed.fps = 60; proposed.bitrateMbps = 30; }
            else if (profile == "sharp") { proposed.streamProfile = od::StreamProfile::Sharp; proposed.fps = 60; proposed.bitrateMbps = 45; }
            else if (profile == "smooth") { proposed.streamProfile = od::StreamProfile::LowBandwidth; proposed.fps = 60; proposed.bitrateMbps = 15; }
            else { error = "未知画质预设"; return false; }
        }
        if (IntField(payload, "fps", number)) { if (number < 30 || number > 120) { error = "帧率必须在 30 到 120 之间"; return false; } proposed.fps = static_cast<uint32_t>(number); }
        if (IntField(payload, "bitrate_mbps", number)) { if (number < 5 || number > 100) { error = "码率必须在 5 到 100 Mbps 之间"; return false; } proposed.bitrateMbps = static_cast<uint32_t>(number); }
        if (IntField(payload, "port", number)) { if (number < 1 || number > 65535) { error = "端口无效"; return false; } proposed.port = static_cast<uint16_t>(number); }
        BoolField(payload, "require_private_network", proposed.requirePrivateNetwork);
        BoolField(payload, "auto_reconnect", proposed.autoReconnect);
        BoolField(payload, "taskbar_routing", proposed.taskbarRouting);
        if (IntField(payload, "launcher_mode", number) && number >= 0 && number <= 2) { proposed.launcherMode = static_cast<od::LauncherMode>(number); proposed.showLauncher = number != 0; }
        if (Field(payload, "preferred_device_id")) proposed.preferredDeviceId = StringField(payload, "preferred_device_id");
        config = std::move(proposed);
        if (!Save()) { error = "无法保存副屏设置，请检查数据目录权限"; return false; }
        return true;
    }

    bool Start(const Json::Object& payload, std::string& error) {
        if (noDevice) { error = "no-device 检查模式禁止连接硬件"; return false; }
        if (started || sender.IsRunning()) { error = "副屏已启动；请先停止当前会话"; return false; }
        auto target = StringField(payload, "target");
        auto id = StringField(payload, "device_id");
        const auto mode = StringField(payload, "transport");
        if (!mode.empty() && mode != "auto" && mode != "usb" && mode != "wifi") { error = "连接方式无效"; return false; }
        uint16_t port = config.port;
        int64_t requestedPort = 0;
        if (IntField(payload, "port", requestedPort)) { if (requestedPort < 1 || requestedPort > 65535) { error = "端口无效"; return false; } port = static_cast<uint16_t>(requestedPort); }
        if (target.size() > 512 || target.find_first_of("\r\n\t /\\") != std::string::npos) { error = "请输入有效的设备地址"; return false; }
        if (!od::IsUsbMuxTarget(target)) {
            auto colon = target.find(':');
            if (colon != std::string::npos) {
                const auto part = std::string_view(target).substr(colon + 1);
                unsigned value = 0; auto parsed = std::from_chars(part.data(), part.data() + part.size(), value);
                if (parsed.ec != std::errc{} || parsed.ptr != part.data() + part.size() || value == 0 || value > 65535) { error = "地址端口格式无效"; return false; }
                port = static_cast<uint16_t>(value); target.resize(colon);
            }
            if (!target.empty() && !std::all_of(target.begin(), target.end(), [](unsigned char c) { return std::isalnum(c) || c == '.' || c == '-'; })) { error = "请输入 IPv4 地址或本地设备主机名"; return false; }
        }
        if ((mode == "wifi" && od::IsUsbMuxTarget(target)) || (mode == "usb" && !target.empty() && !od::IsUsbMuxTarget(target))) { error = "所选设备与连接方式不匹配"; return false; }
        if (!Configure(payload, error)) return false;
        if (id.starts_with("wifi:") || id.starts_with("usb:")) id.clear();
        attempts.clear(); attemptIndex = 0; fallbackStopping = false;
        std::vector<Attempt> wifiAttempts;
        if (!target.empty() && !od::IsUsbMuxTarget(target)) {
            od::DeviceConfig device; device.id = id; device.lastIpv4 = target; device.port = port;
            for (const auto& saved : config.devices) if ((!id.empty() && id == saved.id) || target == saved.lastIpv4) { device = saved; if (id.empty()) id = device.id; break; }
            auto candidates = od::BuildDeviceCandidates(device, cache.Records(), neighbors);
            if (candidates.empty()) candidates.push_back(target);
            wifiAttempts.push_back({candidates.front(), id, "wifi", port, candidates});
        } else if (mode != "usb") {
            coordinator.Begin(config.devices, id.empty() ? config.preferredDeviceId : id);
            for (auto index : coordinator.Order()) {
                const auto& device = config.devices[index];
                if (od::IsUsbMuxTarget(device.lastIpv4)) continue;
                auto candidates = od::BuildDeviceCandidates(device, cache.Records(), neighbors);
                if (!candidates.empty()) wifiAttempts.push_back({candidates.front(), device.id, "wifi", device.port, candidates});
                if (!id.empty() && id == device.id) break;
            }
            if (wifiAttempts.empty()) for (const auto& record : cache.Records()) {
                if (!record.online || record.address.empty() || (!id.empty() && id != record.id)) continue;
                wifiAttempts.push_back({record.address, record.id, "wifi", record.port ? record.port : port, {record.address}});
                break;
            }
        }
        std::string usbTarget = od::IsUsbMuxTarget(target) ? target : "";
        if (usbTarget.empty() && usb.size() == 1) usbTarget = od::MakeUsbMuxTarget(usb.front().udid);
        if (usbTarget.empty()) for (const auto& device : usb) if (od::MakeUsbMuxTarget(device.udid) == config.lastUsbTarget) usbTarget = config.lastUsbTarget;
        const bool preferUsb = od::IsUsbMuxTarget(target) || (target.empty() && config.lastConnectionTransport != "wifi");
        if (mode != "wifi" && preferUsb && !usbTarget.empty()) attempts.push_back({usbTarget, id, "usb", port, {usbTarget}});
        if (mode != "usb") attempts.insert(attempts.end(), wifiAttempts.begin(), wifiAttempts.end());
        if (mode != "wifi" && !preferUsb && !usbTarget.empty()) attempts.push_back({usbTarget, id, "usb", port, {usbTarget}});
        if (attempts.empty()) { error = "未找到可连接的 iPad；请刷新设备或填写地址，并在 iPad 上打开 OpenDisplay"; return false; }
        if (attempts.size() > 8) attempts.resize(8);
        // Use the existing tray gate while this engine owns display resources.
        // An older tray starting later sees this same name and stays inactive.
        oldTrayGate.value = CreateMutexW(nullptr, FALSE, L"Local\\IpadConnect.Tray.v1");
        const DWORD mutexError = GetLastError();
        if (!oldTrayGate.value || mutexError == ERROR_ALREADY_EXISTS || FindWindowExW(HWND_MESSAGE, nullptr, L"MouseLinkTrayWindow", nullptr)) {
            if (oldTrayGate.value) { CloseHandle(oldTrayGate.value); oldTrayGate.value = nullptr; }
            error = "旧版 iPad互联正在运行，请从其托盘菜单退出后再连接；旧软件会保留"; return false;
        }
        forcedFailure.clear(); started = true; everStreaming = false;
        BeginAttempt(); return true;
    }

    void BeginAttempt() {
        const auto& attempt = attempts[attemptIndex];
        od::StreamSettings settings;
        settings.fps = attempt.transport == "usb" ? std::min(config.fps, 60u) : config.fps;
        settings.bitrateBps = config.bitrateMbps * 1000000;
        settings.requirePrivateNetwork = config.requirePrivateNetwork;
        settings.candidateTargets = attempt.candidates;
        sender.Start(attempt.target, attempt.port, settings, attempt.id, attempt.transport);
        attemptAt = Clock::now();
    }

    void Tick() {
        if (discovery.valid() && discovery.wait_for(std::chrono::milliseconds(0)) == std::future_status::ready) {
            auto result = discovery.get(); cache.Merge(result.wifi); cache.Expire();
            usb = std::move(result.usb); neighbors = std::move(result.neighbors.records);
            pipe.Event("devices", Devices());
            if (started && attemptIndex < attempts.size() && attempts[attemptIndex].transport == "wifi") {
                od::DeviceConfig device; device.id = attempts[attemptIndex].id; device.lastIpv4 = attempts[attemptIndex].target;
                for (const auto& saved : config.devices) if (!device.id.empty() && saved.id == device.id) { device = saved; break; }
                sender.UpdateConnectionCandidates(od::BuildDeviceCandidates(device, cache.Records(), neighbors));
            }
        }
        if (started && !discovery.valid() && Clock::now() - lastDiscovery >= std::chrono::seconds(10)) Discover();
        const auto snapshot = sender.Snapshot();
        if (started && snapshot.phase == od::ConnectionPhase::Streaming) {
            coordinator.ReportStreaming(snapshot.deviceId);
            const auto transport = od::IsUsbMuxTarget(snapshot.connectedAddress) ? "usb" : "wifi";
            if (!everStreaming || config.lastConnectionTransport != transport) {
                everStreaming = true; config.lastConnectionTransport = transport;
                if (transport == std::string("usb")) config.lastUsbTarget = attempts[attemptIndex].target;
                else {
                    config.lastWifiAddress = snapshot.connectedAddress;
                    auto found = std::find_if(config.devices.begin(), config.devices.end(), [&](const auto& d) { return (!snapshot.deviceId.empty() && d.id == snapshot.deviceId) || d.lastIpv4 == snapshot.connectedAddress; });
                    if (found == config.devices.end()) { od::DeviceConfig device; device.name = "iPad"; config.devices.push_back(device); found = std::prev(config.devices.end()); }
                    found->id = snapshot.deviceId; found->lastIpv4 = snapshot.connectedAddress; found->port = attempts[attemptIndex].port;
                    found->lastSeen = std::chrono::duration_cast<std::chrono::seconds>(std::chrono::system_clock::now().time_since_epoch()).count();
                    if (const auto record = cache.FindById(snapshot.deviceId)) { found->bonjourHost = record->host; found->name = record->name; }
                    od::LearnVerifiedMacBinding(*found, snapshot.connectedAddress, snapshot.deviceId, neighbors);
                    config.preferredDeviceId = snapshot.deviceId;
                }
                Save();
            }
            launcher.SetMode(config.showLauncher ? config.launcherMode : od::LauncherMode::Hidden);
            if (const auto rect = sender.StreamMonitorRect()) launcher.Sync({{snapshot.connectedAddress, L"iPad", *rect}});
            if (config.taskbarRouting && !taskbarStarted) taskbarStarted = taskbar.Start();
        } else {
            launcher.Sync({});
            if (taskbarStarted) { taskbar.Stop(); taskbarStarted = false; }
        }
        if (started && !everStreaming && attemptIndex + 1 < attempts.size()) {
            const auto failure = snapshot.failure;
            const bool global = failure == od::FailureReason::DriverUnavailable || failure == od::FailureReason::ResolutionUnavailable ||
                failure == od::FailureReason::EncoderUnavailable || failure == od::FailureReason::CaptureUnavailable || failure == od::FailureReason::AnotherSender;
            const auto budget = std::chrono::seconds(8 * std::clamp<size_t>(attempts[attemptIndex].candidates.size(), 1, 8) + 4);
            if (!global && !fallbackStopping && Clock::now() - attemptAt > budget) { sender.RequestStop(); fallbackStopping = true; }
            if (fallbackStopping && !sender.IsRunning()) { sender.Stop(); ++attemptIndex; fallbackStopping = false; BeginAttempt(); }
        }
        if (started && everStreaming && !config.autoReconnect && snapshot.phase != od::ConnectionPhase::Streaming) {
            sender.RequestStop(); forcedFailure = "连接已中断，自动重连已关闭；请停止后重新连接";
        }
        const auto status = Status(); const auto encoded = Dump(status);
        if (encoded != lastStatus) { pipe.Event("status", status); lastStatus = encoded; }
    }

    void Stop() {
        sender.RequestStop();
        taskbar.Stop(); taskbarStarted = false;
        launcher.Shutdown(); sender.Stop();
        if (discovery.valid()) discovery.wait();
        if (oldTrayGate.value) { CloseHandle(oldTrayGate.value); oldTrayGate.value = nullptr; }
        coordinator.Reset(); started = false;
    }

    void Command(const std::string& bytes) {
        if (bytes.empty() || !MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, bytes.data(), static_cast<int>(bytes.size()), nullptr, 0)) {
            pipe.Response("", false, O(), "控制消息不是有效 UTF-8"); return;
        }
        auto parsed = JsonParser(bytes).Parse();
        const auto* object = parsed ? std::get_if<Json::Object>(&parsed->value) : nullptr;
        if (!object) { pipe.Response("", false, O(), "无效 JSON 控制消息"); return; }
        const auto request = StringField(*object, "request");
        int64_t version = 0;
        if (!IntField(*object, "v", version) || version != 1 || StringField(*object, "session") != pipe.session || request.empty() || request.size() > 128) {
            pipe.Response(request, false, O(), "协议版本、会话或请求标识无效"); return;
        }
        const auto* payloadValue = Field(*object, "payload");
        const auto* payload = payloadValue ? std::get_if<Json::Object>(&payloadValue->value) : nullptr;
        if (!payload) { pipe.Response(request, false, O(), "payload 必须是对象"); return; }
        const auto command = StringField(*object, "command");
        if (command == "stop" || command == "shutdown") {
            closing = true; pipe.Event("status", Status());
            Stop();
            const bool clean = od::VirtualDisplay::CleanupSucceeded();
            pipe.Response(request, clean, O(), clean ? "" : "虚拟屏未完成移除，请检查 Windows 显示设置后重试");
            pipe.Event("released", O({{"clean", B(clean)}})); return;
        }
        if (command == "status") pipe.Response(request, true, Status());
        else if (command == "discover") { Discover(); pipe.Response(request, true, Devices()); }
        else if (command == "configure" || command == "start") {
            std::string error;
            const bool ok = command == "start" ? Start(*payload, error) : Configure(*payload, error);
            if (!ok && command == "start") forcedFailure = error;
            pipe.Response(request, ok, Status(), error);
        } else pipe.Response(request, false, O(), "未知控制命令");
    }
};
}

int wmain(int argc, wchar_t** argv) {
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    if (argc == 4 && std::wstring_view(argv[1]) == L"--register-resolution") {
        const auto axis = [](const wchar_t* text) -> uint32_t { wchar_t* end = nullptr; const auto value = wcstoul(text, &end, 10); return end != text && !*end && value >= 320 && value <= 8192 && !(value & 1) ? value : 0; };
        const auto width = axis(argv[2]), height = axis(argv[3]);
        if (!width || !height || static_cast<uint64_t>(width) * height > 8192ull * 4096ull) return 2;
        Handle resourceGate{CreateMutexW(nullptr, FALSE, L"Local\\IpadConnect.Tray.v1")};
        if (!resourceGate.value || GetLastError() == ERROR_ALREADY_EXISTS) return 9;
        return od::VirtualDisplay::RegisterResolutions(width, height) ? 0 : 1;
    }
    std::wstring pipeName, directory, session;
    DWORD parentPid = 0; bool noDevice = false;
    for (int i = 1; i < argc; ++i) {
        const std::wstring_view option(argv[i]);
        if (option == L"--no-device") { noDevice = true; continue; }
        if (i + 1 >= argc) return 2;
        const std::wstring value(argv[++i]);
        if (option == L"--pipe") pipeName = value;
        else if (option == L"--session") session = value;
        else if (option == L"--data-dir") directory = value;
        else if (option == L"--parent-pid") { wchar_t* end = nullptr; parentPid = wcstoul(value.c_str(), &end, 10); if (!end || *end) return 2; }
        else return 2;
    }
    if (!pipeName.starts_with(L"\\\\.\\pipe\\") || pipeName.size() > 240 || session.empty() || session.size() > 128 ||
        !std::filesystem::path(directory).is_absolute() || !parentPid || parentPid == GetCurrentProcessId()) return 2;
    Handle parent{OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, parentPid)};
    if (!parent.value || WaitForSingleObject(parent.value, 0) != WAIT_TIMEOUT) return 3;
    Pipe pipe; pipe.parent = parent.value; pipe.session = Utf8(session);
    const auto deadline = Clock::now() + std::chrono::seconds(10);
    while (pipe.ParentAlive() && Clock::now() < deadline) {
        pipe.handle.value = CreateFileW(pipeName.c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr, OPEN_EXISTING,
                                        FILE_FLAG_OVERLAPPED | SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION, nullptr);
        if (pipe.handle.value != INVALID_HANDLE_VALUE) break;
        Sleep(25);
    }
    if (pipe.handle.value == INVALID_HANDLE_VALUE) return 4;
    ULONG serverPid = 0;
    if (!GetNamedPipeServerProcessId(pipe.handle.value, &serverPid) || serverPid != parentPid) return 5;
    try {
        std::filesystem::create_directories(directory);
        _wputenv_s(L"IPAD_CONNECT_CONFIG_DIR", directory.c_str());
        RedirectLog(directory);
        // Process-wide references protect MF/DXGI background threads through
        // each SenderApp teardown, matching the upstream lifecycle.
        CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        MFStartup(MF_VERSION, MFSTARTUP_NOSOCKET);
        WSADATA data{}; if (WSAStartup(MAKEWORD(2, 2), &data) != 0) return 6;
        Engine engine(pipe, directory, noDevice);
        pipe.Event("hello", O({{"engine", S("display")}, {"version", S("0.1.0")}}));
        pipe.Event("devices", engine.Devices()); pipe.Event("status", engine.Status());
        while (pipe.alive && pipe.ParentAlive() && !engine.closing) {
            // Process at most one frame per iteration so a noisy peer cannot
            // starve parent checks, lifecycle work or the Windows message pump.
            if (auto frame = pipe.Read()) engine.Command(*frame);
            if (engine.closing) break;
            engine.Tick();
            MSG msg{}; while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) { TranslateMessage(&msg); DispatchMessageW(&msg); }
            Sleep(20);
        }
        engine.Stop();
        const bool clean = od::VirtualDisplay::CleanupSucceeded();
        if (!engine.closing) pipe.Event("released", O({{"clean", B(clean)}}));
        return clean ? 0 : 8;
    } catch (const std::exception& exception) {
        pipe.Event("status", O({{"state", S("failed")}, {"detail", S(exception.what())}, {"running", B(false)}}));
        return 7;
    }
}
