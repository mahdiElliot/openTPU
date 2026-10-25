// Bare-metal TCP transport for the openTPU Engine. Little-endian protocol:
// request {op, physical_address, length, reserved}; response {error, bytes}.
// op 1 writes bytes, op 2 reads bytes, op 3 loads/runs a program (8 arguments).
#include <stdint.h>
#include <string.h>
#include "lwip/tcp.h"
#include "xil_cache.h"
#include "xil_io.h"
#include "xil_printf.h"
#include "xtime_l.h"
#include "netif/xadapter.h"

#define CSR 0x80000000U
#define MAX_TRANSFER (16U << 20)
static struct tcp_pcb *client;
static uint32_t header[4], arguments[8], reply[8];
static unsigned header_bytes, argument_bytes;
static uint32_t operation, address, remaining;
static unsigned char *transmit;
static uint32_t transmit_bytes;
static unsigned run_phase;
static XTime run_started;
static uint32_t crc_table[256];
extern struct netif *echo_netif;
void tcp_fasttmr(void);
void tcp_slowtmr(void);

static int valid_region(uint32_t at, uint32_t bytes) {
    uint64_t end = (uint64_t)at + bytes;
    if (bytes > MAX_TRANSFER || !bytes) return 0;
    if (at >= 0x10000000U && end <= 0x80000000ULL) return 1;
    return at >= CSR && end <= (uint64_t)CSR + 4096 && !(at & 3) && !(bytes & 3);
}

static void pump(void) {
    while (client && transmit_bytes) {
        unsigned size = tcp_sndbuf(client);
        if (size > 4096) size = 4096;
        if (size > transmit_bytes) size = transmit_bytes;
        if (!size) break;
        if (tcp_write(client, transmit, size, TCP_WRITE_FLAG_COPY) != ERR_OK) break;
        transmit += size;
        transmit_bytes -= size;
    }
    if (client) tcp_output(client);
}

static void response(uint32_t error, void *data, uint32_t bytes) {
    uint32_t prefix[2] = {error, bytes};
    if (!client) return;
    if (tcp_write(client, prefix, sizeof(prefix), TCP_WRITE_FLAG_COPY) != ERR_OK) {
        tcp_abort(client); client = NULL; return;
    }
    transmit = data;
    transmit_bytes = bytes;
    header_bytes = 0;
    operation = 0;
    pump();
}

static void start_run(void) {
    Xil_Out32(CSR + 8, 4);
    Xil_Out32(CSR + 0x10, address);
    Xil_Out32(CSR + 0x14, remaining);
    Xil_Out32(CSR + 8, 2);
    run_phase = 1;
    XTime_GetTime(&run_started);
}

static int begin_request(void) {
    operation = header[0]; address = header[1]; remaining = header[2];
    if (operation == 3) {
        if (address >= 0x70000000U || !remaining || remaining > 4096) {
            response(1, NULL, 0); return 0;
        }
        argument_bytes = 0;
        return 1;
    }
    if ((operation != 1 && operation != 2 && operation != 5) || !valid_region(address, remaining)) {
        response(1, NULL, 0); return 0;
    }
    if (operation == 5) {
        if (address >= CSR) {response(1, NULL, 0); return 0;}
        Xil_DCacheInvalidateRange(address, remaining);
        const unsigned char *data = (const unsigned char *)(uintptr_t)address;
        uint32_t crc = 0xffffffffU;
        for (uint32_t i = 0; i < remaining; i++) crc = crc_table[(crc ^ data[i]) & 255] ^ (crc >> 8);
        reply[0] = crc ^ 0xffffffffU;
        response(0, reply, 4);
        return 0;
    }
    if (operation == 2) {
        if (address < CSR) {
            Xil_DCacheInvalidateRange(address, remaining);
            response(0, (void *)(uintptr_t)address, remaining);
        } else {
            if (remaining > sizeof(reply)) {response(1, NULL, 0); return 0;}
            for (unsigned i = 0; i < remaining / 4; i++) reply[i] = Xil_In32(address + i * 4);
            response(0, reply, remaining);
        }
        return 0;
    }
    return 1;
}

