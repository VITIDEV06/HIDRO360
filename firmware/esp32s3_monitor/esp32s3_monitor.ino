/*
 * HIDRO360 (TEAM VIFY AI) - TERMINAL OPERATIVA DE CARRO CISTERNA
 * Placa: Hosyond/lcdwiki "2.8inch ESP32-S3 Display" (ES3C28P, ILI9341V 240x320, tactil FT6336G, N16R8).
 * Pines: docs/HARDWARE.md. Este sketch solo usa TFT (TFT_eSPI, User_Setup.h del fabricante) y tactil 16/15/17/18.
 * La placa NO tiene GPS verificado: la terminal no reporta posicion; el destino viene de la mision.
 *
 * Protocolo (HTTP en LAN o HTTPS publico contra FastAPI, nunca SQLite; la identidad sale del token del vehiculo):
 *   GET  /dispositivo/mision   -> mision actual (se cachea en NVS para mostrarla sin conexion) + "protocolo"
 *   POST /dispositivo/eventos  -> llegada | entrega | incidencia, idempotente por uid
 *   POST /dispositivo/latido   -> firmware, rssi, pendientes, uptime
 * Cabeceras: X-Device-Token (vehiculo) y X-Firmware-Version.
 *
 * HTTPS: si API_BASE_URL empieza por https:// se valida el certificado del servidor con el bundle de CAs raiz del core
 * (o con la CA fijada en API_CA_ROOT_PEM). Exige hora valida (NTP). NUNCA se desactiva la validacion salvo que se defina
 * explicitamente API_TLS_INSECURE (solo laboratorio; aviso permanente en pantalla y por serie).
 *
 * Cola de eventos: se guarda en NVS (Preferences) en cada cambio; sobrevive a reinicios y cortes de luz.
 * Un evento sale de la cola solo cuando el servidor responde aplicado | duplicado | rechazado. Una accion del conductor
 * NO se muestra como "confirmada" mientras siga en la cola: se muestra "POR ENVIAR".
 *
 * ESTADO: compila (scripts/compilar_firmware.ps1); NO probado en la placa. Ver docs/HARDWARE.md.
 * Librerias: TFT_eSPI 2.5.43, FT6336-arduino (fabricante), ArduinoJson 7.x, core esp32 3.x (WiFi, HTTPClient, Preferences).
 */
#include <TFT_eSPI.h>
#include <FT6336.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <ArduinoJson.h>
#include <time.h>
#include "config_dispositivo.h"

#define FIRMWARE_VERSION "2.1.0"
#define PROTOCOLO_ESPERADO 1     // version del contrato dispositivo-API que este firmware entiende

// Valores por defecto para configuraciones antiguas (config_dispositivo.h puede redefinirlos)
#ifndef HTTPS_TIMEOUT_MS
#define HTTPS_TIMEOUT_MS 15000   // conexion + handshake TLS + lectura (el handshake es lento)
#endif
#ifndef NTP_SERVIDOR_1
#define NTP_SERVIDOR_1 "pool.ntp.org"
#endif
#ifndef NTP_SERVIDOR_2
#define NTP_SERVIDOR_2 "time.google.com"
#endif
#ifndef NTP_ESPERA_MS
#define NTP_ESPERA_MS 15000      // espera acotada de la 1.a sincronizacion antes de mostrar "HORA NO SINCRONIZADA"
#endif

// Tactil (documentacion del fabricante, comprobado contra Demo/Example_08)
#define TP_SDA 16
#define TP_SCL 15
#define TP_INT 17
#define TP_RST 18

#define MAX_PARADAS 10
#define MAX_COLA 24
#define LOTE_EVENTOS 10
#define BACKOFF_MIN_MS 2000UL
#define BACKOFF_MAX_MS 60000UL
#define MAX_JSON_BYTES 24000
#define CONFIRM_TIMEOUT_MS 15000UL
#define PANTALLA_AUX_TIMEOUT_MS 30000UL
#define HORA_MINIMA_VALIDA 1735689600L   // 2025-01-01 UTC: por debajo, el reloj no se ha sincronizado

TFT_eSPI lcd = TFT_eSPI();
FT6336 ts = FT6336(TP_SDA, TP_SCL, TP_INT, TP_RST, 240, 320);
Preferences prefs;

// Colores (RGB565). Texto secundario en gris oscuro para que se lea al sol; nunca gris claro sobre blanco.
#define COL_FONDO TFT_WHITE
#define COL_TXT TFT_BLACK
#define COL_GRIS 0x4A49
#define COL_GRIS_CLARO 0xC618

// ---------------------------------------------------------------- modelo
enum EstadoParada : uint8_t { P_PENDIENTE = 0, P_LLEGADA, P_ENTREGADA, P_INCIDENCIA };
enum TipoEvento : uint8_t { E_LLEGADA = 0, E_ENTREGA, E_INCIDENCIA };
enum NetEstado : uint8_t { NET_INICIANDO = 0, NET_OK, NET_SIN_WIFI, NET_TOKEN, NET_LIMITE, NET_SERVIDOR, NET_SIN_RESPUESTA, NET_TLS, NET_HORA };

// Codigos propios de llamar() (no chocan con los de HTTPClient, que son -1..-11, ni con los HTTP)
#define LL_SIN_WIFI -1000
#define LL_BEGIN -1001
#define LL_GRANDE -1002
#define LL_TLS -1003
#define LL_HORA -1004

// estado MOSTRADO al conductor (ver estadoVisible)
enum Visible : uint8_t { V_PEND = 0, V_LLEG_ENV, V_LLEG_OK, V_ENT_ENV, V_ENT_OK, V_INC_ENV, V_INC_OK };

struct Parada {
  uint8_t orden = 0;
  char sector[28] = "";
  char instruccion[72] = "";
  int32_t litros = 0;
  int32_t litrosEntregados = 0;
  EstadoParada estado = P_PENDIENTE;   // estado local; "confirmado" solo si no hay evento en cola (ver estadoVisible)
};

struct Mision {
  bool valida = false;      // hay mision en cache/servidor
  int32_t id = 0;
  char estado[14] = "";     // asignada | en_curso | completada | cancelada
  uint8_t n = 0;
  bool truncada = false;
  Parada p[MAX_PARADAS];
} mision;

struct Evento {
  char uid[28];
  uint8_t tipo;
  uint8_t orden;
  uint8_t intentos;         // solo cuenta rechazos HTTP 4xx de formato
  int32_t misionId;
  int32_t litros;
};
Evento cola[MAX_COLA];
uint8_t nCola = 0;

// ---------------------------------------------------------------- estado de ejecucion
NetEstado net = NET_INICIANDO;
int netHttp = 0;
bool sincronizadoDesdeArranque = false;  // true tras la 1a respuesta valida de GET /mision en esta sesion
char ultimaSyncHora[12] = "";            // HH:MM:SS (UTC) segun servidor_hora
unsigned long ultimaSyncMs = 0;
char avisoTxt[40] = "";                  // aviso transitorio (rechazos, doble toque, etc.)
unsigned long avisoHasta = 0;
uint32_t bootCnt = 0, seqEvento = 0;
unsigned long proxPoll = 0, proxLatido = 0, proxEnvio = 0, proxWifi = 0;
uint8_t backoffN = 0, fallosPoll = 0;
bool dirty = true, dirtyTodo = true;
uint8_t sel = 0;                         // indice de parada mostrada

