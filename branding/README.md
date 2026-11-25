# Identidad de tu instalación

Lo que dejes aquí no se versiona (salvo este fichero) y sobrevive a las actualizaciones.

| Fichero | Para qué |
|---|---|
| `logo.png` | Logo de la cabecera de los presupuestos en PDF. PNG con fondo transparente, cuadrado o apaisado, de unos 400 px de ancho. |

Los datos del emisor (razón social, dirección, pie con IBAN y CIF) se editan dentro del CRM, en
**Presupuestos → Plantillas**. El prefijo de la numeración, con `QUOTE_NUMBER_PREFIX` en `.env`.

Después de cambiar el logo no hace falta reiniciar: el siguiente PDF ya lo usa.
