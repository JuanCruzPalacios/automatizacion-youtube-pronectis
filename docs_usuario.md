# Guía de Usuario: Plataforma de Automatización YouTube (V2)

Bienvenido a la plataforma centralizada de gestión de contenido de Pronectis. Esta herramienta, impulsada por la Inteligencia Artificial de Gemini, te permite procesar, editar, clasificar y publicar videos automáticamente, liberándote de las tareas operativas repetitivas.

---

## 1. Acceso y Encendido del Sistema

Para optimizar costos, el servidor de renderizado de la plataforma **se mantiene apagado** hasta que un usuario lo necesite.

1. **Ingresá al portal:** [yt-pipeline.pronectis.com](https://yt-pipeline.pronectis.com)
2. **Autenticación Segura:** El sistema te pedirá iniciar sesión con tu cuenta de Google (debe estar autorizada internamente).
3. **Encendido a Demanda:**
   - Si el servidor está apagado (pantalla de inicio ligera), vas a ver un botón para **Encender Servidor**.
   - Al hacer clic, Google Cloud arrancará la máquina pesada. Esperá unos minutos y luego haz clic en **Ingresar** para acceder a la aplicación principal.

---

## 2. Iniciar un Nuevo Proyecto (Panel de Control)

Desde la pestaña inicial configurarás cómo debe trabajar la IA y el motor de video:

- **Enlace o Carpeta:** Pega la URL del video de YouTube, una URL directa de archivo en **Google Drive** (ej. `drive.google.com/file/d/...`), o selecciona un archivo de video local de tu computadora (formatos soportados: `.mp4`, `.mov`, `.avi`, `.mkv` y `.m4v`).
- **Formato del Video:**
  - `Normal (16:9)`: Formato horizontal clásico. El sistema pegará la Intro y la Outro corporativas, y normalizará la resolución y los FPS del video.
  - `Short (Cortar bordes)`: Corta el video al centro para adaptarlo a formato vertical (9:16). *No se agregan intros ni outros.*
  - `Short (Blur arriba y abajo)`: Mantiene el video entero en el centro y rellena los espacios vacíos arriba y abajo con un fondo difuminado estético. *No se agregan intros ni outros.*
  - `Ya es Short`: Si el video original ya es vertical, elegí esta opción para que se copie directamente en su formato original. *No se agregan intros ni outros.*
- **Contexto:** Esta es la caja de texto más importante para guiar a la IA (ej. *"Este video es un tutorial de Google Workspace enfocado a empresas argentinas, mencioná la importancia de la ciberseguridad"*).
  - *Nota Importante:* Si utilizas un **video local o un enlace de Google Drive**, el sistema no puede extraer la transcripción automática de audio que provee YouTube. Por lo tanto, el campo de **Contexto es obligatorio y debe tener al menos 50 caracteres** para que Gemini pueda redactar los activos.

---

## 3. El Panel de Revisión (Human-in-the-Loop)

Una vez que el sistema descarga y renderiza el video, entra en estado de **pausa** (a menos que marques subida automática). En la pestaña **Revisión** tienes control total antes de publicar:

- **Revisión de Contenido:** Puedes darle Play al video renderizado para asegurarte de que el formato y las intros (en formato normal) estén bien ensamblados.
- **Edición en Caliente:** ¿No te gustó el título, la descripción, o los tags generados por Gemini? Al lado de cada campo hay un botón de **Regenerar**. Al presionarlo, el sistema te pedirá que ingreses una instrucción o contexto extra para guiar a la IA en la reescritura en tiempo real.
- **Gestión de Miniatura:** El sistema generará una imagen de alta calidad con IA para videos horizontales.
  - **Aplicar Logotipo:** La miniatura inicial no tiene el logo integrado. Si deseas colocarlo, debes hacer clic manualmente en el botón **Aplicar Logo** en el Panel de Revisión. Esto superpondrá el logo transparente de Pronectis en la esquina superior derecha.
  - **Regenerar Miniatura:** Si no te gusta el arte generado, podés presionar **Regenerar Miniatura** (ingresando un nuevo prompt descriptor) para intentar otro concepto visual, o subir una imagen tuya manualmente desde tu computadora.
- **Publicación:** Cuando todo esté perfecto, presioná "Subir a YouTube". El video se subirá de forma *Privada* a tu canal. Asegurate de **seleccionar manualmente la lista de reproducción (Playlist)** correspondiente antes de subirlo para clasificarlo correctamente.

### Control de Errores Interactivo (Continuar o Cancelar)
Si ocurre un inconveniente durante el procesamiento (ej. fallo temporal de red o error de API), el sistema pausará el pipeline y te mostrará un cartel consultando si deseas **Continuar** o **Cancelar**.
* **Cuándo elegir Cancelar (Recomendado):** Si el fallo ocurrió en la descarga del video, recorte o procesamiento multimedia (FFmpeg). Si decides continuar, el video final se generará con cortes abruptos, corrupto o incompleto. Es mejor cancelar (lo que limpia automáticamente los archivos temporales) y volver a empezar.
* **Cuándo elegir Continuar:** Si el fallo ocurrió en un paso de Inteligencia Artificial (ej. error temporal en Gemini al redactar títulos, tags o miniatura). Puedes hacer clic en continuar con seguridad, ya que el video se terminará de procesar bien y podrás generar todos los textos o la miniatura en caliente directamente desde la pantalla de revisión.

---

## 4. Historial de Proyectos

El sistema guarda memoria de tu trabajo reciente:
- **Recuperación:** La pestaña muestra los últimos 5 proyectos que generaste. Si se te cerró la pestaña del navegador o cancelaste un proyecto por accidente, podés hacer clic en él en el Historial. Esto lo enviará nuevamente al Panel de Revisión con todo el progreso intacto (textos, miniatura y video renderizado listos).
- **Estados:** Monitoreá fácilmente si un proyecto está *En progreso*, *Completado* o *Cancelado*.

---

## 5. Ajustes y Apagado Automático

Desde la pestaña de Ajustes puedes configurar plantillas predeterminadas (ej. los links de redes sociales que van siempre al final de cada descripción).

### Eficiencia y Auto-Apagado
No te preocupes por dejar el servidor encendido. La plataforma cuenta con un sofisticado **reloj de inactividad**.
- Mientras el sistema esté procesando y renderizando un video pesado, se mantendrá despierto (aunque demore horas).
- Si el sistema termina de trabajar y pasan **15 minutos sin actividad en el servidor** (sin clics, subidas ni ejecuciones), te mostrará un cartel con una cuenta regresiva de 30 segundos. Si no interactúas con él, se apagará automáticamente para ahorrar recursos.
  - **Advertencia:** El servidor mide la inactividad por tráfico de red. Si pasas más de 15 minutos escribiendo un texto largo localmente en la pantalla de "Contexto" o editando campos sin realizar acciones del servidor (guardar, iniciar, etc.), el temporizador podría activarse. Si aparece el cartel de cuenta regresiva, simplemente haz clic en **Cancelar Apagado** para reiniciar el temporizador de 15 minutos.
- **Apagado Manual:** Siempre que termines tu jornada, se recomienda ir a Ajustes y utilizar el botón de apagado manual para ahorrar al máximo.

---

## 6. FAQ y Resolución de Problemas (Guía del Usuario)

Preguntas y situaciones de uso real de la plataforma resueltas paso a paso:

### Q1: El sistema me da un error inmediato al enviar un archivo de Google Drive o un video local
* **Causa 1 (Enlace Privado):** Si el enlace de Google Drive no es público, la plataforma no puede descargarlo.
* **Causa 2 (Contexto Insuficiente):** Al usar Drive o video local, no hay transcripción de YouTube disponible. El sistema requiere obligatoriamente que expliques de qué trata el video.
* **Resolución:**
  1. Si es de Drive, asegúrate de que el permiso de compartir esté configurado en *"Cualquier persona con el enlace puede ver"* (rol Lector).
  2. Escribe una descripción detallada en el campo de **Contexto** que tenga al menos **50 caracteres** (aproximadamente una oración larga). Si tiene menos de 50 caracteres, el sistema rechazará la solicitud por seguridad.

### Q2: Estoy escribiendo detenidamente el contexto del video y me aparece el aviso de apagado automático
* **Causa:** El sistema mide la "actividad" por peticiones de red al servidor. Si estás redactando un texto directamente en la pantalla de tu navegador durante más de 15 minutos sin guardar ni clickear botones, el servidor cree que la pestaña fue abandonada.
* **Resolución:** No te preocupes, no perderás tu trabajo. Cuando veas el cartel en pantalla con la cuenta regresiva de 30 segundos, haz clic en **Cancelar Apagado**. Esto enviará una señal de actividad y restablecerá el reloj por otros 15 minutos.

### Q3: La miniatura no tiene el logotipo de la empresa integrado cuando la veo en la revisión
* **Causa:** Por diseño y para dar flexibilidad (por si quieres usar la imagen limpia o editarla externamente), el sistema no graba el logotipo automáticamente.
* **Resolución:** En la tarjeta de miniatura del Panel de Revisión, simplemente presiona el botón **Aplicar Logo**. El servidor procesará la imagen y agregará el logo transparente en la esquina superior derecha en segundos.

### Q4: ¿Qué pasa si cierro la pestaña del navegador o se me corta el internet a mitad de un renderizado?
* **Causa:** El procesamiento de video ocurre directamente dentro de la máquina en la nube de Google Cloud, no en tu computadora local.
* **Resolución:** Puedes cerrar el navegador con tranquilidad. El proceso seguirá ejecutándose de fondo en el servidor. Cuando vuelvas a ingresar a la web más tarde, ve a la pestaña **Historial**, selecciona tu proyecto y el sistema te lo cargará en el Panel de Revisión con todo el progreso completado hasta el momento.

### Q5: Los videos se suben siempre como "Privados". ¿Cómo los hago Públicos?
* **Causa:** Por políticas de control de calidad corporativa, la subida a YouTube se realiza con estado *Privado* de forma predeterminada para evitar que videos sin revisar se publiquen automáticamente en el canal.
* **Resolución:** Una vez que el video se sube exitosamente (el sistema te dará el enlace directo en el Panel de Revisión), ingresa al YouTube Studio de la empresa, verifica que el video esté correcto y cambia el estado de visibilidad a **Público** o prográmalo para su publicación.

### Q6: El selector de listas de reproducción (Playlists) se queda cargando o está vacío
* **Causa:** La autenticación de la API de YouTube con la cuenta corporativa necesita actualizarse o expiró de forma inesperada.
* **Resolución:** Recarga la página (`F5`). Si el problema persiste, significa que la sesión del token se cerró por completo. Notifica al administrador técnico para que acceda al servidor mediante **Escritorio Remoto** y reestablezca la sesión con el script de renovación de credenciales.

### Q7: Durante el procesamiento me salió una advertencia de error preguntándome si quiero "Continuar" o "Cancelar". ¿Qué elijo?
* **Causa:** Ocurrió una falla intermedia (ej. microcorte en la API de Gemini o interrupción en el renderizado).
* **Resolución:** 
  - Si el error dice **Descarga, Recorte o Fusión de video (FFmpeg)**: Haz clic firmemente en **Cancelar** y vuelve a iniciar el pipeline. Si continúas, obtendrás un video final corrupto o vacío.
  - Si el error dice **Gemini IA, Título, Descripción o Miniatura**: Haz clic en **Continuar**. El pipeline finalizará correctamente la edición multimedia y podrás regenerar todos los textos e imágenes rotas en el Panel de Revisión una vez finalice.
