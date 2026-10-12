# Inspección de gobernanza OCI — 2026-10-12

Se ejecutó el proveedor real `oci==2.187.2`, con autenticación API Key local,
contra `nuevamente-contenidos-educativos`, región `sa-vinhedo-1`. No se subieron
documentos ni se modificaron recursos cloud. La configuración original conserva
su ruta anterior; la ejecución usó una copia temporal corregida que se eliminó.

| Control leído del SDK | Resultado |
|---|---|
| Home region de la tenancy | Coincide con `sa-vinhedo-1` |
| Bucket | Privado, Standard, clave administrada por Oracle |
| Versionado | Disabled |
| Replicación | false |
| Auto-tiering | Disabled |
| Política de ciclo de vida | Sin transiciones de clase habilitadas |
| Inventario del bucket | 0 bytes |
| Solicitudes de la inspección | 5, contabilizadas por operación |
| Límites de aplicación | 1.000.000.000 bytes / 5.000 solicitudes mensuales |
| Margen de limpieza | 10.000.000 bytes / 500 solicitudes |

El reporte local detallado, sin credenciales, está fuera de Git en
`.data/issue50-oci-inspection.json`. Este snapshot prueba la configuración leída
en esa ejecución; el arranque real vuelve a verificar los controles.

También se verificó la API Budgets real, inicialmente sin presupuestos. Se creó
`nuevamente-cost-governance` para el compartimento del proyecto, período MONTHLY,
monto de observación 1 (mínimo admitido por OCI), con regla ACTUAL al 80%. Una
segunda ejecución confirmó su existencia sin crear copias. No se configuraron
destinatarios ni se enviaron correos. El umbral no autoriza gasto ni lo corta;
los límites de aplicación siguen siendo la protección de consumo.
Reporte local: `.data/issue50-budget-rule.json`.