// protocolo / compatibilidad
int protocoloServidor = PROTOCOLO_ESPERADO;
bool firmwareDesactualizado = false;     // el servidor habla un protocolo mas nuevo: no se envian eventos

// hora (NTP) y TLS
bool horaOk = false;
bool ntpIniciado = false;
unsigned long ntpDesde = 0, ntpReintento = 0;
int tlsCodigo = 0;                       // ultimo error mbedTLS (0 = ninguno)
char tlsMsg[48] = "";
uint32_t heapLibreAntes = 0, heapMinSesion = 0;

enum Modo : uint8_t { M_NORMAL = 0, M_CONFIRMAR, M_LISTA, M_ESTADO };
Modo modo = M_NORMAL;
TipoEvento confirmTipo = E_ENTREGA;
unsigned long confirmDesde = 0;

// (Va tras los tipos: el preprocesador de Arduino inserta los prototipos antes de la primera funcion.)
// ---- esquema de la URL, comprobado al compilar
constexpr bool empiezaCon(const char* s, const char* p) { return *p == 0 ? true : (*s == *p && empiezaCon(s + 1, p + 1)); }
constexpr bool API_HTTPS = empiezaCon(API_BASE_URL, "https://");
static_assert(API_HTTPS || empiezaCon(API_BASE_URL, "http://"), "API_BASE_URL debe empezar por http:// o https://");
#ifdef API_TLS_INSECURE
#warning "API_TLS_INSECURE activo: NO se valida el certificado del servidor. Solo para laboratorio."
#endif

// ---------------------------------------------------------------- utilidades
void aviso(const char* t, unsigned long ms = 6000) {
  strlcpy(avisoTxt, t, sizeof(avisoTxt));
  avisoHasta = millis() + ms;
  dirty = true;
}

const char* tipoStr(uint8_t t) { return t == E_LLEGADA ? "llegada" : (t == E_ENTREGA ? "entrega" : "incidencia"); }

// Las fuentes integradas de TFT_eSPI son ASCII: el texto del servidor (UTF-8) se transcribe para no mostrar basura.
// a-acento -> a, n-tilde -> n, signos de apertura de pregunta/exclamacion -> ?/!, comillas y rayas tipograficas -> ASCII.
void copiarAscii(char* dst, size_t n, const char* src) {
  static const char MAYUS[] = "AAAAAAACEEEEIIIIDNOOOOOxOUUUUYTs";     // U+00C0..U+00DF
  static const char MINUS[] = "aaaaaaaceeeeiiiidnooooo/ouuuuyty";     // U+00E0..U+00FF
  size_t o = 0;
  if (n == 0) return;
  for (size_t i = 0; src[i] && o + 1 < n;) {
    uint8_t c = (uint8_t)src[i];
    if (c < 0x80) { dst[o++] = (c >= 32) ? (char)c : ' '; i++; continue; }
    uint8_t c1 = (uint8_t)src[i + 1];
    if (c == 0xC3 && c1 >= 0x80 && c1 <= 0xBF) { dst[o++] = c1 < 0xA0 ? MAYUS[c1 - 0x80] : MINUS[c1 - 0xA0]; i += 2; continue; }
    if (c == 0xC2 && c1 == 0xA1) { dst[o++] = '!'; i += 2; continue; }
    if (c == 0xC2 && c1 == 0xBF) { dst[o++] = '?'; i += 2; continue; }
    if (c == 0xC2 && c1 == 0xB0) { dst[o++] = 'o'; i += 2; continue; }       // grado
    if (c == 0xE2 && c1 == 0x80) {
      uint8_t c2 = (uint8_t)src[i + 2];
      char r = (c2 == 0x93 || c2 == 0x94) ? '-' : (c2 == 0x98 || c2 == 0x99) ? '\'' : (c2 == 0x9C || c2 == 0x9D) ? '"' : '?';
      if (c2 == 0xA6) { for (int k = 0; k < 3 && o + 1 < n; k++) dst[o++] = '.'; }
      else dst[o++] = r;
      i += 3; continue;
    }
    // cualquier otra secuencia multibyte: un solo '?' por caracter
    size_t len = (c >= 0xF0) ? 4 : (c >= 0xE0) ? 3 : (c >= 0xC0) ? 2 : 1;
    dst[o++] = '?';
    for (size_t k = 0; k < len && src[i]; k++) i++;
  }
  dst[o] = 0;
}

int contarEstado(EstadoParada e) {
  int c = 0;
  for (int i = 0; i < mision.n; i++) if (mision.p[i].estado == e) c++;
  return c;
}
bool paradaAbierta(const Parada& p) { return p.estado == P_PENDIENTE || p.estado == P_LLEGADA; }
int abiertas() { int c = 0; for (int i = 0; i < mision.n; i++) if (paradaAbierta(mision.p[i])) c++; return c; }
bool hayEventoEnCola(int32_t misionId, uint8_t orden) {
  for (int i = 0; i < nCola; i++) if (cola[i].misionId == misionId && cola[i].orden == orden) return true;
  return false;
}
void seleccionarPrimeraAbierta() {
  for (int i = 0; i < mision.n; i++) if (paradaAbierta(mision.p[i])) { sel = i; return; }
  sel = mision.n ? mision.n - 1 : 0;
}

// Estado que se MUESTRA al conductor: solo es "confirmado" si el servidor ya reconocio todos los eventos de la parada.
Visible estadoVisible(const Parada& p) {
  bool env = hayEventoEnCola(mision.id, p.orden);
  switch (p.estado) {
    case P_LLEGADA: return env ? V_LLEG_ENV : V_LLEG_OK;
    case P_ENTREGADA: return env ? V_ENT_ENV : V_ENT_OK;
    case P_INCIDENCIA: return env ? V_INC_ENV : V_INC_OK;
    default: return V_PEND;
  }
}
const char* etiquetaVisible(Visible v) {
  switch (v) {
    case V_LLEG_ENV: return "LLEGADA POR ENVIAR";
    case V_LLEG_OK: return "LLEGADA CONFIRMADA";
    case V_ENT_ENV: return "ENTREGA POR ENVIAR";
    case V_ENT_OK: return "ENTREGA CONFIRMADA";
    case V_INC_ENV: return "INCIDENCIA POR ENVIAR";
    case V_INC_OK: return "INCIDENCIA CONFIRMADA";
    default: return "PENDIENTE";
  }
}
const char* etiquetaCorta(Visible v) {
  switch (v) {
    case V_LLEG_ENV: case V_ENT_ENV: case V_INC_ENV: return "POR ENVIAR";
    case V_LLEG_OK: return "LLEGADA";
    case V_ENT_OK: return "ENTREGADA";
    case V_INC_OK: return "INCIDENCIA";
    default: return "PENDIENTE";
  }
}
uint16_t colorVisible(Visible v) {
  switch (v) {
    case V_ENT_OK: return TFT_DARKGREEN;
    case V_INC_OK: return TFT_RED;
    case V_LLEG_OK: return TFT_BLUE;
    case V_PEND: return COL_GRIS;
    default: return 0xC260;   // naranja oscuro: pendiente de que el servidor confirme
  }
}
int hechasConfirmadas() {
  int c = 0;
  for (int i = 0; i < mision.n; i++) if (estadoVisible(mision.p[i]) == V_ENT_OK) c++;
  return c;
}

