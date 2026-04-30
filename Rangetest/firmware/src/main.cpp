#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <esp_random.h>
#include <Preferences.h>
#include "protocol.h"

#ifndef NODE_ID
#define NODE_ID 1
#endif
#ifndef ESPNOW_CHANNEL
#define ESPNOW_CHANNEL 1
#endif
#ifndef ESPNOW_LR
#define ESPNOW_LR 0
#endif
#ifndef RELAY_ENABLED
// Sender hat Relay default aus, alles andere default an.
#  if ROLE_SENDER
#    define RELAY_ENABLED 0
#  else
#    define RELAY_ENABLED 1
#  endif
#endif
#ifndef ALLOWED_PREV_MASK
// 0 = alle Vorgänger erlaubt (kein Topologie-Filter).
// Sonst: Bitmaske, Bit n gesetzt => NODE_ID n als letzter Hop akzeptiert.
#define ALLOWED_PREV_MASK 0
#endif
#ifndef FW_VERSION
#define FW_VERSION "espnow-rangetest-0.5-cfg"
#endif
#ifndef PROBE_ENABLED
// 1 = Relays emittieren PROBE-Beacons nach jedem DATA-Forward.
#define PROBE_ENABLED 1
#endif

static const uint8_t MY_ID = (uint8_t)NODE_ID;
static const uint8_t BCAST[6] = {0xff,0xff,0xff,0xff,0xff,0xff};

// ===================================================================
//   Runtime-Konfiguration mit NVS-Persistenz (Phase 4c)
//   Build-Flags liefern Default-Werte; loadCfg() überschreibt zur
//   Laufzeit aus dem NVS-Namespace "rt".
//   Schreibend gesetzt durch  cmd=cfg key=value [save=0/1] [reboot=0/1]
//   Gelesen durch              cmd=getcfg
// ===================================================================
struct RuntimeCfg {
    uint8_t  channel;       // 1..14
    uint8_t  lr;            // 0/1
    uint8_t  relay_enabled; // 0/1 (nur wirksam wenn build RELAY_ENABLED=1)
    uint8_t  probe_enabled; // 0/1 (nur wirksam wenn build PROBE_ENABLED=1)
    uint32_t prev_mask;     // 0 = alle Vorgänger erlaubt
};

static RuntimeCfg g_cfg = {
    (uint8_t)ESPNOW_CHANNEL,
    (uint8_t)ESPNOW_LR,
    (uint8_t)RELAY_ENABLED,
    (uint8_t)PROBE_ENABLED,
    (uint32_t)ALLOWED_PREV_MASK,
};

static const char *CFG_NS = "rt";

static void loadCfg() {
    Preferences p;
    if (!p.begin(CFG_NS, true)) return;
    if (p.isKey("channel"))   g_cfg.channel       = p.getUChar("channel",   g_cfg.channel);
    if (p.isKey("lr"))        g_cfg.lr            = p.getUChar("lr",        g_cfg.lr);
    if (p.isKey("relay"))     g_cfg.relay_enabled = p.getUChar("relay",     g_cfg.relay_enabled);
    if (p.isKey("probe"))     g_cfg.probe_enabled = p.getUChar("probe",     g_cfg.probe_enabled);
    if (p.isKey("prev_mask")) g_cfg.prev_mask     = p.getUInt ("prev_mask", g_cfg.prev_mask);
    p.end();
    if (g_cfg.channel < 1 || g_cfg.channel > 14) g_cfg.channel = (uint8_t)ESPNOW_CHANNEL;
}

static void saveCfg() {
    Preferences p;
    if (!p.begin(CFG_NS, false)) return;
    p.putUChar("channel",   g_cfg.channel);
    p.putUChar("lr",        g_cfg.lr);
    p.putUChar("relay",     g_cfg.relay_enabled);
    p.putUChar("probe",     g_cfg.probe_enabled);
    p.putUInt ("prev_mask", g_cfg.prev_mask);
    p.end();
}

static void clearCfgNvs() {
    Preferences p;
    if (!p.begin(CFG_NS, false)) return;
    p.clear();
    p.end();
}

// ===================================================================
//   ACK-/RX-State (Sender bzw. Receiver-Endpunkt)
// ===================================================================
static volatile bool     g_ackReceived  = false;
static volatile uint16_t g_ackSeq       = 0;
static volatile int16_t  g_ackRssiRem   = 0;
static volatile int8_t   g_ackSnrRem    = 0;
static volatile int16_t  g_ackRssiLoc   = 0;   // Sender: RSSI des ACK lokal (Sniffer)
static volatile int8_t   g_ackSnrLoc    = 0;
static volatile uint8_t  g_ackPathLen   = 0;
static uint8_t           g_ackPath[MAX_RELAY_HOPS] = {0};

// Wi-Fi-Promiscuous-Sniffer fuer RSSI/SNR. Wird parallel zu ESP-NOW
// betrieben; der Sniffer-Callback feuert kurz vor onDataRecv und
// hinterlegt den letzten gemessenen RSSI-/Noisefloor-Wert. Der ESP-NOW-
// Callback liest g_lastSniff* synchron ab und ordnet den Wert dem
// passenden Frame zu (DATA -> Empfaenger, ACK -> Sender).
static volatile int16_t g_lastSniffRssi   = 0;
static volatile int8_t  g_lastSniffNoise  = -96;

// Sender-only: PROBE-basiertes Progress-Tracking
//   g_pendingSeq: aktuell gesendete seq (16 Bit), nur gültig wenn
//                 g_pendingActive==true.
//   g_progressPath/Len: längste via PROBE bestätigte Teilstrecke. Wird
//                 bei jedem matching PROBE überschrieben, wenn länger.
static volatile bool     g_pendingActive   = false;
static volatile uint16_t g_pendingSeq      = 0;
static volatile uint8_t  g_progressPathLen = 0;
static uint8_t           g_progressPath[MAX_RELAY_HOPS] = {0};

struct RxFrame {
    volatile bool present;
    uint8_t  src;
    uint16_t seq;
    uint8_t  size;
    uint8_t  attempt;
    bool     crcOk;
    int16_t  rssi;     // dBm, am Empfaenger via Promiscuous-Sniffer gemessen
    int8_t   snr;      // dB,  rssi - noise_floor
    uint8_t  pathLen;
    uint8_t  path[MAX_RELAY_HOPS];
};
static RxFrame g_rx = {};

