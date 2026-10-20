# Preparación de la VM — Issue 47

El objetivo vigente es Ubuntu 24.04 ARM64, VM.Standard.A1.Flex con 2 OCPU y
12 GB en la home region, respetando la asignación Always Free de la cuenta.
Referencia: `decisiones_proyecto.md` §9 y
`docs/issues/fase-5-despliegue-endurecimiento.md`.

- `provision_vm.py`: consulta inventario, cuotas y capacidad; `--apply` crea o retoma
  únicamente la configuración A1 elegida, con diario local y tokens idempotentes.
- `bootstrap.sh`: preparación de una VM nueva, Docker/Compose, actualizaciones
  automáticas de seguridad y SSH por clave sin acceso root ni contraseña.
- `verify_host.sh`: comprobaciones que se ejecutan con sudo dentro de la VM.
  La red OCI, el acceso desde otra IP y la captura de gratuidad se verifican aparte.
- `verify_listeners.py`: permite listeners locales en todo el rango loopback
  (incluido el DNS de Ubuntu en `127.0.0.53`) y rechaza puertos de app expuestos.

Estado documental contrastado el **16/10/2026** sobre `develop` `f292415`:
#47 y #48 están cerrados con su implementación integrada. La aceptación real
de VM, ARM64/Docker, SSH y gratuidad permanece en
[#133](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/issues/133).
El cierre de los issues no acredita una VM operativa.
La consulta del aprovisionador tiene evidencia OCI real histórica; las guardas,
creación/reanudación y sintaxis de scripts tienen verificaciones previas con el
alcance registrado en cada revisión, sin una nueva ejecución en esta actualización.
La configuración de aplicación, Caddy y credenciales está integrada en #48.
La precarga offline de las fuentes demo del #49 y sus pendientes se describen
en el [runbook de precarga](../../docs/demo-precarga.md).
El [mapa de fuentes y evidencias de #58](../../docs/verificacion-issue-58.md)
separa implementación, evidencia histórica y aceptación pendiente.
La [receta de instalación de #58](../../docs/despliegue-oci.md) consolida desde
prerrequisitos hasta salud y preparación demo; su ejecución real sigue pendiente.

Contraste documental del **17/10/2026** sobre la base integrada `5988244`
(#137/#138/#140): aprovisionador, bootstrap y verificadores de host/listeners no
cambiaron desde `f292415`. Sus procedimientos siguen aplicables, sin renovar la
evidencia de capacidad o de VM. La aplicación vigente usa sólo React, admite
paquetes 1.0/1.1 y conserva el modo al recuperar; el rollback requiere lectores
compatibles con datos locales y OCI, además del esquema SQLite. Ver el
[runbook actualizado](../../docs/despliegue-oci.md). No se ejecutaron operaciones
OCI ni nuevas pruebas en este contraste.

## Bloqueo observado el 8 de octubre de 2026

Registro histórico: las menciones al estado de #47 en esta sección y en las
reconsultas del 8 y 12 de octubre describen esas revisiones, anteriores al estado
de integración indicado arriba. Sus consultas no acreditan capacidad disponible hoy.

La consulta real `CreateComputeCapacityReport` en `sa-vinhedo-1`, para 2 OCPU y
12 GB, devolvió `OUT_OF_HOST_CAPACITY` en el único dominio de disponibilidad.
El inventario no tenía instancias activas ni volúmenes boot/block consumidos;
las cuotas permitían la configuración, pero no había capacidad física.
No se creó una VM ni se cambió a otra región o configuración paga.
El reporte local de esta consulta está en `.data/oci-47-preflight-43e0e819de1d483e99c1256fe4dc46ac.json`;
es temporal y no sustituye una nueva consulta antes de aprovisionar.

El #47 permanece pendiente: faltan provisión real, comprobación de ARM64/Docker,
prueba SSH desde una IP no autorizada y captura de consola con gratuidad/costo cero.


## Consulta y provisión

Usar Python 3.11 y `oci==2.187.2` (ya fijado en `backend/requirements.txt`).
La herramienta no carga `.env`, no llama a Gemini y no monta credenciales en la VM.
La configuración del **operador** necesita permisos de lectura del inventario de la tenancy
y creación de Compute/red en el compartimento elegido. La identidad del backend sigue
siendo el usuario técnico limitado al bucket del #14.

Desde la raíz, en PowerShell, completar valores locales; las claves permanecen fuera de Git:

```powershell
$configuracionOci = "C:/ruta/fuera-del-repo/.oci/config"
$compartimentoOci = "ocid1.compartment.oc1..REEMPLAZAR"
.venv/Scripts/python.exe -B infra/oci/provision_vm.py --config-file $configuracionOci --compartment-id $compartimentoOci
```

Sin `--apply` consulta la asignación y el estado de capacidad. El reporte queda en
`.data/oci47/preflight-*.json`. Son evidencias de ese momento, no autorizan por sí solas
una creación posterior. Cada ejecución vuelve a consultar OCI.

Cuando haya capacidad, preparar un par SSH Ed25519 fuera del repositorio, con la clave
privada protegida por permisos del usuario; solo se envía la `.pub` a OCI.
`--confirm-always-free` expresa que el operador revisó la franquicia mensual,
el consumo previo de A1 y los discos de la tenancy. Las cuotas de servicio pueden ser
mayores que la franquicia gratuita y **no** autorizan aumentar 2 OCPU / 12 GB.

```powershell
$ipAdministracion = "TU_IPV4_PUBLICA/32"
$clavePublicaSsh = "C:/ruta/fuera-del-repo/id_ed25519.pub"
.venv/Scripts/python.exe -B infra/oci/provision_vm.py --config-file $configuracionOci --compartment-id $compartimentoOci --admin-cidr $ipAdministracion --ssh-public-key $clavePublicaSsh --confirm-always-free --apply
```

La receta fija 2 OCPU / 12 GB, Ubuntu 24.04 ARM64 oficial, boot de 50 GB con
rendimiento Balanced (10 VPU/GB), sin Marketplace, AI Enterprise, reservas de
capacidad ni políticas de backup. Solo usa la home region. Cuenta también instancias
A1 detenidas y discos sin instancia; rechaza superar 2 OCPU / 12 GB o 200 GB de discos
combinados. La capacidad se vuelve a comprobar inmediatamente antes del lanzamiento.

El diario operativo es `.data/oci47/state.json`, separado de los reportes. Antes de
crear se guarda el token idempotente; al retomar se verifican IDs, pertenencia, tags,
reglas y parámetros contra OCI. Un token vencido sin confirmación o una configuración
diferente exigen revisión manual. El bloqueo local impide ejecuciones simultáneas con
ese diario. Conservarlo para retomar; no reemplazarlo con un snapshot antiguo.

## Red y host

- VCN/subred propias: la subred usa exclusivamente la lista de seguridad creada por
  esta receta, evitando el SSH abierto de la lista predeterminada de OCI.
- Ingreso TCP: 22 desde la IPv4 administrativa /32; 80/443 para el proxy del #48.
  ICMP tipo 3/código 4 permite mensajes de MTU. Egreso habilitado para actualizaciones,
  Docker, Gemini y OCI. No se habilita IPv6 ni se agregan NSG a la VNIC.
- Ubuntu: clave pública, sin root/contraseña por SSH; actualizaciones de seguridad
  automáticas sin reinicio automático. El usuario `ubuntu` conserva sudo.
- Firewall del host: protege INPUT y DOCKER-USER; se reaplica al arrancar/reiniciar
  Docker. El tráfico nuevo desde la interfaz exterior no llega a puertos publicados
  por contenedores. Caddy corre en el host en #48.
- `backend_data` está definido en `docker-compose.yml`: su driver local almacena
  datos en el disco de la VM. El overlay `docker-compose.production.yml` fija el
  proyecto `nuevamente` y el volumen `nuevamente_backend_data`; el helper de #48
  exige comprobar su identidad y los datos existentes antes de activarlo.
- El montaje OCI de solo lectura está definido en `docker-compose.oci.yml` (#14).
  Se configura con el usuario técnico al desplegar #48; el config del operador no
  se copia al host ni al backend.

Si cambia la IP de administración, actualizar de forma coordinada la lista OCI y
el firewall del host. La receta rechaza cambios silenciosos de sus parámetros;
no ampliar SSH a `0.0.0.0/0` para resolver un bloqueo de acceso.

## Verificación pendiente en la VM

`RUNNING` de OCI no acredita que cloud-init ni Docker hayan terminado. Esperar
`cloud-init status --wait --long`, comprobar la clave de host por un canal OCI de
confianza antes de aceptarla en SSH, y ejecutar:

```bash
sudo bash infra/oci/verify_host.sh
```

Además comprobar persistencia de las reglas al reiniciar Docker y el servicio SSH.
Desde la IP administrativa, SSH debe conectar. Desde otra red/IP debe fallar la
conexión TCP al 22; una prueba desde la misma conexión no satisface ese criterio.
Registrar también que 8000/8501 no son accesibles directamente.

La evidencia de costo exige la consola OCI con la instancia A1 y su asignación/costo
cero. No publicar claves ni configuración privada en capturas. Estos controles siguen
pendientes en #133: la evidencia revisada no acredita una VM. Los tests simulados y
el cierre de implementación de #47 no satisfacen esa aceptación externa.

Fuentes: [Always Free](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm),
[reglas de red OCI](https://docs.oracle.com/en-us/iaas/Content/Network/Concepts/securityrules.htm),
[instalación oficial de Docker](https://docs.docker.com/engine/install/ubuntu/).


### Reconsulta y último intento del 8 de octubre

Tres consultas de capacidad durante esta continuación devolvieron
`OUT_OF_HOST_CAPACITY`. El último intento ejecutó `--apply` con la configuración
autorizada y se detuvo en la guarda previa: no creó VCN/subred, VM ni discos.
Las cuotas efectivas consultadas permitían 2 OCPU / 12 GB; el inventario seguía
sin A1 ni volúmenes. Esto distingue falta de capacidad física de falta de cuota.
Reportes locales de esta continuación: `.data/oci47/preflight-*.json`.

### Sincronización y reconsulta del 12 de octubre de 2026

La rama se sincronizó con `develop` (`370df46`, incluyendo el #25). La nueva consulta
real en la home region `sa-vinhedo-1`, realizada a las 18:45 de Argentina, volvió a
devolver `OUT_OF_HOST_CAPACITY` en su único dominio de disponibilidad. El inventario
de la tenancy registró 0 OCPU A1, 0 GB de RAM A1 y 0 GB de volúmenes boot/block.
Las cuotas de servicio permiten la configuración de 2 OCPU / 12 GB; esos valores
no amplían la franquicia gratuita. Ubuntu 24.04 ARM64 oficial sigue disponible.
No se crearon recursos ni se cambió a una instancia paga.

El PR puede integrar la receta reproducible y sus controles; el #47 permanece
abierto hasta obtener la VM y completar los tres criterios de aceptación reales.
Se corrigió un falso rechazo del DNS local de Ubuntu en la verificación de listeners.
Las pruebas cubren loopback IPv4/IPv6 y el rechazo de puertos de aplicación/DNS
expuestos mediante direcciones públicas, privadas o wildcard.

La configuración OCI local apuntaba a una ruta de clave de otra PC. Para esta
consulta se corrigió únicamente en memoria, usando la clave contigua al archivo
de configuración; no se copiaron secretos al repositorio. Antes de usar el CLI,
el operador debe asegurar que `key_file` apunte a una ruta existente en su PC.
La disponibilidad de capacidad debe reconsultarse antes de cada intento manual.