static err_t received(void *unused, struct tcp_pcb *pcb, struct pbuf *p, err_t error) {
    (void)unused; (void)error;
    if (!p) {tcp_close(pcb); client = NULL; return ERR_OK;}
    for (struct pbuf *part = p; part; part = part->next) {
        const unsigned char *data = part->payload;
        unsigned available = part->len;
        while (available) {
            if (!operation) {
                unsigned n = 16 - header_bytes;
                if (n > available) n = available;
                memcpy((unsigned char *)header + header_bytes, data, n);
                header_bytes += n; data += n; available -= n;
                if (header_bytes == 16) begin_request();
            } else if (operation == 1) {
                unsigned n = remaining < available ? remaining : available;
                if (address < CSR) {
                    memcpy((void *)(uintptr_t)address, data, n);
                    Xil_DCacheFlushRange(address, n);
                } else {
                    // CSR requests from the host are one aligned uint32 word.
                    if (remaining != 4 || n != 4) {response(1, NULL, 0); break;}
                    uint32_t value; memcpy(&value, data, 4); Xil_Out32(address, value);
                }
                address += n; remaining -= n; data += n; available -= n;
                if (!remaining) response(0, NULL, 0);
            } else if (operation == 3) {
                unsigned n = 32 - argument_bytes;
                if (n > available) n = available;
                memcpy((unsigned char *)arguments + argument_bytes, data, n);
                argument_bytes += n; data += n; available -= n;
                if (argument_bytes == 32) {
                    start_run();
                    operation = 4;
                }
            } else {available = 0;}
        }
    }
    tcp_recved(pcb, p->tot_len);
    pbuf_free(p);
    return ERR_OK;
}

static err_t acknowledged(void *unused, struct tcp_pcb *pcb, u16_t bytes) {
    (void)unused; (void)pcb; (void)bytes; pump(); return ERR_OK;
}

static void disconnected(void *unused, err_t error) {
    (void)unused; (void)error; client = NULL;
}

static err_t accepted(void *unused, struct tcp_pcb *pcb, err_t error) {
    (void)unused;
    if (error != ERR_OK) return error;
    if (client) {tcp_abort(pcb); return ERR_ABRT;}
    client = pcb; header_bytes = 0; operation = 0; run_phase = 0; transmit_bytes = 0;
    tcp_nagle_disable(pcb);
    tcp_recv(pcb, received); tcp_sent(pcb, acknowledged); tcp_err(pcb, disconnected);
    return ERR_OK;
}

int transfer_data(void) {
    static XTime last_fast, last_slow, last_link;
    XTime tick; XTime_GetTime(&tick);
    if (tick - last_fast >= COUNTS_PER_SECOND / 4) {tcp_fasttmr(); last_fast = tick;}
    if (tick - last_slow >= COUNTS_PER_SECOND / 2) {tcp_slowtmr(); last_slow = tick;}
    if (tick - last_link >= 5ULL * COUNTS_PER_SECOND) {eth_link_detect(echo_netif); last_link = tick;}
    if (run_phase) {
        uint32_t status = Xil_In32(CSR + 0xc);
        XTime now; XTime_GetTime(&now);
        if ((status & 0x112) || now - run_started > 180ULL * COUNTS_PER_SECOND) {
            reply[0] = status;
            Xil_Out32(CSR + 8, 0); run_phase = 0; response(2, reply, 4);
        } else if (run_phase == 1 && !(status & 4)) {
            for (unsigned k = 0; k < 8; k++) Xil_Out32(CSR + 0x60 + 4 * k, arguments[k]);
            Xil_Out32(CSR + 8, 5); run_phase = 2;
        } else if (run_phase == 2 && (status & 9) == 9) {
            reply[0] = Xil_In32(CSR + 0x18); reply[1] = Xil_In32(CSR + 0x1c);
            reply[2] = Xil_In32(CSR + 0x20); reply[3] = status;
            Xil_Out32(CSR + 8, 0); run_phase = 0; response(0, reply, 16);
        }
    }
    pump();
    return 0;
}

void print_app_header(void) {xil_printf("openTPU bare-metal transport: 192.168.200.2:8020\r\n");}

int start_application(void) {
    for (unsigned i = 0; i < 256; i++) {
        uint32_t crc = i;
        for (unsigned k = 0; k < 8; k++) crc = (crc >> 1) ^ (0xedb88320U & (0U - (crc & 1U)));
        crc_table[i] = crc;
    }
    struct tcp_pcb *pcb = tcp_new_ip_type(IPADDR_TYPE_V4);
    if (!pcb || tcp_bind(pcb, IP_ANY_TYPE, 8020) != ERR_OK) return -1;
    pcb = tcp_listen(pcb);
    tcp_accept(pcb, accepted);
    return 0;
}