// ===================================================================
//   Dedup-Cache: verhindert endlose Forward-Loops bei Flutung
// ===================================================================
struct DedupEntry {
    uint8_t  magic;   // MAGIC_DATA oder MAGIC_ACK -> separate Streams
    uint8_t  src;
    uint16_t seq;
    bool     used;
};
static const int DEDUP_SLOTS = 32;
static DedupEntry g_dedup[DEDUP_SLOTS];
static uint8_t    g_dedupNext = 0;

static bool dedupCheckAndMark(uint8_t magic, uint8_t src, uint16_t seq) {
    for (int i = 0; i < DEDUP_SLOTS; i++) {
        if (g_dedup[i].used && g_dedup[i].magic == magic
            && g_dedup[i].src == src && g_dedup[i].seq == seq) {
            return true; // schon gesehen
        }
    }
    g_dedup[g_dedupNext] = { magic, src, seq, true };
    g_dedupNext = (g_dedupNext + 1) % DEDUP_SLOTS;
    return false;
}

// ===================================================================
//   Forward-Queue: aus dem RX-Callback befüllt, im loop() gesendet
//   (Kein esp_now_send direkt im Callback, um Latenzspitzen und
//    Re-Entrancy zu vermeiden, plus zufälliges Jitter gegen Kollisionen.)
// ===================================================================
struct PendingFwd {
    uint8_t  buf[250];
    uint8_t  len;
    uint32_t sendAt;
    bool     used;
};
static const int FWD_SLOTS = 8;
static PendingFwd g_fwd[FWD_SLOTS];

static bool enqueueForward(const uint8_t *buf, uint8_t len) {
    for (int i = 0; i < FWD_SLOTS; i++) {
        if (!g_fwd[i].used) {
            memcpy(g_fwd[i].buf, buf, len);
            g_fwd[i].len    = len;
            g_fwd[i].sendAt = millis() + (uint32_t)(esp_random() % 7); // 0..6 ms Jitter
            g_fwd[i].used   = true;
            return true;
        }
    }
    return false; // queue voll, drop
}

static void drainForwardQueue() {
    uint32_t now = millis();
    for (int i = 0; i < FWD_SLOTS; i++) {
        if (g_fwd[i].used && (int32_t)(now - g_fwd[i].sendAt) >= 0) {
            esp_now_send(BCAST, g_fwd[i].buf, g_fwd[i].len);
            g_fwd[i].used = false;
        }
    }
}

// ===================================================================
//   Topologie-Filter (Whitelist erlaubter Vorgänger-Hops)
//   Erzwingt eine logische Chain unabhängig von der physischen Reichweite.
//
//   DATA: Relays/Empfänger prüfen, von welchem Knoten sie das Frame
//         entgegennehmen dürfen. prev = letzter Eintrag in path[],
//         oder src wenn path leer.
//   ACK : Filter NUR für den Endpunkt (Sender) aktiv, nicht für
//         Relay-Forwarding. ACK trägt den eingefrorenen DATA-Path; aus
//         Sicht des Senders ist der erste Hop zurück path[0]
//         (Reverse-Path), nicht path[last].
// ===================================================================
static uint8_t framePrevHopForward(const uint8_t *frame) {
    uint8_t pathLen = frame[OFF_PATH_LEN];
    if (pathLen > MAX_RELAY_HOPS) pathLen = MAX_RELAY_HOPS;
    return (pathLen > 0) ? frame[OFF_PATH + pathLen - 1] : frame[OFF_SRC];
}

static uint8_t framePrevHopReverse(const uint8_t *frame) {
    uint8_t pathLen = frame[OFF_PATH_LEN];
    if (pathLen > MAX_RELAY_HOPS) pathLen = MAX_RELAY_HOPS;
    return (pathLen > 0) ? frame[OFF_PATH] : frame[OFF_SRC];
}

static bool prevAllowedMask(uint8_t prev) {
    if (g_cfg.prev_mask == 0) return true;
    if (prev >= 32) return false;
    return (g_cfg.prev_mask >> prev) & 1u;
}

static bool dataPrevAllowed(const uint8_t *frame) {
    return prevAllowedMask(framePrevHopForward(frame));
}

static bool ackEndpointPrevAllowed(const uint8_t *frame) {
    return prevAllowedMask(framePrevHopReverse(frame));
}

// ===================================================================
//   Frame-Helpers
// ===================================================================
static bool myIdInPath(const uint8_t *path, uint8_t pathLen) {
    for (uint8_t i = 0; i < pathLen && i < MAX_RELAY_HOPS; i++) {
        if (path[i] == MY_ID) return true;
    }
    return false;
}

static void appendToPath(uint8_t *path, uint8_t &pathLen) {
    if (pathLen < MAX_RELAY_HOPS) {
        path[pathLen++] = MY_ID;
    }
}

// Validiert CRC und basale Felder. Liefert true wenn ok.
static bool validateData(const uint8_t *d, int len) {
    if (len < DATA_HDR_SIZE + 2 || len > 250) return false;
    if (d[OFF_MAGIC] != MAGIC_DATA) return false;
    if (d[OFF_SIZE]  != len) return false;
    uint16_t crc_calc = crc16_ccitt(d, len - 2);
    uint16_t crc_rx   = ((uint16_t)d[len - 2] << 8) | d[len - 1];
    return crc_calc == crc_rx;
}

static bool validateAck(const uint8_t *d, int len) {
    if (len != ACK_FRAME_SIZE) return false;
    if (d[OFF_MAGIC] != MAGIC_ACK) return false;
    uint16_t crc_calc = crc16_ccitt(d, len - 2);
    uint16_t crc_rx   = ((uint16_t)d[len - 2] << 8) | d[len - 1];
    return crc_calc == crc_rx;
}

static bool validateProbe(const uint8_t *d, int len) {
    if (len != PROBE_FRAME_SIZE) return false;
    if (d[OFF_MAGIC] != MAGIC_PROBE) return false;
    uint16_t crc_calc = crc16_ccitt(d, len - 2);
    uint16_t crc_rx   = ((uint16_t)d[len - 2] << 8) | d[len - 1];
    return crc_calc == crc_rx;
}

