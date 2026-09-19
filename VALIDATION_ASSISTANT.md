# Validación del asistente de texto y voz

- `python3 -m unittest discover -s tests`: 115 pruebas aprobadas, 28 nuevas.
- `python3 -m src.main --help`: correcto; se conservan los comandos existentes.
- `git diff --check`: sin errores.

Las regresiones nuevas cubren herramientas permitidas, importes y unidades,
modo simulación por defecto, confirmación por teclado, errores HTTP y timeout sin
reintentos, dictado hasta la revisión, limpieza de audio y cancelación. Se probaron
Enter, Ctrl+T, Escape, Ctrl+C y el temporizador con la entrada real de prompt-toolkit y un
micrófono simulado. Los datos son sintéticos y pequeños.

No se hicieron llamadas reales a Groq/Buda ni se enviaron órdenes. Falta validar
un dictado con micrófono físico y la cuenta Groq del usuario. Se creó `.venv` con `requirements-voice.txt` y se verificó la carga de
sounddevice 0.5.6 y PortAudio 19.7.0, sin abrir el micrófono.

Ctrl+T se verificó con entrada de terminal real desde el menú principal y el
selector del asistente, incluyendo inicio directo del dictado sin otro menú.

Ajuste de siglas: se agregó contexto de vocabulario a la solicitud de Whisper y
nombres/deletreos al intérprete, con instrucción de aclarar ambigüedades.
Las 28 pruebas de asistente, voz y atajo pasan. Se verificó el envío del vocabulario
a Groq con HTTP simulado; la mejora de reconocimiento requiere probar voz real.

Unidad de venta: se aclaró la diferencia entre la moneda del importe y la moneda
recibida. Los errores de preparación vuelven al asistente para corregir o pedir
aclaración, con un máximo de cuatro llamadas. Se probaron la recuperación del caso
«Vender 0.3 USDC a CLP», ventas por equivalente en CLP y el límite de correcciones,
sin llamadas de trading. Suite completa: 118 pruebas aprobadas. Groq simulado;
no se enviaron órdenes reales ni se verificó el modelo remoto con este pedido.
