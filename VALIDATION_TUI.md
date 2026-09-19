# Validación de la interfaz de cuenta

## Revisión independiente y correcciones — 2026-09-19

- Corregidos tres P1 confirmados por un agente independiente: detención que interrumpía la limpieza tras un error; pérdida de seguimiento del hijo ante una línea de salida mayor a 64 KiB; grilla inaccesible por prompts síncronos dentro del event loop de Textual.
- La limpieza ignora SIGINT/SIGTERM antes de cancelar; el lector solicita detención y sigue drenando hasta que termine el hijo ante salida excesiva. La grilla se ejecuta entre sesiones del dashboard, en el hilo principal y fuera del event loop; al volver se abre una nueva sesión del panel.
- Regresiones con procesos sintéticos y prompt InquirerPy real. Suite completa: 134 pruebas aprobadas. `git diff --check` sin errores. Sin red, credenciales ni operaciones reales.
- El agente independiente volvió a revisar las correcciones y ejecutó las seis pruebas de procesos: aprobadas, sin nuevos bloqueos críticos.
- P2 resuelto: cada consulta del dashboard recibe una señal de cancelación cooperativa. El asistente la verifica antes y después de llamadas al modelo/herramientas y antes de guardar su respuesta. `/limpiar` invalida los turnos pendientes; un bloqueo protege el borrado y la incorporación de respuestas al historial.
- Regresiones P2: cancelación antes de iniciar el hilo, durante respuesta del modelo y durante consulta a Buda; borrado con respuesta pendiente; secuencia real del panel Ctrl+C → `/limpiar` → conversación nueva. Aprobadas las 20 pruebas de asistente y las 10 de dashboard. Datos sintéticos mínimos, sin red. Una petición HTTP ya enviada puede finalizar, pero su resultado cancelado se descarta.

Las secciones siguientes registran etapas anteriores de implementación.

- Se tomó como referencia la composición y CSS de `bot-zesty`: Header, navegación lateral de 24 columnas, ContentSwitcher, tablas, barra de texto/voz y Footer.
- `python -m unittest discover -s tests -q`: 122 pruebas aprobadas.
- Tras ajustar el manejo de errores de grilla y revisión: 4 pruebas de dashboard aprobadas.
- Pruebas headless con Textual 8.2.8: balances, navegación al libro, comandos, preparación sin envío, validación de importes, cambio de unidades/estrategia, recuperación ante error y Ctrl+T.
- Fixtures sintéticos mínimos; sin consultas a Buda/Groq, grabación de micrófono ni órdenes reales.
- Se generó una vista SVG temporal; el navegador no permitió abrir el archivo local. No se completó inspección visual en navegador.
- La revisión/ejecución de órdenes, grillas y grabación conservan los flujos de terminal existentes mediante suspensión temporal de la pantalla Textual. No se probó ejecución real ni micrófono.

## Corrección de confirmación de órdenes

- Reproducido: la confirmación síncrona de InquirerPy fallaba después del resumen con `RuntimeError: asyncio.run() cannot be called from a running event loop` al abrirse desde Textual.
- La pantalla usa ahora `execute_async()` para confirmar, comparte la preparación del resumen con el menú clásico y conserva la ejecución del bot en el hilo principal para mantener su manejo de Ctrl+C.
- Regresión con prompt real y entrada de teclado sintética dentro de Textual: Enter cancela; confirmar compra/venta alcanza el motor una vez con los parámetros originales; se cubre también el acceso desde el asistente. Sólo se sustituye el motor de trading, sin envíos externos.
- Suite completa: 123 pruebas aprobadas. No se verificaron operaciones reales.

## Seguimiento y detención dentro del panel

- Compras/ventas: revisión modal nativa con Volver por defecto y registro continuo en Proceso. Ya no se suspende Textual para confirmar o ejecutar estas órdenes.
- El motor corre en un proceso independiente de la terminal de la interfaz. Detener, Ctrl+C y Ctrl+Q solicitan su limpieza y mantienen el panel abierto. Los pedidos repetidos no interrumpen la limpieza.
- Se espera a que el proceso instale su manejo de señales antes de enviar la detención; la cancelación del worker también espera la limpieza. No se fuerza la terminación del motor.
- Pruebas: confirmación/cancelación nativa, compras y ventas, ruta del asistente, navegación tras detener y señal real a un proceso con bot sintético. Se verifican limpieza ante interrupción/error y ausencia de cancelación tras éxito normal.
- Suite completa: 128 pruebas aprobadas, sin red ni órdenes reales. Las grillas y la grabación de voz mantienen sus flujos auxiliares de terminal.

## Dictado dentro del panel

- Se reemplazó la suspensión de la terminal por un diálogo Textual con contador, límite de 30 segundos, botones para terminar/transcribir y descartar, y mensajes de error del micrófono.
- Enter/Ctrl+T termina; Escape/Ctrl+C descarta sin enviar audio. El dispositivo se libera también ante errores y al desmontar el diálogo.
- La transcripción y la respuesta quedan visibles en el chat. Los estados distinguen transcripción y consulta; cancelar durante la transcripción impide enviar después su resultado al asistente, aunque el audio ya enviado pueda haber llegado a Groq.
- Pruebas con micrófono y modelo sintéticos: atajos, botones, límite automático, permisos denegados, liberación del dispositivo, transcripción visible y cancelación de una transcripción pendiente. No se abrió un micrófono real ni se llamó a Groq.