static void recomputeCrc(uint8_t *frame, uint8_t len) {
    uint16_t crc = crc16_ccitt(frame, len - 2);
    frame[len - 2] = (crc >> 8) & 0xFF;
    frame[len - 1] = crc & 0xFF;
}

// Wenn dst != MY_ID UND src != MY_ID UND wir nicht in path sind UND ttl > 0,
// dann path-erweitern, ttl--, CRC neu, in Forward-Queue legen.
// Dedup wurde bereits separat geprüft.
static void tryRelay(const uint8_t *in, int len) {
#if !RELAY_ENABLED
    (void)in; (void)len;
    return;
#else
    // Runtime-Toggle: erlaubt im Feldtest temporäres Abschalten ohne
    // Reflash. Persistiert via cmd=cfg relay=0 save=1.
    if (!g_cfg.relay_enabled) return;
    // PROBE hat eigenes Layout (TTL auf Byte 5, kein dst-Feld): separater Pfad.
    if (in[OFF_MAGIC] == MAGIC_PROBE) {
        if (len != PROBE_FRAME_SIZE) return;
        if (in[OFF_PR_MY_ID] == MY_ID) return;       // unser eigener Beacon
        if (in[OFF_PR_ORIG_SRC] == MY_ID) return;    // wir waren originaler Sender
        if (in[OFF_PR_TTL] == 0) return;
        uint8_t pbuf[PROBE_FRAME_SIZE];
        memcpy(pbuf, in, PROBE_FRAME_SIZE);
        pbuf[OFF_PR_TTL] = in[OFF_PR_TTL] - 1;
        recomputeCrc(pbuf, PROBE_FRAME_SIZE);
        enqueueForward(pbuf, PROBE_FRAME_SIZE);
        return;
    }

    if (in[OFF_TTL] == 0) return;
    if (in[OFF_SRC] == MY_ID) return;
    if (in[OFF_DST] == MY_ID) return;

    uint8_t buf[250];
    memcpy(buf, in, len);
    buf[OFF_TTL] = in[OFF_TTL] - 1;

    uint8_t newPath[MAX_RELAY_HOPS] = {0};
    uint8_t newPathLen = 0;

    if (in[OFF_MAGIC] == MAGIC_DATA) {
        // DATA: path mit MY_ID erweitern, Loop-Check via myIdInPath.
        uint8_t pathLen = in[OFF_PATH_LEN];
        if (pathLen > MAX_RELAY_HOPS) pathLen = MAX_RELAY_HOPS;
        uint8_t path[MAX_RELAY_HOPS];
        memcpy(path, in + OFF_PATH, MAX_RELAY_HOPS);
        if (myIdInPath(path, pathLen)) return;
        appendToPath(path, pathLen);
        buf[OFF_PATH_LEN] = pathLen;
        memcpy(buf + OFF_PATH, path, MAX_RELAY_HOPS);
        memcpy(newPath, path, MAX_RELAY_HOPS);
        newPathLen = pathLen;
    }
    // ACK: path bleibt unverändert (Snapshot der Forward-Route).
    // Loop-Schutz übernimmt der Dedup-Cache.
    // PROBE: path bleibt unverändert (war beim ursprünglichen Emitter
    //   befüllt). Nur TTL und CRC werden erneuert.

    recomputeCrc(buf, (uint8_t)len);
    enqueueForward(buf, (uint8_t)len);

#if PROBE_ENABLED
    // Beim DATA-Forward zusätzlich einen PROBE-Beacon einreihen, damit der
    // Sender bei ACK-Timeout weiß, wie weit das Frame in der Kette kam.
    // Runtime-Toggle: g_cfg.probe_enabled.
    if (g_cfg.probe_enabled && in[OFF_MAGIC] == MAGIC_DATA) {
        uint16_t seq = ((uint16_t)in[OFF_SEQ_HI] << 8) | in[OFF_SEQ_LO];
        uint8_t probe[PROBE_FRAME_SIZE];
        memset(probe, 0, sizeof(probe));
        probe[OFF_MAGIC]       = MAGIC_PROBE;
        probe[OFF_PR_ORIG_SRC] = in[OFF_SRC];
        probe[OFF_PR_MY_ID]    = MY_ID;
        probe[OFF_PR_SEQ_HI]   = (seq >> 8) & 0xFF;
        probe[OFF_PR_SEQ_LO]   = seq & 0xFF;
        // Eigene PROBE-TTL: gleicher Reichweiten-Wert wie das geforwardete
        // DATA, damit der Beacon bis zum Sender zurückfluten kann.
        probe[OFF_PR_TTL]      = buf[OFF_TTL];
        probe[OFF_PR_PATH_LEN] = newPathLen;
        memcpy(probe + OFF_PR_PATH, newPath, MAX_RELAY_HOPS);
        recomputeCrc(probe, PROBE_FRAME_SIZE);
        // PROBE im Dedup-Cache vormerken, damit wir unseren eigenen
        // Beacon beim Echo nicht erneut weiterleiten.
        (void)dedupCheckAndMark(MAGIC_PROBE, MY_ID, seq);
        enqueueForward(probe, PROBE_FRAME_SIZE);
    }
#endif
#endif
}

