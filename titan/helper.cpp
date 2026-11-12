// titan_helper: piccolo processo che tiene aperto il driver TitanCSA Interceptor-Keyboard-Mouse
// e invia i tasti / movimenti del mouse che Python gli scrive su stdin, una riga per comando.
//
//   K <scan> <ext 0|1> <up 0|1>     tasto (codice di scansione, E0, rilascio)
//   M <dx> <dy>                     mouse: spostamento RELATIVO in conteggi
//   A <x> <y>                       mouse: posizione ASSOLUTA (0..65535 sul desktop virtuale)
//   B <button_flags>                pulsanti del mouse (LeftDown=1, LeftUp=2, RightDown=4, ...)
//   Q                               esci
//
// Risposta a ogni comando: "OK" oppure "ERR <codice>". All'avvio: "READY <kb> <ms>" o "FATAL <testo>".
// Uso:  titan_helper.exe [id_tastiera|-1] [id_mouse|-1]     (-1 = il primo dispositivo trovato)
//
// Va avviato da un processo con privilegi di amministratore (lo richiede il driver).
#define NOMINMAX
#include "Interceptor.h"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

using namespace Interceptor;

static bool pick(const std::vector<Device>& devs, Device_Type type, long want, uint32_t& out) {
    for (const auto& d : devs) {
        if (d.type != static_cast<uint32_t>(type)) continue;
        if (want < 0 || static_cast<long>(d.id) == want) { out = d.id; return true; }
    }
    return false;
}

static void reply(const std::string& s) {
    std::fputs(s.c_str(), stdout);
    std::fputc('\n', stdout);
    std::fflush(stdout);
}

static std::string status(const Send_Result& r) {
    if (r.error == 0 && r.accepted == 1) return "OK";
    return "ERR " + std::to_string(r.error ? r.error : 1);
}

int main(int argc, char** argv) {
    long wantKb = argc > 1 ? std::atol(argv[1]) : -1;
    long wantMs = argc > 2 ? std::atol(argv[2]) : -1;
    uint32_t kb = 0, ms = 0;

    try {
        const auto init = Init_AllDevices();
        if (init.reboot_required) { reply("FATAL riavvio richiesto: riavvia Windows e rilancia il bot"); return 3; }
        if (!init.ready) { reply("FATAL driver non pronto (errore " + std::to_string(init.error) + ")"); return 2; }

        const auto devs = Get_Devices();
        if (!pick(devs, Device_Type::Keyboard, wantKb, kb)) { reply("FATAL nessuna tastiera trovata dal driver"); return 4; }
        if (!pick(devs, Device_Type::Mouse, wantMs, ms)) { reply("FATAL nessun mouse trovato dal driver"); return 4; }
    } catch (const std::exception& e) {
        reply(std::string("FATAL ") + e.what());
        return 1;
    }
    reply("READY " + std::to_string(kb) + " " + std::to_string(ms));

    std::string line;
    while (std::getline(std::cin, line)) {
        std::istringstream in(line);
        std::string cmd;
        in >> cmd;
        try {
            if (cmd == "Q") { reply("OK"); break; }

            if (cmd == "K") {
                int scan = 0, ext = 0, up = 0;
                in >> scan >> ext >> up;
                Keyboard_Input k{};
                k.device = kb;
                k.stroke.make_code = static_cast<uint16_t>(scan);
                k.stroke.flags = static_cast<uint16_t>((up ? KeyUp : 0) | (ext ? KeyE0 : 0));
                reply(status(Send_Input(k)));
            } else if (cmd == "M") {
                int dx = 0, dy = 0;
                in >> dx >> dy;
                Mouse_Input m{};
                m.device = ms;
                m.stroke.x = dx;
                m.stroke.y = dy;
                reply(status(Send_Input(m)));
            } else if (cmd == "A") {
                int x = 0, y = 0;
                in >> x >> y;
                Mouse_Input m{};
                m.device = ms;
                m.stroke.flags = static_cast<uint16_t>(MouseAbsolute | MouseVirtualDesktop);
                m.stroke.x = x;
                m.stroke.y = y;
                reply(status(Send_Input(m)));
            } else if (cmd == "B") {
                unsigned flags = 0;
                in >> flags;
                Mouse_Input m{};
                m.device = ms;
                m.stroke.button_flags = static_cast<uint16_t>(flags);
                reply(status(Send_Input(m)));
            } else {
                reply("ERR comando");
            }
        } catch (const std::system_error& e) {
            reply("ERR " + std::to_string(e.code().value()));
        } catch (const std::exception&) {
            reply("ERR 1");
        }
    }
    return 0;
}
