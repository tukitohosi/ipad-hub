#pragma once
// Bounded IPC JSON parser derived from the existing Config parser.
#include <algorithm>
#include <charconv>
#include <cctype>
#include <cmath>
#include <map>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>
#include <variant>
#include <vector>
namespace od::hub {
struct Json {
    using Object = std::map<std::string, Json>;
    using Array = std::vector<Json>;
    std::variant<std::nullptr_t, bool, int64_t, double, std::string, Object, Array> value;
};

inline void AppendUtf8(std::string& out, uint32_t codepoint)
{
    if (codepoint <= 0x7F) out.push_back(static_cast<char>(codepoint));
    else if (codepoint <= 0x7FF) {
        out.push_back(static_cast<char>(0xC0 | (codepoint >> 6)));
        out.push_back(static_cast<char>(0x80 | (codepoint & 0x3F)));
    } else if (codepoint <= 0xFFFF) {
        out.push_back(static_cast<char>(0xE0 | (codepoint >> 12)));
        out.push_back(static_cast<char>(0x80 | ((codepoint >> 6) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | (codepoint & 0x3F)));
    } else {
        out.push_back(static_cast<char>(0xF0 | (codepoint >> 18)));
        out.push_back(static_cast<char>(0x80 | ((codepoint >> 12) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | ((codepoint >> 6) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | (codepoint & 0x3F)));
    }
}

class JsonParser {
public:
    explicit JsonParser(std::string_view text) : text_(text) {}
    std::optional<Json> Parse()
    {
        auto value = ParseValue();
        Skip();
        if (!value || pos_ != text_.size()) return std::nullopt;
        return value;
    }
private:
    void Skip() { while (pos_ < text_.size() && std::isspace(static_cast<unsigned char>(text_[pos_]))) ++pos_; }
    bool Consume(char c) { Skip(); if (pos_ >= text_.size() || text_[pos_] != c) return false; ++pos_; return true; }
    std::optional<std::string> ParseString()
    {
        if (!Consume('"')) return std::nullopt;
        std::string out;
        while (pos_ < text_.size()) {
            const unsigned char c = static_cast<unsigned char>(text_[pos_++]);
            if (c == '"') return out;
            if (c < 0x20) return std::nullopt;
            if (c != '\\') { out.push_back(static_cast<char>(c)); continue; }
            if (pos_ >= text_.size()) return std::nullopt;
            const char e = text_[pos_++];
            switch (e) {
                case '"': case '\\': case '/': out.push_back(e); break;
                case 'b': out.push_back('\b'); break; case 'f': out.push_back('\f'); break;
                case 'n': out.push_back('\n'); break; case 'r': out.push_back('\r'); break; case 't': out.push_back('\t'); break;
                case 'u': {
                    if (pos_ + 4 > text_.size()) return std::nullopt;
                    uint32_t cp = 0;
                    for (int i = 0; i < 4; ++i) {
                        char h = text_[pos_++];
                        cp <<= 4;
                        if (h >= '0' && h <= '9') cp |= h - '0';
                        else if (h >= 'a' && h <= 'f') cp |= h - 'a' + 10;
                        else if (h >= 'A' && h <= 'F') cp |= h - 'A' + 10;
                        else return std::nullopt;
                    }
                    if (cp >= 0xD800 && cp <= 0xDBFF) {
                        if (pos_ + 6 > text_.size() || text_.substr(pos_, 2) != "\\u") return std::nullopt;
                        pos_ += 2; uint32_t low = 0;
                        for (int i = 0; i < 4; ++i) {
                            char h = text_[pos_++]; low <<= 4;
                            if (h >= '0' && h <= '9') low |= h - '0';
                            else if (h >= 'a' && h <= 'f') low |= h - 'a' + 10;
                            else if (h >= 'A' && h <= 'F') low |= h - 'A' + 10;
                            else return std::nullopt;
                        }
                        if (low < 0xDC00 || low > 0xDFFF) return std::nullopt;
                        cp = 0x10000 + ((cp - 0xD800) << 10) + low - 0xDC00;
                    } else if (cp >= 0xDC00 && cp <= 0xDFFF) return std::nullopt;
                    AppendUtf8(out, cp);
                    break;
                }
                default: return std::nullopt;
            }
        }
        return std::nullopt;
    }
    std::optional<Json> ParseValue()
    {
        struct DepthGuard { size_t& value; ~DepthGuard() { --value; } } guard{depth_};
        if (++depth_ > 32) return std::nullopt;
        Skip(); if (pos_ >= text_.size()) return std::nullopt;
        if (text_[pos_] == '"') { auto s = ParseString(); if (!s) return std::nullopt; return Json{std::move(*s)}; }
        if (text_[pos_] == '{') return ParseObject();
        if (text_[pos_] == '[') return ParseArray();
        if (text_.substr(pos_, 4) == "true") { pos_ += 4; return Json{true}; }
        if (text_.substr(pos_, 5) == "false") { pos_ += 5; return Json{false}; }
        if (text_.substr(pos_, 4) == "null") { pos_ += 4; return Json{nullptr}; }
        size_t start = pos_;
        if (text_[pos_] == '-') ++pos_;
        while (pos_ < text_.size() && std::isdigit(static_cast<unsigned char>(text_[pos_]))) ++pos_;
        if (start == pos_ || (text_[start] == '-' && start + 1 == pos_)) return std::nullopt;
        const size_t digits = text_[start] == '-' ? start + 1 : start;
        if (pos_ > digits + 1 && text_[digits] == '0') return std::nullopt;
        if (pos_ < text_.size() && (text_[pos_] == '.' || text_[pos_] == 'e' || text_[pos_] == 'E')) {
            if (text_[pos_] == '.') {
                const size_t fraction = ++pos_;
                while (pos_ < text_.size() && std::isdigit(static_cast<unsigned char>(text_[pos_]))) ++pos_;
                if (pos_ == fraction) return std::nullopt;
            }
            if (pos_ < text_.size() && (text_[pos_] == 'e' || text_[pos_] == 'E')) {
                ++pos_; if (pos_ < text_.size() && (text_[pos_] == '+' || text_[pos_] == '-')) ++pos_;
                const size_t exponent = pos_;
                while (pos_ < text_.size() && std::isdigit(static_cast<unsigned char>(text_[pos_]))) ++pos_;
                if (pos_ == exponent) return std::nullopt;
            }
            double number = 0;
            auto result = std::from_chars(text_.data() + start, text_.data() + pos_, number);
            if (result.ec != std::errc{} || !std::isfinite(number)) return std::nullopt;
            return Json{number};
        }
        int64_t number = 0;
        auto result = std::from_chars(text_.data() + start, text_.data() + pos_, number);
        if (result.ec != std::errc{}) return std::nullopt;
        return Json{number};
    }
    std::optional<Json> ParseObject()
    {
        if (!Consume('{')) return std::nullopt;
        Json::Object object; Skip();
        if (Consume('}')) return Json{std::move(object)};
        while (true) {
            auto key = ParseString(); if (!key || !Consume(':')) return std::nullopt;
            auto value = ParseValue(); if (!value) return std::nullopt;
            if (!object.emplace(std::move(*key), std::move(*value)).second) return std::nullopt;
            if (Consume('}')) return Json{std::move(object)};
            if (!Consume(',')) return std::nullopt;
        }
    }
    std::optional<Json> ParseArray()
    {
        if (!Consume('[')) return std::nullopt;
        Json::Array array; Skip();
        if (Consume(']')) return Json{std::move(array)};
        while (true) {
            auto value = ParseValue(); if (!value) return std::nullopt;
            array.push_back(std::move(*value));
            if (Consume(']')) return Json{std::move(array)};
            if (!Consume(',')) return std::nullopt;
        }
    }
    std::string_view text_; size_t pos_ = 0, depth_ = 0;
};

inline const Json* Field(const Json::Object& object, const char* key)
{
    auto it = object.find(key); return it == object.end() ? nullptr : &it->second;
}
inline std::string StringField(const Json::Object& object, const char* key)
{
    const Json* value = Field(object, key); if (!value) return {};
    const auto* text = std::get_if<std::string>(&value->value); return text ? *text : std::string{};
}
inline bool BoolField(const Json::Object& object, const char* key, bool& out)
{
    const Json* value = Field(object, key); if (!value) return false;
    const auto* flag = std::get_if<bool>(&value->value); if (!flag) return false; out = *flag; return true;
}
inline bool IntField(const Json::Object& object, const char* key, int64_t& out)
{
    const Json* value = Field(object, key); if (!value) return false;
    const auto* number = std::get_if<int64_t>(&value->value); if (!number) return false; out = *number; return true;
}

inline std::string EscapeJson(std::string_view value)
{
    static constexpr char hex[] = "0123456789abcdef";
    std::string out;
    for (unsigned char c : value) {
        switch (c) {
            case '"': out += "\\\""; break; case '\\': out += "\\\\"; break;
            case '\b': out += "\\b"; break; case '\f': out += "\\f"; break;
            case '\n': out += "\\n"; break; case '\r': out += "\\r"; break; case '\t': out += "\\t"; break;
            default:
                if (c < 0x20) { out += "\\u00"; out.push_back(hex[c >> 4]); out.push_back(hex[c & 15]); }
                else out.push_back(static_cast<char>(c));
        }
    }
    return out;
}


inline std::string Dump(const Json& value) {
    if (std::holds_alternative<std::nullptr_t>(value.value)) return "null";
    if (const auto* x = std::get_if<bool>(&value.value)) return *x ? "true" : "false";
    if (const auto* x = std::get_if<int64_t>(&value.value)) return std::to_string(*x);
    if (const auto* x = std::get_if<double>(&value.value)) { std::ostringstream s; s.precision(17); s << *x; return s.str(); }
    if (const auto* x = std::get_if<std::string>(&value.value)) return "\"" + EscapeJson(*x) + "\"";
    if (const auto* x = std::get_if<Json::Array>(&value.value)) {
        std::string out = "["; for (const auto& item : *x) { if (out.size() > 1) out += ','; out += Dump(item); } return out + ']';
    }
    std::string out = "{";
    for (const auto& [key, item] : std::get<Json::Object>(value.value)) {
        if (out.size() > 1) out += ','; out += "\"" + EscapeJson(key) + "\":" + Dump(item);
    }
    return out + '}';
}
inline Json S(std::string value) { return Json{std::move(value)}; }
inline Json N(int64_t value) { return Json{value}; }
inline Json B(bool value) { return Json{value}; }
inline Json O(Json::Object value = {}) { return Json{std::move(value)}; }
inline Json A(Json::Array value = {}) { return Json{std::move(value)}; }
}