// ===================================================================
//   ESP-NOW RX-Callback (gemeinsam für alle Rollen)
// ===================================================================
static void onDataRecv(const uint8_t * /*mac*/, const uint8_t *data, int len) {
    if (len < 1) return;

    if (data[OFF_MAGIC] == MAGIC_DATA) {
        if (!validateData(data, len)) return;
        if (!dataPrevAllowed(data)) return;
        uint8_t  src = data[OFF_SRC];
        uint8_t  dst = data[OFF_DST];
        uint16_t seq = ((uint16_t)data[OFF_SEQ_HI] << 8) | data[OFF_SEQ_LO];

        // Dedup egal ob für uns oder zum Forwarden -> ein Frame, eine Reaktion.
        if (dedupCheckAndMark(MAGIC_DATA, src, seq)) return;

#if ROLE_RECEIVER
        if (dst == MY_ID) {
            uint8_t pathLen = data[OFF_PATH_LEN];
            if (pathLen > MAX_RELAY_HOPS) pathLen = MAX_RELAY_HOPS;
            g_rx.src     = src;
            g_rx.seq     = seq;
            g_rx.size    = data[OFF_SIZE];
            g_rx.attempt = data[OFF_ATTEMPT];
            g_rx.crcOk   = true;
            g_rx.rssi    = (int16_t)g_lastSniffRssi;
            {
                int snr = (int)g_lastSniffRssi - (int)g_lastSniffNoise;
                if (snr < -128) snr = -128;
                if (snr >  127) snr =  127;
                g_rx.snr = (int8_t)snr;
            }
            g_rx.pathLen = pathLen;
            memcpy(g_rx.path, data + OFF_PATH, MAX_RELAY_HOPS);
            g_rx.present = true;
            return; // nicht weiterforwarden (sind Endpunkt)
        }
#endif
        tryRelay(data, len);
        return;
    }

    if (data[OFF_MAGIC] == MAGIC_ACK) {
        if (!validateAck(data, len)) return;
        uint8_t  src = data[OFF_SRC];
        uint8_t  dst = data[OFF_DST];
        uint16_t seq = ((uint16_t)data[OFF_SEQ_HI] << 8) | data[OFF_SEQ_LO];

        if (dedupCheckAndMark(MAGIC_ACK, src, seq)) return;

#if ROLE_SENDER
        // Endpunkt-Filter: ACK nur akzeptieren, wenn der erste Hop zurück
        // (path[0] bzw. src wenn path leer) in unserer Whitelist steht.
        if (dst == MY_ID && ackEndpointPrevAllowed(data)) {
            uint8_t pathLen = data[OFF_PATH_LEN];
            if (pathLen > MAX_RELAY_HOPS) pathLen = MAX_RELAY_HOPS;
            g_ackSeq      = seq;
            g_ackRssiRem  = (int)data[OFF_ACK_RSSI] - 164;
            g_ackSnrRem   = (int8_t)data[OFF_ACK_SNR];
            g_ackRssiLoc  = (int16_t)g_lastSniffRssi;
            {
                int snr = (int)g_lastSniffRssi - (int)g_lastSniffNoise;
                if (snr < -128) snr = -128;
                if (snr >  127) snr =  127;
                g_ackSnrLoc = (int8_t)snr;
            }
            g_ackPathLen  = pathLen;
            memcpy(g_ackPath, data + OFF_PATH, MAX_RELAY_HOPS);
            g_ackReceived = true;
            return;
        }
#endif
        // Forward ungefiltert: TTL und Dedup begrenzen die Flut, der
        // DATA-Path im ACK bleibt readonly als Reporting-Information.
        tryRelay(data, len);
        return;
    }

    if (data[OFF_MAGIC] == MAGIC_PROBE) {
        if (!validateProbe(data, len)) return;
        uint8_t  relay_id = data[OFF_PR_MY_ID];
        uint16_t seq      = ((uint16_t)data[OFF_PR_SEQ_HI] << 8) | data[OFF_PR_SEQ_LO];
        // Eigenes PROBE? droppen.
        if (relay_id == MY_ID) return;
        // Dedup-Key: (MAGIC_PROBE, relay_id, seq), eindeutig pro Emitter.
        if (dedupCheckAndMark(MAGIC_PROBE, relay_id, seq)) return;

#if ROLE_SENDER
        // Beacon hochzählen, falls passend zur aktuell wartenden seq.
        if (g_pendingActive && g_pendingSeq == seq
            && data[OFF_PR_ORIG_SRC] == MY_ID) {
            uint8_t pl = data[OFF_PR_PATH_LEN];
            if (pl > MAX_RELAY_HOPS) pl = MAX_RELAY_HOPS;
            if (pl > g_progressPathLen) {
                g_progressPathLen = pl;
                memcpy(g_progressPath, data + OFF_PR_PATH, MAX_RELAY_HOPS);
            }
        }
#endif
        // PROBE wie ACK weiterfluten (TTL/Dedup begrenzen die Reichweite).
        tryRelay(data, len);
        return;
    }
}

static void onDataSent(const uint8_t *, esp_now_send_status_t) {
    // Broadcast -> immer SUCCESS, kein App-Handling.
}

// Promiscuous-Sniffer: nur MGMT-Frames betrachten (ESP-NOW laeuft als
// Vendor-Specific Action-Frame, also Mgmt/Subtype 0xD0). Wir speichern
// nur die letzten Messwerte; der ESP-NOW-Callback feuert direkt im
// Anschluss und ordnet den Wert dem aktuellen Frame zu.
static void IRAM_ATTR onWifiSniff(void *buf, wifi_promiscuous_pkt_type_t type) {
    if (type != WIFI_PKT_MGMT) return;
    const wifi_promiscuous_pkt_t *p = (const wifi_promiscuous_pkt_t *)buf;
    g_lastSniffRssi  = p->rx_ctrl.rssi;
    g_lastSniffNoise = (int8_t)p->rx_ctrl.noise_floor;
}

static bool wifiInit() {
    WiFi.mode(WIFI_STA);
    WiFi.disconnect(false, true);
    delay(50);

    if (esp_wifi_set_channel(g_cfg.channel, WIFI_SECOND_CHAN_NONE) != ESP_OK) return false;
    if (g_cfg.lr) {
        esp_wifi_set_protocol(WIFI_IF_STA,
            WIFI_PROTOCOL_11B | WIFI_PROTOCOL_11G | WIFI_PROTOCOL_11N | WIFI_PROTOCOL_LR);
    } else {
        esp_wifi_set_protocol(WIFI_IF_STA,
            WIFI_PROTOCOL_11B | WIFI_PROTOCOL_11G | WIFI_PROTOCOL_11N);
    }

    if (esp_now_init() != ESP_OK) return false;
    esp_now_register_send_cb(onDataSent);
    esp_now_register_recv_cb(onDataRecv);

    // Sniffer fuer RSSI-/Noise-Messung. Laeuft parallel zu ESP-NOW.
    wifi_promiscuous_filter_t flt = { .filter_mask = WIFI_PROMIS_FILTER_MASK_MGMT };
    esp_wifi_set_promiscuous_filter(&flt);
    esp_wifi_set_promiscuous_rx_cb(&onWifiSniff);
    esp_wifi_set_promiscuous(true);
    // Kanal nach Promisc nochmals setzen (manche IDF-Versionen ueberschreiben ihn).
    esp_wifi_set_channel(g_cfg.channel, WIFI_SECOND_CHAN_NONE);

    esp_now_peer_info_t peer = {};
    memcpy(peer.peer_addr, BCAST, 6);
    peer.channel = g_cfg.channel;
    peer.encrypt = false;
    peer.ifidx   = WIFI_IF_STA;
    if (esp_now_add_peer(&peer) != ESP_OK) return false;
    return true;
}