// ---------------------------------------------------------------- persistencia (NVS)
// Se eligio NVS/Preferences (no LittleFS): los datos son pequenos (cola < 1 KB, mision < 3 KB), NVS es
// transaccional por clave (una escritura interrumpida no deja un blob a medias) y no hay que montar ni formatear
// un sistema de archivos. Limite: ~4000 bytes por cadena, por eso la mision se limita a MAX_PARADAS.
void guardarCola() {
  prefs.putUChar("ncola", nCola);
  prefs.putBytes("cola", cola, sizeof(cola));
}
void cargarCola() {
  nCola = 0;
  size_t tam = prefs.getBytesLength("cola");
  if (tam != sizeof(cola)) { if (tam) Serial.println("cola NVS con formato distinto: descartada"); return; }
  prefs.getBytes("cola", cola, sizeof(cola));
  nCola = min<uint8_t>(prefs.getUChar("ncola", 0), MAX_COLA);
}

void guardarMision() {
  if (!mision.valida) { prefs.remove("mision"); return; }
  JsonDocument d;
  d["id"] = mision.id;
  d["e"] = mision.estado;
  JsonArray ps = d["p"].to<JsonArray>();
  for (int i = 0; i < mision.n; i++) {
    JsonObject o = ps.add<JsonObject>();
    o["o"] = mision.p[i].orden;
    o["s"] = mision.p[i].sector;
    o["i"] = mision.p[i].instruccion;
    o["l"] = mision.p[i].litros;
    o["le"] = mision.p[i].litrosEntregados;
    o["st"] = (int)mision.p[i].estado;
  }
  String s;
  serializeJson(d, s);
  if (s.length() < 3900) prefs.putString("mision", s);
  else Serial.println("mision demasiado grande para NVS: no se guarda");
}
void cargarMision() {
  String s = prefs.getString("mision", "");
  if (s.length() == 0) return;
  JsonDocument d;
  if (deserializeJson(d, s)) return;
  mision.valida = true;
  mision.id = d["id"] | 0;
  strlcpy(mision.estado, d["e"] | "", sizeof(mision.estado));
  mision.n = 0;
  for (JsonObject o : d["p"].as<JsonArray>()) {
    if (mision.n >= MAX_PARADAS) break;
    Parada& p = mision.p[mision.n++];
    p.orden = o["o"] | 0;
    strlcpy(p.sector, o["s"] | "", sizeof(p.sector));
    strlcpy(p.instruccion, o["i"] | "", sizeof(p.instruccion));
    p.litros = o["l"] | 0;
    p.litrosEntregados = o["le"] | 0;
    p.estado = (EstadoParada)constrain((int)(o["st"] | 0), 0, 3);
  }
}

// ---------------------------------------------------------------- cola de eventos
void nuevoUid(char* out, size_t n) {
  // arranque persistente + secuencia + aleatorio de hardware => unico aun tras reinicios y perdida de NVS
  snprintf(out, n, "t%lx-%lx-%08lx", (unsigned long)bootCnt, (unsigned long)(++seqEvento), (unsigned long)esp_random());
}

bool encolar(TipoEvento t, const Parada& p) {
  if (nCola >= MAX_COLA) { aviso("Cola llena: espere conexion"); return false; }
  Evento& e = cola[nCola++];
  memset(&e, 0, sizeof(e));
  nuevoUid(e.uid, sizeof(e.uid));
  e.tipo = t;
  e.misionId = mision.id;
  e.orden = p.orden;
  e.litros = (t == E_ENTREGA) ? p.litros : 0;
  guardarCola();           // primero la cola (lo que se enviara), luego el estado local
  proxEnvio = 0;           // intentar enviar ya
  backoffN = 0;
  return true;
}

void quitarEvento(int i) {
  for (int k = i; k < nCola - 1; k++) cola[k] = cola[k + 1];
  nCola--;
}

// ---------------------------------------------------------------- hora (NTP)
// HTTP local no necesita hora. HTTPS si: sin reloj valido el certificado "aun no es valido" y la conexion falla.
void gestionarHora() {
  horaOk = time(nullptr) > HORA_MINIMA_VALIDA;
  if (!API_HTTPS || WiFi.status() != WL_CONNECTED) return;
  unsigned long ahora = millis();
  if (!ntpIniciado) {
    configTime(0, 0, NTP_SERVIDOR_1, NTP_SERVIDOR_2);   // UTC; el servicio SNTP del core reintenta solo
    ntpIniciado = true; ntpDesde = ahora; ntpReintento = ahora;
    dirty = true;
  }
  if (horaOk) {
    if (net == NET_HORA) { net = NET_INICIANDO; proxPoll = 0; dirty = dirtyTodo = true; }
    return;
  }
  if (net != NET_HORA) { net = NET_HORA; dirty = dirtyTodo = true; }
  if (ahora - ntpReintento > 30000UL) { ntpReintento = ahora; configTime(0, 0, NTP_SERVIDOR_1, NTP_SERVIDOR_2); }
}
bool ntpFallo() { return !horaOk && (millis() - ntpDesde) > NTP_ESPERA_MS; }

// ---------------------------------------------------------------- HTTP / HTTPS
void registrarHeap(const char* etiqueta) {
  uint32_t libre = ESP.getFreeHeap();
  heapMinSesion = ESP.getMinFreeHeap();
  Serial.printf("[heap] %s libre=%lu minHistorico=%lu mayorBloque=%lu\n", etiqueta, (unsigned long)libre,
                (unsigned long)heapMinSesion, (unsigned long)ESP.getMaxAllocHeap());
}

// Una peticion con el cliente dado. Devuelve el codigo HTTP (>0) o <= 0 (codigos de HTTPClient / LL_*).
int pedir(WiFiClient& cli, unsigned long timeoutMs, bool post, const char* ruta, const String& cuerpo, String& resp) {
  HTTPClient http;
  http.setConnectTimeout(timeoutMs);
  http.setTimeout(timeoutMs);
  http.setReuse(false);                     // sin conexiones persistentes: libera buffers (y el TLS) en cada peticion
  String url = String(API_BASE_URL) + ruta;
  if (!http.begin(cli, url)) return LL_BEGIN;
  http.addHeader("X-Device-Token", DEVICE_TOKEN);
  http.addHeader("X-Firmware-Version", FIRMWARE_VERSION);
  http.addHeader("Accept", "application/json");
  int code;
  if (post) {
    http.addHeader("Content-Type", "application/json");
    code = http.POST((uint8_t*)cuerpo.c_str(), cuerpo.length());
  } else {
    code = http.GET();
  }
  if (code > 0) {
    int tam = http.getSize();
    if (tam > MAX_JSON_BYTES) { http.end(); return LL_GRANDE; }
    resp = http.getString();
    if (resp.length() > MAX_JSON_BYTES) resp = "";
  }
  http.end();     // libera conexion y buffers
  return code;
}

