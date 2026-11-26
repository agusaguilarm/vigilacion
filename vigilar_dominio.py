#!/usr/bin/env python3
"""
Vigila un dominio .cl en NIC Chile y avisa apenas deje de estar
"en proceso de eliminación" (es decir, cuando probablemente quede disponible).

Cómo funciona:
  - Consulta el servidor WHOIS oficial de NIC Chile (whois.nic.cl, puerto 43)
    cada N minutos.
  - Guarda la primera respuesta como referencia. Si la respuesta cambia, o si
    aparece "no entries found" (dominio no inscrito), lanza una alerta:
    pitido, notificación de escritorio y, opcionalmente, una notificación
    al celular vía ntfy.sh.

Uso:
  python3 vigilar_dominio.py ejemplo.cl           # vigila ejemplo.cl cada 5 min
  python3 vigilar_dominio.py ejemplo.cl -i 2      # cada 2 minutos
  python3 vigilar_dominio.py ejemplo.cl --ntfy mi-tema-secreto-123

Para recibir el aviso en el celular: instala la app "ntfy" (Android/iOS),
suscríbete a un tema con un nombre difícil de adivinar y pásalo con --ntfy.

Solo usa la biblioteca estándar de Python 3 (no requiere instalar nada).
"""

import argparse
import datetime as dt
import os
import urllib.parse
import platform
import re
import socket
import subprocess
import sys
import time
import urllib.request

WHOIS_SERVER = "whois.nic.cl"
WHOIS_PORT = 43


def consultar_whois(dominio: str, timeout: float = 15.0) -> str:
    """Consulta el WHOIS de NIC Chile y devuelve la respuesta como texto."""
    with socket.create_connection((WHOIS_SERVER, WHOIS_PORT), timeout=timeout) as s:
        s.sendall((dominio + "\r\n").encode("utf-8"))
        partes = []
        while True:
            datos = s.recv(4096)
            if not datos:
                break
            partes.append(datos)
    return b"".join(partes).decode("utf-8", errors="replace")


def normalizar(texto: str) -> str:
    """Quita líneas que cambian en cada consulta (comentarios, fechas actuales)."""
    lineas = []
    for linea in texto.splitlines():
        l = linea.strip()
        if not l or l.startswith("%"):
            continue
        if re.search(r"fecha actual|current date|last update", l, re.I):
            continue
        lineas.append(l)
    return "\n".join(lineas)


def parece_disponible(texto: str) -> bool:
    t = texto.lower()
    return "no entries found" in t or "no existe" in t or "not found" in t


def notificar(titulo: str, mensaje: str, ntfy_tema: str | None) -> None:
    # Pitido en la terminal
    print("\a" * 3, end="", flush=True)

    # Notificación de escritorio según sistema operativo
    sistema = platform.system()
    try:
        if sistema == "Darwin":
            subprocess.run(
                ["osascript", "-e",
                 f'display notification "{mensaje}" with title "{titulo}" sound name "Glass"'],
                check=False)
        elif sistema == "Linux":
            subprocess.run(["notify-send", "-u", "critical", titulo, mensaje], check=False)
        elif sistema == "Windows":
            ps = (
                "Add-Type -AssemblyName System.Windows.Forms;"
                f"[System.Windows.Forms.MessageBox]::Show('{mensaje}','{titulo}')"
            )
            subprocess.Popen(["powershell", "-NoProfile", "-Command", ps])
    except FileNotFoundError:
        pass

    # Notificación al celular vía ntfy.sh (opcional)
    if ntfy_tema:
        try:
            req = urllib.request.Request(
                f"https://ntfy.sh/{ntfy_tema}",
                data=mensaje.encode("utf-8"),
                headers={"Title": titulo, "Priority": "urgent", "Tags": "rotating_light"},
            )
            urllib.request.urlopen(req, timeout=15)
        except Exception as e:  # noqa: BLE001
            print(f"  (No se pudo enviar a ntfy: {e})")

    # Notificación de emergencia vía Pushover (opcional): se repite cada
    # 60 s hasta que la confirmes en el celular, hasta por 1 hora.
    po_token = os.environ.get("PUSHOVER_TOKEN")
    po_user = os.environ.get("PUSHOVER_USER")
    if po_token and po_user:
        try:
            datos = urllib.parse.urlencode({
                "token": po_token, "user": po_user,
                "title": titulo, "message": mensaje,
                "priority": 2, "retry": 60, "expire": 3600,
                "sound": "siren",
                "url": "https://clientes.nic.cl", "url_title": "Abrir NIC Chile",
            }).encode("utf-8")
            urllib.request.urlopen("https://api.pushover.net/1/messages.json",
                                   data=datos, timeout=15)
        except Exception as e:  # noqa: BLE001
            print(f"  (No se pudo enviar a Pushover: {e})")