// ===================================================================
//   JSON-Output
// ===================================================================
static void appendPathJson(const uint8_t *path, uint8_t pathLen) {
    Serial.print(",\"path\":[");
    for (uint8_t i = 0; i < pathLen; i++) {
        if (i) Serial.print(",");
        Serial.print((unsigned)path[i]);
    }
    Serial.print("]");
    Serial.printf(",\"hops\":%u", (unsigned)pathLen);
}

static void appendReachedPathJson(const uint8_t *path, uint8_t pathLen) {
    Serial.print(",\"reached_path\":[");
    for (uint8_t i = 0; i < pathLen; i++) {
        if (i) Serial.print(",");
        Serial.print((unsigned)path[i]);
    }
    Serial.print("]");
    Serial.printf(",\"reached_hops\":%u", (unsigned)pathLen);
    Serial.printf(",\"last_hop_ok\":%u",
                  pathLen > 0 ? (unsigned)path[pathLen - 1] : 0);
}

static void printJsonResult(uint32_t seq, uint8_t dst, uint8_t size,
                            uint8_t attempt, bool success, const char *code,
                            int rssi_remote, int snr_remote,
                            int rssi_local,  int snr_local,
                            uint32_t latency_ms, uint32_t airtime_ms,
                            uint8_t via_relay,
                            const uint8_t *ackPath, uint8_t ackPathLen,
                            const uint8_t *reachedPath, uint8_t reachedPathLen,
                            const char *fail_reason) {
    Serial.print("{\"type\":\"result\"");
    Serial.printf(",\"seq\":%lu", (unsigned long)seq);
    Serial.printf(",\"dst\":%u", dst);
    Serial.printf(",\"size\":%u", size);
    Serial.printf(",\"attempt\":%u", attempt);
    Serial.printf(",\"success\":%s", success ? "true" : "false");
    Serial.printf(",\"code\":\"%s\"", code);
    Serial.printf(",\"rssi_remote\":%d", rssi_remote);
    Serial.printf(",\"snr_remote\":%d", snr_remote);
    Serial.printf(",\"rssi_local\":%d", rssi_local);
    Serial.printf(",\"snr_local\":%d", snr_local);
    Serial.printf(",\"latency_ms\":%lu", (unsigned long)latency_ms);
    Serial.printf(",\"airtime_ms\":%lu", (unsigned long)airtime_ms);
    Serial.printf(",\"via_relay\":%u", via_relay);
    appendPathJson(ackPath, ackPathLen);
    appendReachedPathJson(reachedPath, reachedPathLen);
    Serial.printf(",\"fail_reason\":\"%s\"", fail_reason ? fail_reason : "");
    Serial.println("}");
}

static uint32_t estimateAirtimeMs(uint8_t total_payload) {
    uint32_t bits = ((uint32_t)total_payload + 50) * 8;
    uint32_t us = g_cfg.lr ? (bits * 1000 / 250) : bits;
    return (us + 999) / 1000;
}

// ===================================================================
//   Paketbau
// ===================================================================
static uint32_t g_seq_prefix = 0;
static uint16_t g_seq = 0;
static uint32_t nextSeq() {
    g_seq++;
    return ((uint32_t)g_seq_prefix << 16) | g_seq;
}

static uint16_t buildDataPacket(uint8_t *buf, uint8_t dst, uint8_t size,
                                uint8_t attempt, uint16_t seq, uint8_t ttl) {
    if (size < DATA_HDR_SIZE + 2) size = DATA_HDR_SIZE + 2;
    if (size > 250) size = 250;
    memset(buf, 0, size);
    buf[OFF_MAGIC]    = MAGIC_DATA;
    buf[OFF_SRC]      = MY_ID;
    buf[OFF_DST]      = dst;
    buf[OFF_SEQ_HI]   = (seq >> 8) & 0xFF;
    buf[OFF_SEQ_LO]   = seq & 0xFF;
    buf[OFF_SIZE]     = size;
    buf[OFF_ATTEMPT]  = attempt;
    buf[OFF_TTL]      = ttl;
    buf[OFF_PATH_LEN] = 0;
    // path[] bleibt 0
    // Payload mit zufaelligen Bytes fuellen, damit jede Sendung einen
    // neuen Inhalt hat (kein deterministisches Saegezahn-Muster).
    int payload_len = (int)size - OFF_PAYLOAD - 2;
    if (payload_len > 0) {
        esp_fill_random(buf + OFF_PAYLOAD, (size_t)payload_len);
    }
    recomputeCrc(buf, size);
    return seq;
}

static void buildAckPacket(uint8_t *buf, uint8_t dst, uint16_t seq,
                           int rssi, int snr, uint8_t ttl,
                           const uint8_t *dataPath, uint8_t dataPathLen) {
    memset(buf, 0, ACK_FRAME_SIZE);
    buf[OFF_MAGIC]   = MAGIC_ACK;
    buf[OFF_SRC]     = MY_ID;
    buf[OFF_DST]     = dst;
    buf[OFF_SEQ_HI]  = (seq >> 8) & 0xFF;
    buf[OFF_SEQ_LO]  = seq & 0xFF;
    int rssi_off = rssi + 164;
    if (rssi_off < 0)   rssi_off = 0;
    if (rssi_off > 255) rssi_off = 255;
    buf[OFF_ACK_RSSI] = (uint8_t)rssi_off;
    buf[OFF_ACK_SNR]  = (uint8_t)(int8_t)snr;
    buf[OFF_TTL]      = ttl;
    if (dataPathLen > MAX_RELAY_HOPS) dataPathLen = MAX_RELAY_HOPS;
    buf[OFF_PATH_LEN] = dataPathLen;
    if (dataPathLen > 0 && dataPath) {
        memcpy(buf + OFF_PATH, dataPath, dataPathLen);
    }
    recomputeCrc(buf, ACK_FRAME_SIZE);
}

