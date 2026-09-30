#include "ble_hid_api.h"
#include "ble_hid_internal.h"
#include "hid_reports.h"
#include <string.h>

#define KB_RPT_LEN  7
#define MOUSE_RPT_LEN 6

esp_err_t esp_hidd_register_callbacks(esp_hidd_event_cb_t cb, uint8_t gamepad) {
    if (!cb) return ESP_FAIL;
    hidd_le_env.hidd_cb = cb;
    esp_err_t r = hidd_register_cb(gamepad);
    if (r != ESP_OK) return r;
    esp_ble_gatts_app_register(BATTERY_APP_ID);
    return esp_ble_gatts_app_register(HIDD_APP_ID);
}

esp_err_t esp_hidd_profile_init(void) {
    if (hidd_le_env.enabled) return ESP_FAIL;
    memset(&hidd_le_env, 0, sizeof(hidd_le_env_t));
    hidd_le_env.enabled = true;
    return ESP_OK;
}

esp_err_t esp_hidd_profile_deinit(void) {
    uint16_t hdl = hidd_le_env.hidd_inst.att_tbl[HIDD_LE_IDX_SVC];
    if (!hidd_le_env.enabled) return ESP_OK;
    if (hdl != 0) {
        esp_ble_gatts_stop_service(hdl);
        esp_ble_gatts_delete_service(hdl);
    } else {
        return ESP_FAIL;
    }
    esp_ble_gatts_app_unregister(hidd_le_env.gatt_if);
    hidd_le_env.enabled = false;
    return ESP_OK;
}

uint16_t esp_hidd_get_version(void) {
    return HIDD_VERSION;
}

void esp_hidd_send_keyboard_value(uint16_t conn_id, key_mask_t mods, uint8_t *keys, uint8_t num) {
    if (num > KB_RPT_LEN - 1) return;
    uint8_t buf[KB_RPT_LEN] = {0};
    buf[0] = mods;
    for (int i = 0; i < num; i++) buf[i + 1] = keys[i];
    hid_dev_send_report(hidd_le_env.gatt_if, conn_id, HID_RPT_ID_KEY_IN, HID_REPORT_TYPE_INPUT, KB_RPT_LEN, buf);
}

void esp_hidd_send_mouse_value(uint16_t conn_id, uint8_t btn, int16_t x, int16_t y, int8_t w) {
    uint8_t buf[MOUSE_RPT_LEN];
    uint16_t ux = (uint16_t)x;
    uint16_t uy = (uint16_t)y;
    buf[0] = btn;
    buf[1] = (uint8_t)(ux & 0xFF);
    buf[2] = (uint8_t)((ux >> 8) & 0xFF);
    buf[3] = (uint8_t)(uy & 0xFF);
    buf[4] = (uint8_t)((uy >> 8) & 0xFF);
    buf[5] = (uint8_t)w;
    hid_dev_send_report(hidd_le_env.gatt_if, conn_id, HID_RPT_ID_MOUSE_IN, HID_REPORT_TYPE_INPUT, MOUSE_RPT_LEN, buf);
}

bool esp_hidd_absolute_ready(uint16_t conn_id) {
    for (uint8_t i = 0; i < HID_MAX_APPS; i++) {
        const hidd_clcb_t *p = &hidd_le_env.hidd_clcb[i];
        if (p->in_use && p->connected && p->conn_id == conn_id && p->absolute_subscribed)
            return true;
    }
    return false;
}

esp_err_t esp_hidd_send_absolute_value(uint16_t conn_id, uint8_t btn, uint16_t x, uint16_t y, int8_t w) {
    if (!esp_hidd_absolute_ready(conn_id)) return ESP_ERR_INVALID_STATE;
    if (x > 32767 || y > 32767) return ESP_ERR_INVALID_ARG;
    uint8_t buf[MOUSE_RPT_LEN] = {
        (uint8_t)(btn & 0x1f), (uint8_t)x, (uint8_t)(x >> 8),
        (uint8_t)y, (uint8_t)(y >> 8), (uint8_t)w
    };
    return hid_dev_send_report(hidd_le_env.gatt_if, conn_id, HID_RPT_ID_ABSOLUTE_IN,
                        HID_REPORT_TYPE_INPUT, MOUSE_RPT_LEN, buf);
}