def ahora() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def main() -> None:
    p = argparse.ArgumentParser(description="Vigila un dominio .cl en NIC Chile.")
    p.add_argument("dominio", nargs="?", default=os.environ.get("DOMINIO"),
                   help="dominio a vigilar (o usa la variable de entorno DOMINIO)")
    p.add_argument("-i", "--intervalo", type=float, default=5,
                   help="minutos entre consultas (por defecto 5; no bajes de 1)")
    p.add_argument("--ntfy", metavar="TEMA", default=os.environ.get("NTFY_TOPIC"),
                   help="tema de ntfy.sh para recibir el aviso en el celular")
    p.add_argument("--una-vez", action="store_true",
                   help="consulta una sola vez y termina (para GitHub Actions o cron)")
    args = p.parse_args()

    if not args.dominio:
        p.error("indica el dominio como argumento o en la variable de entorno DOMINIO")
    dominio = args.dominio.strip().lower()

    if args.una_vez:
        respuesta = consultar_whois(dominio)
        print(f"[{ahora()}] Respuesta de NIC para {dominio}:\n{respuesta.strip()}")
        if parece_disponible(respuesta):
            msg = f"{dominio} parece DISPONIBLE. ¡Inscríbelo ya en clientes.nic.cl!"
            print(f"*** {msg}")
            notificar("Dominio disponible", msg, args.ntfy)
        return

    intervalo_s = max(args.intervalo, 1) * 60

    print(f"[{ahora()}] Vigilando {dominio} cada {intervalo_s/60:g} min. Ctrl+C para salir.")

    referencia = None
    errores_seguidos = 0

    while True:
        try:
            respuesta = consultar_whois(dominio)
            errores_seguidos = 0
        except Exception as e:  # noqa: BLE001
            errores_seguidos += 1
            print(f"[{ahora()}] Error consultando WHOIS ({errores_seguidos}): {e}")
            time.sleep(min(intervalo_s, 60 * errores_seguidos))
            continue

        actual = normalizar(respuesta)

        if referencia is None:
            referencia = actual
            print(f"[{ahora()}] Estado inicial:\n{respuesta.strip()}\n")
            if parece_disponible(respuesta):
                msg = f"{dominio} ya aparece como NO inscrito. ¡Inscríbelo en clientes.nic.cl!"
                print(f"[{ahora()}] *** {msg}")
                notificar("Dominio disponible", msg, args.ntfy)
                return
        elif parece_disponible(respuesta):
            msg = f"{dominio} parece DISPONIBLE. ¡Inscríbelo ya en clientes.nic.cl!"
            print(f"\n[{ahora()}] *** {msg}\n{respuesta.strip()}\n")
            notificar("Dominio disponible", msg, args.ntfy)
            return
        elif actual != referencia:
            msg = f"Cambió el estado de {dominio} en NIC Chile. Revísalo ahora."
            print(f"\n[{ahora()}] *** {msg}\nNueva respuesta:\n{respuesta.strip()}\n")
            notificar("Cambio en dominio", msg, args.ntfy)
            referencia = actual  # sigue vigilando por si luego queda libre
        else:
            print(f"[{ahora()}] Sin cambios.")

        time.sleep(intervalo_s)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nDetenido.")
        sys.exit(0)