int llamar(bool post, const char* ruta, const String& cuerpo, String& resp) {
  resp = "";
  if (WiFi.status() != WL_CONNECTED) return LL_SIN_WIFI;
  if (API_HTTPS) {
    if (!horaOk) return LL_HORA;
    WiFiClientSecure sec;
#ifdef API_TLS_INSECURE
    sec.setInsecure();                      // SIN validacion de certificado: solo laboratorio
#elif defined(API_CA_ROOT_PEM)
    sec.setCACert(API_CA_ROOT_PEM);         // CA raiz fijada en config_dispositivo.h
#else
    sec.useBuiltinCACertBundle();           // bundle de CAs raiz del core ESP32 (Mozilla)
#endif
    sec.setHandshakeTimeout(HTTPS_TIMEOUT_MS / 1000);
    heapLibreAntes = ESP.getFreeHeap();
    int code = pedir(sec, HTTPS_TIMEOUT_MS, post, ruta, cuerpo, resp);
    if (code <= 0 && code != LL_GRANDE) {
      char buf[48] = "";
      int err = sec.lastError(buf, sizeof(buf));
      if (err != 0) {                       // el fallo ocurrio en TLS (certificado, hora, handshake), no en la red
        tlsCodigo = err;
        strlcpy(tlsMsg, buf, sizeof(tlsMsg));
        Serial.printf("TLS error -0x%X: %s (heap libre antes=%lu)\n", (unsigned)(-err), buf, (unsigned long)heapLibreAntes);
        code = LL_TLS;
      }
    } else if (code > 0) {
      tlsCodigo = 0; tlsMsg[0] = 0;
    }
    registrarHeap(ruta);
    return code;
  }
  WiFiClient plain;
  return pedir(plain, HTTP_TIMEOUT_MS, post, ruta, cuerpo, resp);
}

void clasificarRed(int code) {
  netHttp = code;
  if (code == 200 || code == 202) net = NET_OK;
  else if (code == 401 || code == 403) net = NET_TOKEN;
  else if (code == 429) net = NET_LIMITE;
  else if (code == LL_SIN_WIFI) net = NET_SIN_WIFI;
  else if (code == LL_TLS) net = NET_TLS;
  else if (code == LL_HORA) net = NET_HORA;
  else if (code <= 0) net = NET_SIN_RESPUESTA;
  else net = NET_SERVIDOR;
  dirty = true;
}

// ---------------------------------------------------------------- sondeo de mision
void aplicarMision(JsonObjectConst m) {
  Mision nueva;
  nueva.valida = true;
  nueva.id = m["id"] | 0;
  copiarAscii(nueva.estado, sizeof(nueva.estado), m["estado"] | "");
  for (JsonObjectConst o : m["paradas"].as<JsonArrayConst>()) {
    if (nueva.n >= MAX_PARADAS) { nueva.truncada = true; break; }
    Parada& p = nueva.p[nueva.n++];
    p.orden = o["orden"] | (nueva.n);
    copiarAscii(p.sector, sizeof(p.sector), o["sector"] | "?");
    copiarAscii(p.instruccion, sizeof(p.instruccion), o["instruccion"] | "");
    p.litros = o["litros"] | 0;
    p.litrosEntregados = o["litros_entregados"] | 0;
    const char* est = o["estado"] | "pendiente";
    p.estado = !strcmp(est, "llegada") ? P_LLEGADA : !strcmp(est, "entregada") ? P_ENTREGADA
             : !strcmp(est, "incidencia") ? P_INCIDENCIA : P_PENDIENTE;
    // Si hay eventos nuestros sin confirmar para esta parada, el estado local manda (el servidor aun no los vio).
    if (mision.valida && mision.id == nueva.id && hayEventoEnCola(nueva.id, p.orden)) {
      for (int i = 0; i < mision.n; i++) if (mision.p[i].orden == p.orden) { p.estado = mision.p[i].estado; p.litrosEntregados = mision.p[i].litrosEntregados; }
    }
  }
  bool cambioMision = !mision.valida || mision.id != nueva.id;
  mision = nueva;
  if (cambioMision) seleccionarPrimeraAbierta();
  if (sel >= mision.n) sel = mision.n ? mision.n - 1 : 0;
  guardarMision();
}

void sondearMision() {
  String resp;
  int code = llamar(false, "/dispositivo/mision", "", resp);
  clasificarRed(code);
  if (code != 200) {
    // Sondeo fallido: retroceso (10, 20, 40, 60 s) para no bloquear la pantalla cada 10 s con un handshake inutil
    if (fallosPoll < 6) fallosPoll++;
    unsigned long espera = (unsigned long)MISION_POLL_MS << min<uint8_t>(fallosPoll, 3);
    proxPoll = millis() + min<unsigned long>(espera, BACKOFF_MAX_MS);
    return;
  }

  JsonDocument filtro;
  filtro["servidor_hora"] = true;
  filtro["protocolo"] = true;
  filtro["mision"]["id"] = true;
  filtro["mision"]["estado"] = true;
  filtro["mision"]["paradas"][0]["orden"] = true;
  filtro["mision"]["paradas"][0]["sector"] = true;
  filtro["mision"]["paradas"][0]["litros"] = true;
  filtro["mision"]["paradas"][0]["instruccion"] = true;
  filtro["mision"]["paradas"][0]["estado"] = true;
  filtro["mision"]["paradas"][0]["litros_entregados"] = true;
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, resp, DeserializationOption::Filter(filtro));
  if (err) { net = NET_SERVIDOR; netHttp = 200; Serial.printf("JSON invalido: %s\n", err.c_str()); return; }
  fallosPoll = 0;

  // Compatibilidad: si el campo falta (API antigua) se asume el protocolo 1; si es mayor al soportado, no se opera.
  int proto = doc["protocolo"] | 1;
  if (proto != protocoloServidor || (proto > PROTOCOLO_ESPERADO) != firmwareDesactualizado) dirtyTodo = true;
  protocoloServidor = proto;
  firmwareDesactualizado = proto > PROTOCOLO_ESPERADO;

  const char* hora = doc["servidor_hora"] | "";
  if (strlen(hora) >= 19) { memcpy(ultimaSyncHora, hora + 11, 8); ultimaSyncHora[8] = 0; }
  ultimaSyncMs = millis();
  sincronizadoDesdeArranque = true;
  if (firmwareDesactualizado) { dirty = dirtyTodo = true; return; }   // el contrato cambio: no se interpreta la mision

  JsonObjectConst m = doc["mision"].as<JsonObjectConst>();
  if (doc["mision"].isNull() || m.isNull()) {
    // El servidor solo devuelve misiones ACTIVAS: al completarse devuelve null. Si localmente ya estaban todas las
    // paradas cerradas y no queda nada por enviar, se muestra "MISION COMPLETADA"; si no (cancelada/reasignada) se limpia.
    if (mision.valida && mision.n > 0 && abiertas() == 0 && nCola == 0) {
      if (strcmp(mision.estado, "completada")) { strlcpy(mision.estado, "completada", sizeof(mision.estado)); guardarMision(); }
    } else if (mision.valida) { mision = Mision(); guardarMision(); }
  } else {
    aplicarMision(m);
  }
  dirty = dirtyTodo = true;
}

// ---------------------------------------------------------------- envio de eventos con retroceso exponencial
void programarReintento() {
  unsigned long espera = BACKOFF_MIN_MS << min<uint8_t>(backoffN, 5);
  if (espera > BACKOFF_MAX_MS) espera = BACKOFF_MAX_MS;
  if (backoffN < 10) backoffN++;
  proxEnvio = millis() + espera;
}

