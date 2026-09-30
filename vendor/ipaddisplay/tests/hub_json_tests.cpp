#include "app/HubJson.h"
#include <cstdio>

using namespace od::hub;
int main() {
    int failed = 0;
    auto check = [&](bool condition, const char* label) { if (!condition) { fprintf(stderr, "FAIL: %s\n", label); ++failed; } };
    for (const char* invalid : {"", "[", "{", "{\"a\":1,}", "{\"a\":1,\"a\":2}", "01", "-01", "1.", "1e", "1e+", "1e999", "true false", "\"\\ud800\"", "\"\\udc00\"", "\"\\ud800\\u0041\""}) {
        check(!JsonParser(invalid).Parse(), invalid);
    }
    const auto example = JsonParser(R"({"v":1,"session":"中文","payload":{"unknown":1.25,"emoji":"\ud83d\ude80","flags":[true,false,null,-4]}})").Parse();
    check(example.has_value(), "unicode/float payload");
    if (example) {
        const auto roundtrip = JsonParser(Dump(*example)).Parse();
        check(roundtrip && Dump(*roundtrip) == Dump(*example), "JSON roundtrip preserves unknown fields");
        check(Dump(*example).find("🚀") != std::string::npos, "surrogate pair decoded to UTF8");
    }
    std::string deep(40, '['); deep += "0"; deep += std::string(40, ']');
    check(!JsonParser(deep).Parse(), "nesting bound");
    auto quoted = S("\"\\\b\f\n\r\t");
    check(JsonParser(Dump(quoted)).Parse().has_value(), "all control escapes");
    printf("Hub JSON checks: %s\n", failed ? "FAILED" : "passed");
    return failed ? 1 : 0;
}
