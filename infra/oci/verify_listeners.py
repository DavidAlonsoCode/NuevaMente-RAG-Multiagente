"""Comprobar que los listeners de aplicación solo se publican mediante el proxy."""

import ipaddress
import subprocess


def verify_listeners(output):
    for line in output.splitlines():
        local = line.split()[3]
        host, port = local.rsplit(":", 1)
        host = host.strip("[]").split("%", 1)[0]
        address = None if host == "*" else ipaddress.ip_address(host)
        if address is not None and address.is_loopback:
            continue
        if port not in ("22", "80", "443"):
            raise RuntimeError("Listener TCP expuesto fuera del proxy/SSH")


if __name__ == "__main__":
    verify_listeners(subprocess.check_output(["ss", "-H", "-lnt"], text=True))
    print("Listeners verificados. Revisar aparte reglas OCI y prueba desde otra IP.")