void enviarCola() {
  if (nCola == 0 || millis() < proxEnvio || firmwareDesactualizado) return;
  int n = min<int>(nCola, LOTE_EVENTOS);
  JsonDocument body;
  JsonArray arr = body["eventos"].to<JsonArray>();
  for (int i = 0; i < n; i++) {
    JsonObject o = arr.add<JsonObject>();
    o["uid"] = cola[i].uid;
    o["tipo"] = tipoStr(cola[i].tipo);
    o["mision_id"] = cola[i].misionId;
    o["parada_orden"] = cola[i].orden;
    if (cola[i].tipo == E_ENTREGA) o["litros_entregados"] = cola[i].litros;
    o["nota"] = cola[i].tipo == E_INCIDENCIA ? "incidencia reportada desde la terminal" : "";
    o["fecha_dispositivo"] = nullptr;   // el reloj puede no estar sincronizado: el servidor pone la hora
  }
  String s, resp;
  serializeJson(body, s);
  int code = llamar(true, "/dispositivo/eventos", s, resp);
  clasificarRed(code);

  if (code == 200) {
    JsonDocument filtro;
    filtro["resultados"][0]["uid"] = true;
    filtro["resultados"][0]["resultado"] = true;
    filtro["resultados"][0]["motivo"] = true;
    JsonDocument doc;
    if (deserializeJson(doc, resp, DeserializationOption::Filter(filtro))) { programarReintento(); return; }
    bool alguno = false;
    for (JsonObject r : doc["resultados"].as<JsonArray>()) {
      const char* uid = r["uid"] | "";
      const char* res = r["resultado"] | "";
      bool final = !strcmp(res, "aplicado") || !strcmp(res, "duplicado") || !strcmp(res, "rechazado");
      if (!final) continue;                 // resultado desconocido: se conserva y se reintenta
      for (int i = 0; i < nCola; i++) {
        if (strcmp(cola[i].uid, uid)) continue;
        if (!strcmp(res, "rechazado")) {
          char motivo[28], t[40];
          copiarAscii(motivo, sizeof(motivo), r["motivo"] | "sin motivo");
          snprintf(t, sizeof(t), "Rechazado: %s", motivo);
          aviso(t, 12000);
        }
        quitarEvento(i);
        alguno = true;
        break;
      }
    }
    if (alguno) { guardarCola(); guardarMision(); backoffN = 0; proxEnvio = 0; proxPoll = 0; }  // sondear ya para reflejar el servidor
    else programarReintento();
    dirty = true;
  } else if (code == 400 || code == 422) {
    // el servidor no entiende el lote: no reintentar para siempre
    for (int i = 0; i < n; i++) if (cola[i].intentos < 255) cola[i].intentos++;
    for (int i = nCola - 1; i >= 0; i--) if (cola[i].intentos >= 5) { aviso("Evento descartado (HTTP 4xx)", 12000); quitarEvento(i); }
    guardarCola();
    programarReintento();
  } else {
    programarReintento();   // red caida, TLS, 401/403 (token), 429, 5xx: se conserva todo
  }
}

void enviarLatido() {
  JsonDocument body;
  body["firmware"] = FIRMWARE_VERSION;
  body["wifi_rssi"] = WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : 0;
  body["pendientes"] = nCola;
  body["uptime_s"] = (uint32_t)(millis() / 1000);
  String s, resp;
  serializeJson(body, s);
  int code = llamar(true, "/dispositivo/latido", s, resp);
  clasificarRed(code);
}

// ---------------------------------------------------------------- acciones del operador
void registrar(TipoEvento t) {
  if (!mision.valida || mision.n == 0) return;
  if (firmwareDesactualizado) { aviso("Actualice el firmware"); return; }
  if (strcmp(mision.estado, "asignada") && strcmp(mision.estado, "en_curso")) { aviso("Mision no activa"); return; }
  Parada& p = mision.p[sel];
  if (t == E_LLEGADA && p.estado != P_PENDIENTE) { aviso("Llegada ya registrada"); return; }
  if (t != E_LLEGADA && !paradaAbierta(p)) { aviso("Parada ya cerrada"); return; }
  if (!encolar(t, p)) return;
  p.estado = t == E_LLEGADA ? P_LLEGADA : (t == E_ENTREGA ? P_ENTREGADA : P_INCIDENCIA);
  if (t == E_ENTREGA) p.litrosEntregados = p.litros;
  guardarMision();
  if (t != E_LLEGADA) seleccionarPrimeraAbierta();
  aviso("Guardado. Enviando...", 4000);   // NO "registrado": falta la confirmacion del servidor
  dirty = dirtyTodo = true;
}

// ---------------------------------------------------------------- dibujo
void txt(const String& s, int x, int y, int fuente, uint16_t fg, uint16_t bg = COL_FONDO, int dato = TL_DATUM) {
  lcd.setTextDatum(dato);
  lcd.setTextColor(fg, bg);
  lcd.drawString(s, x, y, fuente);
  lcd.setTextDatum(TL_DATUM);
}
void boton(int x, int y, int w, int h, const char* t, uint16_t col, uint16_t fg = TFT_WHITE, int fuente = 2) {
  lcd.fillRoundRect(x, y, w, h, 6, col);
  txt(t, x + w / 2, y + h / 2, fuente, fg, col, MC_DATUM);
}
String recortar(const char* s, int max) {
  String r(s);
  if ((int)r.length() > max) r = r.substring(0, max - 1) + ".";
  return r;
}
// Parte el texto por palabras en hasta 'lineas' lineas de 'ancho' caracteres.
void dibujarParrafo(const char* s, int x, int y, int ancho, int lineas, int alto) {
  String resto(s);
  for (int l = 0; l < lineas && resto.length(); l++) {
    String linea;
    if ((int)resto.length() <= ancho) { linea = resto; resto = ""; }
    else {
      int corte = resto.lastIndexOf(' ', ancho);
      if (corte <= 0) corte = ancho;
      linea = resto.substring(0, corte);
      resto = resto.substring(corte);
      resto.trim();
      if (l == lineas - 1 && resto.length()) linea = linea.substring(0, max(0, (int)linea.length() - 1)) + "...";
    }
    txt(linea, x, y + l * alto, 2, COL_TXT);
  }
}

const char* etiquetaRed() {
  switch (net) {
    case NET_OK: return "EN LINEA";
    case NET_SIN_WIFI: return "SIN WIFI";
    case NET_TOKEN: return "TOKEN RECHAZADO";
    case NET_LIMITE: return "LIMITE (429)";
    case NET_SERVIDOR: return "ERROR SERVIDOR";
    case NET_SIN_RESPUESTA: return "SIN RESPUESTA";
    case NET_TLS: return "ERROR TLS";
    case NET_HORA: return ntpFallo() ? "HORA NO SINC." : "SINC. HORA...";
    default: return "CONECTANDO";
  }
}

