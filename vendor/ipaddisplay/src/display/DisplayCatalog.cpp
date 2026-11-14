#include "display/DisplayCatalog.h"

#include <algorithm>
#include <climits>
#include <cwchar>
#include <utility>

namespace od {

namespace {

BOOL CALLBACK CollectMonitor(HMONITOR monitor, HDC, LPRECT, LPARAM context)
{
    auto* displays = reinterpret_cast<std::vector<DisplayInfo>*>(context);
    MONITORINFOEXW info{};
    info.cbSize = sizeof(info);
    if (!GetMonitorInfoW(monitor, &info))
        return TRUE;

    DisplayInfo display;
    display.deviceName = info.szDevice;
    display.bounds = info.rcMonitor;
    display.primary = (info.dwFlags & MONITORINFOF_PRIMARY) != 0;
    displays->push_back(std::move(display));
    return TRUE;
}

} // namespace

std::vector<DisplayInfo> EnumerateAttachedDisplays()
{
    std::vector<DisplayInfo> displays;
    EnumDisplayMonitors(nullptr, nullptr, CollectMonitor, reinterpret_cast<LPARAM>(&displays));
    return displays;
}

std::optional<DisplayInfo> FindAttachedDisplay(std::wstring_view deviceName)
{
    for (DisplayInfo& display : EnumerateAttachedDisplays()) {
        if (_wcsicmp(display.deviceName.c_str(), std::wstring(deviceName).c_str()) == 0)
            return display;
    }
    return std::nullopt;
}

std::optional<POINT> SidePosition(const std::vector<DisplayInfo>& displays, std::wstring_view extension, bool left)
{
    const DisplayInfo* primary = nullptr;
    const DisplayInfo* target = nullptr;
    for (const auto& display : displays) {
        if (display.primary) {
            if (primary) return {};
            primary = &display;
        }
        if (display.deviceName == extension) {
            if (target) return {};
            target = &display;
        }
    }
    if (!primary || !target || target->primary) return {};
    const auto width = static_cast<long long>(target->bounds.right) - target->bounds.left;
    const auto height = static_cast<long long>(target->bounds.bottom) - target->bounds.top;
    if (width <= 0 || height <= 0) return {};
    const LONG top = primary->bounds.top;
    const long long bottom = top + height;
    long long edge = left ? primary->bounds.left : primary->bounds.right;
    for (;;) {
        const auto x = left ? edge - width : edge;
        if (x < LONG_MIN || x + width > LONG_MAX || bottom > LONG_MAX) return {};
        long long next = edge;
        for (const auto& display : displays) {
            const auto& r = display.bounds;
            if (&display != target && top < r.bottom && bottom > r.top && x < r.right && x + width > r.left)
                next = left ? std::min<long long>(next, r.left) : std::max<long long>(next, r.right);
        }
        if (next == edge) return POINT{static_cast<LONG>(x), top};
        edge = next;
    }
}

std::string HorizontalSide(const std::vector<DisplayInfo>& displays, std::wstring_view extension)
{
    const auto primary = std::find_if(displays.begin(), displays.end(), [](const auto& d) { return d.primary; });
    const auto target = std::find_if(displays.begin(), displays.end(), [&](const auto& d) { return d.deviceName == extension; });
    if (primary == displays.end() || target == displays.end() || target->primary ||
        target->bounds.top >= primary->bounds.bottom || target->bounds.bottom <= primary->bounds.top) return {};
    if (target->bounds.right <= primary->bounds.left) return "left";
    if (target->bounds.left >= primary->bounds.right) return "right";
    return {};
}

} // namespace od
