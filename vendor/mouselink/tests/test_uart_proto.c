#include <assert.h>
#include <string.h>
#include "uart_proto.h"

static int keyboard_count, relative_count, absolute_count;
static absolute_report_t last;
static void keyboard_cb(const kbd_report_t *r) { (void)r; keyboard_count++; }
static void mouse_cb(const mouse_report_t *r) { (void)r; relative_count++; }
static void absolute_cb(const absolute_report_t *r) { last = *r; absolute_count++; }
static void send(proto_ctx_t *ctx, uint8_t type, uint16_t x, uint16_t y, bool corrupt) {
    uint8_t data[8] = {type == PROTO_TYPE_ABSOLUTE ? 42 : 0, type, 5, (uint8_t)x, (uint8_t)(x >> 8),
                      (uint8_t)y, (uint8_t)(y >> 8), 255};
    proto_feed(ctx, PROTO_HEADER);
    for (unsigned i = 0; i < sizeof(data); i++) proto_feed(ctx, data[i]);
    proto_feed(ctx, (uint8_t)(proto_calc_checksum(data, 8) ^ (corrupt ? 1 : 0)));
}
int main(void) {
    proto_ctx_t ctx;
    proto_init(&ctx, keyboard_cb, mouse_cb, absolute_cb);
    send(&ctx, PROTO_TYPE_ABSOLUTE, 0, 32767, false);
    assert(absolute_count == 1 && last.x == 0 && last.y == 32767);
    assert(last.buttons == 5 && last.wheel == -1);
    assert(last.sequence == 42);
    send(&ctx, PROTO_TYPE_ABSOLUTE, 32768, 0, false);
    send(&ctx, PROTO_TYPE_ABSOLUTE, 100, 200, true);
    assert(absolute_count == 1);
    send(&ctx, PROTO_TYPE_ABSOLUTE, 16384, 16384, false);
    assert(absolute_count == 2 && last.x == 16384);
    send(&ctx, PROTO_TYPE_KBD, 0, 0, false);
    send(&ctx, PROTO_TYPE_MOUSE, 0, 0, false);
    assert(keyboard_count == 1 && relative_count == 1);
    return 0;
}