// ===================================================================
//   Kommando-Parser
// ===================================================================
struct Command {
    char  cmd[16];
    int   dst, size, retry, timeout_ms, run_id, samples, ttl;
    // Phase 4c: cfg-Felder. Sentinel -1 == "nicht gesetzt".
    int   cfg_channel;
    int   cfg_lr;
    int   cfg_relay;
    int   cfg_probe;
    long  cfg_prev_mask;
    int   cfg_save;     // default 1
    int   cfg_reboot;   // default 0
    int   cfg_reset;    // default 0  (NVS clear, dann Boot-Defaults)
};

static long parseLongMaybeHex(const String &v) {
    String s = v; s.trim();
    if (s.startsWith("0x") || s.startsWith("0X")) {
        return strtol(s.c_str() + 2, nullptr, 16);
    }
    return strtol(s.c_str(), nullptr, 10);
}

static bool parseCommand(const String &line, Command &c) {
    c.cmd[0] = 0; c.dst = 0; c.size = 32; c.retry = 4; c.timeout_ms = 200;
    c.run_id = 0; c.samples = 16; c.ttl = DEFAULT_TTL;
    c.cfg_channel = -1; c.cfg_lr = -1; c.cfg_relay = -1; c.cfg_probe = -1;
    c.cfg_prev_mask = -1; c.cfg_save = 1; c.cfg_reboot = 0; c.cfg_reset = 0;
    int idx = 0;
    while (idx < (int)line.length()) {
        int sp = line.indexOf(' ', idx);
        if (sp < 0) sp = line.length();
        String tok = line.substring(idx, sp);
        int eq = tok.indexOf('=');
        if (eq > 0) {
            String k = tok.substring(0, eq);
            String v = tok.substring(eq + 1);
            if      (k == "cmd")        { strncpy(c.cmd, v.c_str(), sizeof(c.cmd) - 1); c.cmd[sizeof(c.cmd)-1] = 0; }
            else if (k == "dst")        c.dst = v.toInt();
            else if (k == "size")       c.size = v.toInt();
            else if (k == "retry")      c.retry = v.toInt();
            else if (k == "timeout_ms") c.timeout_ms = v.toInt();
            else if (k == "run_id")     c.run_id = v.toInt();
            else if (k == "samples")    c.samples = v.toInt();
            else if (k == "ttl")        c.ttl = v.toInt();
            else if (k == "channel")    c.cfg_channel = v.toInt();
            else if (k == "lr")         c.cfg_lr = v.toInt();
            else if (k == "relay")      c.cfg_relay = v.toInt();
            else if (k == "probe")      c.cfg_probe = v.toInt();
            else if (k == "prev_mask")  c.cfg_prev_mask = parseLongMaybeHex(v);
            else if (k == "save")       c.cfg_save = v.toInt();
            else if (k == "reboot")     c.cfg_reboot = v.toInt();
            else if (k == "reset")      c.cfg_reset = v.toInt();
        }
        idx = sp + 1;
    }
    return c.cmd[0] != 0;
}

// ===================================================================
//   Gemeinsame cfg/getcfg/version-Handler (rolle-unabhängig)
// ===================================================================
static const char *roleStr() {
#if ROLE_SENDER
    return "sender";
#elif ROLE_RECEIVER
    return "receiver";
#elif ROLE_RELAY
    return "relay";
#else
    return "unknown";
#endif
}

static void emitCfgJson(const char *cmd, bool reboot_required) {
    Serial.printf("{\"type\":\"cfg\",\"cmd\":\"%s\",\"id\":%u,\"role\":\"%s\","
                  "\"channel\":%u,\"lr\":%u,\"relay\":%u,\"probe\":%u,"
                  "\"prev_mask\":%lu,\"build_relay\":%d,\"build_probe\":%d,"
                  "\"reboot_required\":%s}\n",
                  cmd, MY_ID, roleStr(),
                  g_cfg.channel, g_cfg.lr, g_cfg.relay_enabled, g_cfg.probe_enabled,
                  (unsigned long)g_cfg.prev_mask,
                  RELAY_ENABLED, PROBE_ENABLED,
                  reboot_required ? "true" : "false");
}

static void handleCfgCmd(const Command &c) {
    if (c.cfg_reset) {
        clearCfgNvs();
        // Boot-Defaults aus den Build-Flags wiederherstellen.
        g_cfg.channel       = (uint8_t)ESPNOW_CHANNEL;
        g_cfg.lr            = (uint8_t)ESPNOW_LR;
        g_cfg.relay_enabled = (uint8_t)RELAY_ENABLED;
        g_cfg.probe_enabled = (uint8_t)PROBE_ENABLED;
        g_cfg.prev_mask     = (uint32_t)ALLOWED_PREV_MASK;
        emitCfgJson("cfg-reset", true);
        if (c.cfg_reboot) { delay(120); ESP.restart(); }
        return;
    }
    bool needReboot = false;
    if (c.cfg_channel >= 1 && c.cfg_channel <= 14) {
        if ((uint8_t)c.cfg_channel != g_cfg.channel) needReboot = true;
        g_cfg.channel = (uint8_t)c.cfg_channel;
    }
    if (c.cfg_lr == 0 || c.cfg_lr == 1) {
        if ((uint8_t)c.cfg_lr != g_cfg.lr) needReboot = true;
        g_cfg.lr = (uint8_t)c.cfg_lr;
    }
    if (c.cfg_relay == 0 || c.cfg_relay == 1)  g_cfg.relay_enabled = (uint8_t)c.cfg_relay;
    if (c.cfg_probe == 0 || c.cfg_probe == 1)  g_cfg.probe_enabled = (uint8_t)c.cfg_probe;
    if (c.cfg_prev_mask >= 0)                  g_cfg.prev_mask     = (uint32_t)c.cfg_prev_mask;
    if (c.cfg_save) saveCfg();
    emitCfgJson("cfg", needReboot);
    if (c.cfg_reboot) { delay(120); ESP.restart(); }
}

static void handleGetCfgCmd(const Command &) {
    emitCfgJson("getcfg", false);
}

