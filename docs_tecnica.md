# Documentación Técnica: Pipeline YouTube Automático

## Arquitectura del Sistema (Google Cloud Platform)

El sistema opera bajo un entorno de alta disponibilidad y bajo costo, utilizando una arquitectura dual-backend gestionada por un **Load Balancer (Balanceador de Cargas)** de Google Cloud. El acceso está restringido vía **Google IAP (Identity-Aware Proxy)**, exigiendo que todos los usuarios se autentiquen mediante una cuenta de Google corporativa autorizada antes de alcanzar cualquier infraestructura subyacente.

El dominio base es: `https://yt-pipeline.pronectis.com`

### Componentes de Infraestructura

#### 1. Load Balancer & IAP
- Actúa como la única puerta de entrada pública.
- Verifica tokens de identidad (IAP).
- Encamina el tráfico a uno de los dos Backends dependiendo de la ruta (URL path).

#### 2. Backend 1: Portal de Gestión (Cloud Run)
- **Ruta asociada:** `/` (Raíz).
- **Entorno:** Cloud Run (Serverless).
- **Función:** Script en Python que consulta el estado actual de la Máquina Virtual (VM) en Compute Engine.
- **Flujo:** Si la VM está apagada, expone una interfaz con un botón para encender la instancia a través de la API de GCP. Una vez que la VM está encendida y activa, el usuario puede presionar el botón para **ingresar**, el cual redirige a la aplicación principal montada en `/app`.

#### 3. Backend 2: Servidor de Procesamiento (Compute Engine VM)
- **Ruta asociada:** `/app` y `/api/*`
- **Entorno:** Máquina Virtual (VM) Ubuntu Linux (Compute Engine).
- **Función:** Contiene el "core" pesado de la aplicación de automatización (FFmpeg, Python, FastAPI, Gemini AI).
- **Optimización de enrutamiento:** El middleware de FastAPI (`web_app.py`) intercepta las llamadas entrantes con el prefijo `/app` y lo recorta dinámicamente (`request.scope["path"].replace("/app", "", 1)`) para que las rutas internas de la aplicación funcionen transparente y nativamente como si estuvieran operando en la raíz (`/`).

---

## Mecanismos de Ahorro de Costos y Apagado

Para optimizar el costo de Compute Engine (dado que la automatización de video requiere hardware robusto que es costoso de mantener 24/7), el sistema está programado para **apagarse automáticamente** cuando no está en uso.

1. **Auto-apagado por inactividad (15 min):** 
   - Se ejecuta a través de la tarea en segundo plano `_inactivity_monitor()` dentro de `web_app.py`.
   - El middleware registra el tiempo (timestamp) de la última petición HTTP recibida.
   - Si no hay nuevas peticiones durante 15 minutos, y el pipeline **no** está en ejecución (`runner.status == "idle"`), se envía una notificación por WebSockets (`shutdown_warning`) con una cuenta regresiva de 30 segundos a los clientes conectados.
   - Si no hay interacción para cancelar el apagado en esos 30 segundos, la VM ejecuta internamente los comandos del sistema operativo (`shutdown -h now` / `systemctl poweroff`) para apagar la instancia de Compute Engine.

2. **Apagado Manual (Botón UI):**
   - Implementado en el Panel de Ajustes.
   - Acciona el endpoint `/api/shutdown` invocando la detención inmediata de la instancia de Linux.

---

## Despliegue y Mantenimiento

El código se mantiene exclusivamente a través de un repositorio en **Git**. 
Para subir actualizaciones y desplegarlas:

1. Ingresar por SSH a la VM de Ubuntu en Compute Engine.
2. Navegar al directorio del repositorio (Ej: `/home/usuario/automatizacion-youtube-pronectis`).
3. Ejecutar un `git pull` de la rama principal para descargar los últimos cambios.
4. Reiniciar el servicio que ejecuta Uvicorn / FastAPI para aplicar la nueva versión.
