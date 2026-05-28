# Automatización de Descarga y Fusión de Videos de YouTube (Intro & Outro)

Este script de automatización en Python descarga un video de YouTube a través de su URL y le agrega de forma automática e integrada la introducción (**Intro Pronectis.mp4**) y el cierre (**Outro Pronectis.mp4**) al inicio y final respectivamente.

La herramienta cuenta con un **sistema de normalización inteligente** que ajusta automáticamente la resolución, la tasa de cuadros (FPS), el formato de color y el audio de los videos de intro/outro para que coincidan exactamente con las propiedades del video descargado de YouTube. Esto evita problemas de desincronización, congelamiento de imagen o fallos al reproducir.

---

## Requisitos del Sistema

1. **Python 3.10+**
2. **FFmpeg** (requerido para el procesamiento de video y la descarga del flujo multimedia de YouTube)

### Instalación de requisitos

Puedes instalar los requisitos ejecutando los siguientes comandos en tu terminal de Windows (PowerShell/CMD):

```powershell
# Instalar Python 3.12 y FFmpeg usando winget
winget install --id Python.Python.3.12 --silent --accept-source-agreements --accept-package-agreements
winget install --id Gyan.FFmpeg --silent --accept-source-agreements --accept-package-agreements
```

> [!NOTE]
> El script de Python cuenta con un auto-refresco del `PATH` en Windows, lo que significa que detectará FFmpeg y Python inmediatamente después de ser instalados sin necesidad de reiniciar tu terminal.

---

## Configuración del Proyecto

1. Ubícate en el directorio del proyecto:
   ```powershell
   cd C:\Users\jpalacios_pronectis\.gemini\antigravity\scratch\youtube_automation
   ```
2. Instala la dependencia de Python requerida (`yt-dlp`):
   ```powershell
   pip install -r requirements.txt
   ```

---

## Modo de Uso

El script se ejecuta desde la línea de comandos con `python automate.py`. Los archivos `Intro Pronectis.mp4` y `Outro Pronectis.mp4` deben encontrarse en el mismo directorio que el script.

### Parámetros Disponibles

* `-u`, `--url` (Obligatorio): La dirección/URL del video de YouTube a procesar.
* `--out` (Opcional): Nombre y ruta del archivo final generado. Por defecto se guarda como `final_output.mp4` en el directorio del script.

### Ejemplos de Comandos

#### 1. Descargar video y fusionar con Intro y Outro (nombre por defecto)
```powershell
python automate.py -u "https://www.youtube.com/watch?v=jNQXAC9IVRw"
```

#### 2. Descargar video y fusionar con Intro y Outro (nombre personalizado)
```powershell
python automate.py -u "https://www.youtube.com/watch?v=jNQXAC9IVRw" --out "video_final_pronectis.mp4"
```

---

## Cómo Funciona Internamente

1. **Descarga**: `yt-dlp` descarga el video de YouTube seleccionando la mejor calidad disponible (en contenedor compatible MP4) y lo guarda de forma temporal.
2. **Análisis**: `ffprobe` analiza el video descargado para determinar su resolución exacta (ancho x alto), su FPS de origen y si tiene canal de audio.
3. **Normalización y Fusión**:
   * **Video**: Cada clip (intro, video principal, outro) se escala manteniendo la relación de aspecto original (`force_original_aspect_ratio=decrease`). Si hay espacio sobrante, se agregan barras negras (letterbox/pillarbox) automáticas para evitar que la imagen se estire o se deforme.
   * **FPS**: Se unifican todos los clips a la tasa de cuadros del video principal.
   * **Audio**: Todo el audio se resamplea a 48000 Hz, estéreo. Si algún clip (como la intro) no tiene pista de audio, el script genera un canal de silencio con la duración exacta de ese clip para evitar desincronizaciones en el archivo de salida final.
4. **Limpieza**: Se eliminan los archivos temporales de descarga de forma segura al finalizar.