// Verarbeitet einen einzelnen JSON-/Whitespace-Befehl. Für alle Rollen
// werden mindestens cfg/getcfg/version unterstützt; rollenspezifische
// Befehle landen über dispatchRoleCommand() im jeweiligen Block.
static bool dispatchRoleCommand(const Command &c);  // forward decl

static void handleSerialLine(const String &line) {
    Command c;
    if (!parseCommand(line, c)) return;
    if      (strcmp(c.cmd, "cfg")     == 0) { handleCfgCmd(c);    return; }
    else if (strcmp(c.cmd, "getcfg")  == 0) { handleGetCfgCmd(c); return; }
    dispatchRoleCommand(c);
}

// Min. Serial-Drainer mit gleicher Linien-Logik wie der Sender-Loop.
static void drainSerial() {
    static String line;
    while (Serial.available()) {
        char ch = (char)Serial.read();
        if (ch == '\n' || ch == '\r') {
            if (line.length() > 0) handleSerialLine(line);
            line = "";
        } else if (line.length() < 200) {
            line += ch;
        }
    }
}

// ===================================================================
//   ROLE_SENDER
// ===================================================================
#if ROLE_SENDER
void setup() {
    Serial.begin(115200);
    delay(200);
    loadCfg();
    if (!wifiInit()) {
        Serial.println("{\"type\":\"fatal\",\"msg\":\"espnow_init_failed\"}");
        while (true) delay(1000);
    }
    uint8_t mac[6]; esp_wifi_get_mac(WIFI_IF_STA, mac);
    Serial.printf("{\"type\":\"ready\",\"role\":\"sender\",\"id\":%u,\"fw\":\"%s\","
                  "\"channel\":%u,\"lr\":%u,\"relay\":%u,\"probe\":%u,"
                  "\"prev_mask\":%lu,\"default_ttl\":%d,"
                  "\"mac\":\"%02X:%02X:%02X:%02X:%02X:%02X\"}\n",
                  MY_ID, FW_VERSION,
                  g_cfg.channel, g_cfg.lr, g_cfg.relay_enabled, g_cfg.probe_enabled,
                  (unsigned long)g_cfg.prev_mask, DEFAULT_TTL,
                  mac[0],mac[1],mac[2],mac[3],mac[4],mac[5]);
}

static void handleSetRun(const Command &c) {
    g_seq_prefix = (uint32_t)c.run_id & 0xFFFF;
    g_seq = 0;
    Serial.printf("{\"type\":\"ack\",\"cmd\":\"setrun\",\"run_id\":%d}\n", c.run_id);
}

static void handleNoiseFloor(const Command &c) {
    int samples = c.samples > 0 ? c.samples : 16;
    Serial.printf("{\"type\":\"noisefloor\",\"samples\":%d,\"avg\":0,\"min\":0,\"max\":0}\n",
                  samples);
}

static void handleVersion(const Command &) {
    Serial.printf("{\"type\":\"version\",\"fw\":\"%s\",\"id\":%u,\"role\":\"sender\","
                  "\"radio\":\"esp-now\",\"channel\":%u,\"lr\":%u,\"relay\":%u,"
                  "\"probe\":%u,\"prev_mask\":%lu,\"default_ttl\":%d}\n",
                  FW_VERSION, MY_ID,
                  g_cfg.channel, g_cfg.lr, g_cfg.relay_enabled,
                  g_cfg.probe_enabled, (unsigned long)g_cfg.prev_mask, DEFAULT_TTL);
}

static void handleSend(const Command &c) {
    uint8_t buf[256];
    uint8_t size = (uint8_t)constrain(c.size, DATA_HDR_SIZE + 2, 250);
    uint32_t seq = nextSeq();
    bool done = false;
    uint8_t ttl = (uint8_t)constrain(c.ttl, 0, 15);

    for (int attempt = 1; attempt <= c.retry && !done; attempt++) {
        buildDataPacket(buf, (uint8_t)c.dst, size, (uint8_t)attempt,
                        (uint16_t)(seq & 0xFFFF), ttl);
        // Progress-Tracking aktivieren: Sender hört ab jetzt PROBEs für diese seq.
        g_ackReceived     = false;
        g_ackPathLen      = 0;
        g_progressPathLen = 0;
        memset(g_progressPath, 0, sizeof(g_progressPath));
        g_pendingSeq      = (uint16_t)(seq & 0xFFFF);
        g_pendingActive   = true;
        uint32_t at_est = estimateAirtimeMs(size);
        uint32_t t0 = millis();
        esp_err_t er = esp_now_send(BCAST, buf, size);
        if (er != ESP_OK) {
            uint8_t empty[MAX_RELAY_HOPS] = {0};
            g_pendingActive = false;
            printJsonResult(seq, c.dst, size, attempt, false,
                            "failed_tx_error",
                            0, 0, 0, 0, millis() - t0, at_est, MY_ID,
                            empty, 0, empty, 0, "tx_error");
            continue;
        }
        uint32_t t_end = t0 + (uint32_t)c.timeout_ms;
        while (millis() < t_end) {
            drainForwardQueue(); // falls Sender doch relayt (RELAY_ENABLED=1)
            if (g_ackReceived && g_ackSeq == (uint16_t)(seq & 0xFFFF)) break;
            delay(1);
        }
        uint32_t lat = millis() - t0;
        // Snapshot bevor wir das Tracking deaktivieren (RX-Callback könnte
        // sonst nach unserem Lesen noch schreiben).
        uint8_t reachedPath[MAX_RELAY_HOPS];
        memcpy(reachedPath, g_progressPath, MAX_RELAY_HOPS);
        uint8_t reachedLen = g_progressPathLen;
        g_pendingActive = false;

        if (g_ackReceived && g_ackSeq == (uint16_t)(seq & 0xFFFF)) {
            const char *code = (attempt == 1) ? "success_first_try"
                                              : "success_after_retry";
            uint8_t firstHop = (g_ackPathLen > 0) ? g_ackPath[0] : MY_ID;
            // Bei Erfolg ist die offizielle Pfad-Info der ACK-Path; reached
            // wird redundant ebenfalls übermittelt (ist in der Regel gleich
            // oder Teilmenge. Falls weniger, deutet das auf einen Pfad mit
            // Lücken im PROBE-Empfang hin).
            printJsonResult(seq, c.dst, size, attempt, true, code,
                            g_ackRssiRem, g_ackSnrRem,
                            g_ackRssiLoc, g_ackSnrLoc,
                            lat, at_est, firstHop,
                            g_ackPath, g_ackPathLen,
                            reachedPath, reachedLen,
                            "none");
            done = true;
        } else {
            uint8_t empty[MAX_RELAY_HOPS] = {0};
            const char *fail_reason;
            const char *code;
            if (reachedLen == 0) {
                fail_reason = "no_relay_response";   // kein einziges PROBE
                code        = "failed_no_progress";
            } else {
                fail_reason = "ack_timeout_after_hop";
                code        = "failed_after_hop";
            }
            printJsonResult(seq, c.dst, size, attempt, false,
                            code,
                            0, 0, 0, 0, lat, at_est, MY_ID,
                            empty, 0,
                            reachedPath, reachedLen,
                            fail_reason);
        }
    }
    Serial.printf("{\"type\":\"done\",\"seq\":%lu,\"success\":%s}\n",
                  (unsigned long)seq, done ? "true" : "false");
}