void dibujarCabecera() {
  uint16_t col = net == NET_OK ? TFT_DARKGREEN : (net == NET_INICIANDO ? TFT_NAVY : TFT_MAROON);
#ifdef API_TLS_INSECURE
  col = TFT_RED;
#endif
  lcd.fillRect(0, 0, 240, 22, col);
#ifdef API_TLS_INSECURE
  txt("TLS INSEGURO!", 6, 3, 2, TFT_YELLOW, col);
#else
  txt(CISTERNA_ID, 6, 3, 2, TFT_WHITE, col);
#endif
  txt(etiquetaRed(), 234, 3, 2, TFT_WHITE, col, TR_DATUM);
}

void dibujarSync() {
  lcd.fillRect(0, 24, 240, 18, COL_FONDO);
  char b[48];
  if (sincronizadoDesdeArranque) {
    snprintf(b, sizeof(b), "Sync %sZ hace %lus", ultimaSyncHora, (millis() - ultimaSyncMs) / 1000UL);
    txt(b, 6, 25, 2, net == NET_OK ? COL_GRIS : TFT_RED);
  } else {
    txt(mision.valida ? "SIN SINCRONIZAR (datos guardados)" : "Sin sincronizar", 6, 25, 2, TFT_RED);
  }
}

void pantallaCompleta(const char* l1, const char* l2, const char* l3, uint16_t col) {
  lcd.fillRect(0, 44, 240, 276, COL_FONDO);
  int fuente = strlen(l1) > 16 ? 2 : 4;          // font 4 admite ~16 caracteres en 240 px
  txt(l1, 120, 110, fuente, col, COL_FONDO, MC_DATUM);
  txt(l2, 120, 150, 2, COL_TXT, COL_FONDO, MC_DATUM);
  txt(l3, 120, 175, 2, COL_GRIS, COL_FONDO, MC_DATUM);
  if (nCola) txt(String("Por enviar: ") + nCola, 120, 215, 2, 0xC260, COL_FONDO, MC_DATUM);
  txt("Toque arriba: estado", 120, 300, 2, COL_GRIS, COL_FONDO, MC_DATUM);
}

void dibujarConfirmacion() {
  lcd.fillScreen(COL_FONDO);
  const Parada& p = mision.p[sel];
  bool ent = confirmTipo == E_ENTREGA;
  txt(ent ? "CONFIRMAR ENTREGA" : "CONFIRMAR INCIDENCIA", 120, 24, 2, ent ? TFT_DARKGREEN : TFT_RED, COL_FONDO, MC_DATUM);
  txt(String("Parada ") + p.orden, 120, 58, 2, COL_TXT, COL_FONDO, MC_DATUM);
  txt(recortar(p.sector, 18), 120, 88, 4, COL_TXT, COL_FONDO, MC_DATUM);
  if (ent) txt(String(p.litros) + " litros", 120, 126, 4, TFT_BLUE, COL_FONDO, MC_DATUM);
  txt(net == NET_OK ? "Se enviara al servidor." : "Sin conexion: se guarda y se envia luego.", 120, 160, 2,
      net == NET_OK ? COL_GRIS : 0xC260, COL_FONDO, MC_DATUM);
  boton(10, 190, 220, 54, "CONFIRMAR", ent ? TFT_DARKGREEN : TFT_RED, TFT_WHITE, 4);
  boton(10, 254, 220, 54, "CANCELAR", COL_GRIS, TFT_WHITE, 4);
}

// Texto de la linea de alertas operativas (una sola linea, por prioridad)
void dibujarAlerta() {
  lcd.fillRect(0, 186, 240, 18, COL_FONDO);
  int y = 187;
  if (avisoTxt[0] && millis() < avisoHasta) txt(avisoTxt, 6, y, 2, TFT_RED);
  else if (nCola >= MAX_COLA * 8 / 10) txt(String("COLA CASI LLENA ") + nCola + "/" + MAX_COLA, 6, y, 2, TFT_RED);
  else if (nCola) txt(String("Por enviar: ") + nCola + (net == NET_OK ? "" : " (sin conexion)"), 6, y, 2, 0xC260);
  else if (net == NET_TOKEN) txt("Token rechazado (HTTP " + String(netHttp) + ")", 6, y, 2, TFT_RED);
  else if (net == NET_TLS) txt("Error TLS: certificado u hora", 6, y, 2, TFT_RED);
  else if (net == NET_HORA) txt("Hora no sincronizada (HTTPS)", 6, y, 2, TFT_RED);
  else if (net == NET_SERVIDOR || net == NET_LIMITE) txt("Servidor: HTTP " + String(netHttp), 6, y, 2, TFT_RED);
  else if (mision.truncada) txt("Hay mas paradas de las mostradas", 6, y, 2, TFT_RED);
  else if (abiertas() == 0) txt("Todas registradas. Espere cierre.", 6, y, 2, TFT_DARKGREEN);
}

void dibujarPrincipal() {
  const Parada& p = mision.p[sel];
  Visible v = estadoVisible(p);
  lcd.fillRect(0, 42, 240, 144, COL_FONDO);
  char b[48];
  snprintf(b, sizeof(b), "Mision #%ld %s", (long)mision.id, mision.estado);
  txt(b, 6, 43, 2, TFT_NAVY);
  snprintf(b, sizeof(b), "%d/%d", sel + 1, mision.n);
  txt(b, 234, 43, 2, TFT_NAVY, COL_FONDO, TR_DATUM);
  // destino actual, con flechas laterales grandes para recorrer las paradas
  boton(2, 62, 34, 54, "<", COL_GRIS, TFT_WHITE, 4);
  boton(204, 62, 34, 54, ">", COL_GRIS, TFT_WHITE, 4);
  String sec = recortar(p.sector, 18);
  txt(sec, 120, 75, sec.length() > 11 ? 2 : 4, COL_TXT, COL_FONDO, MC_DATUM);
  snprintf(b, sizeof(b), "%ld L", (long)p.litros);
  txt(b, 120, 102, 4, TFT_BLUE, COL_FONDO, MC_DATUM);
  txt(etiquetaVisible(v), 120, 124, 2, colorVisible(v), COL_FONDO, MC_DATUM);
  dibujarParrafo(p.instruccion[0] ? p.instruccion : "(sin instruccion)", 6, 138, 33, 2, 16);
  // proxima parada abierta despues de la mostrada
  int prox = -1;
  for (int i = sel + 1; i < mision.n; i++) if (paradaAbierta(mision.p[i])) { prox = i; break; }
  if (prox >= 0) txt(String("Prox: ") + recortar(mision.p[prox].sector, 22), 6, 170, 2, COL_GRIS);
  else txt("Sin mas paradas abiertas", 6, 170, 2, COL_GRIS);
  dibujarAlerta();
  // contadores (solo cuentan lo confirmado por el servidor) y acceso a la lista
  lcd.fillRect(0, 204, 240, 18, COL_FONDO);
  snprintf(b, sizeof(b), "Hechas %d/%d  Inc %d", hechasConfirmadas(), mision.n, contarEstado(P_INCIDENCIA));
  txt(b, 6, 205, 2, COL_TXT);
  boton(176, 204, 62, 17, "LISTA", TFT_NAVY, TFT_WHITE, 2);
}

