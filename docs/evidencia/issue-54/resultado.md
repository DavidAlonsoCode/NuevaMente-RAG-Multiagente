# Redes VCN en Oracle Cloud Infrastructure

**Audiencia:** Principiante / Transición de carrera

**Idioma:** Español

**Tiempo estimado de estudio:** 15 minutos

## Objetivos de aprendizaje

- Comprender qué es una Virtual Cloud Network \(VCN\) y cómo aísla recursos\.
- Identificar la diferencia estructural entre subredes públicas y privadas mediante tablas de ruteo\.
- Reconocer las funciones principales de los gateways y los mecanismos de seguridad en OCI\.

## Prerrequisitos

Sin prerrequisitos declarados.

## Conceptos clave

- Virtual Cloud Network \(VCN\)
- CIDR
- Subred pública
- Subred privada
- Gateway
- Security List
- Network Security Group

## Introducción

Una Virtual Cloud Network \(VCN\) es una red privada que aísla y organiza los recursos de cómputo dentro de Oracle Cloud Infrastructure \(OCI\)\. Esta introducción presenta los conceptos fundamentales para quienes se inician en la nube\.

## Tarjetas

### Tarjeta 1

**Frente:** ¿Qué es una VCN?

**Dorso:** Vamos paso a paso: Una Virtual Cloud Network \(VCN\) es la red privada que aísla y organiza los recursos de cómputo dentro de Oracle Cloud Infrastructure\. Analogía ilustrativa: \(Escenario ilustrativo\) Piensa en una VCN como un edificio corporativo privado donde cada piso y oficina tiene reglas estrictas sobre quién puede entrar o salir\.

**Pista didáctica:** Piensa en un contenedor de red regional\.

**Etiquetas:** vcn, redes, oci

[^1]

### Tarjeta 2

**Frente:** ¿Qué decide si una subred es pública o privada en OCI?

**Dorso:** Vamos paso a paso: Lo que decide si una subred es pública o privada NO es una casilla de configuración, sino su tabla de ruteo\. Si la tabla envía el tráfico hacia un Internet Gateway, la subred es pública\. Analogía ilustrativa: \(Escenario ilustrativo\) Es como la puerta principal de una casa: si tiene una puerta abierta directamente a la calle principal, es pública; si solo conecta con pasillos internos hacia otras habitaciones seguras, es privada\.

**Pista didáctica:** Revisa el destino configurado en la tabla de ruteo\.

**Etiquetas:** subredes, ruteo, oci

[^2]

### Tarjeta 3

**Frente:** ¿Para qué sirve un NAT Gateway?

**Dorso:** Vamos paso a paso: Un NAT Gateway permite la salida exclusiva a Internet para instancias privadas que necesitan actualizarse o llamar a APIs externas sin exponerse a recibir conexiones desde Internet\. Analogía ilustrativa: \(Escenario ilustrativo\) Es como un buzón de salida: puedes enviar cartas hacia afuera, pero el cartero de la calle no puede usar ese mismo buzón para meter correspondencia directamente a tu escritorio\.

**Pista didáctica:** Permite solo la salida de tráfico sin IP pública\.

**Etiquetas:** gateways, nat, oci

[^3]

### Tarjeta 4

**Frente:** ¿Qué diferencia existe entre un Security List \(SL\) y un Network Security Group \(NSG\)?

**Dorso:** Vamos paso a paso: Una Security List \(SL\) se aplica a nivel de subred completa compartida por todas sus instancias, mientras que un Network Security Group \(NSG\) se aplica a nivel de interfaz de red \(vNIC\) individual, permitiendo reglas específicas por rol\. Analogía ilustrativa: \(Escenario ilustrativo\) El SL es como el guardia de seguridad de la entrada principal del edificio para todos los empleados de ese piso, y el NSG es como la tarjeta de acceso digital exclusiva para la puerta de una oficina específica dentro de ese piso\.

**Pista didáctica:** Compara el nivel de aplicación: subred completa versus interfaz de red\.

**Etiquetas:** firewall, sl, nsg

[^4]

## Documento fuente

Redes VCN en Oracle Cloud Infrastructure

**Versión:** v1

SHA-256: f29f930aa861f4069631181286dbcca510779bf61e4b3298c734f1fb02087214

## Referencias

[^1]: Fragmento: chk\_2b19b3dab8a99b7a5c40fd73 · Página: 1
[^2]: Fragmento: chk\_bcf3ca1a72533af87361c442 · Página: 2
[^3]: Fragmento: chk\_67ec0307904c6e71b46b25b6 · Página: 4
[^4]: Fragmento: chk\_cf47c9be5867247aeb56ea11 · Página: 4