static bool dispatchRoleCommand(const Command &c) {
    if      (strcmp(c.cmd, "send")       == 0) { handleSend(c);       return true; }
    else if (strcmp(c.cmd, "setrun")     == 0) { handleSetRun(c);     return true; }
    else if (strcmp(c.cmd, "noisefloor") == 0) { handleNoiseFloor(c); return true; }
    else if (strcmp(c.cmd, "version")    == 0) { handleVersion(c);    return true; }
    return false;
}

void loop() {
    drainForwardQueue();
    drainSerial();
}
#endif // ROLE_SENDER

// ===================================================================
//   ROLE_RECEIVER (Endpunkt + auto-Relay für fremde Frames)
// ===================================================================
#if ROLE_RECEIVER
void setup() {
    Serial.begin(115200);
    delay(200);
    loadCfg();
    if (!wifiInit()) {
        Serial.println("{\"type\":\"fatal\",\"msg\":\"espnow_init_failed\"}");
        while (true) delay(1000);
    }
    uint8_t mac[6]; esp_wifi_get_mac(WIFI_IF_STA, mac);
    Serial.printf("{\"type\":\"ready\",\"role\":\"receiver\",\"id\":%u,\"fw\":\"%s\","
                  "\"channel\":%u,\"lr\":%u,\"relay\":%u,\"probe\":%u,"
                  "\"prev_mask\":%lu,"
                  "\"mac\":\"%02X:%02X:%02X:%02X:%02X:%02X\"}\n",
                  MY_ID, FW_VERSION,
                  g_cfg.channel, g_cfg.lr, g_cfg.relay_enabled, g_cfg.probe_enabled,
                  (unsigned long)g_cfg.prev_mask,
                  mac[0],mac[1],mac[2],mac[3],mac[4],mac[5]);
}

static bool dispatchRoleCommand(const Command &) { return false; }

void loop() {
    drainForwardQueue();
    drainSerial();
    if (!g_rx.present) { delay(1); return; }

    uint8_t  src     = g_rx.src;
    uint16_t seq     = g_rx.seq;
    uint8_t  size    = g_rx.size;
    uint8_t  pathLen = g_rx.pathLen;
    int      rxRssi  = g_rx.rssi;
    int      rxSnr   = g_rx.snr;
    uint8_t  path[MAX_RELAY_HOPS];
    memcpy(path, g_rx.path, MAX_RELAY_HOPS);
    g_rx.present = false;

    // ACK bauen mit den am Empfaenger via Promiscuous-Sniffer gemessenen
    // RSSI-/SNR-Werten der DATA. Der Sender erhaelt sie in rssi_remote.
    // Forward-Route der DATA wird im ACK eingefroren mitgesendet,
    // damit der Sender die tatsaechlich gelaufene Hop-Kette sieht.
    uint8_t ack[ACK_FRAME_SIZE];
    buildAckPacket(ack, src, seq, rxRssi, rxSnr, DEFAULT_TTL,
                   path, pathLen);
    esp_now_send(BCAST, ack, sizeof(ack));

    Serial.print("{\"type\":\"rx\",\"src\":");
    Serial.print((unsigned)src);
    Serial.printf(",\"seq\":%u,\"size\":%u,\"rssi\":%d,\"snr\":%d,\"dup\":false",
                  seq, size, rxRssi, rxSnr);
    appendPathJson(path, pathLen);
    Serial.println("}");
}
#endif // ROLE_RECEIVER

// ===================================================================
//   ROLE_RELAY (reiner Forwarder, kein Endpunkt)
// ===================================================================
#if ROLE_RELAY
void setup() {
    Serial.begin(115200);
    delay(200);
    loadCfg();
    if (!wifiInit()) {
        Serial.println("{\"type\":\"fatal\",\"msg\":\"espnow_init_failed\"}");
        while (true) delay(1000);
    }
    uint8_t mac[6]; esp_wifi_get_mac(WIFI_IF_STA, mac);
    Serial.printf("{\"type\":\"ready\",\"role\":\"relay\",\"id\":%u,\"fw\":\"%s\","
                  "\"channel\":%u,\"lr\":%u,\"relay\":%u,\"probe\":%u,"
                  "\"prev_mask\":%lu,"
                  "\"mac\":\"%02X:%02X:%02X:%02X:%02X:%02X\"}\n",
                  MY_ID, FW_VERSION,
                  g_cfg.channel, g_cfg.lr, g_cfg.relay_enabled, g_cfg.probe_enabled,
                  (unsigned long)g_cfg.prev_mask,
                  mac[0],mac[1],mac[2],mac[3],mac[4],mac[5]);
}

static bool dispatchRoleCommand(const Command &) { return false; }

void loop() {
    drainForwardQueue();
    drainSerial();
    delay(1);
}
#endif // ROLE_RELAY

#if !defined(ROLE_SENDER) && !defined(ROLE_RECEIVER) && !defined(ROLE_RELAY)
#error "Bitte ROLE_SENDER, ROLE_RECEIVER oder ROLE_RELAY als Build-Flag setzen."
#endif
