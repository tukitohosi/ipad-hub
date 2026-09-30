#include "ble_hid_internal.h"
#include <stdint.h>

static hid_report_map_t *rpt_tbl;
static uint8_t rpt_tbl_len;
static uint8_t last_report_id;
static esp_err_t last_result = ESP_ERR_INVALID_STATE;
static uint16_t report_count;

static hid_report_map_t *rpt_by_id(uint8_t id, uint8_t type) {
    hid_report_map_t *r = rpt_tbl;
    for (uint8_t i = rpt_tbl_len; i > 0; i--, r++) {
        if (r->id == id && r->type == type && r->mode == hidProtocolMode)
            return r;
    }
    return NULL;
}

void hid_dev_register_reports(uint8_t num_reports, hid_report_map_t *p_report) {
    rpt_tbl = p_report;
    rpt_tbl_len = num_reports;
}

esp_err_t hid_dev_send_report(esp_gatt_if_t gatts_if, uint16_t conn_id, uint8_t id, uint8_t type, uint8_t length, uint8_t *data) {
    hid_report_map_t *r = rpt_by_id(id, type);
    esp_err_t result = r
        ? esp_ble_gatts_send_indicate(gatts_if, conn_id, r->handle, length, data, false)
        : ESP_ERR_NOT_FOUND;
    last_report_id = id;
    last_result = result;
    report_count++;
    return result;
}

void hid_dev_transport_status(uint8_t status[8]) {
    // Diagnostic only: ESP_OK confirms BLE enqueue, never screen coordinates.
    uint16_t count = report_count;
    uint16_t result = (uint16_t)last_result;
    status[0] = 0xF8;
    status[1] = last_report_id;
    status[2] = (uint8_t)result;
    status[3] = (uint8_t)(result >> 8);
    status[4] = (uint8_t)count;
    status[5] = (uint8_t)(count >> 8);
    status[6] = hidProtocolMode;
    status[7] = 0xA5;
    for (int i = 1; i < 7; i++) status[7] ^= status[i];
}

