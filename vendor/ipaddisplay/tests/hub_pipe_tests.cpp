#include <windows.h>
#include "app/HubJson.h"
#include <chrono>
#include <filesystem>
#include <fstream>
#include <cstdio>
#include <memory>
#include <thread>

using namespace od::hub;
using Clock = std::chrono::steady_clock;
namespace {
int failures = 0;
void Check(bool condition, const char* label) { if (!condition) { fprintf(stderr, "FAIL: %s\n", label); ++failures; } }
struct Session {
    HANDLE pipe = INVALID_HANDLE_VALUE, process = nullptr;
    DWORD pid = 0;
    std::wstring dir, session = L"test-session";
    std::string buffered;
    ~Session() {
        if (pipe != INVALID_HANDLE_VALUE) CloseHandle(pipe);
        if (process) { if (WaitForSingleObject(process, 5000) != WAIT_OBJECT_0) { TerminateProcess(process, 99); ++failures; } CloseHandle(process); }
    }
    bool Start(const std::wstring& exe, int index) {
        wchar_t temp[32768]{}; GetTempPathW(32768, temp);
        dir = std::wstring(temp) + L"ipadhub-pipe-test-" + std::to_wstring(GetCurrentProcessId()) + L"-" + std::to_wstring(index);
        std::filesystem::create_directories(dir);
        std::ofstream(std::filesystem::path(dir) / L"config.json") << R"({"version":3,"futureOption":{"gain":1.25},"fps":90,"taskbarRouting":true,"devices":[]})";
        const auto name = L"\\\\.\\pipe\\ipadhub-test-" + std::to_wstring(GetCurrentProcessId()) + L"-" + std::to_wstring(index);
        pipe = CreateNamedPipeW(name.c_str(), PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED,
             PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS, 1, 512 * 1024, 512 * 1024, 0, nullptr);
        if (pipe == INVALID_HANDLE_VALUE) return false;
        HANDLE event = CreateEventW(nullptr, TRUE, FALSE, nullptr); OVERLAPPED op{}; op.hEvent = event;
        BOOL connecting = ConnectNamedPipe(pipe, &op);
        const auto code = GetLastError();
        if (!connecting && code != ERROR_IO_PENDING && code != ERROR_PIPE_CONNECTED) { CloseHandle(event); return false; }
        auto command = L"\"" + exe + L"\" --pipe \"" + name + L"\" --session " + session + L" --parent-pid " +
            std::to_wstring(GetCurrentProcessId()) + L" --data-dir \"" + dir + L"\" --no-device";
        STARTUPINFOW startup{sizeof(startup)}; PROCESS_INFORMATION info{};
        if (!CreateProcessW(exe.c_str(), command.data(), nullptr, nullptr, FALSE, CREATE_NO_WINDOW, nullptr, nullptr, &startup, &info)) { CancelIoEx(pipe, &op); CloseHandle(event); return false; }
        process = info.hProcess; pid = info.dwProcessId; CloseHandle(info.hThread);
        const bool connected = connecting || code == ERROR_PIPE_CONNECTED || WaitForSingleObject(event, 5000) == WAIT_OBJECT_0;
        if (!connected) { CancelIoEx(pipe, &op); DWORD count = 0; GetOverlappedResult(pipe, &op, &count, TRUE); }
        CloseHandle(event); return connected;
    }
    bool Send(const std::string& bytes) {
        HANDLE event = CreateEventW(nullptr, TRUE, FALSE, nullptr); OVERLAPPED op{}; op.hEvent = event;
        DWORD sent = 0; BOOL ok = WriteFile(pipe, bytes.data(), static_cast<DWORD>(bytes.size()), &sent, &op);
        if (!ok && GetLastError() == ERROR_IO_PENDING) {
            if (WaitForSingleObject(event, 2000) != WAIT_OBJECT_0) CancelIoEx(pipe, &op);
            ok = GetOverlappedResult(pipe, &op, &sent, TRUE);
        }
        CloseHandle(event); return ok && sent == bytes.size();
    }
    bool Command(const char* command, const char* request, Json payload = O(), std::string sessionOverride = "test-session") {
        return Send(Dump(O({{"v", N(1)}, {"session", S(sessionOverride)}, {"request", S(request)}, {"command", S(command)}, {"payload", payload}})) + '\n');
    }
    std::optional<Json::Object> Receive(const char* key, const char* value, int timeout = 5000) {
        auto deadline = Clock::now() + std::chrono::milliseconds(timeout);
        while (Clock::now() < deadline) {
            auto end = buffered.find('\n');
            if (end != std::string::npos) {
                auto parsed = JsonParser(buffered.substr(0, end)).Parse(); buffered.erase(0, end + 1);
                if (parsed) if (const auto* object = std::get_if<Json::Object>(&parsed->value)) {
                    if (StringField(*object, key) == value) return *object;
                }
                continue;
            }
            DWORD count = 0;
            if (!PeekNamedPipe(pipe, nullptr, 0, nullptr, &count, nullptr)) return {};
            if (count) {
                std::string block(std::min<DWORD>(count, 32768), '\0'); DWORD read = 0;
                HANDLE event = CreateEventW(nullptr, TRUE, FALSE, nullptr); OVERLAPPED op{}; op.hEvent = event;
                BOOL ok = ReadFile(pipe, block.data(), static_cast<DWORD>(block.size()), &read, &op);
                if (!ok && GetLastError() == ERROR_IO_PENDING) { WaitForSingleObject(event, 1000); ok = GetOverlappedResult(pipe, &op, &read, TRUE); }
                CloseHandle(event); if (!ok) return {}; buffered.append(block.data(), read);
            } else Sleep(10);
        }
        return {};
    }
    bool ExitZero(int timeout = 5000) {
        if (WaitForSingleObject(process, timeout) != WAIT_OBJECT_0) return false;
        DWORD code = 99; return GetExitCodeProcess(process, &code) && code == 0;
    }
};
bool Ok(const std::optional<Json::Object>& response) { bool result = false; return response && BoolField(*response, "ok", result) && result; }
}

