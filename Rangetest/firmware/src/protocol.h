#pragma once
#include <Arduino.h>

// Wire-Protokoll v2 mit Store-and-Forward Relay-Support.
//
// DATA-Paket (variable Größe, min. DATA_HDR + 2 = 15 Byte):
//   [0]=MAGIC_DATA, [1]=src, [2]=dst, [3..4]=seq(BE), [5]=size(gesamt),
//   [6]=attempt, [7]=ttl, [8]=path_len, [9..12]=path[4],
//   [13..size-3]=payload, [size-2..size-1]=crc16(BE)
//
// ACK-Paket (fix 15 Byte):
//   [0]=MAGIC_ACK, [1]=src(antwortender Empfänger), [2]=dst(=DATA.src),
//   [3..4]=seq(BE), [5]=rssi_off (rssi+164), [6]=snr(signed),
//   [7]=ttl, [8]=path_len, [9..12]=path[4], [13..14]=crc16(BE)
//
// path[]: enthält die Node-IDs aller Relays in der Reihenfolge, in der das
// Frame sie durchlaufen hat. path_len <= MAX_RELAY_HOPS. Freie Slots = 0.
//
// Forward-Regeln (DATA und ACK gleich, nur der "Endpunkt" unterscheidet sich):
//   1. Falls dst == MY_ID -> Frame ist für uns, kein Forward.
//   2. Falls src == MY_ID -> eigenes Echo, droppen.
//   3. Falls MY_ID in path -> Loop, droppen.
//   4. Falls ttl == 0 -> Lifetime aus, droppen.
//   5. Falls (src, seq, magic) bereits im Dedup-Cache -> droppen.
//   6. Sonst: ttl--, MY_ID an path anhängen (falls Platz), broadcast.
// Vor dem Re-Broadcast wird ein zufälliger Jitter (0..6 ms) eingefügt, damit
// mehrere Relays nicht synchron senden und sich gegenseitig zerschlagen.
//
// PROBE-Paket (fix 13 Byte): Hop-Progress-Beacon.
//   [0]=MAGIC_PROBE, [1]=orig_src(=DATA.src), [2]=my_id(=Relay, der das DATA
//   weitergeleitet hat), [3..4]=seq(BE), [5]=ttl, [6]=path_len,
//   [7..10]=path[4] (inkl. my_id am Ende), [11..12]=crc16(BE)
//
// PROBE-Regel: Nach erfolgreichem DATA-Forward emittiert das Relay genau ein
// PROBE pro (my_id, seq). PROBEs werden wie ACKs geflutet (TTL-begrenzt,
// Dedup über (MAGIC_PROBE, my_id, seq)). Der Sender überhört die Beacons und
// kennt dadurch bei ACK-Timeout die tatsächlich erreichte Teilstrecke
// ("reached_path"). Endpunkt-Empfänger ignorieren PROBEs.

#define MAGIC_DATA       0xAA
#define MAGIC_ACK        0x55
#define MAGIC_PROBE      0xCC

#define MAX_RELAY_HOPS   4
#define DATA_HDR_SIZE    13     // 7 Basisfelder + ttl + path_len + path[4]
#define ACK_FRAME_SIZE   15     // DATA_HDR_SIZE + 2 Byte CRC
#define PROBE_FRAME_SIZE 13     // 11 Byte Header + 2 Byte CRC
#define DEFAULT_TTL      3      // 0 = nur direkt, 3 = bis zu 3 Hops

// Feldoffsets DATA (Byte-Index)
#define OFF_MAGIC        0
#define OFF_SRC          1
#define OFF_DST          2
#define OFF_SEQ_HI       3
#define OFF_SEQ_LO       4
#define OFF_SIZE         5
#define OFF_ATTEMPT      6
#define OFF_TTL          7
#define OFF_PATH_LEN     8
#define OFF_PATH         9      // 4 Byte path[0..3]
#define OFF_PAYLOAD      13

// Feldoffsets ACK (RSSI/SNR statt size+attempt)
#define OFF_ACK_RSSI     5
#define OFF_ACK_SNR      6

// Feldoffsets PROBE
#define OFF_PR_ORIG_SRC  1
#define OFF_PR_MY_ID     2
#define OFF_PR_SEQ_HI    3
#define OFF_PR_SEQ_LO    4
#define OFF_PR_TTL       5
#define OFF_PR_PATH_LEN  6
#define OFF_PR_PATH      7      // 4 Byte path[0..3]

static inline uint16_t crc16_ccitt(const uint8_t *data, size_t len) {
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < len; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (int b = 0; b < 8; b++) {
            if (crc & 0x8000) crc = (crc << 1) ^ 0x1021;
            else              crc <<= 1;
        }
    }
    return crc;
}