void dibujarControles() {
  const Parada& p = mision.p[sel];
  lcd.fillRect(0, 222, 240, 98, COL_FONDO);
  bool activa = !firmwareDesactualizado && (!strcmp(mision.estado, "asignada") || !strcmp(mision.estado, "en_curso"));
  bool puedeLleg = activa && p.estado == P_PENDIENTE;
  bool puedeOtro = activa && paradaAbierta(p);
  boton(2, 222, 117, 46, "LLEGUE", puedeLleg ? TFT_BLUE : COL_GRIS_CLARO, puedeLleg ? TFT_WHITE : COL_GRIS, 2);
  boton(121, 222, 117, 46, "INCIDENCIA", puedeOtro ? TFT_RED : COL_GRIS_CLARO, puedeOtro ? TFT_WHITE : COL_GRIS, 2);
  boton(2, 270, 236, 48, "ENTREGADO", puedeOtro ? TFT_DARKGREEN : COL_GRIS_CLARO, puedeOtro ? TFT_WHITE : COL_GRIS, 4);
}

void dibujarLista() {
  lcd.fillScreen(COL_FONDO);
  dibujarCabecera();
  char b[48];
  snprintf(b, sizeof(b), "ENTREGAS - Mision #%ld", (long)mision.id);
  txt(b, 6, 26, 2, TFT_NAVY);
  for (int i = 0; i < mision.n; i++) {
    int y = 46 + 22 * i;
    Visible v = estadoVisible(mision.p[i]);
    if (i == sel) lcd.fillRect(0, y, 240, 21, 0xDEFB);
    uint16_t bg = (i == sel) ? 0xDEFB : COL_FONDO;
    snprintf(b, sizeof(b), "%d %s", mision.p[i].orden, recortar(mision.p[i].sector, 12).c_str());
    txt(b, 4, y + 2, 2, COL_TXT, bg);
    txt(etiquetaCorta(v), 236, y + 2, 2, colorVisible(v), bg, TR_DATUM);
  }
  boton(2, 270, 236, 48, "VOLVER", COL_GRIS, TFT_WHITE, 4);
}

void dibujarEstado() {
  lcd.fillScreen(COL_FONDO);
  dibujarCabecera();
  char b[64];
  int y = 26;
  txt("ESTADO DE CONEXION", 6, y, 2, TFT_NAVY); y += 20;
  if (WiFi.status() == WL_CONNECTED) snprintf(b, sizeof(b), "WiFi %s %ddBm", recortar(WIFI_SSID, 14).c_str(), WiFi.RSSI());
  else snprintf(b, sizeof(b), "WiFi: sin conexion");
  txt(b, 6, y, 2, WiFi.status() == WL_CONNECTED ? COL_TXT : TFT_RED); y += 18;
#ifdef API_TLS_INSECURE
  txt("API: HTTPS SIN VALIDAR!", 6, y, 2, TFT_RED);
#else
  txt(API_HTTPS ? "API: HTTPS (cert. validado)" : "API: HTTP local (sin cifrar)", 6, y, 2, COL_TXT);
#endif
  y += 18;
  if (API_HTTPS) {
    if (tlsCodigo) snprintf(b, sizeof(b), "TLS: error -0x%X", (unsigned)(-tlsCodigo));
    else snprintf(b, sizeof(b), "TLS: %s", net == NET_OK ? "ok" : "sin errores");
    txt(b, 6, y, 2, tlsCodigo ? TFT_RED : COL_TXT); y += 18;
    time_t t = time(nullptr);
    if (horaOk) { struct tm tmv; gmtime_r(&t, &tmv); strftime(b, sizeof(b), "Hora UTC %H:%M:%S", &tmv); }
    else snprintf(b, sizeof(b), "HORA NO SINCRONIZADA");
    txt(b, 6, y, 2, horaOk ? COL_TXT : TFT_RED); y += 18;
  }
  snprintf(b, sizeof(b), "Servidor: %s (HTTP %d)", net == NET_OK ? "ok" : "problema", netHttp);
  txt(b, 6, y, 2, net == NET_OK ? COL_TXT : TFT_RED); y += 18;
  if (sincronizadoDesdeArranque) snprintf(b, sizeof(b), "Sync %sZ hace %lus", ultimaSyncHora, (millis() - ultimaSyncMs) / 1000UL);
  else snprintf(b, sizeof(b), "Sin sincronizar desde el inicio");
  txt(b, 6, y, 2, COL_TXT); y += 18;
  snprintf(b, sizeof(b), "Cola: %d/%d eventos", nCola, MAX_COLA);
  txt(b, 6, y, 2, nCola ? 0xC260 : COL_TXT); y += 18;
  snprintf(b, sizeof(b), "Heap %luK (min %luK)", (unsigned long)(ESP.getFreeHeap() / 1024), (unsigned long)(ESP.getMinFreeHeap() / 1024));
  txt(b, 6, y, 2, COL_TXT); y += 18;
  snprintf(b, sizeof(b), "FW %s  protocolo %d/%d", FIRMWARE_VERSION, PROTOCOLO_ESPERADO, protocoloServidor);
  txt(b, 6, y, 2, firmwareDesactualizado ? TFT_RED : COL_TXT);
  boton(2, 270, 236, 48, "VOLVER", COL_GRIS, TFT_WHITE, 4);
}

void dibujar() {
  if (modo == M_CONFIRMAR) { if (dirtyTodo) dibujarConfirmacion(); dirty = dirtyTodo = false; return; }
  if (modo == M_LISTA) { dibujarLista(); dirty = dirtyTodo = false; return; }
  if (modo == M_ESTADO) { dibujarEstado(); dirty = dirtyTodo = false; return; }
  if (dirtyTodo) lcd.fillScreen(COL_FONDO);
  dibujarCabecera();
  dibujarSync();
  // pantalla de bloqueo por version de protocolo (la terminal no opera con un contrato que no conoce)
  if (firmwareDesactualizado) {
    char b[48];
    snprintf(b, sizeof(b), "Servidor: protocolo %d. Terminal: %d", protocoloServidor, PROTOCOLO_ESPERADO);
    pantallaCompleta("ACTUALICE EL FIRMWARE", b, "No se envian eventos nuevos", TFT_RED);
    dirty = dirtyTodo = false;
    return;
  }
  // pantallas completas: sin mision usable, o token rechazado (no se puede operar; la cola se conserva)
  if (!mision.valida || net == NET_TOKEN) {
    if (net == NET_TOKEN) pantallaCompleta("TOKEN RECHAZADO", (String("HTTP ") + netHttp + ": terminal no autorizada").c_str(), "Avise al administrador", TFT_RED);
    else if (net == NET_SIN_WIFI) pantallaCompleta("SIN WIFI", "Buscando red...", "Reconexion automatica", TFT_RED);
    else if (net == NET_TLS) {
      char b[48];
      snprintf(b, sizeof(b), "Codigo -0x%X", (unsigned)(-tlsCodigo));
      pantallaCompleta("ERROR TLS", "Certificado u hora invalidos", tlsCodigo ? b : "Reintentando...", TFT_RED);
    }
    else if (net == NET_HORA) pantallaCompleta(ntpFallo() ? "HORA NO SINCRONIZADA" : "SINCRONIZANDO HORA", "HTTPS necesita la hora (NTP)", ntpFallo() ? "Sin acceso a NTP. Reintentando" : "Espere unos segundos", ntpFallo() ? TFT_RED : TFT_NAVY);
    else if (net == NET_SERVIDOR || net == NET_LIMITE) pantallaCompleta("ERROR DE SERVIDOR", (String("HTTP ") + netHttp).c_str(), "Reintentando...", TFT_RED);
    else if (net == NET_SIN_RESPUESTA) pantallaCompleta("SERVIDOR SIN RESPUESTA", "Verifique API_BASE_URL y red", "Reintentando...", TFT_RED);
    else if (net == NET_OK) pantallaCompleta("SIN MISION ASIGNADA", "Espere una asignacion", "Se consulta cada pocos segundos", TFT_NAVY);
    else pantallaCompleta("CONECTANDO...", "", "", TFT_NAVY);
    dirty = dirtyTodo = false;
    return;
  }
  if (!strcmp(mision.estado, "completada") || !strcmp(mision.estado, "cancelada") || mision.n == 0) {
    bool comp = !strcmp(mision.estado, "completada");
    pantallaCompleta(comp ? "MISION COMPLETADA" : (mision.n ? "MISION CANCELADA" : "MISION SIN PARADAS"),
                     (String("#") + mision.id + "  entregas: " + hechasConfirmadas() + "/" + mision.n).c_str(),
                     nCola ? (String("Por enviar: ") + nCola).c_str() : "Espere nueva asignacion", comp ? TFT_DARKGREEN : TFT_RED);
    dirty = dirtyTodo = false;
    return;
  }
  dibujarPrincipal();
  dibujarControles();
  dirty = dirtyTodo = false;
}