int wmain(int argc, wchar_t** argv) {
    if (argc == 4 && std::wstring_view(argv[1]) == L"--orphan") {
        auto session = std::make_unique<Session>();
        if (!session->Start(argv[2], 90) || !session->Receive("event", "hello")) return 8;
        { std::ofstream output{std::filesystem::path(argv[3])}; output << session->pid; }
        // Model the UI dying without ever sending stop/shutdown. The OS closes
        // its pipe and parent handle; the engine must clean up and exit itself.
        ExitProcess(0);
    }
    if (argc != 2) return 2;
    const std::wstring exe(argv[1]);
    {
        Session session; Check(session.Start(exe, 1), "start isolated backend");
        const auto hello = session.Receive("event", "hello"); Check(hello.has_value(), "versioned hello");
        session.Command("start", "stale", O(), "old-session");
        Check(!Ok(session.Receive("request", "stale")), "reject stale session");
        session.Command("start", "hardware"); Check(!Ok(session.Receive("request", "hardware")), "no-device refuses start");
        session.Command("configure", "cfg", O({{"profile", S("sharp")}, {"preferred_device_id", S("保留设备")}}));
        Check(Ok(session.Receive("request", "cfg")), "configure acknowledged");
        std::ifstream file(std::filesystem::path(session.dir) / L"config.json");
        const std::string saved((std::istreambuf_iterator<char>(file)), {});
        Check(saved.find("futureOption") != std::string::npos && saved.find("1.25") != std::string::npos, "unknown config retained");
        Check(saved.find("\"taskbarRouting\":true") != std::string::npos, "known config survives unknown decimals");
        Check(saved.find("保留设备") != std::string::npos, "UTF8 config retained");
        session.Command("configure", "bad-range", O({{"fps", N(-10)}}));
        Check(!Ok(session.Receive("request", "bad-range")), "reject invalid config range");
        session.Command("discover", "discover"); Check(Ok(session.Receive("request", "discover")), "bounded discovery");
        session.Command("stop", "stop"); Check(Ok(session.Receive("request", "stop")), "stop acknowledged after cleanup");
        Check(session.Receive("event", "released").has_value(), "clean released event");
        Check(session.ExitZero(), "stop exits zero");
    }
    {
        Session session; Check(session.Start(exe, 2), "start disconnect test"); Check(session.Receive("event", "hello").has_value(), "disconnect hello");
        CloseHandle(session.pipe); session.pipe = INVALID_HANDLE_VALUE;
        Check(session.ExitZero(), "broken pipe cleanup exits zero");
    }
    {
        Session session; Check(session.Start(exe, 3), "start oversize test"); session.Receive("event", "hello");
        session.Send(std::string(256 * 1024 + 1, 'x'));
        Check(session.ExitZero(), "oversized control frame exits cleanly");
    }
    {
        Session session; Check(session.Start(exe, 4), "start fragmented protocol test"); session.Receive("event", "hello");
        const auto message = Dump(O({{"v", N(1)}, {"session", S("test-session")}, {"request", S("fragment")}, {"command", S("status")}, {"payload", O()}})) + '\n';
        for (size_t i = 0; i < message.size(); i += 3) session.Send(message.substr(i, 3));
        Check(Ok(session.Receive("request", "fragment")), "fragmented frame reassembled");
        session.Command("shutdown", "shutdown"); Check(Ok(session.Receive("request", "shutdown")), "shutdown acknowledged");
        Check(session.Receive("event", "released").has_value(), "shutdown released"); Check(session.ExitZero(), "shutdown exits zero");
    }
    {
        wchar_t self[32768]{}, temp[32768]{}; GetModuleFileNameW(nullptr, self, 32768); GetTempPathW(32768, temp);
        auto marker = std::wstring(temp) + L"ipadhub-orphan-" + std::to_wstring(GetCurrentProcessId()) + L".txt";
        auto command = L"\"" + std::wstring(self) + L"\" --orphan \"" + exe + L"\" \"" + marker + L"\"";
        STARTUPINFOW startup{sizeof(startup)}; PROCESS_INFORMATION info{};
        Check(CreateProcessW(self, command.data(), nullptr, nullptr, FALSE, CREATE_NO_WINDOW, nullptr, nullptr, &startup, &info) != FALSE, "launch dying parent");
        if (info.hProcess) {
            Check(WaitForSingleObject(info.hProcess, 7000) == WAIT_OBJECT_0, "parent exited"); CloseHandle(info.hThread); CloseHandle(info.hProcess);
            DWORD enginePid = 0; std::ifstream(std::filesystem::path(marker)) >> enginePid;
            Check(enginePid != 0, "parent wrote engine PID");
            HANDLE engine = OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, enginePid);
            if (engine) { Check(WaitForSingleObject(engine, 5000) == WAIT_OBJECT_0, "engine exits after parent death"); CloseHandle(engine); }
        }
    }
    printf("Hub pipe lifecycle: %s\n", failures ? "FAILED" : "passed");
    return failures ? 1 : 0;
}