// ---------------------------------------------------------------- tactil
bool dentro(int x, int y, int bx, int by, int bw, int bh) { return x >= bx && x < bx + bw && y >= by && y < by + bh; }

void abrirModo(Modo m) { modo = m; confirmDesde = millis(); dirty = dirtyTodo = true; }

void manejarToque(int x, int y) {
  if (modo == M_CONFIRMAR) {
    if (dentro(x, y, 10, 190, 220, 54)) { registrar(confirmTipo); modo = M_NORMAL; dirty = dirtyTodo = true; }
    else if (dentro(x, y, 10, 254, 220, 54)) { modo = M_NORMAL; dirty = dirtyTodo = true; }
    return;
  }
  if (modo == M_LISTA) {
    if (dentro(x, y, 0, 266, 240, 54)) { abrirModo(M_NORMAL); return; }
    if (y >= 46 && y < 46 + 22 * mision.n) { sel = (y - 46) / 22; abrirModo(M_NORMAL); }
    return;
  }
  if (modo == M_ESTADO) {
    if (dentro(x, y, 0, 266, 240, 54) || y < 24) abrirModo(M_NORMAL);
    return;
  }
  if (y < 24) { abrirModo(M_ESTADO); return; }         // tocar la cabecera abre el estado de conexion
  if (!mision.valida || mision.n == 0 || net == NET_TOKEN || firmwareDesactualizado) return;
  if (!strcmp(mision.estado, "completada") || !strcmp(mision.estado, "cancelada")) return;
  if (dentro(x, y, 0, 60, 40, 58)) { sel = (sel + mision.n - 1) % mision.n; dirty = true; }
  else if (dentro(x, y, 200, 60, 40, 58)) { sel = (sel + 1) % mision.n; dirty = true; }
  else if (dentro(x, y, 0, 200, 240, 22)) abrirModo(M_LISTA);
  else if (dentro(x, y, 2, 222, 117, 46)) registrar(E_LLEGADA);
  else if (dentro(x, y, 121, 222, 117, 46) || dentro(x, y, 2, 270, 236, 48)) {
    TipoEvento t = y >= 270 ? E_ENTREGA : E_INCIDENCIA;
    const Parada& p = mision.p[sel];
    if (!paradaAbierta(p)) { aviso("Parada ya cerrada"); return; }
    confirmTipo = t; abrirModo(M_CONFIRMAR);
  }
}

// ---------------------------------------------------------------- setup / loop
void setup() {
  Serial.begin(115200);
  lcd.begin();
  lcd.setRotation(0);
  lcd.fillScreen(COL_FONDO);
  ts.begin();
  ts.setRotation(ROTATION_NORMAL);
  Serial.printf("HIDRO360 terminal FW %s, protocolo %d, API %s (%s)\n", FIRMWARE_VERSION, PROTOCOLO_ESPERADO,
                API_HTTPS ? "HTTPS" : "HTTP", CISTERNA_ID);
#ifdef API_TLS_INSECURE
  Serial.println("ADVERTENCIA: API_TLS_INSECURE activo, el certificado del servidor NO se valida.");
#endif

  prefs.begin("hidro360", false);
  bootCnt = prefs.getUInt("boot", 0) + 1;
  prefs.putUInt("boot", bootCnt);
  cargarCola();
  cargarMision();      // ultima mision conocida: se muestra marcada "sin sincronizar" hasta el primer sondeo
  seleccionarPrimeraAbierta();

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  registrarHeap("arranque");
  dibujar();
}

void loop() {
  static bool tocando = false;
  static unsigned long tToque = 0;
  unsigned long ahora = millis();

  // WiFi: reconexion automatica + respaldo manual cada 10 s
  if (WiFi.status() != WL_CONNECTED) {
    if (net != NET_SIN_WIFI && net != NET_INICIANDO) { net = NET_SIN_WIFI; dirty = dirtyTodo = true; }
    if (net == NET_INICIANDO && ahora > 15000) { net = NET_SIN_WIFI; dirty = dirtyTodo = true; }
    if (ahora > proxWifi) { proxWifi = ahora + 10000; WiFi.disconnect(); WiFi.begin(WIFI_SSID, WIFI_PASS); }
  }
  gestionarHora();

  // Tactil (se actua en el flanco de pulsacion)
  ts.read();
  if (ts.isTouched) {
    if (!tocando && ahora - tToque > 250) {
      tocando = true; tToque = ahora;
      manejarToque(ts.points[0].x, ts.points[0].y);
    }
  } else {
    tocando = false;
  }
  if (modo == M_CONFIRMAR && ahora - confirmDesde > CONFIRM_TIMEOUT_MS) { modo = M_NORMAL; dirty = dirtyTodo = true; }
  if ((modo == M_LISTA || modo == M_ESTADO) && ahora - confirmDesde > PANTALLA_AUX_TIMEOUT_MS) { modo = M_NORMAL; dirty = dirtyTodo = true; }

  // Red: solo si no se esta confirmando (una peticion bloquea hasta el tiempo de espera) y, en HTTPS, con hora valida
  if (modo != M_CONFIRMAR && !tocando && WiFi.status() == WL_CONNECTED && (!API_HTTPS || horaOk)) {
    enviarCola();
    if (ahora >= proxPoll) { proxPoll = millis() + MISION_POLL_MS; sondearMision(); }
    if (ahora >= proxLatido) { proxLatido = millis() + LATIDO_MS; enviarLatido(); }
  }

  // Refresco periodico del "hace N s" y caducidad de avisos
  static unsigned long tRefresco = 0;
  if (ahora - tRefresco > 5000) { tRefresco = ahora; dirty = true; }
  if (avisoTxt[0] && ahora > avisoHasta) { avisoTxt[0] = 0; dirty = true; }
  if (dirty) dibujar();
  delay(15);
}
